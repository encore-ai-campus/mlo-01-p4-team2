"""구간별 정합 manifest의 규칙(DRD §5.6 "구간별 manifest 계약", 계획 4-3b). 파이프라인 쪽이 같은 규칙으로 같은 값을 만들 수 있게 **언어 중립 규칙**으로 고정한다.

- **창**: `produced_at` 기준 UTC 자정 고정 하루(기본, 옵션으로 UTC 정시 고정 한 시간 — `WINDOWS`). 창이 끝나고 `grace` 초(설정 `EVENTS_HOP_GRACE_SECONDS`, 기본 120)가 지나면 닫힌 창이다. 닫히기 전의 창은 요약하지 않는다.
- **문서 형태**(MongoDB 에 쓰는 형태): `{"event_id", "event_name", "schema_version", "produced_at", "payload"}`. `event_id` 는 소문자 하이픈 UUID 문자열,
  `produced_at` 은 **밀리초까지의 UTC** 문자열 `YYYY-MM-DDTHH:MM:SS.mmmZ`(MongoDB 날짜의 정밀도). `payload` 는 이벤트에 저장된 JSON 그대로.
- `ids_sha256`: 창·이벤트 이름의 이벤트 ID 문자열을 오름차순으로 정렬해 `\\n` 으로 이은 UTF-8 바이트의 SHA-256(0건이면 빈 바이트의 해시).
- `content_sha256`: 위 문서를 정규화 JSON 으로 만들어 이벤트 ID 순으로 `\\n` 으로 이은 UTF-8 바이트의 SHA-256. 정규화는 **RFC 8785(JCS)**: 객체 키를 UTF-16 코드 단위 순으로 정렬,
  공백 없음, 문자열은 JSON 이스케이프(비 ASCII 는 그대로), 불리언·`null`·숫자는 JSON 값 그대로, 정수는 10진수, 실수는 ECMAScript 숫자 문자열(`300.0` → `300`).
  정수와 같은 값의 실수는 JCS 에서 같은 문자열이 된다 — 타입까지의 엄격한 구분은 중계의 충돌 판정과 `verify_events` 가 맡는다.

이 파일이 **공용 요약 규칙**이다(계획 02 P1-1, 설계 01 D19·S5). 표준 라이브러리만 쓴다 — Django·프로젝트 모듈·상대 import 를 쓰지 않는다
(시험 `case_hop_rules_is_stdlib_only`). 파이프라인 쪽(Bronze manifest 잡)은 이 파일을 그대로 복사해 쓰고, `rule_sha256()` 을 manifest 의
`rule_sha256` 에 실어 광고 서버와 같은 판인지 맞춘다. 내용을 바꾸면 해시가 바뀌므로 파이프라인 쪽 사본도 함께 바꾼다.
Bronze 행 요약(`summarize_changes`)은 원천 설정(`ADS`·`METAVERSE` — MVR-1)을 받는다. 원천별 처리는 그 함수 안에서만 하고, 위의 창·문서·정규화 함수의 모양과 출력은 원천과 무관하다.
"""
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

DAY = timedelta(days=1)
HOUR = timedelta(hours=1)


# ---------------------------------------------------------------- 구간 계약 값 (내보내는 쪽 `export_hop_manifests` 와 대조하는 쪽 `hopcheck` 이 같이 쓴다)

HOP_SCHEMA_VERSION = 1  # 구간 줄 모양의 계약 버전(manifest 의 `schema_version`)
WINDOWS = {"day": DAY, "hour": HOUR}  # 창의 크기: UTC 자정 고정 하루(기본), 옵션으로 UTC 정시 고정 한 시간


def window_label(name):
    """창 옵션 이름(`day`·`hour`) → `checkpoint.window` 선언(`utc-day`·`utc-hour`)."""
    return f"utc-{name}"


WINDOW_SIZES = {window_label(name): size for name, size in WINDOWS.items()}  # `checkpoint.window` 선언 → 크기(대조 쪽이 읽는다)

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # 창 경계·열린 창·보존 기준 시각(초까지의 UTC)
MILLIS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"  # 문서의 `produced_at`(밀리초까지의 UTC, 출력은 `_millis_utc`)


def format_time(moment):
    return moment.strftime(TIME_FORMAT)  # 받은 UTC 시각 그대로(변환하지 않는다)


def parse_time(text):
    """`format_time` 의 반대. 형식이 아니면 `ValueError`(문자열이 아니면 `TypeError`)."""
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- 정규화 (RFC 8785)

