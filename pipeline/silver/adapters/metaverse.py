"""메타버스 Silver 어댑터(계획 15 §2.1·§2.1a·§2.2, SL-4): Bronze DataFrame → (정상 DataFrame, 오류 DataFrame). 판정 순서 ①~⑤는 framework 머리말이다.

- `settings`: 출처 `{"db", "schema", "table"}` — outbox 표의 이름(로컬 `metaverse_lab.public.eventing_eventoutbox`, 운영은 운영 DB 이름).
  모양은 15 §9 #30(Planner 결정 (가) 2026-11-04)이다. 검사는 `control.source_settings` 하나다(키는 정확히 셋, 값은 앞뒤 공백 없는 빈 문자열이
  아닌 str — 어긋나면 ValueError). 잡 입구가 Spark 전에 한 번 부르고 어댑터도 부른다(잡 없이 부르는 시험도 같은 규칙 — Planner 결정 2026-11-04).
- Silver 열 값은 모두 Spark 내장 식(contracts.extract)으로 꺼낸다. Python UDF 는 둘이다:
  엄격 검사(contracts.strict_reasons — 봉투와 `after.raw_data`·`after.payload_json` 문자열)와
  계약 확인 + 내용 해시(metaverse_rules.adapter_result — 사유와 content_sha256 만 돌려줌. SL-3 결정 1·4, Planner 승인 2026-11-04).
- ⑤의 순서: payload_json NULL(contract — 15 §5.4) → (종류, 판)이 표에 없음(unknown_schema) → 꺼내기 실패·event_id 모양(contract) → 계약(contract).
  (종류, 판)을 꺼내기 실패보다 먼저 보는 것은 새 판의 raw_data 모양이 달라 꺼내기가 틀려도 contract 가 아니라 unknown_schema 로 드러나게 하기 위해서다.
- 중간 결과(UDF 결과까지)를 cache 한다. 그러지 않으면 정상·오류 두 결과와 check_adapted 의 부분 질의마다 UDF 가 다시 돈다(framework.check_adapted).
"""
from functools import reduce

from pyspark.sql import functions as F, types as T

from pipeline.silver import contracts, control
from pipeline.silver.adapters import metaverse_rules as rules

DOMAIN = "metaverse"
NESTED = (("after", "raw_data"), ("after", "payload_json"))
ENVELOPE_SPEC = [  # (열, 경로, 형, NULL 허용) — 15 §2.1a 메타버스 열. "_" 로 시작하는 열은 Silver 열이 아니다(source_meta·UDF 입력)
    ("event_id", "$.after.event_uuid", "string", False), ("event_type", "$.after.event_type", "string", False),
    ("occurred_at", "$.after.occurred_at", "timestamp", False), ("produced_at", "$.after.created_at", "timestamp", False),
    ("payload", "$.after.raw_data", "string", False), ("envelope", "$.after.payload_json", "string", True),
    ("src_lsn", "$.source.lsn", "bigint", True), ("src_txid", "$.source.txId", "bigint", True), ("first_op", "$.op", "string", False),
    ("_topic", "$.after.topic", "string", False), ("_message_key", "$.after.message_key", "string", False),
    ("_published_at", "$.after.published_at", "string", True), ("_retry_count", "$.after.retry_count", "bigint", False),
    ("_occurred_text", "$.after.occurred_at", "string", False), ("_created_text", "$.after.created_at", "string", False),
]
RAW_SPEC = [("actor_user_uuid", "$.user_uuid", "string", True), ("session_uuid", "$.session_uuid", "string", True)]  # 값 규칙은 계약이 본다
MANIFEST_SPEC = [("schema_version", "$.raw_manifest.schema_version", "string", False)]
RESULT_TYPE = T.StructType([T.StructField("reason", T.StringType()), T.StructField("sha", T.StringType())])
_CHECK = F.udf(rules.adapter_result, RESULT_TYPE, useArrow=False)
_NULL = {"actor_subject_id", "actor_person_id", "aggregate_id"}  # 메타버스에 없는 열


