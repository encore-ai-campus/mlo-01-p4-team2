"""광고 Gold 소비자 표(계획 temp/pipeline/41_ads-gold-plan.md r3e §3 — B1·B2 받음, AG1~AG10). 공통 틀(pipeline.gold.framework)이 부른다.

- ML 표 `creative_course_daily` v1(§3.2·§3.3, B2 — 과정별 광고 적합도 모델): 소재 × 과정 × KST 날짜, 인정된 노출·클릭·전환(AG10 (가) — converted_clicks 없음).
- 성과 보고 표 `performance_hourly` v1(§3.1, B1 "콘솔 기준으로 시작"): UTC 시간 × 캠페인 × 광고그룹 × 소재 × 매체 × 슬롯 × 과정,
  인정된 노출·클릭·전환, converted_clicks(클릭 칸 — 같은 (결정, 사람)에 인정된 전환이 이 Silver 판 어디에든 있는 클릭), spent(원장 charged_amount 합).
- 칸 시각(AG2 (가)) = Silver occurred_at: 반응은 수신 시각, 과금은 원장 시각(= 반응 수신 시각), 결정은 쓰지 않는다(반응·과금이 결정의 차원을 빌린다).
- 차원은 결정(aggregate_id = decision_id)에서 가져온다. 결정이 Silver 본 표에 없으면 차원 열 모두 null("차원 없음" — §2.5).
  과정 = 결정 3판 segment_id → ad.segment_changed 의 code(코드는 등록 뒤 바뀌지 않음 — 어느 판이든 같다, 매체 하나 전제). 보관된 그룹(segment_active 거짓)도
  그 코드다. 과정 null 이유: 결정 2판(decision_v2), 3판인데 그룹 없음(no_segment — 일괄 슬롯 포함, Q4), 그룹은 있는데 code 를 모름(unknown_code).
- 의심 시간(r2b C1·r3b C2): "차원 없음"과 unknown_code 사건의 시간 — 틀이 창 mismatch 회차에만 미확정으로 더한다.
- 확인(AG7)은 결정·그룹과 잇지 않은 단순 식으로 센다(잇기가 사건을 부풀리면 어긋남 — 같은 segment_id 에 code 가 둘이어도 여기서 잡힌다).
"""
from pyspark.sql import functions as F

from pipeline.gold import framework
from pipeline.silver import contracts

DELAY_SECONDS = 330  # 반응 보류 300초(REACTION_HOLD_SECONDS) + 다시 판정 30초 주기 = 5분 30초(계획 41 AG5)
DECISION, REACTION, CHARGE, SEGMENT = "ad.decision", "ad.reaction", "ad.charge", "ad.segment_changed"
TYPES = {"IMPRESSION": "impressions", "CLICK": "clicks", "CONVERSION": "conversions"}  # 반응 유형 → 지표
ACCEPTED = "ACCEPTED"
NO_DIMENSION = "no_dimension"
COURSE_NULL = ("decision_v2", "no_segment", "unknown_code")
DIMENSIONS = ("campaign_id", "ad_group_id", "creative_id", "media_id", "slot_id", "course")


def _text(path):
    return contracts.field(F.col("_v"), path, "string", nullable=True)[0]


def _parsed(frame, kind):
    return frame.where(F.col("event_type") == kind).withColumn("_v", F.try_parse_json("payload"))


def _decisions(frame):
    """decision_id → 차원 + 과정 + 과정 null 이유."""
    decisions = _parsed(frame, DECISION).select(
        F.col("aggregate_id").alias("decision_id"), _text("$.selected.campaign_id").alias("campaign_id"), _text("$.selected.ad_group_id").alias("ad_group_id"),
        _text("$.selected.creative_id").alias("creative_id"), _text("$.media_id").alias("media_id"), _text("$.slot_id").alias("slot_id"),
        F.col("schema_version").alias("_version"), _text("$.segment_id").alias("_segment"))
    codes = _parsed(frame, SEGMENT).select(F.col("aggregate_id").alias("_segment"), _text("$.code").alias("course")).distinct()
    reason = (F.when(F.col("_version") == "2", F.lit(COURSE_NULL[0])).when(F.col("_segment").isNull(), F.lit(COURSE_NULL[1]))
              .when(F.col("course").isNull(), F.lit(COURSE_NULL[2])))
    return decisions.join(codes, "_segment", "left").select("decision_id", *DIMENSIONS, reason.alias("_reason"))


def _accepted(frame):
    """인정된 반응(노출·클릭·전환) — 반응 하나에 판정 이벤트 하나(광고 서버 records/processing._finish)."""
    parsed = _parsed(frame, REACTION)
    kind = _text("$.type")
    return (parsed.where((_text("$.status") == ACCEPTED) & kind.isin(*TYPES))
            .select(F.col("occurred_at").alias("at"), F.col("aggregate_id").alias("decision_id"), kind.alias("_type"), F.col("actor_person_id").alias("_person")))


