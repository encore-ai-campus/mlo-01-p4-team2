"""광고 Gold 소비자 표(계획 temp/pipeline/41_ads-gold-plan.md r3e §3 — B1·B2 받음, AG1~AG10; ML 표 v2 = ACW-2 계획 66 r3, 기획 63 §6). 공통 틀(pipeline.gold.framework)이 부른다.

- ML 표 `creative_course_daily` v2(기획 63 §6 — U4·U6·U7·U8·U13·U24·U33, 학습용·외부 ML): KST 날짜 × 광고(결정의 선택된 광고그룹), 과정 셋(MLO → AIO → SKN)마다
  인정된 클릭 수·클릭률(클릭 / 노출)·전환율(전환이 이어진 클릭 / 클릭 — 클릭 코호트, 성과 보고 converted_clicks 와 같은 규칙), 그날 끝(다음 KST 자정) 이하
  마지막 판의 카테고리. 집계 안 지표는 과정 셋 × 노출·클릭·전환 이어진 클릭 9개이고 표에는 클릭 수만 남는다(shape — 13열). 정정 기록을 쓰지 않는다(U33).
  `mle`·과정 null·"차원 없음" 반응은 행에 들지 않고 회차 기록 counts 에 이유별로 센다. 카테고리는 틀 안에서 키 열이다((날짜, 광고)마다 하나라 행이 갈라지지 않음).
- 성과 보고 표 `performance_hourly` v1(§3.1, B1 "콘솔 기준으로 시작"): UTC 시간 × 캠페인 × 광고그룹 × 소재 × 매체 × 슬롯 × 과정,
  인정된 노출·클릭·전환, converted_clicks(클릭 칸 — 같은 (결정, 사람)에 인정된 전환이 이 Silver 판 어디에든 있는 클릭), spent(원장 charged_amount 합).
- 칸 시각(AG2 (가)) = Silver occurred_at: 반응은 수신 시각, 과금은 원장 시각(= 반응 수신 시각), 결정은 쓰지 않는다(반응·과금이 결정의 차원을 빌린다).
- 차원은 결정(aggregate_id = decision_id)에서 가져온다. 결정이 Silver 본 표에 없으면 차원 열 모두 null("차원 없음" — §2.5).
  과정 = 결정 3판 segment_id → ad.segment_changed 의 code(코드는 등록 뒤 바뀌지 않음 — 어느 판이든 같다, 매체 하나 전제). 보관된 그룹(segment_active 거짓)도
  그 코드다. 과정 null 이유: 결정 2판(decision_v2), 3판인데 그룹 없음(no_segment — 일괄 슬롯 포함, Q4), 그룹은 있는데 code 를 모름(unknown_code).
- 의심 시간(r2b C1·r3b C2): "차원 없음"과 unknown_code 사건의 시간 — 틀이 창 mismatch 회차에만 미확정으로 더한다.
- 확인(AG7)은 결정·그룹과 잇지 않은 단순 식으로 센다(잇기가 사건을 부풀리면 어긋남 — 같은 segment_id 에 code 가 둘이어도 여기서 잡힌다).
  ML 표는 "행 + 행 밖 = Silver 단순 셈"을 본다(행 밖 = 카테고리를 붙이기 전 잇기 결과에서 이유가 있는 사건 — 계획 66 §5.7).
"""
from pyspark.sql import functions as F

from pipeline.gold import framework
from pipeline.silver import contracts

