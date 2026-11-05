"""구간별 정합 manifest 대조(계획 4-3c). `export_hop_manifests` 가 낸 manifest(또는 파이프라인 쪽이 같은 규칙으로 낸 것) 두 개를 (창, 이벤트 이름)별로 맞춰 본다.

판정 분류(설계 §6.3a, DRD §5.6):
- `match`: 건수·`ids_sha256`·`content_sha256` 이 모두 같다.
- `mismatch`: **확정 불일치**. 어느 필드가 다른지 `fields` 에 적는다.
- `relay_lag_candidate`: 중계 지연 후보 — MongoDB 쪽이 상류(원본·outbox)보다 적고 그 부족분이 outbox 의 미전송으로 설명된다. 다시 낸 manifest 로 다시 확인한다(실패가 아니다).
  MongoDB → 파이프라인에는 쓰지 않는다 — 미전송은 MongoDB 에도 아직 없으므로 파이프라인이 적은 이유가 될 수 없다.
- `open_window`: 한쪽에서 아직 열려 있는 창이라 비교하지 않는다.
- `not_comparable`: outbox 가 보존 정리로 비교할 수 없는 창.

파이프라인 구간(`hop_pipeline`, DRD §5.6 [P1-3]): 줄의 `duplicates` 는 `match` 를 막지 않는다. `conflicts`·`not_insert` 가 0 보다 크면 건수·해시가 같아도 `mismatch` 이고
`fields` 에 그 원인을 적어 유실과 구별한다. `checkpoint` 의 `not_insert_unplaced`·`foreign_source_rows` 가 0 보다 크거나 `rule_sha256` 이 이 광고 서버의 공용 요약 규칙
(`hop_rules.py`)과 다르면 그 쌍은 대조하지 않고 실패한다. outbox → 파이프라인은 지연 후보가 없다(`use_unsent=False` — CDC 는 `sent_at` 을 채우지 않는다).
"""
import json
import re
from pathlib import Path

from foundation.api import audit_entries_in_order

from . import hop_rules, hops, ndjson

MATCH, MISMATCH, LAG, OPEN, NOT_COMPARABLE = "match", "mismatch", "relay_lag_candidate", "open_window", "not_comparable"
FIELDS = ("count", "ids_sha256", "content_sha256")


class HopInputError(Exception):
    pass


HOP_KINDS = ("hop_source", "hop_outbox", "hop_mongo", "hop_pipeline")
HEX64 = re.compile(r"[0-9a-f]{64}")


class Hop:
    def __init__(self, name, manifest, lines):
        self.name, self.manifest, self.lines = name, manifest, lines
        checkpoint = manifest.get("checkpoint") or {}
        self.window = checkpoint.get("window")
        self.since = checkpoint.get("since")  # 범위 시작(UTC 정시 문자열 또는 None, 설계 01 S1) — 같은 값끼리만 대조한다
        self.open_windows = set(checkpoint.get("open_windows", []))
        self.not_comparable = set(checkpoint.get("not_comparable_windows", []))
        self.unresolved_charges = checkpoint.get("unresolved_charges", 0)  # 원본 구간: 창에 놓을 수 없는 과금 원장 줄(대조할 줄이 없으므로 따로 실패)
        # outbox 구간: 비교 불가 창은 줄을 빼므로 그 창의 미전송 건수는 checkpoint 에 따로 싣는다(원본 → MongoDB 의 지연 후보 근거)
        self.not_comparable_unsent = {(u["window_start"], u["event_name"]): u["unsent"] for u in checkpoint.get("not_comparable_unsent", [])}
        self.pipeline = manifest.get("source_kind") == "hop_pipeline"


def _time(text, what):
    try:
        return hops.parse_time(text)
    except (TypeError, ValueError):
        raise HopInputError(f"{what} 시각이 올바르지 않다: {text!r}")