def _counted(events, decisions):
    """사건 → 결정 차원을 붙인 행 + `_why`(차원 없음 · 과정 null 이유 · 둘 다 아니면 null)."""
    joined = events.join(decisions.withColumn("_found", F.lit(True)), "decision_id", "left")
    return joined.withColumn("_why", F.when(F.col("_found").isNull(), F.lit(NO_DIMENSION)).otherwise(F.col("_reason")))


def _counts(rows):
    """실행 기록의 이유별 사건 수(그 Silver 판 전체 — 아직 쓰지 않은 칸의 사건 포함)."""
    found = {r["_why"]: r["count"] for r in rows.groupBy("_why").count().collect()}
    return {name: found.get(name, 0) for name in (NO_DIMENSION, *COURSE_NULL)}


def _doubt(rows):
    return rows.where(F.col("_why").isin(NO_DIMENSION, COURSE_NULL[2])).select("at")


def _metrics():
    return [F.when(F.col("_type") == kind, F.lit(1)).otherwise(F.lit(0)).cast("bigint").alias(name) for kind, name in TYPES.items()]


def _type_totals(frame, finished):
    done = _parsed(frame, REACTION).where(finished(F.col("occurred_at")) & (F.get_json_object("payload", "$.status") == ACCEPTED))
    counted = {r[0]: r[1] for r in done.groupBy(F.get_json_object("payload", "$.type")).count().collect()}
    return {metric: counted.get(kind, 0) for kind, metric in TYPES.items()}


def course_build(frame):
    rows = _counted(_accepted(frame), _decisions(frame))
    events = rows.select("at", "creative_id", "course", *_metrics())
    return framework.Built(events, _doubt(rows), _counts(rows))


def course_totals(frame, finished):
    return _type_totals(frame, finished)


def performance_build(frame):
    accepted = _accepted(frame)
    converted = accepted.where(F.col("_type") == "CONVERSION").select("decision_id", "_person").distinct().withColumn("_converted", F.lit(True))
    reactions = accepted.join(converted, ["decision_id", "_person"], "left").select(
        "at", "decision_id", *_metrics(),
        F.when((F.col("_type") == "CLICK") & F.col("_converted").isNotNull(), F.lit(1)).otherwise(F.lit(0)).cast("bigint").alias("converted_clicks"),
        F.lit(0).cast("bigint").alias("spent"), F.lit(True).alias("_valid"))
    amount, ok = contracts.field(F.col("_v"), "$.charged_amount", "bigint")
    charges = _parsed(frame, CHARGE).select(
        F.col("occurred_at").alias("at"), F.col("aggregate_id").alias("decision_id"), *[F.lit(0).cast("bigint").alias(m) for m in TYPES.values()],
        F.lit(0).cast("bigint").alias("converted_clicks"), F.coalesce(amount, F.lit(0)).cast("bigint").alias("spent"), ok.alias("_valid"))
    rows = _counted(reactions.unionByName(charges), _decisions(frame))
    events = rows.select("at", *DIMENSIONS, *TYPES.values(), "converted_clicks", "spent", "_valid")
    return framework.Built(events, _doubt(rows), _counts(rows))


def performance_totals(frame, finished):
    totals = _type_totals(frame, finished)
    accepted = _parsed(frame, REACTION).where(F.get_json_object("payload", "$.status") == ACCEPTED)
    kind = F.get_json_object("payload", "$.type")
    pairs = accepted.where(kind == "CONVERSION").select("aggregate_id", "actor_person_id")
    clicks = accepted.where((kind == "CLICK") & finished(F.col("occurred_at"))).select("aggregate_id", "actor_person_id")
    totals["converted_clicks"] = clicks.join(pairs, ["aggregate_id", "actor_person_id"], "left_semi").count()
    spent = _parsed(frame, CHARGE).where(finished(F.col("occurred_at"))).agg(F.sum(F.get_json_object("payload", "$.charged_amount").cast("bigint"))).first()[0]
    totals["spent"] = spent or 0
    return totals


COURSE_DAILY = framework.Table("creative_course_daily", 1, framework.KST_DAILY, (("creative_id", "string"), ("course", "string")), tuple(TYPES.values()),
                               course_build, course_totals)
PERFORMANCE_HOURLY = framework.Table("performance_hourly", 1, framework.HOURLY, tuple((name, "string") for name in DIMENSIONS),
                                     (*TYPES.values(), "converted_clicks", "spent"), performance_build, performance_totals)
TABLES = (COURSE_DAILY, PERFORMANCE_HOURLY)