def _first(rules_in_order):
    """[(조건, 코드, 사유)] → 앞에서 맞은 줄의 (코드, 사유) 열. 조건이 null 이면 맞지 않은 것으로 본다."""
    code, reason = F.lit(None).cast("string"), F.lit(None).cast("string")
    for condition, rule_code, rule_reason in reversed(rules_in_order):
        code = F.when(F.coalesce(condition, F.lit(False)), F.lit(rule_code)).otherwise(code)
        reason = F.when(F.coalesce(condition, F.lit(False)), rule_reason).otherwise(reason)
    return code, reason


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
    staged = staged.withColumn("_raw", F.try_parse_json(F.col("payload"))).withColumn("_manifest", F.try_parse_json(F.col("envelope")))
    raw_values, raw_problem = contracts.extract(F.col("_raw"), RAW_SPEC)
    manifest_values, manifest_problem = contracts.extract(F.col("_manifest"), MANIFEST_SPEC)
    staged = staged.select("*", *raw_values, *manifest_values, F.concat_ws(",", F.col("_problem_envelope"), raw_problem, manifest_problem).alias("_problem"))
    staged = staged.withColumn("_c", _CHECK(*[F.col(name) for name in (
        "event_id", "event_type", "schema_version", "_topic", "_message_key", "payload", "envelope", "_occurred_text", "_created_text")]))

    source_name = F.concat_ws(".", *[F.coalesce(F.col(f"_source_{key}"), F.lit("?")) for key in control.SOURCE_KEYS])
    same_source = reduce(lambda a, b: a & b, [F.col(f"_source_{key}") == source[key] for key in control.SOURCE_KEYS])
    known = reduce(lambda a, b: a | b, [(F.col("event_type") == t) & (F.col("schema_version") == s) for t, s in sorted(rules.FIELDS)])
    code, reason = _first([
        (F.col("value_binary").isNull(), "not_insert", F.lit("value 가 null(tombstone)")),  # ①
        (F.col("_r.value").isNotNull() | F.col("_v").isNull(), "decode", F.coalesce(F.col("_r.value"), F.lit("value: Spark 가 해석하지 못함"))),  # ②
        (~F.coalesce(same_source, F.lit(False)), "foreign_source", F.concat(F.lit("다른 출처: "), source_name)),  # ③ 출처
        (~F.coalesce(F.col("first_op").isin("c", "r"), F.lit(False)), "not_insert", F.concat(F.lit("op "), F.coalesce(F.col("first_op"), F.lit("없음")))),  # ③ op
        (~F.coalesce(F.col("_after_type").startswith("OBJECT"), F.lit(False)), "not_insert", F.lit("after 가 없다")),  # ③ after
        (F.col("_r.nested").isNotNull(), "decode", F.col("_r.nested")),  # ④
        (F.col("payload").isNotNull() & F.col("_raw").isNull(), "decode", F.lit("after.raw_data: Spark 가 해석하지 못함")),
        (F.col("envelope").isNotNull() & F.col("_manifest").isNull(), "decode", F.lit("after.payload_json: Spark 가 해석하지 못함")),
        (F.col("envelope").isNull(), "contract", F.lit("payload_json 이 NULL — schema_version 을 알 수 없다(15 §5.4)")),  # ⑤
        (F.col("event_type").isNotNull() & F.col("schema_version").isNotNull() & ~known, "unknown_schema",
         F.concat(F.lit("계약 표에 없는 (종류, 판): "), F.to_json(F.array("event_type", "schema_version")))),
        (F.col("_problem") != "", "contract", F.concat(F.lit("꺼낼 수 없는 열: "), F.col("_problem"))),
        (~F.col("event_id").rlike(contracts.UUID_RE), "contract", F.lit("event_id 가 소문자 UUID 가 아니다")),
        (F.col("_c.reason").isNotNull(), "contract", F.concat(F.lit("계약: "), F.col("_c.reason"))),
    ])
    source_meta = F.to_json(F.struct(F.col("_topic").alias("topic"), F.col("_message_key").alias("message_key"),
                                     F.col("_published_at").alias("published_at"), F.col("_retry_count").alias("retry_count")),
                            {"ignoreNullFields": "false"})  # published_at 은 원문 문자열(15 §2.1a — timestamp 로 바꾸면 밀리초로 잘림)
    extra = {"domain": F.lit(DOMAIN), "source_meta": source_meta, "content_sha256": F.col("_c.sha"), **{name: F.lit(None) for name in _NULL}}
    bronze_columns = [F.col(name) for name, _, _ in contracts.BRONZE_FIELDS]
    adapter_columns = [(extra[name] if name in extra else F.col(name)).cast(kind).alias(name) for name, kind, _ in contracts.ADAPTER_FIELDS]
    event_id = F.when(F.col("event_id").rlike(contracts.UUID_RE), F.col("event_id"))
    staged = staged.select(*bronze_columns, *adapter_columns, code.alias("_code"), reason.alias("_why"), event_id.alias("_event_id")).cache()
    ok = staged.where(F.col("_code").isNull()).select(*[name for name, _, _ in contracts.BRONZE_FIELDS], *[name for name, _, _ in contracts.ADAPTER_FIELDS])
    errors = contracts.adapter_errors(staged.where(F.col("_code").isNotNull()), F.col("_code"), F.col("_why"), F.col("_event_id"))
    return ok, errors
