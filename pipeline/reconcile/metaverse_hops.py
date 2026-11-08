"""메타버스 창 대사 한 번(MVR-2 — 계획 temp/review/MVR-2-plan.md 3판, 광고 P2-2 `reconcile_hops` 와 같은 틀).

한 번의 순서(창 크기마다 따로: hour·day): 잠금 → 받기(파이프라인 manifest `hop_pipeline_<창>`, S3) → since 확인 → 내보내기(메타버스 DB 한 읽기 스냅샷 →
공용 규칙 `summarize_changes(source=METAVERSE)` → `hop_outbox` manifest) → 대조(outbox → 파이프라인 한 쌍) → 판정 사슬 → 상태 파일 `<dir>/reconcile-<창>.json`.
종료 코드: 0 정상, 1 확정 불일치(상태 파일을 쓰기 전에 멈춘 경우도 1 — 상태 파일로 구별), 2 입력 실패, 3 늦음(받은 since 가 기대와 다르다 — 대조는 했다),
75 같은 창 크기 대사가 돌고 있어 건너뜀. 불일치가 늦음보다 앞선다.

광고와 다른 점(계획 §2a, 사용자·Planner 승인):
- 받기는 포인터 → 가리키는 데이터 → SHA-256·행 수·줄 형식을 메모리에서 확인한 뒤에만 쓴다. 세대 사슬 걷기·로컬 고정점은 하지 않는다 — 쓰는 쪽(Bronze manifest 잡)과
  받는 쪽이 모두 파이프라인이고, 옛 포인터로 되돌려져도 거짓 확정이 없다(Bronze 는 추가만 하고 DB 쪽은 늘 지금 스냅샷).
- 확정 기록은 DB 감사 로그가 아니라 작업 폴더의 판정 사슬 `<dir>/<창>/verdict/hop_verdict` 다(메타버스 DB 에 감사 표가 없고 앱 코드를 바꾸지 않는다).
  잃으면 확정이 없어져 지우지 않는 쪽으로 실패한다. 창마다 마지막 판정 = 가장 새 세대.
- 대조 규칙은 광고 `events/hopcheck.py`(Django 에 묶여 import 할 수 없다) 가운데 이 쌍에 쓰는 검사·분류·판정을 표준 라이브러리로 옮긴 것이다.
- 원본·MongoDB 구간·미전송·보존 기준 창·알려진 예외가 없다.

설정(기본값 없음 — 빠지면 그 회차는 입력 실패): HOP_S3_ENDPOINT·HOP_S3_REGION·HOP_S3_BUCKET·HOP_S3_PREFIX·HOP_S3_ACCESS_KEY_FILE·HOP_S3_SECRET_KEY_FILE(키는 파일에서 읽고
출력하지 않는다), HOP_GRACE_SECONDS, libpq PGHOST·PGPORT·PGDATABASE·PGUSER·PGPASSFILE(암호는 libpq 가 파일에서 읽는다 — 이 도구는 다루지 않는다).
실행: 저장소 뿌리에서 `python -m pipeline.reconcile.metaverse_hops --window hour|day --dir <작업 폴더> [--as-of …]`(래퍼 run-reconcile-metaverse.sh).
"""
import argparse
import fcntl
import json
import os
import re
import resource
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.common import hop_rules, ndjson

SPANS = {"hour": timedelta(hours=48), "day": timedelta(days=8)}  # Bronze manifest 잡(pipeline/bronze/hop_manifest_job.py SPANS)과 같게 — 기대 since 계산
RC = {"ok": 0, "mismatch": 1, "input_failed": 2, "late": 3, "skipped": 75}
POINTER_LIMIT, DATA_LIMIT = 1 << 20, 16 << 20  # 광고 fetch_hop_manifest 와 같은 상한(포인터·세대 사본 1 MiB, 데이터 16 MiB)
PREFIX_FORMAT = re.compile(r"(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/)+")  # 상대 경로, '/' 로 끝남, 빈 칸·'.'·'..' 마디 없음(광고와 같음)
HEX64 = re.compile(r"[0-9a-f]{64}")
ENV = ("HOP_S3_ENDPOINT", "HOP_S3_REGION", "HOP_S3_BUCKET", "HOP_S3_PREFIX", "HOP_S3_ACCESS_KEY_FILE", "HOP_S3_SECRET_KEY_FILE", "HOP_GRACE_SECONDS",
       "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSFILE")
