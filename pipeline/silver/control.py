"""Silver 잡의 드라이버 쪽 설정·정체성 계산(계획 15 §2.1, SL-3 결정 3) — 데이터 변환이 아니라 잡 시작 때 한 번 도는 검사라 표준 라이브러리로 쓴다.

- 넘김 목록(C4): 사람이 확인한 Bronze 위치. 파일은 `pipeline/silver/acknowledged/<domain>.json`(파일 자체는 SL-3c·SL-5 에서 둔다).
- Delta 멱등 표시 appId 와 checkpoint 정체성(SL-2 ①~⑨, D4).
- `silver_release`(코드 판).
WIP(temp/review/SL-3a_wip-stdlib_r1.patch 의 framework.py)에서 그대로 옮겼다.
"""
import hashlib
import re
from datetime import datetime
from pathlib import Path

from pipeline.silver import strict

STOP_CODES = ("foreign_source", "not_insert")  # 설정 이상 — 배치를 멈춘다(SV7 (다)). 넘김 목록에 있는 위치만 예외

_ACK_COMMON = ("topic", "partition", "error_code", "reason", "incident", "acknowledged_by", "acknowledged_at")
_ACK_POINT = frozenset(_ACK_COMMON + ("offset",))
_ACK_RANGE = frozenset(_ACK_COMMON + ("offset_start", "offset_end", "row_count"))
_NAME = re.compile(r"[a-z0-9-]+")  # appId 조각(도메인·checkpoint 식별자) — ':' 가 들어가면 접두사 비교가 깨진다
TXN_TABLES = ("events", "errors", "stops")  # Silver MERGE, 오류 append, 멈춤 진단 append(D4 — 같은 오류 표, 다른 appId)


def _count(value, name, minimum):
    if type(value) is not int or value < minimum:
        raise ValueError(f"넘김 목록: {name} 은(는) {minimum} 이상의 정수다: {value!r}")
    return value