DELAY_SECONDS = 330  # 반응 보류 300초(REACTION_HOLD_SECONDS) + 다시 판정 30초 주기 = 5분 30초(계획 41 AG5)
DECISION, REACTION, CHARGE, SEGMENT = "ad.decision", "ad.reaction", "ad.charge", "ad.segment_changed"
PROFILE = "ad.ad_group_profile_changed"  # 광고 카테고리·과정 가중치의 판(기획 63 §4)
TYPES = {"IMPRESSION": "impressions", "CLICK": "clicks", "CONVERSION": "conversions"}  # 반응 유형 → 지표
ACCEPTED = "ACCEPTED"
NO_DIMENSION = "no_dimension"
COURSE_NULL = ("decision_v2", "no_segment", "unknown_code")
DIMENSIONS = ("campaign_id", "ad_group_id", "creative_id", "media_id", "slot_id", "course")
COURSES = ("mlo", "aio", "skn")  # ML 표의 과정 열 순서(U6)
OTHER_COURSE = "other_course"  # 세 과정 밖의 코드(지금은 mle) — ML 표 행 밖(계획 66 C4)
COURSE_METRICS = ("impressions", "clicks", "converted_clicks")  # ML 표 집계 안 지표(과정마다) — 확인도 이 셋
UNCLASSIFIED, NO_PROFILE = "unclassified", "no_profile"  # 카테고리 null 의 두 이유 — (날짜, 광고) 짝 수(계획 66 C2·C3)
_INT_MAX = 2 ** 31 - 1  # category 열(int)의 위 끝


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


def _counts(rows, names=(NO_DIMENSION, *COURSE_NULL)):
    """실행 기록의 이유별 사건 수(그 Silver 판 전체 — 아직 쓰지 않은 칸의 사건 포함)."""
    found = {r["_why"]: r["count"] for r in rows.groupBy("_why").count().collect()}
    return {name: found.get(name, 0) for name in names}


def _doubt(rows):
    return rows.where(F.col("_why").isin(NO_DIMENSION, COURSE_NULL[2])).select("at")


def _metrics():
    return [F.when(F.col("_type") == kind, F.lit(1)).otherwise(F.lit(0)).cast("bigint").alias(name) for kind, name in TYPES.items()]


def _reactions(frame):
    """인정된 반응 사건 — at, decision_id, 지표 셋(노출·클릭·전환 몫) + converted_clicks(같은 (결정, 사람)에 인정된 전환이 이 Silver 판 어디든 있는 클릭).
    성과 보고 표와 ML 표가 함께 쓴다."""
    accepted = _accepted(frame)
    converted = accepted.where(F.col("_type") == "CONVERSION").select("decision_id", "_person").distinct().withColumn("_converted", F.lit(True))
    return accepted.join(converted, ["decision_id", "_person"], "left").select(
        "at", "decision_id", *_metrics(),
        F.when((F.col("_type") == "CLICK") & F.col("_converted").isNotNull(), F.lit(1)).otherwise(F.lit(0)).cast("bigint").alias("converted_clicks"))


def _type_totals(frame, finished):
    done = _parsed(frame, REACTION).where(finished(F.col("occurred_at")) & (F.get_json_object("payload", "$.status") == ACCEPTED))
    counted = {r[0]: r[1] for r in done.groupBy(F.get_json_object("payload", "$.type")).count().collect()}
    return {metric: counted.get(kind, 0) for kind, metric in TYPES.items()}


def _converted_total(frame, finished):
    """끝난 칸의 converted_clicks 를 잇기 없이: 인정된 클릭 가운데 같은 (결정, 사람)에 인정된 전환이 있는 것."""
    accepted = _parsed(frame, REACTION).where(F.get_json_object("payload", "$.status") == ACCEPTED)
    kind = F.get_json_object("payload", "$.type")
    pairs = accepted.where(kind == "CONVERSION").select("aggregate_id", "actor_person_id")
    clicks = accepted.where((kind == "CLICK") & finished(F.col("occurred_at"))).select("aggregate_id", "actor_person_id")
    return clicks.join(pairs, ["aggregate_id", "actor_person_id"], "left_semi").count()


