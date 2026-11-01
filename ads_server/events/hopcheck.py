"""구간별 정합 manifest 대조(계획 4-3c). `export_hop_manifests` 가 낸 manifest(또는 파이프라인 쪽이 같은 규칙으로 낸 것) 두 개를 (창, 이벤트 이름)별로 맞춰 본다.

판정 분류(설계 §6.3a, DRD §5.6):
- `match`: 건수·`ids_sha256`·`content_sha256` 이 모두 같다.
- `mismatch`: **확정 불일치**. 어느 필드가 다른지 `fields` 에 적는다.
- `relay_lag_candidate`: 중계 지연 후보 — MongoDB 쪽이 상류(원본·outbox)보다 적고 그 부족분이 outbox 의 미전송으로 설명된다. 다시 낸 manifest 로 다시 확인한다(실패가 아니다).
  MongoDB → 파이프라인에는 쓰지 않는다 — 미전송은 MongoDB 에도 아직 없으므로 파이프라인이 적은 이유가 될 수 없다.
- `open_window`: 한쪽에서 아직 열려 있는 창이라 비교하지 않는다.
- `not_comparable`: outbox 가 보존 정리로 비교할 수 없는 창.
"""
import json
import re
from datetime import timedelta
from pathlib import Path

from . import hops, ndjson

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
        self.open_windows = set(checkpoint.get("open_windows", []))
        self.not_comparable = set(checkpoint.get("not_comparable_windows", []))
        self.unresolved_charges = checkpoint.get("unresolved_charges", 0)  # 원본 구간: 창에 놓을 수 없는 과금 원장 줄(대조할 줄이 없으므로 따로 실패)
        # outbox 구간: 비교 불가 창은 줄을 빼므로 그 창의 미전송 건수는 checkpoint 에 따로 싣는다(원본 → MongoDB 의 지연 후보 근거)
        self.not_comparable_unsent = {(u["window_start"], u["event_name"]): u["unsent"] for u in checkpoint.get("not_comparable_unsent", [])}


def _time(text, what):
    try:
        return hops.parse_time(text)
    except (TypeError, ValueError):
        raise HopInputError(f"{what} 시각이 올바르지 않다: {text!r}")


def _check_line(number, line, size):
    """한 줄이 계약 모양인지 본다: 필수 필드, 창 시작·끝이 UTC 경계에 맞고 끝 - 시작 = 선언한 창 크기, 건수·해시 형식."""
    for field in ("window_start", "window_end", "event_name", "count", "ids_sha256", "content_sha256"):
        if field not in line:
            raise HopInputError(f"{number}번째 줄에 {field} 가 없다")
    start, end = _time(line["window_start"], f"{number}번째 줄 window_start"), _time(line["window_end"], f"{number}번째 줄 window_end")
    if end - start != size:
        raise HopInputError(f"{number}번째 줄의 창 길이({end - start})가 선언한 창 크기({size})와 다르다 — 집계 범위가 다른 manifest 다")
    aligned = start.minute == 0 and start.second == 0 and (size != timedelta(days=1) or start.hour == 0)
    if not aligned:
        raise HopInputError(f"{number}번째 줄의 창 시작 {line['window_start']} 이 UTC 경계(자정·정시)에 맞지 않는다")
    if not isinstance(line["event_name"], str) or not line["event_name"]:
        raise HopInputError(f"{number}번째 줄의 event_name 이 올바르지 않다")
    if isinstance(line["count"], bool) or not isinstance(line["count"], int) or line["count"] < 0:
        raise HopInputError(f"{number}번째 줄의 count 는 0 이상의 정수여야 한다")
    for field in ("ids_sha256", "content_sha256"):
        if not isinstance(line[field], str) or not HEX64.fullmatch(line[field]):
            raise HopInputError(f"{number}번째 줄의 {field} 는 소문자 16진 SHA-256 이어야 한다")


def load_hop(manifest_path, name=None, expected_kind=None):
    """manifest 와 데이터 파일을 검증하고(SHA-256·행 수·형식) 줄을 읽는다. 검증에 실패하면 `HopInputError`.

    파일 checksum 이 맞는 것과 **집계 범위가 같은 것은 별개**라서 대조 전에 계약을 확인한다: 계약 버전, 구간 종류(`expected_kind` 가 있으면 그것과 같아야 한다),
    창 크기 선언(`checkpoint.window`), 줄마다 창 시작·끝·크기·경계 정렬, 건수·해시 형식, (창, 이벤트 이름) 중복 없음.
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
    lines = {}
    for number, text in enumerate((manifest_path.parent / manifest["file"]).read_text(encoding="utf-8").splitlines(), 1):
        line = json.loads(text)
        if not isinstance(line, dict):
            raise HopInputError(f"{number}번째 줄이 객체가 아니다")
        _check_line(number, line, size)
        key = (line["window_start"], line["event_name"])
        if key in lines:
            raise HopInputError(f"{number}번째 줄: (창, 이벤트 이름) {key} 가 중복이다 — 마지막 줄로 덮어쓰지 않고 거부한다")
        lines[key] = line
    return Hop(name or kind, manifest, lines)


def compare(a, b, unsent_from=None):
    """두 구간을 대조한다. `a` 가 상류, `b` 가 하류다. `unsent_from`: 미전송 건수(`unsent`)를 가진 outbox 구간 — 원본 → MongoDB 의 지연 후보를 가리는 데 쓴다.
    반환: 줄(dict) 목록 — `{"pair", "window_start", "event_name", "status", "fields", "a", "b"}`."""
    if a.window != b.window:
        raise HopInputError(f"창 크기가 다른 manifest 는 대조하지 않는다: {a.name}={a.window}, {b.name}={b.window}")
    out = []
    pair = f"{a.name}->{b.name}"
    for key in sorted(a.lines.keys() | b.lines.keys()):
        window, name = key
        la, lb = a.lines.get(key), b.lines.get(key)
        status, fields = _classify(a, b, key, la, lb, unsent_from)
        out.append({"pair": pair, "window_start": window, "event_name": name, "status": status, "fields": fields,
                    "a": _summary(la), "b": _summary(lb)})
    # 줄이 하나도 없는 열린 창·비교 불가 창도 기록으로 남긴다(조용히 사라지지 않게)
    for window in sorted((a.open_windows | b.open_windows) - {k[0] for k in a.lines.keys() | b.lines.keys()}):
        out.append({"pair": pair, "window_start": window, "event_name": None, "status": OPEN, "fields": [], "a": None, "b": None})
    return out


def _summary(line):
    return None if line is None else {f: line[f] for f in FIELDS} | ({"unsent": line["unsent"]} if "unsent" in line else {})


def _classify(a, b, key, la, lb, unsent_from):
    window = key[0]
    if window in a.open_windows or window in b.open_windows:
        return OPEN, []
    if window in a.not_comparable or window in b.not_comparable:
        return NOT_COMPARABLE, []
    if la is not None and lb is not None and all(la[f] == lb[f] for f in FIELDS):
        return MATCH, []
    fields = [f for f in FIELDS if (la or {}).get(f) != (lb or {}).get(f)] if la and lb else ["line_missing_in_" + ("b" if lb is None else "a")]
    if _is_lag(a, b, key, la, lb, unsent_from):
        return LAG, fields
    return MISMATCH, fields


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
