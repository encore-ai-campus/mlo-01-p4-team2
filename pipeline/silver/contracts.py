"""Silver 의 Spark 쪽 계약(계획 15 §2.1·§2.2, 설계 13 SV2·SV7, SL-3a 결정 D1~D5, SL-3 결정 1·4): 표 모양, 엄격 검사, 값 꺼내기, 오류 행.

- 엄격 검사는 Python UDF(`strict_reasons`)가 하고 사유만 돌려준다. 값 꺼내기·변환은 Spark 내장 식이다(SL-3 결정 1 (나)).
  UDF 는 Arrow 를 쓰지 않는다 — 2026-11-04 실측(코어 1, 1.3 KB 행 10만)에서 Arrow 없는 UDF 2.91~2.95초, Arrow UDF 2.79~2.85초로 거의 같아
  pandas·pyarrow 를 두지 않는다(결정 1 ②).
- 값 꺼내기(`field`·`extract`)는 Variant 의 형을 먼저 확인하고, 맞을 때만 꺼낸다. 형이 다르거나 범위를 넘으면 값은 null, 맞음 표시는 거짓이고
  어댑터는 그 행을 `contract` 오류로 보낸다 — 조용히 바뀌는 값이 없다. Variant 는 38자리를 넘는 수를 DOUBLE 로 바꾸므로(39자리 정수 →
  1.2345678901234568E38, 1,000자리 정수 → Infinity) 수를 문자열로 꺼내지 않는다. `payload`·`envelope` 는 JSON 문자열 값을 원문 그대로 꺼낸다.
"""
import re

from pyspark.sql import functions as F, types as T

from pipeline.silver import control, strict

ADAPTER_ERROR_CODES = ("decode", "contract", "unknown_schema") + control.STOP_CODES  # 어댑터가 낼 수 있는 코드
ERROR_CODES = ADAPTER_ERROR_CODES + ("conflict",)  # conflict 는 공통 틀만 낸다(접기·전체 확인 — SL-3b)


_TYPES = {  # 표 모양을 세션 없이 만든다(모듈 import 때 JVM 이 없어도 되게)
    "string": T.StringType(), "binary": T.BinaryType(), "int": T.IntegerType(), "bigint": T.LongType(), "boolean": T.BooleanType(),
    "timestamp": T.TimestampType(), "date": T.DateType(),
    "array<struct<key:string,value:binary>>": T.ArrayType(T.StructType([T.StructField("key", T.StringType()), T.StructField("value", T.BinaryType())])),
}


def _schema(*fields):
    return T.StructType([T.StructField(name, _TYPES[kind], nullable) for name, kind, nullable in fields])


# Bronze 표 열(bronze_ads_outbox.py 의 select 순서·Kafka 원천 형). Kafka 원천 열은 모두 NULL 을 허용한다
BRONZE_FIELDS = (
    ("key_binary", "binary", True), ("value_binary", "binary", True), ("headers", "array<struct<key:string,value:binary>>", True),
    ("topic", "string", True), ("partition", "int", True), ("offset", "bigint", True),
    ("kafka_timestamp", "timestamp", True), ("kafka_timestamp_type", "int", True),
    ("ingested_at", "timestamp", True), ("pipeline_release", "string", True), ("ingest_date", "date", True),
)
# 공통 열(13 SV2 + content_sha256). 이름은 RDB 확장 명세가 나오면 다시 맞춘다(잠정). 열 추가는 추가형이다(Bronze 에만 있는 사건 표시 G11 등).
# src_lsn·src_txid 는 NULL 을 허용한다 — 증분 스냅샷 재발행 사본(op r)은 source.lsn·txId 가 null 이다(MR-5 Bronze 덤프: op r 21행 모두 null).
SILVER_FIELDS = (
    ("event_id", "string", False), ("domain", "string", False), ("event_type", "string", False), ("schema_version", "string", False),
    ("occurred_at", "timestamp", False), ("produced_at", "timestamp", False),
    ("actor_user_uuid", "string", True), ("actor_subject_id", "string", True), ("actor_person_id", "string", True),
    ("session_uuid", "string", True), ("aggregate_id", "string", True),
    ("payload", "string", False), ("envelope", "string", True), ("source_meta", "string", True),
    ("src_topic", "string", False), ("src_partition", "int", False), ("src_offset", "bigint", False),
    ("src_lsn", "bigint", True), ("src_txid", "bigint", True), ("first_op", "string", False), ("copies", "int", False),
    ("ingested_at", "timestamp", False), ("silver_written_at", "timestamp", False), ("silver_release", "string", False),
    ("event_date", "date", False), ("content_sha256", "string", False),
)
FRAMEWORK_FIELDS = ("src_topic", "src_partition", "src_offset", "ingested_at", "copies", "event_date", "silver_written_at", "silver_release")
ADAPTER_FIELDS = tuple(field for field in SILVER_FIELDS if field[0] not in FRAMEWORK_FIELDS)  # first_op 은 이 사본의 op
ERROR_EXTRA_FIELDS = (
    ("existing_event_id", "string", True), ("existing_event_date", "date", True),  # 충돌한 Silver 기존 행(있을 때만, SL-3b)
    ("acknowledged", "boolean", False), ("ack_reason", "string", True), ("ack_incident", "string", True),  # 넘김 목록(C4)
    ("silver_release", "string", False), ("detected_at", "timestamp", False), ("batch_id", "bigint", False),
)
ADAPTER_ERROR_FIELDS = BRONZE_FIELDS + (("error_code", "string", False), ("reason", "string", False), ("event_id", "string", True))  # D1
WRITE_FIELDS = ("silver_written_at", "silver_release")  # 쓸 때 채운다(SL-3c)
MERGE_SOURCE_FIELDS = tuple(field for field in SILVER_FIELDS if field[0] not in WRITE_FIELDS)  # 배치 판정의 MERGE 원본(SL-3b)
BATCH_ERROR_FIELDS = ("silver_release", "detected_at", "batch_id")  # 오류 표 열 가운데 배치마다 하나인 셋(error_rows 가 붙임)
JUDGED_EXTRA_FIELDS = tuple(field for field in ERROR_EXTRA_FIELDS if field[0] not in BATCH_ERROR_FIELDS)  # 이름으로 뺀다(자리로 자르면 앞에 열을 더할 때 어긋남 — SL-3b 리뷰 참고 5)
JUDGED_ERROR_FIELDS = ADAPTER_ERROR_FIELDS + JUDGED_EXTRA_FIELDS  # 배치 판정의 오류·진단 행