def _course_rows(frame):
    """ML 표의 잇기 결과 — 인정된 반응 사건 + 결정 차원·과정 + `_why`(v1 이유 넷, 세 과정 밖이면 other_course, null 이면 행이 될 사건).
    build 와 totals 가 함께 쓴다(계획 66 §5.3·§5.7 — 확인의 행 밖 수는 카테고리를 붙이기 전 이 결과에서 센다)."""
    rows = _counted(_reactions(frame), _decisions(frame))
    outside = F.col("_why").isNull() & ~F.coalesce(F.col("course").isin(*COURSES), F.lit(False))
    return rows.withColumn("_why", F.when(outside, F.lit(OTHER_COURSE)).otherwise(F.col("_why")))


def _profiles(frame):
    """광고 카테고리 판 — ad_group_id, 판 시각 `_at`(µs, Silver occurred_at = 페이로드 changed_at), 판 번호 `_version`, `category`.
    판 번호(1 이상 정수)·카테고리(null 또는 0~2^31-1 정수)가 형·범위 밖인 행이 그 Silver 판에 하나라도 있으면 쓰지 않는다(계획 66 C6 — 개수만 적는다)."""
    parsed = _parsed(frame, PROFILE)
    version, version_ok = contracts.field(F.col("_v"), "$.version", "bigint")
    category, category_ok = contracts.field(F.col("_v"), "$.category", "bigint", nullable=True)
    good = version_ok & category_ok & (version >= 1) & (category.isNull() | category.between(0, _INT_MAX))
    rows = parsed.select(F.col("aggregate_id").alias("ad_group_id"), F.unix_micros("occurred_at").alias("_at"), version.alias("_version"),
                         category.alias("category"), F.coalesce(good, F.lit(False)).alias("_good"))
    bad = rows.where(~F.col("_good")).count()
    if bad:
        raise framework.GoldError(f"creative_course_daily: 카테고리 판({PROFILE})의 version·category {bad} 개가 형·범위를 벗어났다 — 쓰지 않는다")
    return rows.drop("_good")


def _categories(kept, profiles):
    """행이 될 (광고, 그날 끝 `_end`) 짝마다 한 줄: 판 시각 ≤ 그날 끝인 판 가운데 판 번호가 가장 큰 판의 카테고리(U24, 계획 66 C1 — 그 판의
    카테고리가 null 이면 null), `_state` = 카테고리 null 의 이유(그날 끝 이하 판 없음 → no_profile, 그 판의 카테고리 NULL → unclassified)."""
    pairs = kept.select("ad_group_id", "_end").distinct().alias("k")
    p = profiles.alias("p")
    joined = pairs.join(p, (F.col("k.ad_group_id") == F.col("p.ad_group_id")) & (F.col("p._at") <= F.col("k._end")), "left")
    picked = joined.groupBy(F.col("k.ad_group_id").alias("ad_group_id"), F.col("k._end").alias("_end")).agg(
        F.max_by(F.col("p.category"), F.col("p._version")).alias("category"), F.max(F.col("p._version")).alias("_last"))
    state = F.when(F.col("_last").isNull(), F.lit(NO_PROFILE)).when(F.col("category").isNull(), F.lit(UNCLASSIFIED))
    return picked.select("ad_group_id", "_end", F.col("category").cast("int").alias("category"), state.alias("_state"))


def course_build(frame):
    rows = _course_rows(frame).withColumn("_end", framework._end_us(framework.KST_DAILY, framework._hour(F.col("at"))))  # 그날 끝(다음 KST 자정, µs)
    kept = rows.where(F.col("_why").isNull())
    categories = _categories(kept, _profiles(frame))
    events = kept.join(categories, ["ad_group_id", "_end"], "left").select(
        "at", "ad_group_id", "category",
        *[F.when(F.col("course") == course, F.col(metric)).otherwise(F.lit(0)).cast("bigint").alias(f"{course}_{metric}")
          for course in COURSES for metric in COURSE_METRICS])
    counts = _counts(rows, (NO_DIMENSION, *COURSE_NULL, OTHER_COURSE))
    states = {r["_state"]: r["count"] for r in categories.where(F.col("_state").isNotNull()).groupBy("_state").count().collect()}
    counts.update({name: states.get(name, 0) for name in (UNCLASSIFIED, NO_PROFILE)})
    return framework.Built(events, _doubt(rows), counts)