def _count(value):
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _check_line(number, line, size, pipeline=False):
    """한 줄이 계약 모양인지 본다: 필수 필드, 창 시작·끝이 UTC 경계에 맞고 끝 - 시작 = 선언한 창 크기, 건수·해시 형식. 파이프라인 구간은 세 건수(필수)도."""
    for field in ("window_start", "window_end", "event_name", "count", "ids_sha256", "content_sha256", *(hop_rules.PIPELINE_COUNTS if pipeline else ())):
        if field not in line:
            raise HopInputError(f"{number}번째 줄에 {field} 가 없다")
    start, end = _time(line["window_start"], f"{number}번째 줄 window_start"), _time(line["window_end"], f"{number}번째 줄 window_end")
    if end - start != size:
        raise HopInputError(f"{number}번째 줄의 창 길이({end - start})가 선언한 창 크기({size})와 다르다 — 집계 범위가 다른 manifest 다")
    if hops.window_start(start, size) != start:
        raise HopInputError(f"{number}번째 줄의 창 시작 {line['window_start']} 이 UTC 경계(자정·정시)에 맞지 않는다")
    if not isinstance(line["event_name"], str) or not line["event_name"]:
        raise HopInputError(f"{number}번째 줄의 event_name 이 올바르지 않다")
    for field in ("count", *(hop_rules.PIPELINE_COUNTS if pipeline else ())):
        if not _count(line[field]):
            raise HopInputError(f"{number}번째 줄의 {field} 는 0 이상의 정수여야 한다")
    for field in ("ids_sha256", "content_sha256"):
        if not isinstance(line[field], str) or not HEX64.fullmatch(line[field]):
            raise HopInputError(f"{number}번째 줄의 {field} 는 소문자 16진 SHA-256 이어야 한다")