def _es_number(value):
    """ECMAScript `Number::toString` 과 같은 실수 표기(JCS 가 요구한다)."""
    if not math.isfinite(value):
        raise ValueError("JSON 에 NaN·무한대는 없다")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    normalized = Decimal(repr(abs(value))).normalize().as_tuple()
    digits, exponent = normalized.digits, normalized.exponent
    k = len(digits)
    n = k + exponent  # 10^(n-1) <= 값 < 10^n
    d = "".join(map(str, digits))
    if k <= n <= 21:
        return sign + d + "0" * (n - k)
    if 0 < n <= 21:
        return sign + d[:n] + "." + d[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + d
    e = n - 1
    mantissa = d[0] + ("." + d[1:] if k > 1 else "")
    return f"{sign}{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"


def canonical_json(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(int(value))
    if isinstance(value, float):
        return _es_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: kv[0].encode("utf-16-be"))  # UTF-16 코드 단위 순
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical_json(v) for k, v in items) + "}"
    raise TypeError(f"정규화할 수 없는 타입: {type(value).__name__}")


def _millis_utc(moment):
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _produced(doc):
    return datetime.strptime(doc["produced_at"], MILLIS_FORMAT).replace(tzinfo=timezone.utc)


def normal_document(event_id, event_name, schema_version, produced_at, payload):
    return {"event_id": str(event_id).lower(), "event_name": event_name, "schema_version": schema_version, "produced_at": _millis_utc(produced_at), "payload": payload}


# ---------------------------------------------------------------- 창과 요약

def window_start(moment, size=DAY):
    moment = moment.astimezone(timezone.utc)
    return moment.replace(minute=0, second=0, microsecond=0) if size == HOUR else moment.replace(hour=0, minute=0, second=0, microsecond=0)


def is_closed(start, now, grace_seconds, size=DAY):
    return start + size + timedelta(seconds=grace_seconds) <= now


def summarize(documents, now, grace_seconds, since=None, size=DAY):
    """정규화 문서(`normal_document`) 들을 (창, 이벤트 이름)별로 요약한다. 반환: (줄 목록, 열린 창 시작 목록). 열린 창은 줄에 넣지 않는다."""
    return _lines(((_produced(doc), doc) for doc in documents), now, grace_seconds, since, size, _normal_id, _normal_name)


def _normal_id(doc):
    """5필드 문서의 ID — 문서 값 그대로(소문자화는 `normal_document` 의 일)."""
    return doc["event_id"]


def _normal_name(doc):
    return doc["event_name"]


def _lines(entries, now, grace_seconds, since, size, id_of, name_of):
    """항목 (창 시각, 문서) 들을 (창, 이벤트 이름)별 줄로 — 원천과 무관한 한 곳(MVR-1). 이름(`name_of`)·ID(`id_of`)는 since·열린 창 판정 뒤에만 읽는다
    (그 앞에서 걸러지는 문서의 필드는 보지 않는다). 같은 줄 안은 ID 하나로만 안정 정렬한다(같은 ID 의 두 문서는 들어온 순서 그대로 `content_sha256` 에 들어간다)."""
    groups, open_windows = {}, set()
    for moment, doc in entries:
        start = window_start(moment, size)
        if since is not None and start < since:
            continue
        if not is_closed(start, now, grace_seconds, size):
            open_windows.add(start)
            continue
        groups.setdefault((start, name_of(doc)), []).append(doc)
    lines = []
    for (start, name), docs in sorted(groups.items()):
        docs.sort(key=id_of)
        lines.append({
            "window_start": format_time(start), "window_end": format_time(start + size), "event_name": name, "count": len(docs),
            "ids_sha256": hashlib.sha256("\n".join(id_of(d) for d in docs).encode("utf-8")).hexdigest(),
            "content_sha256": hashlib.sha256("\n".join(canonical_json(d) for d in docs).encode("utf-8")).hexdigest(),
        })
    return lines, sorted(open_windows)


# ---------------------------------------------------------------- 파이프라인 구간: Bronze 행(Debezium 원본 봉투) → hop_pipeline 줄 (DRD §5.6, 구현 P1-3·P2-1, 원천별 설정 MVR-1)