SOURCE = hop_rules.METAVERSE
SCOPE = "postgresql-repeatable-read-snapshot"
PAIR = "hop_outbox->pipeline"
MATCH, MISMATCH, OPEN = "match", "mismatch", "open_window"
FIELDS = ("count", "ids_sha256", "content_sha256")
STATEMENT_TIMEOUT_MS = 300_000  # 한 문장으로 다 받으므로(아래 QUERY) 읽기 전체의 한도다

# 8열(내용 비교 열 — 공용 규칙 METAVERSE["content"] 순서). JSON 열은 jsonb 텍스트로 받는다(Debezium 이 싣는 문자열과 해석 값이 같다).
_COLUMNS = "event_uuid, event_type, topic, message_key, raw_data::text, occurred_at, payload_json::text, created_at"
# created_at 만의 색인이 없다(6dcb4b1 모델 — 색인은 PK 와 (published_at, created_at)). 있는 색인으로 두 갈래를 읽는다(계획 V6 (가)).
# 두 갈래는 겹치지 않고 합치면 전부다(published_at 은 NULL 이거나 아니다). 발행기를 끈 동안(늘 NULL) 둘째 갈래는 빈 범위다.
QUERY = (f"SELECT {_COLUMNS} FROM {'.'.join(SOURCE['table'])} WHERE published_at IS NULL AND created_at >= %(since)s "
         f"UNION ALL SELECT {_COLUMNS} FROM {'.'.join(SOURCE['table'])} WHERE published_at IS NOT NULL AND created_at >= %(since)s")


class InputError(Exception):
    pass


# ---------------------------------------------------------------- 설정

def settings(environ):
    missing = [name for name in ENV if not environ.get(name)]
    if missing:
        raise InputError(f"설정이 없다: {', '.join(missing)}")
    if not PREFIX_FORMAT.fullmatch(environ["HOP_S3_PREFIX"]):
        raise InputError(f"HOP_S3_PREFIX 는 '/' 로 끝나는 상대 경로여야 한다('.'·'..'·빈 마디 없음): {environ['HOP_S3_PREFIX']!r}")
    try:
        grace = int(environ["HOP_GRACE_SECONDS"])
    except ValueError:
        raise InputError(f"HOP_GRACE_SECONDS 는 정수여야 한다: {environ['HOP_GRACE_SECONDS']!r}")
    return {"endpoint": environ["HOP_S3_ENDPOINT"], "region": environ["HOP_S3_REGION"], "bucket": environ["HOP_S3_BUCKET"], "prefix": environ["HOP_S3_PREFIX"],
            "access_key_file": environ["HOP_S3_ACCESS_KEY_FILE"], "secret_key_file": environ["HOP_S3_SECRET_KEY_FILE"], "grace": grace}


# ---------------------------------------------------------------- 받기(S3 — 정해진 키만, 목록 조회 없음)

class S3Store:
    """접두사 아래 정해진 키를 받는다. 없는 키는 None(404·403 — 읽기 전용 키에 ListBucket 이 없으면 없는 키도 403), 다른 오류는 InputError."""

    def __init__(self, config):
        import boto3
        from botocore.config import Config

        keys = [Path(config[f]).read_text(encoding="utf-8").strip() for f in ("access_key_file", "secret_key_file")]  # 값은 이 객체 밖으로 내지 않는다
        self.s3 = boto3.client("s3", endpoint_url=config["endpoint"], region_name=config["region"], aws_access_key_id=keys[0], aws_secret_access_key=keys[1],
                               config=Config(connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 3, "mode": "standard"}, s3={"addressing_style": "path"}))
        self.bucket, self.prefix = config["bucket"], config["prefix"]

    def get(self, name, limit):
        from botocore.exceptions import BotoCoreError, ClientError

        key = self.prefix + name
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read(limit + 1)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "AccessDenied", "403"):
                return None
            raise InputError(f"S3 받기 실패: {key} ({code or type(error).__name__})")
        except BotoCoreError as error:  # 접속 실패·시간 초과
            raise InputError(f"S3 받기 실패: {key} ({type(error).__name__})")
        if len(body) > limit:
            raise InputError(f"S3 파일이 상한({limit}바이트)보다 크다: {key}")
        return body