def load_hop(manifest_path, name=None, expected_kind=None):
    """manifest 와 데이터 파일을 검증하고(SHA-256·행 수·형식) 줄을 읽는다. 검증에 실패하면 `HopInputError`.

    파일 checksum 이 맞는 것과 **집계 범위가 같은 것은 별개**라서 대조 전에 계약을 확인한다: 계약 버전, 구간 종류(`expected_kind` 가 있으면 그것과 같아야 한다),
    창 크기 선언(`checkpoint.window`), 범위 시작(`checkpoint.since` — UTC 정시 문자열 또는 null), 줄마다 창 시작·끝·크기·경계 정렬, 건수·해시 형식, (창, 이벤트 이름) 중복 없음.
    """
    manifest_path = Path(manifest_path)
    try:
        problems = ndjson.verify(manifest_path)
        manifest = ndjson.read_manifest(manifest_path)
    except ndjson.NdjsonError as error:
        raise HopInputError(str(error))
    if problems:
        raise HopInputError(f"{manifest_path.name} 검증 실패: " + "; ".join(problems))
    kind = manifest.get("source_kind")
    if kind not in HOP_KINDS:
        raise HopInputError(f"{manifest_path.name}: 구간 manifest 가 아니다(source_kind={kind!r})")
    if expected_kind is not None and kind != expected_kind:
        raise HopInputError(f"{manifest_path.name}: {expected_kind} 가 와야 하는 자리에 {kind} 가 있다")
    if manifest.get("schema_version") != hops.HOP_SCHEMA_VERSION:
        raise HopInputError(f"{manifest_path.name}: 지원하지 않는 계약 버전 schema_version={manifest.get('schema_version')!r}")
    checkpoint = manifest.get("checkpoint") or {}
    if not isinstance(checkpoint, dict):  # 형식이 틀린 manifest 가 날것의 예외로 대조 전체(다른 구간·보고서)를 멈추지 않게(리팩터 감사 D3)
        raise HopInputError(f"{manifest_path.name}: checkpoint 가 객체가 아니다: {checkpoint!r}")
    for field in ("open_windows", "not_comparable_windows", "unresolved_charge_examples"):
        windows = checkpoint.get(field, [])
        if not isinstance(windows, list) or not all(isinstance(w, str) for w in windows):
            raise HopInputError(f"{manifest_path.name}: {field} 는 문자열 목록이어야 한다: {windows!r}")
    window = checkpoint.get("window")
    size = hops.WINDOW_SIZES.get(window) if isinstance(window, str) else None  # 목록·객체는 사전 키가 될 수 없다
    if size is None:
        raise HopInputError(f"{manifest_path.name}: 창 크기 선언(checkpoint.window)이 없거나 알 수 없다: {window!r}")
    since = checkpoint.get("since")
    if since is not None:
        moment = _time(since, f"{manifest_path.name}: checkpoint.since") if isinstance(since, str) else None
        if moment is None or hops.format_time(moment) != since or hops.window_start(moment, hops.HOUR) != moment:  # 왕복이 같아야 표기(0 채움)까지 계약대로다
            raise HopInputError(f"{manifest_path.name}: checkpoint.since 는 UTC 정시(YYYY-MM-DDTHH:00:00Z) 문자열이거나 null 이어야 한다: {since!r}")
    unresolved = checkpoint.get("unresolved_charges", 0)
    if isinstance(unresolved, bool) or not isinstance(unresolved, int) or unresolved < 0:
        raise HopInputError(f"{manifest_path.name}: unresolved_charges 는 0 이상의 정수여야 한다: {unresolved!r}")
    hidden = checkpoint.get("not_comparable_unsent", [])
    if not isinstance(hidden, list):
        raise HopInputError(f"{manifest_path.name}: not_comparable_unsent 는 목록이어야 한다: {hidden!r}")
    seen = set()
    for item in hidden:  # 원본 → MongoDB 부족을 지연으로 설명하는 근거라, 비교하는 창의 항목·중복은 받지 않는다(줄의 중복 거부와 같은 규칙)
        well_formed = isinstance(item, dict) and all(isinstance(item.get(f), str) for f in ("window_start", "event_name")) \
            and not isinstance(item.get("unsent"), bool) and isinstance(item.get("unsent"), int) and item["unsent"] >= 0
        if not well_formed or item["window_start"] not in checkpoint.get("not_comparable_windows", []) or (item["window_start"], item["event_name"]) in seen:
            raise HopInputError(f"{manifest_path.name}: not_comparable_unsent 의 항목이 올바르지 않다(형식·비교 불가 창이 아님·중복): {item!r}")
        seen.add((item["window_start"], item["event_name"]))
    if checkpoint.get("hop", kind) != kind:
        raise HopInputError(f"{manifest_path.name}: checkpoint 의 구간({checkpoint.get('hop')})이 source_kind({kind})와 다르다")
    if kind == "hop_pipeline":
        _check_pipeline_checkpoint(manifest_path.name, checkpoint)
    lines = {}
    for number, text in enumerate((manifest_path.parent / manifest["file"]).read_text(encoding="utf-8").splitlines(), 1):
        line = json.loads(text)
        if not isinstance(line, dict):
            raise HopInputError(f"{number}번째 줄이 객체가 아니다")
        _check_line(number, line, size, pipeline=kind == "hop_pipeline")
        key = (line["window_start"], line["event_name"])
        if key in lines:
            raise HopInputError(f"{number}번째 줄: (창, 이벤트 이름) {key} 가 중복이다 — 마지막 줄로 덮어쓰지 않고 거부한다")
        lines[key] = line
    return Hop(name or kind, manifest, lines)


def _check_pipeline_checkpoint(name, checkpoint):
    """파이프라인 구간의 checkpoint 필수 필드(DRD §5.6 "입력 검증" [P1-3]): 놓을 수 없는 행·다른 출처 행 건수, 위치 예시 넷, 규칙 판."""
    for field in ("not_insert_unplaced", "foreign_source_rows"):
        if not _count(checkpoint.get(field)):
            raise HopInputError(f"{name}: checkpoint.{field} 는 필수이고 0 이상의 정수여야 한다: {checkpoint.get(field)!r}")
    for field in hop_rules.EXAMPLE_FIELDS:
        items = checkpoint.get(field)
        well_formed = isinstance(items, list) and len(items) <= hop_rules.EXAMPLES and all(
            isinstance(i, dict) and set(i) == {"event_id", "partition", "offset"} and (i["event_id"] is None or isinstance(i["event_id"], str)) and _count(i["partition"]) and _count(i["offset"])
            for i in items)
        if not well_formed:
            raise HopInputError(f"{name}: checkpoint.{field} 는 {{event_id, partition, offset}} 객체 목록(최대 {hop_rules.EXAMPLES}개)이어야 한다: {items!r}")
    if not isinstance(checkpoint.get("rule_sha256"), str) or not HEX64.fullmatch(checkpoint["rule_sha256"]):
        raise HopInputError(f"{name}: checkpoint.rule_sha256 는 필수이고 소문자 16진 SHA-256 이어야 한다: {checkpoint.get('rule_sha256')!r}")


