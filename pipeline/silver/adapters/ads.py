"""광고 Silver 어댑터(SL-8 계획 3판 — temp/review/SL-8-plan.md 5bc20d3c, 15 §2.1·§2.1a·§2.2): Bronze DataFrame → (정상 DataFrame, 오류 DataFrame).
판정 순서 ①~⑤는 framework 머리말이다(메타버스 SL-4 와 같은 자리).

- `settings`: 출처 `{"db", "schema", "table"}` — outbox 표의 이름(lab `ads_pipeline_lab.public.events_outbox`, 운영은 운영 DB 이름). 검사는 control.source_settings.
- Silver 열 값은 모두 Spark 내장 식(contracts.extract)으로 꺼낸다. Python UDF 는 둘이다: 엄격 검사(contracts.strict_reasons — 봉투와 `after.payload` 문자열)와
  계약 + 내용 해시 + tombstone 키 ID(ads_rules.adapter_result — 사유·해시·키 ID 만 돌려줌. Silver 열 값은 돌려주지 않는다).
- 종류별 열(occurred_at·actor_subject_id·actor_person_id)은 그 종류의 행에서만 꺼낸다 — 경로가 없으면 contracts.field 는 늘 틀림이라, 과금 행에서
  subject_id 를 꺼내면 안 된다. occurred_at 은 (종류, 판)마다 페이로드의 다른 필드다(ads_rules.CONTRACTS 셋째 칸, 결정 A·B — `+00:00` 표기).
- ⑤의 순서: (종류, 판)이 표에 없음(unknown_schema) → 꺼내기 실패(contract) → event_id 모양(contract, event_id null) → 계약(contract).
  (종류, 판)을 꺼내기 실패보다 먼저 보는 것은 새 판의 페이로드 모양이 달라 꺼내기가 틀려도 contract 가 아니라 unknown_schema 로 드러나게 하기 위해서다.
- op `u` 하나로 광고 Silver 가 멈춘다(③ not_insert → SV7 (다)). hop 대사는 그 창만 실패한다 — Silver 가 더 엄격하다(pipeline/README.md Silver 잡 절).
- 중간 결과(UDF 결과까지)를 cache 한다. 비우는 일은 공통 틀이 배치 끝에 한다.
"""
from functools import reduce

from pyspark.sql import functions as F, types as T

from pipeline.silver import contracts, control
from pipeline.silver.adapters import ads_rules as rules

DOMAIN = "ads"
NESTED = (("after", "payload"),)
ENVELOPE_SPEC = [  # (열, 경로, 형, NULL 허용) — 계획 §2.2. "_" 로 시작하는 열은 Silver 열이 아니다
    ("event_id", "$.after.event_id", "string", False), ("event_type", "$.after.event_name", "string", False),
    ("schema_version", "$.after.schema_version", "bigint", False), ("produced_at", "$.after.created_at", "timestamp", False),  # Silver 는 문자열(아래 cast)
    ("aggregate_id", "$.after.aggregate_id", "string", False), ("payload", "$.after.payload", "string", False),
    ("src_lsn", "$.source.lsn", "bigint", True), ("src_txid", "$.source.txId", "bigint", True), ("first_op", "$.op", "string", False),
]
SUBJECT_KINDS = ("ad.decision", "ad.reaction_received", "ad.reaction")  # 페이로드 subject_id(매체 사용자 — null 가능). 그 밖은 null
PERSON_KINDS = ("ad.reaction",)  # 페이로드 person_id(판정 기준의 사람 — null 가능)
RESULT_TYPE = T.StructType([T.StructField("reason", T.StringType()), T.StructField("sha", T.StringType()), T.StructField("key_id", T.StringType())])
_CHECK = F.udf(rules.adapter_result, RESULT_TYPE, useArrow=False)
_NULL = {"actor_user_uuid", "session_uuid", "envelope", "source_meta"}  # 광고에 없는 열


def _first(rules_in_order):
    """[(조건, 코드, 사유)] → 앞에서 맞은 줄의 (코드, 사유) 열. 조건이 null 이면 맞지 않은 것으로 본다."""
    code, reason = F.lit(None).cast("string"), F.lit(None).cast("string")
    for condition, rule_code, rule_reason in reversed(rules_in_order):
        code = F.when(F.coalesce(condition, F.lit(False)), F.lit(rule_code)).otherwise(code)
        reason = F.when(F.coalesce(condition, F.lit(False)), rule_reason).otherwise(reason)
    return code, reason


def _by_kind(choices, name, missing_ok):
    """[(조건, 경로, 형, NULL 허용)] → 앞에서 맞은 조건의 (값, 맞음). 맞은 조건이 없으면 값 null, 맞음 = missing_ok. 반환: (이름 붙인 값 열, 문제 열)."""
    value, ok = F.lit(None), F.lit(missing_ok)  # 값의 형은 CASE WHEN 이 갈래(contracts.field)의 형으로 맞춘다
    for condition, path, kind, nullable in reversed(choices):
        got, good = contracts.field(F.col("_p"), path, kind, nullable)
        value, ok = F.when(condition, got).otherwise(value), F.when(condition, good).otherwise(ok)
    return value.alias(name), F.when(~F.coalesce(ok, F.lit(False)), F.lit(name))