def fetch(store, name, folder):
    """포인터 → 가리키는 데이터를 메모리로 받아 `ndjson.check` 로 확인하고, 모두 맞을 때만 데이터 → 포인터 순으로 `folder` 에 쓴다(광고 순서 — 계획 C3).
    하나라도 어긋나면 폴더를 그대로 두고 InputError. 반환: 쓴 포인터 경로."""
    pointer = store.get(f"{name}.manifest.json", POINTER_LIMIT)
    if pointer is None:
        raise InputError(f"S3 에 포인터가 없다(게시 전이거나 읽을 권한이 없다): {name}.manifest.json")
    try:
        manifest = json.loads(pointer)
    except ValueError:
        raise InputError("포인터를 읽을 수 없다(JSON 이 아니다)")
    if not isinstance(manifest, dict):
        raise InputError("포인터가 JSON 객체가 아니다")
    data = {}

    def read(file):  # `ndjson.check` 가 이름 형식(경로 구분자 없음)·해시·행 수 형식을 확인한 뒤에만 부른다
        if not file.startswith(name + "."):
            return None  # 다른 이름의 데이터 파일은 묻지 않는다(check 가 그 문제를 적는다)
        data[file] = store.get(file, DATA_LIMIT)
        return data[file]

    problems = ndjson.check(manifest, name, read)
    if problems:
        raise InputError("받은 manifest 가 맞지 않아 아무것도 바꾸지 않았다: " + "; ".join(problems))
    folder.mkdir(parents=True, exist_ok=True)
    ndjson.publish_bytes(folder / manifest["file"], data[manifest["file"]])  # 불변 파일이 먼저, 포인터가 마지막
    path = ndjson.manifest_path(folder, name)
    ndjson.publish_bytes(path, pointer)
    return path


# ---------------------------------------------------------------- 내보내기(메타버스 DB 한 읽기 스냅샷 → hop_outbox)

def read_outbox(connect, since):
    """한 읽기 스냅샷(REPEATABLE READ READ ONLY)에서 since 이후 outbox 행의 8열을 한 문장으로 다 받고, 바로 트랜잭션을 닫는다(계획 R4 — 요약·쓰기는 그 뒤).
    반환: (DB 이름, 행 목록). `connect()` 는 psycopg 연결(문맥 관리자)을 돌려준다."""
    # ponytail: 범위 안 행을 한 번 메모리에 둔다(요약 규칙도 고유 이벤트마다 내용 객체를 둔다 — 같은 차수). 크면 창마다 나눠 읽는다(peak_rss_bytes 로 본다)
    with connect() as conn:
        conn.read_only = True
        conn.isolation_level = 3  # psycopg.IsolationLevel.REPEATABLE_READ
        with conn.transaction():
            database = conn.execute("SELECT current_database()").fetchone()[0]
            rows = conn.execute(QUERY, {"since": since}).fetchall()
    return database, rows


def _uuid_text(value):
    return None if value is None else str(value)


def _utc_text(moment):
    """timestamptz → Debezium ZonedTimestamp 와 같은 UTC 문자열(마이크로초 6자리). 시간대가 없으면 ValueError(값을 싣지 않는다)."""
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise ValueError("시간대가 있는 시각이 아니다")
    return hop_rules._utc_micros(moment)


def outbox_after(row):
    """DB 행(8열, `_COLUMNS` 순서) → Debezium 모양 `after`(Bronze 와 같은 문서가 되게 — 계획 §4.2). JSON 열 NULL 은 None(공용 규칙이 JSON null 로 센다, E3)."""
    event_uuid, event_type, topic, message_key, raw_data, occurred_at, payload_json, created_at = row
    return {"event_uuid": _uuid_text(event_uuid), "event_type": event_type, "topic": topic, "message_key": _uuid_text(message_key), "raw_data": raw_data,
            "occurred_at": _utc_text(occurred_at), "payload_json": payload_json, "created_at": _utc_text(created_at)}


