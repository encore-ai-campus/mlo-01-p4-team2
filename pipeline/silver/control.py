"""Silver 잡의 드라이버 쪽 설정·정체성 계산(계획 15 §2.1, SL-3 결정 3) — 데이터 변환이 아니라 잡 시작 때 한 번 도는 검사라 표준 라이브러리로 쓴다.

- 넘김 목록(C4): 사람이 확인한 Bronze 위치. 파일은 `pipeline/silver/acknowledged/<domain>.json`(파일 자체는 SL-3c·SL-5 에서 둔다).
- Delta 멱등 표시 appId 와 checkpoint 정체성(SL-2 ①~⑨, D4).
- `silver_release`(코드 판).
- (SL-3c) 시작 검사(checkpoint 식별자 ①~④), 어댑터 출처 설정, 잡 설정(환경변수 — 기본값 없음). (SL-5) 읽기 바이트 상한, S3 연결 설정.
WIP(temp/review/SL-3a_wip-stdlib_r1.patch 의 framework.py)에서 그대로 옮겼다.
"""
import hashlib
import json
import re
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from pipeline.silver import strict

STOP_CODES = ("foreign_source", "not_insert")  # 설정 이상 — 배치를 멈춘다(SV7 (다)). 넘김 목록에 있는 위치만 예외

_ACK_COMMON = ("topic", "partition", "error_code", "reason", "incident", "acknowledged_by", "acknowledged_at")
_ACK_POINT = frozenset(_ACK_COMMON + ("offset",))
_ACK_RANGE = frozenset(_ACK_COMMON + ("offset_start", "offset_end", "row_count"))
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
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


class IdentityError(RuntimeError):
    """checkpoint 와 표가 같은 실행 이력이 아니다 — 시작 전에 멈춘다(15 §2.1 시작 검사 ①~④). 규칙: checkpoint 를 새로 만들면 새 세대."""


def checkpoint_id(text):
    """Spark checkpoint `metadata` 파일(`{"id":"<query id>"}`)에서 식별자를 꺼낸다. checkpoint 를 새로 만들면 바뀌고 다시 시작해도 같다(SL-3c 결정 S2).
    모양이 다르면 ValueError — Spark 판을 올려 형식이 바뀌면 시험(spark_job)과 함께 여기서 멈춘다."""
    try:
        document = json.loads(text)
    except (TypeError, ValueError) as error:
        raise ValueError(f"checkpoint metadata 가 JSON 이 아니다: {text!r}") from error
    value = document.get("id") if isinstance(document, dict) else None
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise ValueError(f"checkpoint metadata 에 소문자 UUID id 가 없다: {text!r}")
    return value


def start_check(silver_ids, error_ids, *, domain, generation, checkpoint, finished):
    """시작 검사(SL-3c 결정 S2 (가)). `silver_ids`·`error_ids`: 두 표의 txn appId, `checkpoint`: metadata 식별자(없으면 None),
    `finished`: checkpoint 에 끝난 배치(commits/)가 있나. 어긋나면 IdentityError.
    ① checkpoint 없음 + appId 없음 → 새로 시작  ② checkpoint 없음 + appId 있음 → 멈춤  ③ 다른 checkpoint 식별자 → 멈춤
    ④ 끝난 배치가 있는데 Silver 표에 이 식별자의 events appId 가, 또는 오류 표에 errors appId 가 없음 → 멈춤(표를 지웠거나 경로가 바뀜 —
       Silver 쪽은 지난 배치의 Bronze 가 조용히 빠지고, 오류 쪽은 지난 오류 행과 conflict 보호((a))가 사라진다. 빈 배치도 MERGE 하고 빈 오류
       append 도 하므로 끝난 배치는 늘 두 appId 를 남긴다 — SL-3c 리뷰 R1)."""
    prefix = f"silver:{domain}:g{generation}:"
    ours = sorted(app_id for app_id in set(silver_ids) | set(error_ids) if app_id.startswith(prefix))
    if checkpoint is None:
        if ours or finished:
            raise IdentityError(f"checkpoint 가 없는데(또는 metadata 가 없는데) 이 세대 표에 실행 기록이 있다 — 새 세대로 돌린다: {ours}")
        return
    foreign = foreign_checkpoints(ours, domain, generation, checkpoint)
    if foreign:
        raise IdentityError(f"표에 다른 checkpoint 의 기록이 있다 — checkpoint 를 새로 만들었으면 새 세대로 돌린다: {foreign}")
    missing = [label for label, table, ids in (("Silver", "events", silver_ids), ("오류", "errors", error_ids))
               if txn_app_id(domain, generation, table, checkpoint) not in set(ids)]
    if finished and missing:
        raise IdentityError(f"checkpoint 에 끝난 배치가 있는데 {'·'.join(missing)} 표에 그 기록이 없다 — 표를 지웠거나 경로가 바뀌었다(그대로 가면 지난 배치가 빠진다)")


SOURCE_KEYS = ("db", "schema", "table")


def source_settings(data):
    """어댑터 `settings` = 출처 dict `{"db","schema","table"}`(SL-3c 결정 S1 — 키는 정확히 셋, 값은 빈 문자열이 아닌 str). 잡 입구가 Spark 전에 한 번 검사한다."""
    if not isinstance(data, dict) or set(data) != set(SOURCE_KEYS):
        raise ValueError(f"출처 설정은 키 {SOURCE_KEYS} 만 갖는 dict 다: {data!r}")
    if not all(isinstance(data[key], str) and data[key].strip() == data[key] and data[key] for key in SOURCE_KEYS):
        raise ValueError(f"출처 이름은 앞뒤 공백 없는 빈 문자열이 아닌 str 이다: {data!r}")
    return {key: data[key] for key in SOURCE_KEYS}