BRONZE_SCHEMA = _schema(*BRONZE_FIELDS)
SILVER_SCHEMA = _schema(*SILVER_FIELDS)
OK_SCHEMA = _schema(*BRONZE_FIELDS, *ADAPTER_FIELDS)  # 어댑터 정상 결과 = Bronze 열 + 어댑터 열(접기·충돌 오류 행이 Bronze 원본을 쓴다)
ADAPTER_ERROR_SCHEMA = _schema(*ADAPTER_ERROR_FIELDS)  # 어댑터 오류 결과
ERROR_SCHEMA = _schema(*ADAPTER_ERROR_FIELDS, *ERROR_EXTRA_FIELDS)  # 오류 표
MERGE_SOURCE_SCHEMA = _schema(*MERGE_SOURCE_FIELDS)
JUDGED_ERROR_SCHEMA = _schema(*JUDGED_ERROR_FIELDS)

UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z"  # 소문자 하이픈 표기만. Java 의 $ 는 끝 줄바꿈 앞에서도 맞으므로 \z
SHA256_RE = r"^[0-9a-f]{64}\z"
_UTC_RE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z\z"  # Debezium ZonedTimestamp(UTC). 소수 6자리 넘으면 잘리므로 거부
_UTC_OFFSET_RE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?\+00:00\z"  # 광고 페이로드 시각(isoformat UTC — SL-8 결정 B (가)). Z·다른 오프셋은 거부
_INTEGER_TYPES_RE = r"^(BIGINT|INT|SMALLINT|TINYINT|DECIMAL\([0-9]+,0\))\z"  # Variant 의 정수 형. DOUBLE(1e2·39자리 정수)과 소수(1.5)는 거부
_PATH = re.compile(r"\$(\.[A-Za-z_][A-Za-z0-9_]*)+")
KINDS = ("string", "bigint", "timestamp", "timestamp_utc_offset")
_STAMPS = {"timestamp": _UTC_RE, "timestamp_utc_offset": _UTC_OFFSET_RE}  # 시각 형 → 받는 표기. 값은 둘 다 timestamp(UTC 순간)


STRICT_REASONS_TYPE = T.StructType([T.StructField("value", T.StringType()), T.StructField("nested", T.StringType())])


def strict_reasons(value, nested=()):
    """엄격 검사(strict.strict_reasons)를 Python UDF 로 — 행마다 한 번 부르고 봉투는 한 번만 해석한다. `value`: 바이트·문자열 열.
    `nested`: 그 안의 JSON 문자열 경로들(키 튜플). 반환: 구조체 열(`value` = 봉투 사유, `nested` = 안쪽 사유, 정상이면 null).
    어댑터는 framework 머리말의 순서로 적용한다: `value` 사유는 ②(decode), `nested` 사유는 출처·op 확인(③) 뒤의 ④(decode).
    둘을 나눠 돌려줘도 비용은 같다(2026-11-04 실측, 코어 1·1.3 KB 행 10만: 문자열 하나 2.86~2.88초, 둘 2.87~2.90초)."""
    paths = tuple(tuple(path) for path in nested)

    def check(data):
        return strict.strict_reasons(data, paths)

    return F.udf(check, STRICT_REASONS_TYPE, useArrow=False)(value)