def summarize_outbox(rows, database, as_of, grace, since, size):
    """DB 행을 Bronze 와 **같은 함수**(`summarize_changes`, METAVERSE)로 요약한다(MVR-1 D5). 행은 op `r` 봉투로 흘려 넣는다(목록을 다시 만들지 않는다).
    반환: (hop_outbox 줄, 열린 창). PK 라 같은 event_uuid 가 둘일 수 없다 — 파이프라인 쪽 건수가 0 이 아니면 있을 수 없는 상태라 InputError."""
    origin = {"db": database, "schema": SOURCE["table"][0], "table": SOURCE["table"][1]}
    envelopes = ((None, json.dumps({"op": "r", "source": origin, "after": outbox_after(row)}), 0, number) for number, row in enumerate(rows))
    try:
        lines, open_windows, extra = hop_rules.summarize_changes(envelopes, database, as_of, grace, since, size, source=SOURCE)
    except (KeyError, TypeError, ValueError, RecursionError) as error:  # 공용 규칙의 E1 메시지는 값이 없다. 위치는 DB 에서 뜻이 없다(읽은 순번)
        raise InputError(f"DB 쪽 행 — event_id 로 찾는다: {type(error).__name__}: {error}") from None
    odd = {f: n for f, n in (("not_insert_unplaced", extra["not_insert_unplaced"]), ("foreign_source_rows", extra["foreign_source_rows"]),
                             *((f, sum(line[f] for line in lines)) for f in hop_rules.PIPELINE_COUNTS)) if n}
    if odd:
        raise InputError(f"DB 쪽 요약에 있을 수 없는 건수가 있다: {odd}")
    return [{k: v for k, v in line.items() if k not in hop_rules.PIPELINE_COUNTS} for line in lines], open_windows


def export(connect, folder, window, since, as_of, grace):
    """읽기 → 요약 → `hop_outbox` manifest(세대 사슬). 반환: 포인터 경로."""
    database, rows = read_outbox(connect, since)
    lines, open_windows = summarize_outbox(rows, database, as_of, grace, since, hop_rules.WINDOWS[window])
    checkpoint = {"scope": SCOPE, "hop": "hop_outbox", "window": hop_rules.window_label(window), "grace_seconds": grace, "started_at": as_of.isoformat(),
                  "since": hop_rules.format_time(since), "open_windows": [hop_rules.format_time(w) for w in open_windows],
                  "scanned_events": sum(line["count"] for line in lines)}
    ndjson.write(folder, "hop_outbox", lines, "hop_outbox", hop_rules.HOP_SCHEMA_VERSION, checkpoint=checkpoint, now=as_of, chain=True)
    return ndjson.manifest_path(folder, "hop_outbox")


# ---------------------------------------------------------------- 대조(광고 hopcheck 의 outbox → 파이프라인 쌍 몫)

def _time(text, what):
    try:
        return hop_rules.parse_time(text)
    except (TypeError, ValueError):
        raise InputError(f"{what} 시각이 올바르지 않다: {text!r}")


def _count(value):
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _check_line(number, line, size, pipeline):
    for field in ("window_start", "window_end", "event_name", *FIELDS, *(hop_rules.PIPELINE_COUNTS if pipeline else ())):
        if field not in line:
            raise InputError(f"{number}번째 줄에 {field} 가 없다")
    start, end = _time(line["window_start"], f"{number}번째 줄 window_start"), _time(line["window_end"], f"{number}번째 줄 window_end")
    if end - start != size:
        raise InputError(f"{number}번째 줄의 창 길이({end - start})가 선언한 창 크기({size})와 다르다")
    if hop_rules.window_start(start, size) != start:
        raise InputError(f"{number}번째 줄의 창 시작 {line['window_start']} 이 UTC 경계에 맞지 않는다")
    if not isinstance(line["event_name"], str) or not line["event_name"]:
        raise InputError(f"{number}번째 줄의 event_name 이 올바르지 않다")
    for field in ("count", *(hop_rules.PIPELINE_COUNTS if pipeline else ())):
        if not _count(line[field]):
            raise InputError(f"{number}번째 줄의 {field} 는 0 이상의 정수여야 한다")
    for field in ("ids_sha256", "content_sha256"):
        if not isinstance(line[field], str) or not HEX64.fullmatch(line[field]):
            raise InputError(f"{number}번째 줄의 {field} 는 소문자 16진 SHA-256 이어야 한다")