def _pipeline_problems(hop):
    """그 쌍을 대조하지 않을 파이프라인 구간의 사정(DRD §5.6): 커넥터 설정 이상(놓을 수 없는 행·다른 출처 행)이나 규칙 판이 다름(설계 01 S5)."""
    checkpoint = hop.manifest.get("checkpoint") or {}
    problems = [f"{field} {checkpoint[field]}건(커넥터 설정 이상 — 어느 창에도 놓을 수 없다)" for field in ("not_insert_unplaced", "foreign_source_rows") if checkpoint.get(field)]
    if checkpoint.get("rule_sha256") != hop_rules.rule_sha256():
        problems.append(f"rule_sha256 {checkpoint.get('rule_sha256')} 이 이 광고 서버의 공용 요약 규칙(hop_rules.py {hop_rules.rule_sha256()})과 다르다")
    return problems


def compare(a, b, unsent_from=None, use_unsent=True):
    """두 구간을 대조한다. `a` 가 상류, `b` 가 하류다. `unsent_from`: 미전송 건수(`unsent`)를 가진 outbox 구간 — 원본 → MongoDB 의 지연 후보를 가리는 데 쓴다.
    `use_unsent=False`: outbox 의 미전송을 지연 근거로 쓰지 않는다(outbox → 파이프라인 — CDC 는 `sent_at` 을 채우지 않는다).
    반환: 줄(dict) 목록 — `{"pair", "window_start", "event_name", "status", "fields", "a", "b"}`."""
    if a.window != b.window:
        raise HopInputError(f"창 크기가 다른 manifest 는 대조하지 않는다: {a.name}={a.window}, {b.name}={b.window}")
    if a.since != b.since:  # 한쪽에만 있는 옛 창이 모두 '빠짐'으로 보이지 않게, 범위가 다르면 조용히 좁히지 않고 실패한다(설계 01 S1 (가))
        raise HopInputError(f"범위(checkpoint.since)가 다른 manifest 는 대조하지 않는다: {a.name}={a.since}, {b.name}={b.since}")
    for hop in (a, b):
        if hop.pipeline and (problems := _pipeline_problems(hop)):
            raise HopInputError(f"{hop.name} 은 대조하지 않는다: " + "; ".join(problems))
    keys = {k for k in a.lines.keys() | b.lines.keys() if a.since is None or k[0] >= a.since}  # since 앞 창은 보지 않는다(같은 형식의 UTC 문자열이라 사전순 = 시간순)
    out = []
    pair = f"{a.name}->{b.name}"
    for key in sorted(keys):
        window, name = key
        la, lb = a.lines.get(key), b.lines.get(key)
        status, fields = _classify(a, b, key, la, lb, unsent_from if use_unsent else None, use_unsent)
        out.append({"pair": pair, "window_start": window, "event_name": name, "status": status, "fields": fields,
                    "a": _summary(la), "b": _summary(lb)})
    # 줄이 하나도 없는 열린 창·비교 불가 창도 기록으로 남긴다(조용히 사라지지 않게)
    for window in sorted(w for w in (a.open_windows | b.open_windows) - {k[0] for k in keys} if a.since is None or w >= a.since):
        out.append({"pair": pair, "window_start": window, "event_name": None, "status": OPEN, "fields": [], "a": None, "b": None})
    return out