def field(v, path, kind, nullable=False):
    """Variant `v` 의 `path`(예 "$.after.outbox_uuid")를 `kind` 로 꺼낸다. 반환: (값 열, 맞음 열 — null 이 아닌 참·거짓).
    - string: Variant 형이 STRING 일 때만(수·객체를 문자열로 바꾸지 않는다).
    - bigint: Variant 형이 정수(BIGINT·INT·SMALLINT·TINYINT·DECIMAL(p,0))이고 bigint 범위 안일 때만.
    - timestamp: STRING 이고 `YYYY-MM-DDTHH:MM:SS[.1~6자리]Z` 이고 실제 날짜일 때만.
    - timestamp_utc_offset: STRING 이고 `YYYY-MM-DDTHH:MM:SS[.1~6자리]+00:00` 이고 실제 날짜일 때만(SL-3d — 광고 SL-8 결정 B (가)). 값은 timestamp.
      `Z`·`-00:00`·다른 오프셋·콜론 없는 오프셋·시간대 없음은 틀림이다 — 표기가 바뀌면 원천이 바뀐 것이라 조용히 받지 않는다.
    JSON null 은 nullable 일 때만 맞음(값 null). 경로가 없으면 늘 틀림."""
    if not _PATH.fullmatch(path) or kind not in KINDS:
        raise ValueError(f"꺼낼 경로·형이 잘못됐다: {path!r} {kind!r}")
    vtype = F.schema_of_variant(F.variant_get(v, path, "variant"))  # 경로 없음 = null, JSON null = VOID
    if kind == "string":
        good = vtype == "STRING"
        value = F.when(good, F.variant_get(v, path, "string"))
    elif kind == "bigint":
        candidate = F.try_variant_get(v, path, "bigint")
        good = vtype.rlike(_INTEGER_TYPES_RE) & candidate.isNotNull()
        value = F.when(good, candidate)
    else:
        text = F.when(vtype == "STRING", F.variant_get(v, path, "string"))
        stamp = text.try_cast("timestamp")
        good = text.rlike(_STAMPS[kind]) & stamp.isNotNull()
        value = F.when(good, stamp)
    ok = F.coalesce(good, F.lit(False)) | (F.lit(nullable) & F.coalesce(vtype == "VOID", F.lit(False)))
    return value.cast("timestamp" if kind in _STAMPS else kind), ok


def extract(v, spec):
    """표(`[(열 이름, 경로, 형, NULL 허용)]`)대로 꺼낸다. 반환: (이름을 붙인 값 열 목록, 문제 열 — 틀린 열 이름을 표 순서로 ','로 이음, 없으면 null)."""
    values, problems = [], []
    for name, path, kind, nullable in spec:
        value, ok = field(v, path, kind, nullable)
        values.append(value.alias(name))
        problems.append(F.when(~ok, F.lit(name)))
    joined = F.concat_ws(",", *problems)
    return values, F.when(joined != "", joined)


def adapter_errors(bronze, code, reason, event_id):
    """어댑터 오류 결과(ADAPTER_ERROR_SCHEMA): Bronze 열 + error_code·reason·event_id(모르면 null — D1)."""
    return bronze.select(*[name for name, _, _ in BRONZE_FIELDS], code.alias("error_code"), reason.alias("reason"), event_id.cast("string").alias("event_id"))


def error_rows(errors, *, silver_release, detected_at, batch_id):
    """오류 표 행(ERROR_SCHEMA 순서). `errors` 는 어댑터 오류 열을 갖고, ERROR_EXTRA_FIELDS 가운데 기존 행·넘김 열이 있으면 그대로 쓰고
    없으면 기본값(null, acknowledged 거짓)을 둔다. silver_release·detected_at·batch_id 는 배치마다 하나다.
    detected_at 은 시간대가 있는 datetime 이어야 한다 — 시간대가 없으면 PySpark 가 세션이 아니라 Python 프로세스의 지역 시간대로 읽어
    값이 조용히 밀린다(리뷰 실측: KST 프로세스에서 05:55:37 → 전날 20:55:37 UTC)."""
    if getattr(detected_at, "tzinfo", None) is None:
        raise ValueError(f"detected_at 은 시간대가 있는 datetime 이어야 한다: {detected_at!r}")
    given = {"silver_release": F.lit(silver_release), "detected_at": F.lit(detected_at), "batch_id": F.lit(batch_id)}
    defaults = {"acknowledged": F.lit(False)}
    columns = [F.col(name) for name, _, _ in ADAPTER_ERROR_FIELDS]
    for name, kind, _ in ERROR_EXTRA_FIELDS:
        column = given.get(name, F.col(name) if name in errors.columns else defaults.get(name, F.lit(None)))
        columns.append(column.cast(kind).alias(name))
    return errors.select(*columns)