def load(path, kind):
    """manifest 와 데이터 파일을 검증하고(SHA-256·행 수·형식) 계약을 확인한 뒤 줄을 읽는다(광고 `hopcheck.load_hop` 과 같은 검사). 반환: dict(manifest·checkpoint·lines·…)."""
    path = Path(path)
    try:
        problems = ndjson.verify(path)
        manifest = ndjson.read_manifest(path)
    except ndjson.NdjsonError as error:
        raise InputError(f"{kind}: {error}")
    if problems:
        raise InputError(f"{kind}: {path.name} 검증 실패: " + "; ".join(problems))
    if manifest.get("source_kind") != kind:
        raise InputError(f"{kind} 가 와야 하는 자리에 {manifest.get('source_kind')!r} 가 있다")
    if manifest.get("schema_version") != hop_rules.HOP_SCHEMA_VERSION:
        raise InputError(f"{kind}: 지원하지 않는 계약 버전 schema_version={manifest.get('schema_version')!r}")
    checkpoint = manifest.get("checkpoint")
    if not isinstance(checkpoint, dict):
        raise InputError(f"{kind}: checkpoint 가 객체가 아니다")
    if checkpoint.get("hop", kind) != kind:
        raise InputError(f"{kind}: checkpoint 의 구간({checkpoint.get('hop')!r})이 source_kind 와 다르다")
    opened = checkpoint.get("open_windows", [])
    if not isinstance(opened, list) or not all(isinstance(w, str) for w in opened):
        raise InputError(f"{kind}: open_windows 는 문자열 목록이어야 한다")
    window = checkpoint.get("window")
    size = hop_rules.WINDOW_SIZES.get(window) if isinstance(window, str) else None
    if size is None:
        raise InputError(f"{kind}: 창 크기 선언(checkpoint.window)이 없거나 알 수 없다: {window!r}")
    since = checkpoint.get("since")
    if since is not None:
        moment = _time(since, f"{kind}: checkpoint.since") if isinstance(since, str) else None
        if moment is None or hop_rules.format_time(moment) != since or hop_rules.window_start(moment, hop_rules.HOUR) != moment:
            raise InputError(f"{kind}: checkpoint.since 는 UTC 정시(YYYY-MM-DDTHH:00:00Z) 문자열이거나 null 이어야 한다: {since!r}")
    pipeline = kind == "hop_pipeline"
    if pipeline:
        for field in ("not_insert_unplaced", "foreign_source_rows"):
            if not _count(checkpoint.get(field)):
                raise InputError(f"{kind}: checkpoint.{field} 는 필수이고 0 이상의 정수여야 한다")
        for field in hop_rules.EXAMPLE_FIELDS:
            items = checkpoint.get(field)
            if not (isinstance(items, list) and len(items) <= hop_rules.EXAMPLES and all(
                    isinstance(i, dict) and set(i) == {"event_id", "partition", "offset"} and (i["event_id"] is None or isinstance(i["event_id"], str))
                    and _count(i["partition"]) and _count(i["offset"]) for i in items)):
                raise InputError(f"{kind}: checkpoint.{field} 는 {{event_id, partition, offset}} 객체 목록(최대 {hop_rules.EXAMPLES}개)이어야 한다")
        if not isinstance(checkpoint.get("rule_sha256"), str) or not HEX64.fullmatch(checkpoint["rule_sha256"]):
            raise InputError(f"{kind}: checkpoint.rule_sha256 는 필수이고 소문자 16진 SHA-256 이어야 한다")
    lines = {}
    for number, text in enumerate((path.parent / manifest["file"]).read_text(encoding="utf-8").splitlines(), 1):
        line = json.loads(text)
        if not isinstance(line, dict):
            raise InputError(f"{kind}: {number}번째 줄이 객체가 아니다")
        _check_line(number, line, size, pipeline)
        key = (line["window_start"], line["event_name"])
        if key in lines:
            raise InputError(f"{kind}: {number}번째 줄 (창, 이벤트 이름) {key} 가 중복이다")
        lines[key] = line
    return {"kind": kind, "manifest": manifest, "checkpoint": checkpoint, "window": window, "since": since, "open": set(opened), "lines": lines}