CONTENT_COLUMNS = ("event_id", "event_name", "schema_version", "aggregate_id", "payload", "created_at")  # 사본이 같은지 가르는 after 6열(sent_at 은 뺀다)
PIPELINE_COUNTS = ("duplicates", "conflicts", "not_insert")  # hop_pipeline 줄에만 있는 필수 필드
EXAMPLE_FIELDS = ("duplicate_examples", "conflict_examples", "not_insert_examples", "not_insert_unplaced_examples")  # checkpoint 의 위치 예시(각 최대 EXAMPLES 개)
EXAMPLES = 10
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# 원천 설정(MVR-1 — 계획 temp/review/MVR-1-plan.md D1~D4). 이 파일 안에 두어 `rule_sha256` 이 설정까지 덮는다. 값(필드)으로 읽고 정체(`is`)로 가리지 않는다.
# - table: 출처 (스키마, 표) — DB 이름은 환경마다 달라 부르는 쪽이 준다. id·kind·time: after 의 ID·종류·창 시각 열.
# - document: 창 줄의 정규화 문서. "normal" = MongoDB 모양 5필드(`normal_document`, 내용 정체성은 `after_identity` 6열),
#   "content" = 내용 비교 열 객체(`content` 열, `json` 열은 해석한 값, `times` 열은 UTC 마이크로초) — 내용 정체성과 같은 객체.
ADS = {"name": "ADS", "table": ("public", "events_outbox"), "id": "event_id", "kind": "event_name", "time": "created_at", "document": "normal"}
METAVERSE = {
    "name": "METAVERSE", "table": ("public", "eventing_eventoutbox"), "id": "event_uuid", "kind": "event_type", "time": "created_at", "document": "content",
    "content": ("event_uuid", "event_type", "topic", "message_key", "raw_data", "occurred_at", "payload_json", "created_at"),  # 배달 2열(published_at·retry_count)은 뺀다(04 §25)
    "json": ("raw_data", "payload_json"), "times": ("occurred_at", "created_at"),
}
# Debezium ZonedTimestamp(UTC, 소수 0~6자리) — Silver 메타버스 규칙(pipeline/silver/adapters/metaverse_rules.py `_UTC_RE`·`parse_utc`)과 같은 받기
_UTC_TEXT = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z")


def after_identity(after):
    """두 사본이 같은 내용인지 가르는 값(DRD §5.6 (5)): 6열 객체 — payload 는 JSON 으로 해석한 값, 나머지는 원문 값(created_at 마이크로초 문자열·schema_version 정수 그대로) — 의 JCS.
    payload 의 공백·키 순서·`1` 과 `1.0` 차이는 같은 내용이다. 한계: content_sha256 은 5개 필드라, 사본이 하나뿐인 행의 aggregate_id 만 바뀐 경우는 hop 대조로 잡지 못한다
    (두 사본이 서로 다를 때만 충돌로 잡힌다)."""
    return canonical_json({c: json.loads(after["payload"]) if c == "payload" else after.get(c) for c in CONTENT_COLUMNS})


def _key_event_id(key):
    """메시지 키(JsonConverter — 스키마를 담으면 `payload` 안)의 event_id. 키가 없거나(TRUNCATE) 모양이 다르면 None."""
    return _key_id(key, "event_id")


def _key_id(key, field):
    """메시지 키의 ID 필드(원천 설정의 `id`). 키가 없거나 모양이 다르면 None."""
    try:
        data = json.loads(key) if key is not None else None
    except ValueError:
        return None
    data = data.get("payload", data) if isinstance(data, dict) else None
    return str(data[field]).lower() if isinstance(data, dict) and data.get(field) is not None else None


def _utc_text_time(text):
    """Debezium ZonedTimestamp → 시간대가 있는 datetime. 모양이 다르거나 없는 날짜면 ValueError — 메시지에 입력을 싣지 않는다."""
    match = _UTC_TEXT.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise ValueError("UTC 시각 모양이 아니다")
    *parts, fraction = match.groups()
    return datetime(*map(int, parts), int((fraction or "").ljust(6, "0")), tzinfo=timezone.utc)


def _utc_micros(moment):
    """UTC 마이크로초 6자리 `YYYY-MM-DDTHH:MM:SS.ffffffZ` — strftime 을 쓰지 않는다(Silver `utc_micros` 와 같은 짜기)."""
    m = moment.astimezone(timezone.utc)
    return f"{m.year:04d}-{m.month:02d}-{m.day:02d}T{m.hour:02d}:{m.minute:02d}:{m.second:02d}.{m.microsecond:06d}Z"


def _content(after, source):
    """내용 비교 열 객체(문서 "content" 원천 — 메타버스는 Silver SL-4 `content_sha256` 과 같은 객체). 열이 없으면 KeyError(조용히 None 으로 넣지 않는다).
    JSON 열이 NULL 이면 JSON null(None)로 센다(계획 E3 (가), 사용자 2026-11-05 — 앱은 payload_json 을 NULL 로 넣을 수 있다). 문자열도 NULL 도 아닌 값은 TypeError(멈춤)."""
    return {c: _json_value(after[c]) if c in source["json"] else _utc_micros(_utc_text_time(after[c])) if c in source["times"] else after[c] for c in source["content"]}