def _summary(line):
    return None if line is None else {f: line[f] for f in (*FIELDS, "unsent", *hop_rules.PIPELINE_COUNTS) if f in line}


def _classify(a, b, key, la, lb, unsent_from, use_unsent=True):
    window = key[0]
    if window in a.open_windows or window in b.open_windows:
        return OPEN, []
    if window in a.not_comparable or window in b.not_comparable:
        return NOT_COMPARABLE, []
    causes = list(dict.fromkeys(f for line in (la, lb) if line for f in ("conflicts", "not_insert") if line.get(f)))  # 파이프라인 줄: 유실과 구별할 원인
    if la is not None and lb is not None and all(la[f] == lb[f] for f in FIELDS) and not causes:
        return MATCH, []
    fields = [f for f in FIELDS if (la or {}).get(f) != (lb or {}).get(f)] if la and lb else ["line_missing_in_" + ("b" if lb is None else "a")]
    if not causes and use_unsent and _is_lag(a, b, key, la, lb, unsent_from):
        return LAG, fields
    return MISMATCH, fields + causes


def _is_lag(a, b, key, la, lb, unsent_from):
    """중계 지연 후보: 하류가 상류보다 적고, **그 부족분이 그 (창, 이름)의 미전송 건수로 설명될 때만**. 미전송 하나가 이미 보낸 이벤트의 유실을 가리면 안 된다 —
    설명할 수 없는 부족분(부족 > 미전송)은 확정 불일치다. 내용이 다른데 건수가 같거나 하류가 더 많으면 지연이 아니다."""
    shortfall = (la or {}).get("count", 0) - (lb or {}).get("count", 0)
    if shortfall <= 0:
        return False
    holder = a if "hop_outbox" in a.name else unsent_from
    if holder is None:
        return False
    line = holder.lines.get(key)
    return shortfall <= (line.get("unsent", 0) if line else holder.not_comparable_unsent.get(key, 0))


# ---------------------------------------------------------------- 창 확정 판정(설계 01 D8 구현 규칙 1·S3 (가)·D17, 계획 02 P1-4)

CONFIRM_ACTION = "events.hop_windows_confirmed"  # 판정 한 번에 한 건: 확정한 창과 확정하지 않은 창을 함께 남긴다 — 정리(P1-6)는 창마다 마지막 기록을 본다
PIPELINE_PAIR = "hop_outbox->pipeline"
MONGO_PAIR = "hop_outbox->hop_mongo"


def judge_windows(rows, with_mongo):
    """outbox → 파이프라인 줄이 있는 (닫혀 있고 비교할 수 있는) 창마다, 그 창의 줄이 모두 `match` 이면 확정(충돌·not_insert·유령은 mismatch 라 확정이 아니다).
    `with_mongo`(로컬 병행 기간): 같은 창의 outbox → MongoDB 줄도 모두 `match` 여야 한다(지연 후보도 미확정). 반환: (확정 창, 미확정 창) — 창 시작 문자열, 정렬."""
    judged, bad = set(), set()
    for row in rows:
        if row["status"] in (OPEN, NOT_COMPARABLE) or row["event_name"] is None:
            continue
        if row["pair"] == PIPELINE_PAIR:
            judged.add(row["window_start"])
            if row["status"] != MATCH:
                bad.add(row["window_start"])
        elif with_mongo and row["pair"] == MONGO_PAIR and row["status"] != MATCH:
            bad.add(row["window_start"])
    return sorted(judged - bad), sorted(judged & bad)


def outbox_windows(outbox):
    """판정할 수 없을 때 미확정으로 남길 창: outbox 구간에 줄이 있는 닫힌·비교 가능 창(since 뒤 — 우리 내보내기는 그 앞 줄을 만들지 않는다)."""
    return sorted({w for w, _ in outbox.lines if w not in outbox.open_windows and w not in outbox.not_comparable})