def load_acknowledged(data, domain):
    """넘김 목록 파일(`{"domain": …, "lines": [줄, …]}`)을 읽어 검사한다. 어긋나면 ValueError(설정 이상 — 잡이 시작하지 않는다).
    줄은 위치 하나(`offset`) 또는 범위(`offset_start`·`offset_end`·`row_count` — row_count 는 Bronze 표 전체에서 그 범위의 행 수, D3).
    반환: 줄 목록. 각 줄은 `start`·`end`(위치 하나면 같은 값)·`row_count`(위치 하나면 None)와 원래 값을 갖는다."""
    try:
        _, document = strict.decode_json(data, "넘김 목록")
    except strict.InvalidRecord as error:
        raise ValueError(f"넘김 목록: {error}") from error
    if set(document) != {"domain", "lines"} or document["domain"] != domain or not isinstance(document["lines"], list):
        raise ValueError(f"넘김 목록: {{\"domain\": {domain!r}, \"lines\": [...]}} 모양이 아니다")
    lines = []
    for item in document["lines"]:
        if not isinstance(item, dict) or set(item) not in (_ACK_POINT, _ACK_RANGE):
            raise ValueError(f"넘김 목록: 줄의 키가 위치 줄 {sorted(_ACK_POINT)} 나 범위 줄 {sorted(_ACK_RANGE)} 와 다르다: {item!r}")
        for name in ("topic", "reason", "incident", "acknowledged_by"):
            if not isinstance(item[name], str) or not item[name].strip():
                raise ValueError(f"넘김 목록: {name} 이(가) 빈 문자열이다: {item!r}")
        if item["error_code"] not in STOP_CODES:
            raise ValueError(f"넘김 목록: error_code 는 멈춤 코드 {STOP_CODES} 만 넘긴다: {item['error_code']!r}")
        try:
            at = datetime.strptime(item["acknowledged_at"], "%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            at = None
        if at is None or at.strftime("%Y-%m-%dT%H:%M:%SZ") != item["acknowledged_at"]:  # strptime 은 0 을 채우지 않은 표기도 받는다
            raise ValueError(f"넘김 목록: acknowledged_at 은 YYYY-MM-DDTHH:MM:SSZ 다: {item['acknowledged_at']!r}")
        _count(item["partition"], "partition", 0)
        if "offset" in item:
            start = end = _count(item["offset"], "offset", 0)
            row_count = None
        else:
            start, end = _count(item["offset_start"], "offset_start", 0), _count(item["offset_end"], "offset_end", 0)
            row_count = _count(item["row_count"], "row_count", 1)
            if start > end:
                raise ValueError(f"넘김 목록: offset_start > offset_end: {item!r}")
        lines.append({**item, "start": start, "end": end, "row_count": row_count})
    spans = sorted((line["topic"], line["partition"], line["start"], line["end"]) for line in lines)
    for before, after in zip(spans, spans[1:]):
        if before[:2] == after[:2] and after[2] <= before[3]:  # 겹치면 어느 줄이 넘겼는지 모호하다
            raise ValueError(f"넘김 목록: 같은 (topic, partition) 의 줄이 겹친다: {before} · {after}")
    return lines


def txn_app_id(domain, generation, table, checkpoint_id):
    """Delta 멱등 표시 appId: `silver:<domain>:g<N>:<table>:<checkpoint id>`. txnVersion 은 foreachBatch 의 batchId 그대로다.
    checkpoint 를 새로 만들면 batchId 가 0 부터 다시 세어지므로 appId 에 checkpoint 식별자를 넣는다."""
    if not _NAME.fullmatch(str(domain)) or not _NAME.fullmatch(str(checkpoint_id)) or table not in TXN_TABLES:
        raise ValueError(f"appId 조각이 잘못됐다: domain={domain!r} table={table!r} checkpoint={checkpoint_id!r}")
    if type(generation) is not int or generation < 1:
        raise ValueError(f"세대는 1 이상의 정수다: {generation!r}")
    return f"silver:{domain}:g{generation}:{table}:{checkpoint_id}"


def foreign_checkpoints(app_ids, domain, generation, checkpoint_id):
    """표에 남은 txn appId 가운데 같은 원천·같은 세대인데 checkpoint 식별자가 다른 것(정렬). 하나라도 있으면 시작 전에 멈춘다 —
    checkpoint 를 지우고 같은 세대 표에 다시 돌리면 copies 가 조용히 두 배가 된다(SL-2 ⑨). 규칙: checkpoint 를 새로 만들면 새 세대."""
    prefix = f"silver:{domain}:g{generation}:"
    return sorted(app_id for app_id in app_ids if app_id.startswith(prefix) and app_id.rsplit(":", 1)[-1] != checkpoint_id)


def silver_release(root=None):
    """Silver 코드 판(15 §2.1): `silver@` + SHA-256 앞 12자. 입력은 `pipeline/silver/` 아래 *.py(정렬한 상대 경로)와 `pipeline/common/hop_rules.py`.
    파일마다 상대 경로·길이·바이트를 이어 해시한다(파일 사이에 내용을 옮겨도 값이 바뀐다). 넘김 목록 파일(*.json)·제출 스크립트는 넣지 않는다.
    `root` 는 저장소(또는 이미지 안 코드) 뿌리다. spark-submit 드라이버는 이 모듈을 --py-files zip 에서 읽으므로 잡은 뿌리를 설정으로 넘긴다(SL-3c)."""
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    silver = sorted((root / "pipeline" / "silver").rglob("*.py"))
    if not silver:  # 뿌리 설정을 잘못 두면 hop_rules 하나만 해시한 값이 조용히 나온다
        raise FileNotFoundError(f"{root / 'pipeline' / 'silver'} 아래에 *.py 가 없다 — 코드 뿌리 설정을 확인한다")
    files = silver + [root / "pipeline" / "common" / "hop_rules.py"]
    digest = hashlib.sha256()
    for path in files:
        data = path.read_bytes()  # 사본이 없으면 FileNotFoundError — 판을 모르는 채로 돌지 않는다
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0" + str(len(data)).encode("ascii") + b"\0" + data)
    return "silver@" + digest.hexdigest()[:12]