def compare(outbox, pipeline):
    """outbox(상류) → 파이프라인(하류)을 (창, 이벤트 이름)별로 분류한다(광고 `compare`·`_classify` 의 이 쌍 몫 — 지연 후보 없음). 쌍을 대조할 수 없으면 InputError."""
    if outbox["window"] != pipeline["window"]:
        raise InputError(f"창 크기가 다른 manifest 는 대조하지 않는다: outbox={outbox['window']}, pipeline={pipeline['window']}")
    if outbox["since"] != pipeline["since"]:
        raise InputError(f"범위(checkpoint.since)가 다른 manifest 는 대조하지 않는다: outbox={outbox['since']}, pipeline={pipeline['since']}")
    checkpoint = pipeline["checkpoint"]
    problems = [f"{f} {checkpoint[f]}건(커넥터·원천 설정 이상 — 어느 창에도 놓을 수 없다)" for f in ("not_insert_unplaced", "foreign_source_rows") if checkpoint[f]]
    if checkpoint["rule_sha256"] != hop_rules.rule_sha256():
        problems.append(f"rule_sha256 {checkpoint['rule_sha256']} 이 이 사본의 공용 요약 규칙({hop_rules.rule_sha256()})과 다르다")
    if problems:
        raise InputError("pipeline 은 대조하지 않는다: " + "; ".join(problems))
    since = outbox["since"]
    keys = {k for k in outbox["lines"].keys() | pipeline["lines"].keys() if since is None or k[0] >= since}
    opened = outbox["open"] | pipeline["open"]
    rows = []
    for key in sorted(keys):
        a, b = outbox["lines"].get(key), pipeline["lines"].get(key)
        if key[0] in opened:
            status, fields = OPEN, []
        else:
            causes = [f for f in ("conflicts", "not_insert") if b and b.get(f)]
            if a is not None and b is not None and all(a[f] == b[f] for f in FIELDS) and not causes:
                status, fields = MATCH, []
            else:
                fields = [f for f in FIELDS if a[f] != b[f]] if a and b else ["line_missing_in_" + ("b" if b is None else "a")]
                status, fields = MISMATCH, fields + causes
        rows.append({"pair": PAIR, "window_start": key[0], "event_name": key[1], "status": status, "fields": fields, "a": _summary(a), "b": _summary(b)})
    for window in sorted(w for w in opened - {k[0] for k in keys} if since is None or w >= since):  # 줄이 없는 열린 창도 남긴다
        rows.append({"pair": PAIR, "window_start": window, "event_name": None, "status": OPEN, "fields": [], "a": None, "b": None})
    return rows


def _summary(line):
    return None if line is None else {f: line[f] for f in (*FIELDS, *hop_rules.PIPELINE_COUNTS) if f in line}


def judge(rows):
    """줄이 있는 닫힌 창마다, 그 창의 줄이 모두 match 이면 확정(광고 `judge_windows` 의 파이프라인 쌍). 반환: (확정, 미확정) 창 시작 목록."""
    judged, bad = set(), set()
    for row in rows:
        if row["status"] == OPEN or row["event_name"] is None:
            continue
        judged.add(row["window_start"])
        if row["status"] != MATCH:
            bad.add(row["window_start"])
    return sorted(judged - bad), sorted(judged & bad)


def outbox_windows(outbox):
    """파이프라인 쪽을 판정할 수 없을 때 미확정으로 남길 창: outbox 에 줄이 있는 닫힌 창(since 뒤)."""
    return sorted({w for w, _ in outbox["lines"] if w not in outbox["open"]})


def record(folder, window, since, confirmed, unconfirmed, inputs, reason=None):
    """판정 사슬에 한 세대(줄 = 판정한 창, `confirmed`). 판정한 창이 없으면 쓰지 않는다. 반환: 포인터 SHA-256 또는 None."""
    if not confirmed and not unconfirmed:
        return None
    size = hop_rules.WINDOW_SIZES[window]
    lines = sorted(({"window_start": w, "window_end": hop_rules.format_time(hop_rules.parse_time(w) + size), "confirmed": w in confirmed}
                    for w in (*confirmed, *unconfirmed)), key=lambda line: line["window_start"])
    checkpoint = {"pair": PAIR, "window": window, "since": since, "inputs": inputs, "rule_sha256": hop_rules.rule_sha256(), **({"reason": reason} if reason else {})}
    manifest = ndjson.write(folder, "hop_verdict", lines, "hop_verdict", 1, checkpoint=checkpoint, now=datetime.now(timezone.utc), chain=True)
    return ndjson.manifest_digest(manifest)