def course_totals(frame, finished):
    """유형마다 Silver 단순 셈(끝난 칸) − 행 밖 사건 수(카테고리를 붙이기 전 잇기 결과에서 이유가 있는 사건, 같은 at 으로 끝난 칸)."""
    simple = _type_totals(frame, finished)
    simple["converted_clicks"] = _converted_total(frame, finished)
    outside = _course_rows(frame).where(F.col("_why").isNotNull() & finished(F.col("at"))).agg(
        *[F.coalesce(F.sum(metric), F.lit(0)).alias(metric) for metric in COURSE_METRICS]).first().asDict()
    return {metric: simple[metric] - outside[metric] for metric in COURSE_METRICS}


def course_check(sums):
    """칸의 과정별 지표 합 → 유형마다 세 과정 합."""
    return {metric: sum(sums[f"{course}_{metric}"] for course in COURSES) for metric in COURSE_METRICS}


def course_shape(cells):
    """끝난 칸(키·과정별 지표 9개 합·confirmed) → 표의 13열(기획 63 §6.1): 과정마다 클릭 수·클릭률·전환율(분모 0 이면 null — try_divide).
    분모(노출·전환 이어진 클릭)는 쓰지 않는다(U33)."""
    columns = []
    for course in COURSES:
        clicks = F.col(f"{course}_clicks")
        columns += [clicks, F.try_divide(clicks, F.col(f"{course}_impressions")).alias(f"{course}_ctr"),
                    F.try_divide(F.col(f"{course}_converted_clicks"), clicks).alias(f"{course}_cvr")]
    return cells.select(framework.KST_DAILY, "ad_group_id", "category", *columns, "confirmed")


def performance_build(frame):
    reactions = _reactions(frame).select("*", F.lit(0).cast("bigint").alias("spent"), F.lit(True).alias("_valid"))
    amount, ok = contracts.field(F.col("_v"), "$.charged_amount", "bigint")
    charges = _parsed(frame, CHARGE).select(
        F.col("occurred_at").alias("at"), F.col("aggregate_id").alias("decision_id"), *[F.lit(0).cast("bigint").alias(m) for m in TYPES.values()],
        F.lit(0).cast("bigint").alias("converted_clicks"), F.coalesce(amount, F.lit(0)).cast("bigint").alias("spent"), ok.alias("_valid"))
    rows = _counted(reactions.unionByName(charges), _decisions(frame))
    events = rows.select("at", *DIMENSIONS, *TYPES.values(), "converted_clicks", "spent", "_valid")
    return framework.Built(events, _doubt(rows), _counts(rows))


def performance_totals(frame, finished):
    totals = _type_totals(frame, finished)
    totals["converted_clicks"] = _converted_total(frame, finished)
    spent = _parsed(frame, CHARGE).where(finished(F.col("occurred_at"))).agg(F.sum(F.get_json_object("payload", "$.charged_amount").cast("bigint"))).first()[0]
    totals["spent"] = spent or 0
    return totals


COURSE_DAILY = framework.Table("creative_course_daily", 2, framework.KST_DAILY, (("ad_group_id", "string"), ("category", "int")),
                               tuple(f"{course}_{metric}" for course in COURSES for metric in COURSE_METRICS), course_build, course_totals,
                               shape=course_shape, check=course_check, corrections=False)
PERFORMANCE_HOURLY = framework.Table("performance_hourly", 1, framework.HOURLY, tuple((name, "string") for name in DIMENSIONS),
                                     (*TYPES.values(), "converted_clicks", "spent"), performance_build, performance_totals)
TABLES = (COURSE_DAILY, PERFORMANCE_HOURLY)