def adapt(bronze, settings):
    """Bronze(contracts.BRONZE_SCHEMA) → (정상 contracts.OK_SCHEMA, 오류 contracts.ADAPTER_ERROR_SCHEMA)."""
    source = control.source_settings(settings)
    staged = bronze.withColumn("_r", contracts.strict_reasons(F.col("value_binary"), NESTED))
    staged = staged.withColumn("_v", F.when(F.col("value_binary").isNotNull() & F.col("_r.value").isNull(),
                                            F.try_parse_json(F.col("value_binary").cast("string"))))
    v = F.col("_v")
    values, envelope_problem = contracts.extract(v, ENVELOPE_SPEC)
    names = [contracts.field(v, f"$.source.{key}", "string")[0].alias(f"_source_{key}") for key in control.SOURCE_KEYS]
    after_type = F.schema_of_variant(F.variant_get(v, "$.after", "variant"))
    staged = staged.select("*", *values, envelope_problem.alias("_problem_envelope"), *names, after_type.alias("_after_type"))
    staged = staged.withColumn("_p", F.try_parse_json(F.col("payload")))
    kind, version = F.col("event_type"), F.col("schema_version")
    occurred, occurred_problem = _by_kind([((kind == t) & (version == s), f"$.{line[2]}", "timestamp_utc_offset", False)
                                           for (t, s), line in sorted(rules.CONTRACTS.items())], "occurred_at", False)
    subject, subject_problem = _by_kind([(kind == t, "$.subject_id", "string", True) for t in SUBJECT_KINDS], "actor_subject_id", True)
    person, person_problem = _by_kind([(kind == t, "$.person_id", "string", True) for t in PERSON_KINDS], "actor_person_id", True)
    staged = staged.select("*", occurred, subject, person,
                           F.concat_ws(",", F.col("_problem_envelope"), occurred_problem, subject_problem, person_problem).alias("_problem"))
    staged = staged.withColumn("_c", _CHECK(F.col("value_binary").cast("string"), F.col("key_binary").cast("string")))

    source_name = F.concat_ws(".", *[F.coalesce(F.col(f"_source_{key}"), F.lit("?")) for key in control.SOURCE_KEYS])
    same_source = reduce(lambda a, b: a & b, [F.col(f"_source_{key}") == source[key] for key in control.SOURCE_KEYS])
    known = reduce(lambda a, b: a | b, [(kind == t) & (version == s) for t, s in sorted(rules.CONTRACTS)])
    code, reason = _first([
        (F.col("value_binary").isNull(), "not_insert", F.lit("value 가 null(tombstone)")),  # ①
        (F.col("_r.value").isNotNull() | F.col("_v").isNull(), "decode", F.coalesce(F.col("_r.value"), F.lit("value: Spark 가 해석하지 못함"))),  # ②
        (~F.coalesce(same_source, F.lit(False)), "foreign_source", F.concat(F.lit("다른 출처: "), source_name)),  # ③ 출처
        (~F.coalesce(F.col("first_op").isin("c", "r"), F.lit(False)), "not_insert", F.concat(F.lit("op "), F.coalesce(F.col("first_op"), F.lit("없음")))),  # ③ op
        (~F.coalesce(F.col("_after_type").startswith("OBJECT"), F.lit(False)), "not_insert", F.lit("after 가 없다")),  # ③ after
        (F.col("_r.nested").isNotNull(), "decode", F.col("_r.nested")),  # ④
        (F.col("payload").isNotNull() & F.col("_p").isNull(), "decode", F.lit("after.payload: Spark 가 해석하지 못함")),
        (kind.isNotNull() & version.isNotNull() & ~known, "unknown_schema",  # ⑤
         F.concat(F.lit("계약 표에 없는 (종류, 판): "), F.to_json(F.array(kind, version.cast("string"))))),
        (F.col("_problem") != "", "contract", F.concat(F.lit("꺼낼 수 없는 열: "), F.col("_problem"))),
        (~F.col("event_id").rlike(contracts.UUID_RE), "contract", F.lit("event_id 가 소문자 UUID 가 아니다")),
        (F.col("_c.reason").isNotNull(), "contract", F.concat(F.lit("계약: "), F.col("_c.reason"))),
    ])
    extra = {"domain": F.lit(DOMAIN), "content_sha256": F.col("_c.sha"), **{name: F.lit(None) for name in _NULL}}  # schema_version 은 아래 cast 로 bigint → 문자열
    bronze_columns = [F.col(name) for name, _, _ in contracts.BRONZE_FIELDS]
    adapter_columns = [(extra[name] if name in extra else F.col(name)).cast(kind_).alias(name) for name, kind_, _ in contracts.ADAPTER_FIELDS]
    event_id = F.when(F.col("value_binary").isNull(), F.col("_c.key_id")).when(F.col("event_id").rlike(contracts.UUID_RE), F.col("event_id"))
    staged = staged.select(*bronze_columns, *adapter_columns, code.alias("_code"), reason.alias("_why"), event_id.alias("_event_id")).cache()
    ok = staged.where(F.col("_code").isNull()).select(*[name for name, _, _ in contracts.BRONZE_FIELDS], *[name for name, _, _ in contracts.ADAPTER_FIELDS])
    errors = contracts.adapter_errors(staged.where(F.col("_code").isNotNull()), F.col("_code"), F.col("_why"), F.col("_event_id"))
    return ok, errors