JobSettings = namedtuple("JobSettings", "domain generation silver errors checkpoint bronze source acknowledged code_root max_files max_bytes")
_ENV = ("SILVER_LAKE_ROOT", "SILVER_BRONZE_TABLE", "SILVER_SOURCE_DB", "SILVER_SOURCE_SCHEMA", "SILVER_SOURCE_TABLE",
        "SILVER_ACKNOWLEDGED", "SILVER_CODE_ROOT", "SILVER_MAX_FILES_PER_TRIGGER", "SILVER_MAX_BYTES_PER_TRIGGER")
_BYTES = re.compile(r"[1-9][0-9]*[kmg]?")  # Delta maxBytesPerTrigger 표기(k·m·g = 1024 배수) — 소문자 단위 하나만 받는다
S3_ENV = ("S3_ENDPOINT", "S3_REGION", "S3_ACCESS_KEY_FILE", "S3_SECRET_KEY_FILE")  # Bronze s3a_session 과 같은 이름(SL-3 다시 나누기 §5)


def job_settings(environ, domain, generation):
    """잡 설정(SL-3c 정한 것 1): 환경변수, 기본값 없음 — 하나라도 없거나 비면 ValueError(모두 적음). 표 경로는 15 §5.5 (가):
    `<root>/silver/<d>/events/g<N>`·`<root>/silver/<d>/errors/g<N>`·`<root>/checkpoints/silver/<d>/events/g<N>`."""
    if not _NAME.fullmatch(str(domain)) or type(generation) is not int or generation < 1:
        raise ValueError(f"domain 은 [a-z0-9-]+, generation 은 1 이상의 정수다: {domain!r} {generation!r}")
    missing = [name for name in _ENV if not environ.get(name, "").strip()]
    if missing:
        raise ValueError(f"Silver 잡 설정이 없다(기본값 없음): {', '.join(missing)}")
    value = {name: environ[name] for name in _ENV}  # 앞뒤 공백을 지우지 않는다 — 출처 이름이 조용히 바뀌지 않게(source_settings 가 거부)
    root = value["SILVER_LAKE_ROOT"].rstrip("/")
    for name in ("SILVER_LAKE_ROOT", "SILVER_BRONZE_TABLE"):
        if "`" in value[name] or value[name] != value[name].strip() or not value[name].rstrip("/"):  # 경로는 SQL 의 delta.`<경로>` 에 들어간다
            raise ValueError(f"{name} 에 쓸 수 없는 경로다: {value[name]!r}")
    if not value["SILVER_MAX_FILES_PER_TRIGGER"].isdigit() or int(value["SILVER_MAX_FILES_PER_TRIGGER"]) < 1:
        raise ValueError(f"SILVER_MAX_FILES_PER_TRIGGER 는 1 이상의 정수다: {value['SILVER_MAX_FILES_PER_TRIGGER']!r}")
    if not _BYTES.fullmatch(value["SILVER_MAX_BYTES_PER_TRIGGER"]):  # 파일 하나가 2행~Bronze 상한까지라 파일 수만으로는 행 수를 묶지 못한다(SL-5 D3 (다))
        raise ValueError(f"SILVER_MAX_BYTES_PER_TRIGGER 는 1 이상의 정수 + 단위 k·m·g(없어도 됨)다: {value['SILVER_MAX_BYTES_PER_TRIGGER']!r}")
    source = source_settings({"db": value["SILVER_SOURCE_DB"], "schema": value["SILVER_SOURCE_SCHEMA"], "table": value["SILVER_SOURCE_TABLE"]})
    return JobSettings(domain, generation, f"{root}/silver/{domain}/events/g{generation}", f"{root}/silver/{domain}/errors/g{generation}",
                       f"{root}/checkpoints/silver/{domain}/events/g{generation}", value["SILVER_BRONZE_TABLE"], source,
                       value["SILVER_ACKNOWLEDGED"], value["SILVER_CODE_ROOT"], int(value["SILVER_MAX_FILES_PER_TRIGGER"]),
                       value["SILVER_MAX_BYTES_PER_TRIGGER"])


def s3_settings(environ, settings):
    """S3 연결 설정(SL-5 — SL-3 다시 나누기 §5): Silver 표나 Bronze 가 `s3a://` 면 S3_ENV 넷이 모두 있어야 한다(기본값 없음, 앞뒤 공백 거부 — 빠진 것을 모두 적음).
    반환: {"endpoint", "region", "access_key_file", "secret_key_file"} 또는 None(`s3a://` 경로가 없음 — file:// 시험). 키 값은 읽지 않는다(잡 입구가 파일에서 읽어 conf 로만 넘김)."""
    if not any(path.startswith("s3a://") for path in (settings.silver, settings.bronze)):
        return None
    missing = [name for name in S3_ENV if not environ.get(name, "").strip()]
    if missing:
        raise ValueError(f"s3a 경로를 쓰는데 S3 설정이 없다(기본값 없음): {', '.join(missing)}")
    spaced = [name for name in S3_ENV if environ[name] != environ[name].strip()]
    if spaced:
        raise ValueError(f"S3 설정 값에 앞뒤 공백이 있다: {', '.join(spaced)}")
    return dict(zip(("endpoint", "region", "access_key_file", "secret_key_file"), (environ[name] for name in S3_ENV)))


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