def _json_value(text):
    return None if text is None else json.loads(text)


def _place(after, source, size):
    """한 행의 (소문자 ID, 놓이는 줄 (창 시작, 종류), 창 시각)."""
    if source["document"] == "normal":
        created = datetime.fromisoformat(after["created_at"])
        if created.tzinfo is None:  # 지역 시각으로 읽혀 창이 조용히 어긋나지 않게 — Debezium 은 늘 Z 를 붙인다(형식이 깨진 after 는 멈춘다)
            raise ValueError(f"after.created_at 에 시간대가 없다: {after['created_at']!r}")
    else:
        created = _utc_text_time(after[source["time"]])
    place = (window_start(created, size), after[source["kind"]])
    return str(after[source["id"]]).lower(), place, created


def _identity(after, source):
    """(내용 해시 바이트, 줄 문서로 쓸 내용 객체 또는 None). 광고는 `after_identity`(문서는 5필드라 None), "content" 원천은 내용 객체의 JCS."""
    if source["document"] == "normal":
        return hashlib.sha256(after_identity(after).encode("utf-8")).digest(), None
    content = _content(after, source)
    return hashlib.sha256(canonical_json(content).encode("utf-8")).digest(), content


def summarize_changes(rows, db_name, now, grace_seconds, since=None, size=DAY, *, source=ADS):
    """Bronze 행을 `hop_pipeline` 줄로 요약한다(DRD §5.6 "한 줄"·`checkpoint` [P1-3·P2-1]). 판정은 창마다가 아니라 **읽은 범위 전체에서 event_id 로 먼저 모아** 한다.

    `rows`: (key, value, partition, offset) — key·value 는 메시지의 JSON 문자열(또는 바이트), value 가 None 이면 tombstone. `db_name`: 이 파이프라인의 원천 DB 이름.
    `source`: 원천 설정(`ADS` 기본, `METAVERSE` — MVR-1). 아래 event_id·이벤트 이름·created_at 은 원천 설정의 ID·종류·창 시각 열이다.
    - op `c`·`r` 행: 고유 event_id 로 `count`·두 해시를 낸다. 같은 event_id·같은 내용(`after_identity` — "content" 원천은 내용 객체)의 남는 행은 그 줄의 `duplicates`.
      내용이 다른 사본이 있으면 그 event_id 는 통째로 충돌 — `count`·해시·`duplicates` 에 넣지 않고, 사본이 놓인 모든 (창, 이벤트 이름) 줄의 `conflicts` 에 1씩.
    - op 가 `c`·`r` 가 아니고 after 가 있는 행(`u`): 자기 after 의 줄에 `not_insert` 1(같은 event_id 의 c·r 행은 위대로 센다).
    - value 가 None 이거나 after 가 없는 행(DELETE·tombstone·TRUNCATE): `not_insert_unplaced`. source 가 `<db_name>.<원천 표>` 가 아닌 행: `foreign_source_rows`.
      둘은 어느 줄에도 놓을 수 없으므로 checkpoint 에 싣고, 0 보다 크면 대조 쪽이 그 쌍을 입력 실패로 본다.
    - "content" 원천(메타버스)의 행을 해석할 수 없으면(내용 열 없음·JSON·정규화(깊은 중첩의 RecursionError 포함)·시각 모양) 요약하지 않고 멈춘다(계획 E1 (가)) —
      메시지에는 원천·event_id·partition·offset·예외 종류만 싣고 값은 싣지 않는다(원래 예외도 남기지 않는다). 계약 위반이지만 해석되는 행은 센다(배달 대사).
    반환: (줄 목록, 열린 창 시작 목록, checkpoint 필드). `count` 가 0 이어도 세 건수 중 하나가 0 보다 크면 줄을 낸다. 열린 창·since 앞 창은 줄에 넣지 않는다.
    """
    # ponytail: 읽은 범위의 event_id 마다 첫 문서·내용 해시를 메모리에 둔다(사본 수가 아니라 고유 이벤트 수에 비례). 범위가 아주 크면 Spark 쪽에서 event_id 로 묶어 같은 판정을 한다(P2-1)
    groups, not_insert, unplaced, foreign = {}, Counter(), 0, 0
    examples = {field: [] for field in EXAMPLE_FIELDS}
    table = (db_name, *source["table"])

    def note(field, event_id, partition, offset):
        if len(examples[field]) < EXAMPLES:
            examples[field].append({"event_id": event_id, "partition": partition, "offset": offset})

    for key, value, partition, offset in rows:
        envelope = None if value is None else json.loads(value)
        if envelope is None:  # tombstone(DELETE 뒤)
            unplaced += 1
            note("not_insert_unplaced_examples", _key_id(key, source["id"]), partition, offset)
            continue
        origin = envelope.get("source") or {}
        if (origin.get("db"), origin.get("schema"), origin.get("table")) != table:
            foreign += 1
            continue
        after = envelope.get("after")
        if after is None:  # DELETE(op d)·TRUNCATE(op t)
            unplaced += 1
            note("not_insert_unplaced_examples", _key_id(key, source["id"]), partition, offset)
            continue
        try:
            event_id, place, created = _place(after, source, size)
            if envelope.get("op") not in ("c", "r"):  # r = 재발행(증분 스냅샷) — outbox 는 INSERT 만이라 c 와 같은 문서다
                not_insert[place] += 1
                note("not_insert_examples", event_id, partition, offset)
                continue
            identity, content = _identity(after, source)
        except (KeyError, TypeError, ValueError, RecursionError) as error:
            if source["document"] == "normal":  # 광고: 지금 그대로(DRD §5.6 (7) — 형식이 깨진 after 는 그 예외로 멈춘다)
                raise
            raw_id = after.get(source["id"]) if isinstance(after, dict) else None
            raise ValueError(f"원천 {source['name']} 의 행을 해석하지 못해 멈춘다 — event_id {None if raw_id is None else str(raw_id).lower()}, "
                             f"partition {partition}, offset {offset}, 사유 {type(error).__name__}") from None  # 값을 남기지 않게 원래 예외를 잇지 않는다
        group = groups.get(event_id)
        if group is None:  # [첫 사본의 내용 해시, 첫 사본의 줄 만들기 항목 (창 시각, 문서), 사본이 놓인 줄, 사본 위치(예시용, 최대 EXAMPLES), 사본 수, 충돌]
            if content is None:
                doc = normal_document(after["event_id"], after["event_name"], after["schema_version"], created, json.loads(after["payload"]))
                entry = (_produced(doc), doc)
            else:
                entry = (created, content)
            groups[event_id] = [identity, entry, {place}, [(partition, offset)], 1, False]
            continue
        group[2].add(place)
        if len(group[3]) < EXAMPLES:
            group[3].append((partition, offset))
        group[4] += 1
        group[5] = group[5] or identity != group[0]

    entries, duplicates, conflicts = [], Counter(), Counter()
    for event_id, (_identity_hash, entry, places, positions, copies, conflict) in groups.items():
        if conflict:  # 통째로 충돌 — 사본이 놓인 모든 줄에 1씩, 건수·해시·duplicates 에는 넣지 않는다
            for place in places:
                conflicts[place] += 1
            for partition, offset in positions:
                note("conflict_examples", event_id, partition, offset)
            continue
        entries.append(entry)
        duplicates[next(iter(places))] += copies - 1  # 같은 내용이면 created_at·event_name 도 같아 줄은 하나다
        for partition, offset in positions[1:]:
            note("duplicate_examples", event_id, partition, offset)

    if source["document"] == "normal":
        id_of, name_of = _normal_id, _normal_name
    else:  # 내용 객체의 ID 는 원문 그대로 들어 있다 — 정렬·ID 해시는 소문자로(위 event_id 와 같은 값)
        id_of, name_of = (lambda doc: str(doc[source["id"]]).lower()), (lambda doc: doc[source["kind"]])
    lines, open_windows = _lines(entries, now, grace_seconds, since, size, id_of, name_of)
    by_key = {(parse_time(line["window_start"]), line["event_name"]): line for line in lines}
    open_windows = set(open_windows)
    for start, name in {k for counter in (duplicates, conflicts, not_insert) for k, n in counter.items() if n}:
        if since is not None and start < since:
            continue
        if not is_closed(start, now, grace_seconds, size):
            open_windows.add(start)
            continue
        by_key.setdefault((start, name), {"window_start": format_time(start), "window_end": format_time(start + size), "event_name": name, "count": 0,
                                          "ids_sha256": EMPTY_SHA256, "content_sha256": EMPTY_SHA256})
    out = [line | {"duplicates": duplicates[key], "conflicts": conflicts[key], "not_insert": not_insert[key]} for key, line in sorted(by_key.items())]
    return out, sorted(open_windows), {"not_insert_unplaced": unplaced, "foreign_source_rows": foreign, **examples}


def rule_sha256():
    """이 파일(공용 요약 규칙)의 SHA-256 — 파이프라인 쪽 사본이 같은 판인지 manifest 의 `rule_sha256` 으로 맞춘다(설계 01 S5)."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