def span(starts, window):
    """가장 이른 창부터 가장 늦은 창까지의 모든 창 시작(사이의 빈 창 포함) — 판정 기록이 잘릴 때 전 범위를 미확정 한 구간으로 남기는 데 쓴다."""
    size, first, last = hops.WINDOW_SIZES[window], hops.parse_time(min(starts)), hops.parse_time(max(starts))
    return [hops.format_time(first + k * size) for k in range((last - first) // size + 1)]


def _ranges(starts, base, size):
    """창 시작 목록 → 기준 창 `base` 에서 센 창 단위 오프셋의 [첫, 끝] 구간 — 감사 detail 의 크기 한도(4,000자) 안에 들게 한다(시간 창 8일치 192개도 몇 구간)."""
    out = []
    for k in sorted((hops.parse_time(s) - base) // size for s in starts):
        if out and out[-1][1] == k - 1:
            out[-1][1] = k
        else:
            out.append([k, k])
    return out


def verdict_detail(window, since, confirmed, unconfirmed, inputs, with_mongo, reason=None):
    """`events.hop_windows_confirmed` 의 detail. 창 목록은 `base`(가장 이른 창)에서 센 오프셋 구간이다(`window_verdicts` 가 푼다)."""
    size, base = hops.WINDOW_SIZES[window], hops.parse_time(min(confirmed + unconfirmed))
    return {"pair": PIPELINE_PAIR, "window": window, "since": since, "base": hops.format_time(base),
            "confirmed": _ranges(confirmed, base, size), "unconfirmed": _ranges(unconfirmed, base, size),
            "inputs": inputs, "with_mongo": with_mongo, **({"reason": reason} if reason else {})}


def window_verdicts(window):
    """창 크기 `window`(`utc-hour`·`utc-day`)의 창마다 **마지막 판정**(확정이면 True) — 감사 기록 순서(PK)대로 덮어쓴다(설계 01 S3 (가)).
    다른 창 크기의 기록은 보지 않는다(같은 시각이라도 시간 창과 하루 창의 판정은 서로 다른 창이다)."""
    size, verdicts = hops.WINDOW_SIZES[window], {}
    for _at, detail in audit_entries_in_order(CONFIRM_ACTION, pair=PIPELINE_PAIR, window=window):
        for field, value in (("confirmed", True), ("unconfirmed", False)):
            for start in _starts(detail["base"], detail[field], size):
                verdicts[start] = value
    return verdicts


def _starts(base, ranges, size):
    """`_ranges` 의 반대: 기준 창에서 센 오프셋 구간 → 창 시작 문자열."""
    first = hops.parse_time(base)
    return [hops.format_time(first + k * size) for lo, hi in ranges for k in range(lo, hi + 1)]


# ---------------------------------------------------------------- 알려진 예외(설계 01 D16, 계획 02 P1-5)

EXCEPTION_ACTION = "events.hop_window_exception"  # 사람이 사건을 확인하고 남긴 "끝내 match 가 안 되는 창" — 그때부터 감시(M6·M8)에서 빼고, 기록일부터 30일 뒤 정리한다(P1-6)


def exception_detail(window, starts, reason, incident, os_user):
    """`events.hop_window_exception` 의 detail. 창 목록은 판정 기록과 같은 오프셋 구간이다. 기록자(앱 관리자 계정)는 감사 actor 로 남는다."""
    size, base = hops.WINDOW_SIZES[window], hops.parse_time(min(starts))
    return {"pair": PIPELINE_PAIR, "window": window, "base": hops.format_time(base), "windows": _ranges(starts, base, size),
            "reason": reason, "incident": incident, "os_user": os_user}


def window_exceptions(window):
    """창 크기 `window` 의 알려진 예외 창 → `{"recorded_at", "incident", "reason"}`. 같은 창을 다시 기록하면 나중 기록이 이긴다(기록일이 늦어져 정리도 늦어진다 — 안전한 쪽)."""
    size, found = hops.WINDOW_SIZES[window], {}
    for recorded_at, detail in audit_entries_in_order(EXCEPTION_ACTION, pair=PIPELINE_PAIR, window=window):
        for start in _starts(detail["base"], detail["windows"], size):
            found[start] = {"recorded_at": recorded_at, "incident": detail["incident"], "reason": detail["reason"]}
    return found