# ---------------------------------------------------------------- 한 번의 대사

def reconcile(window, base, now, environ, store_factory, connect):
    """한 번의 순서. 반환: 상태(dict) — 상태 파일 내용. `store_factory(config)`·`connect()` 는 시험이 가짜로 바꾼다."""
    size, name, folder = hop_rules.WINDOWS[window], f"hop_pipeline_{window}", base / window
    status = {"window": window, "started_at": datetime.now(timezone.utc).isoformat(), "as_of": now.isoformat(), "finished_at": None, "status": None, "rc": None,
              "stage": None, "message": None, "expected_since": hop_rules.format_time(hop_rules.window_start(now, size) - SPANS[window]), "pipeline_since": None,
              "pipeline_as_of": None, "late_by_seconds": None, "pipeline_manifest_sha256": None, "outbox_manifest_sha256": None, "verdict_manifest_sha256": None,
              "confirmed": None, "unconfirmed": None, "mismatch_lines": None, "pipeline_grace_seconds": None, "grace_seconds": None, "peak_rss_bytes": None}
    stage = ["settings"]

    def done(state, message=None):
        if isinstance(message, Exception) and not isinstance(message, InputError):
            message = f"{type(message).__name__}: {message}"  # DB 연결 끊김·statement_timeout·S3 등 — 다음 회차에 저절로 낫는 입력 실패다
        status.update(status=state, rc=RC[state], stage=stage[0] if state != "ok" else None, message=None if message is None else " ".join(str(message).split())[:300],
                      finished_at=datetime.now(timezone.utc).isoformat(), peak_rss_bytes=peak_rss_bytes())
        return status

    try:
        config = settings(environ)
        status["grace_seconds"] = config["grace"]
        stage[0] = "fetch"
        pointer = fetch(store_factory(config), name, folder / "pipeline")
        stage[0] = "since"
        checkpoint = ndjson.read_manifest(pointer).get("checkpoint") or {}
        status["pipeline_manifest_sha256"] = ndjson.manifest_sha256(pointer)
        status["pipeline_grace_seconds"] = checkpoint.get("grace_seconds")
        since_text, closed_at = checkpoint.get("since"), checkpoint.get("started_at")  # started_at = 잡이 요약에 준 now(창 닫힘 기준)
        if not isinstance(since_text, str):
            return done("input_failed", f"받은 manifest 에 checkpoint.since 가 없다: {since_text!r}")
        status["pipeline_since"] = since_text
        since = _time(since_text, "받은 checkpoint.since")
        try:
            cut = datetime.fromisoformat(closed_at)
        except (TypeError, ValueError):
            cut = None
        if cut is None or cut.tzinfo is None:
            return done("input_failed", f"받은 manifest 의 checkpoint.started_at 이 시간대를 붙인 ISO 8601 시각이 아니다: {closed_at!r}")
        status["pipeline_as_of"] = cut.astimezone(timezone.utc).isoformat()
        stage[0] = "export"
        outbox_pointer = export(connect, folder / "outbox", window, since, min(cut, now).astimezone(timezone.utc), config["grace"])  # 잡 시계가 앞서면 이쪽 지금(P2-2a A)
        status["outbox_manifest_sha256"] = ndjson.manifest_sha256(outbox_pointer)
        stage[0] = "verify"
        outbox = load(outbox_pointer, "hop_outbox")  # 여기서 실패하면 판정을 남기지 않는다(이 DB 의 outbox 와 이어지지 않는 판정)
        inputs = {"hop_outbox": status["outbox_manifest_sha256"]}
        try:
            rows = compare(outbox, load(pointer, "hop_pipeline"))
        except InputError as error:  # 파이프라인 쪽을 판정할 수 없다 — outbox 에 줄이 있는 창을 모두 미확정으로(확정 전에는 지우지 않는다)
            stage[0] = "record"
            status["verdict_manifest_sha256"] = record(folder / "verdict", outbox["window"], outbox["since"], [], outbox_windows(outbox), inputs,
                                                       reason=f"파이프라인 쪽을 판정할 수 없다: {error}"[:300])
            status["confirmed"], status["unconfirmed"] = 0, len(outbox_windows(outbox))
            stage[0] = "verify"
            return done("input_failed", error)
        counts = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        ndjson.write(folder / "report", "hop_report", rows, "hop_report", 1, checkpoint={"counts": counts, "inputs": {**inputs, "pipeline": status["pipeline_manifest_sha256"]}},
                     now=datetime.now(timezone.utc))
        stage[0] = "record"
        confirmed, unconfirmed = judge(rows)
        status["verdict_manifest_sha256"] = record(folder / "verdict", outbox["window"], outbox["since"], confirmed, unconfirmed,
                                                   {**inputs, "pipeline": status["pipeline_manifest_sha256"]})
        status["confirmed"], status["unconfirmed"], status["mismatch_lines"] = len(confirmed), len(unconfirmed), counts.get(MISMATCH, 0)
        if status["mismatch_lines"]:  # 늦음보다 앞선다 — 늦은 회차의 진짜 불일치를 늦음으로 가리지 않는다
            stage[0] = "verify"
            return done("mismatch", f"확정 불일치 {status['mismatch_lines']}줄")
        lag = (hop_rules.parse_time(status["expected_since"]) - since).total_seconds()
        if lag:
            stage[0] = "since"
            status["late_by_seconds"] = int(lag)
            return done("late", f"받은 since {since_text} 가 기대 {status['expected_since']} 와 다르다(게시가 늦거나 아직 없다)")
        return done("ok")
    except Exception as error:  # noqa: BLE001 — 그 단계의 입력 실패로 상태 파일에 남긴다(KeyboardInterrupt·SystemExit 는 잡지 않는다)
        return done("input_failed", error)


def peak_rss_bytes():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024  # macOS 는 바이트, Linux 는 KiB


def connect_from_env():
    import psycopg

    return psycopg.connect(connect_timeout=10, options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}")  # 접속 값·암호 파일은 libpq 환경(PG*)


def main(argv=None, environ=None, store_factory=S3Store, connect=connect_from_env):
    parser = argparse.ArgumentParser(description="메타버스 창 대사 한 번(MVR-2)")
    parser.add_argument("--window", choices=sorted(SPANS), required=True)
    parser.add_argument("--dir", required=True, help="작업 폴더 — <창>/pipeline·outbox·report·verdict 와 상태 파일 reconcile-<창>.json")
    parser.add_argument("--as-of", help="재현용 기준 시각(시간대를 붙인 ISO 8601) — 기대 since 에 쓴다(기본은 지금). outbox 창 닫힘은 받은 started_at 과 이것 중 이른 쪽")
    args = parser.parse_args(argv)
    try:
        now = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
    except ValueError:
        print("--as-of 는 ISO 8601 시각이어야 한다", file=sys.stderr)
        return RC["input_failed"]
    if now.tzinfo is None:
        print("--as-of 에는 시간대를 붙여야 한다", file=sys.stderr)
        return RC["input_failed"]
    base = Path(args.dir)
    base.mkdir(parents=True, exist_ok=True)
    with open(base / f".reconcile-{args.window}.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:  # 앞 회차가 아직 돈다 — 상태 파일은 그 회차의 것으로 둔다
            print(f"skipped: 같은 창 크기({args.window}) 메타버스 대사가 돌고 있다", flush=True)
            return RC["skipped"]
        status = reconcile(args.window, base, now.astimezone(timezone.utc), os.environ if environ is None else environ, store_factory, connect)
        ndjson.publish_bytes(base / f"reconcile-{args.window}.json", (json.dumps(status, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))  # 잠금 안
    print(f"reconcile metaverse {args.window}: {status['status']} rc={status['rc']} since={status['pipeline_since']} 기대={status['expected_since']}"
          f" 확정={status['confirmed']} 미확정={status['unconfirmed']} 불일치={status['mismatch_lines']} peak_rss={status['peak_rss_bytes'] / 2**20:.1f}MiB"
          + (f" 단계={status['stage']}: {status['message']}" if status["stage"] else ""), flush=True)
    return status["rc"]


if __name__ == "__main__":
    sys.exit(main())
