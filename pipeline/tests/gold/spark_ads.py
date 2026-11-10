"""pipeline/gold/tables/ads.py 광고 소비자 표 둘(계획 temp/pipeline/41_ads-gold-plan.md r3e §3.1·§3.2·§7 — 코드 Step ②) — Spark 시험.

입력은 실제 잡 경로다: 광고 서버 빌더(`ads_server/events/schemas.py` — Django 없이 파일로 부름)의 페이로드를 실제 Bronze 봉투에 끼운 행(tests/ads/rows.made)
→ 실제 광고 어댑터·Silver 잡(silver_job.run) → 대사(reconcile) → 포인터(set_generation) → Gold(framework.run + tables/ads.TABLES).
SL-8 실제 Bronze 행(rows.REAL)도 같은 길로 흘린다(RealRows).
"""
import json
import unittest
from datetime import date, datetime, timedelta, timezone

from pyspark.sql import SparkSession, functions as F

from pipeline.gold import control, framework
from pipeline.gold.tables import ads as tables
from pipeline.silver import pointers, reconcile
from ads import rows, spark_adapter as sa  # 시험 실행기(run_spark_tests)는 pipeline/tests 를 뿌리로 모듈을 찾는다
from silver import spark_job as job

UTC = timezone.utc
DAY = datetime(2026, 11, 8, tzinfo=UTC)  # 지난 날 — 대사 회차 시각(지금)이 사건보다 뒤여야 기준 시각이 사건에서 정해진다
S = rows.schemas
M, SLOT, SLOT2 = rows.U[6], rows.U[7], rows.U[8]
SEG_A, SEG_B, SEG_C = rows.U[10], rows.U[11], rows.U[12]  # mlo · aio · Silver 에 판이 없는 그룹
K1, G1, C1, K2, G2, C2 = (f"01a1c0e{n}-0000-7000-8000-000000000001" for n in range(6))
NOW = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)
RELEASE = "gold@ads00000000"


def setUpModule():
    global spark
    spark = SparkSession.builder.getOrCreate()
    sa.spark = job.spark = spark  # 빌려 쓰는 Lake 의 전역


def at(text, day=DAY):
    hour, minute = map(int, text.split(":"))
    return day + timedelta(hours=hour, minutes=minute)


def uid(group, n):
    return f"01a1c0f{group:x}-0000-7000-8000-{n:012d}"


def made(event_name, payload, created):
    return rows.made(event_name, payload, created_at=created.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))


def decision(n, campaign, ad_group, creative, *, slot=SLOT, segment=None, active=True, v2=False):
    picked = {**rows.selected(None if v2 else ([segment] if segment else [])), "campaign_id": campaign, "ad_group_id": ad_group, "creative_id": creative}
    payload = S.decision_payload(decision_id=uid(1, n), media_id=M, slot_id=slot, decided_at=at("10:00"), audience="individual", subject_id="s",
                                 selected=picked, selection_policy="weighted-by-bid/v1", candidate_count=1, rejected_counts={},
                                 **({} if v2 else {"segment_rule": "segment/v1", "segment_id": segment, "segment_active": active if segment else None}))
    return made("ad.decision", payload, at("10:00"))


def reaction(n, d, kind, received, person, status="ACCEPTED"):
    payload = S.reaction_payload(inbox_id=uid(2, n), reaction_id=uid(3, n), media_id=M, decision_id=uid(1, d), type=kind, occurred_at=at(received),
                                 received_at=at(received), subject_id="s", source="SERVER", status=status, reason_code=None if status == "ACCEPTED" else "R",
                                 applied_rule="funnel/v1", basis={"person": person, "audience": "individual"})
    return made("ad.reaction", payload, at(received) + timedelta(minutes=1))


def charge(n, d, created, amount, campaign=K1, requested=None):
    requested = amount if requested is None else requested  # 잔액이 모자라면 요청보다 덜 받는다 — spent 는 받은 포인트
    payload = S.charge_payload(entry_id=uid(4, n), campaign_id=campaign, charge_policy="share/v1", created_at=at(created), reaction_ref=f"{M}:{uid(3, n)}",
                               decision_id=uid(1, d), requested_amount=requested, charged_amount=amount, shortfall=requested - amount, budget_day=date(2026, 11, 8))
    return made("ad.charge", payload, at(created) + timedelta(minutes=1))


def segment(n, seg, code, version=1, active=True):
    payload = S.segment_changed_payload(segment_id=seg, segment_version_id=uid(5, n), version=version, media_id=M, code=code, name=code.upper(),
                                        is_active=active, changed_at=at("09:00"))
    return made("ad.segment_changed", payload, at("09:00"))


def clock(created, n=99):
    """기준 시각을 정하는 사건(접수 — Gold 가 세지 않음)."""
    payload = S.reaction_received_payload(inbox_id=uid(6, n), reaction_id=uid(7, n), media_id=M, decision_id=uid(1, 1), type="IMPRESSION",
                                          occurred_at=created, received_at=created, subject_id="s", source="MEDIA", raw={})
    return made("ad.reaction_received", payload, created)


SEGMENTS = [segment(1, SEG_A, "mlo"), segment(2, SEG_A, "mlo", version=2, active=False), segment(3, SEG_B, "aio")]
DECISIONS = [decision(1, K1, G1, C1, segment=SEG_A), decision(2, K2, G2, C2, slot=SLOT2, segment=SEG_B), decision(3, K1, G1, C1, segment=SEG_A, active=False),
             decision(4, K1, G1, C1), decision(5, K1, G1, C1, v2=True), decision(6, K1, G1, C1, segment=SEG_C)]  # 결정 7 은 Silver 에 없다
P = [f"person-{i}" for i in range(10)]
REACTIONS = {  # 이름 → (결정, 유형, 수신, 사람, 상태)
    "r1": (1, "IMPRESSION", "10:10", P[1], "ACCEPTED"), "r2": (1, "CLICK", "10:20", P[1], "ACCEPTED"), "r3": (1, "CONVERSION", "13:30", P[1], "ACCEPTED"),
    "r4": (1, "CLICK", "10:25", P[9], "REJECTED"), "r5": (1, "CLICK", "10:40", P[2], "ACCEPTED"), "r6": (2, "IMPRESSION", "11:05", P[3], "ACCEPTED"),
    "r7": (3, "IMPRESSION", "11:15", P[4], "ACCEPTED"), "r8": (4, "IMPRESSION", "11:20", P[5], "ACCEPTED"), "r9": (5, "IMPRESSION", "11:25", P[6], "ACCEPTED"),
    "r10": (6, "IMPRESSION", "09:05", P[7], "ACCEPTED"), "r11": (7, "IMPRESSION", "12:10", P[8], "ACCEPTED"),  # r10 은 "차원 없음"과 다른 시간
}
CHARGES = {"c1": (1, "10:10", 98, K1, 100), "c2": (1, "10:20", 1, K1), "c3": (7, "12:10", 5, K1), "c4": (2, "11:05", 7, K2)}  # c1 은 요청 100 · 받음 98


def reaction_pairs(names=None):
    return [reaction(i, *REACTIONS[name]) for i, name in enumerate(REACTIONS, 1) if names is None or name in names]


def charge_pairs():
    return [charge(i, *spec) for i, spec in enumerate(CHARGES.values(), 1)]


# (GOLD-2 리뷰 권고 1) 끝나지 않은 시간(15시 — 기준 시각 16:00 < 16:05:30)의 인정 클릭·이어진 전환·과금. KST 11-09 이라 하루 표에서도 끝나지 않았다.
# 두 표 어디에도 칸이 없고 확인이 맞아야 한다 — 확인 쪽 세 식(_type_totals·converted_clicks·spent)이 끝난 칸만 세는지를 본다
UNFINISHED = [reaction(21, 2, "CLICK", "15:30", P[3]), reaction(22, 2, "CONVERSION", "15:40", P[3]), charge(21, 2, "15:30", 4, K2)]


class Lake(sa.Lake):
    def __init__(self, test):
        super().__init__(test)
        self.r = str(self.root)

    def cycle(self, pairs):
        """Bronze 에 더하고 Silver 한 회차(잡 + 대사). 첫 회차 뒤에 포인터를 둔다."""
        self.add_pairs(pairs)
        self.run()
        result = reconcile.reconcile(spark, self.settings, release=job.RELEASE, quarantine=[], log=self.logs.append)
        assert result.ok, self.logs
        if framework.silver_framework.table_state(spark, control.silver_control.pointer_path(self.r, "ads"))[0] < 0:
            pointers.set_generation(spark, self.r, "ads", 1, by="시험", reason="Gold ②", log=lambda line: None)

    def gold(self, now=NOW):
        self.logs.clear()
        return framework.run(spark, self.r, "ads", tables.TABLES, RELEASE, delay_seconds=tables.DELAY_SECONDS, log=self.logs.append, now=now)

    def cells(self, table):
        frame = spark.read.format("delta").load(control.table_path(self.r, "ads", table.name, table.version))
        return sorted((tuple(r) for r in frame.collect()), key=repr)

    def record(self, table):
        path = control.table_path(self.r, "ads", table.name, table.version)
        return json.loads(spark.sql(f"DESCRIBE HISTORY delta.`{path}` LIMIT 1").first()["userMetadata"])

    def mismatch_window(self, start):
        """마지막 대사 결과에 창 줄 하나를 mismatch 로 더한 커밋(요약 키·checkpoint 그대로 — 장애 흉내)."""
        path = self.settings.reconcile
        meta = json.loads(spark.sql(f"DESCRIBE HISTORY delta.`{path}` LIMIT 1").first()["userMetadata"])
        current = spark.read.format("delta").load(path)
        stamp = current.select("reconciled_at").first()[0]
        extra = spark.createDataFrame([tuple({"kind": "window", "window_start": start.replace(tzinfo=None), "window_end": (start + timedelta(hours=1)).replace(tzinfo=None),
                                              "status": "mismatch", "reconciled_at": stamp}.get(f.name) for f in reconcile.RESULT_SCHEMA.fields)], reconcile.RESULT_SCHEMA)
        (current.unionByName(extra).write.format("delta").mode("overwrite").option("overwriteSchema", "true")
         .option("userMetadata", json.dumps({**meta, "status": "mismatch"})).save(path))


def started(test, extra=()):
    lake = Lake(test)
    lake.cycle(SEGMENTS + DECISIONS + reaction_pairs() + charge_pairs() + UNFINISHED + [clock(at("16:00"))] + list(extra))
    return lake


def naive(text, day=DAY):
    return at(text, day).replace(tzinfo=None)


MLO, AIO = "mlo", "aio"
PERF = [  # hour, campaign, ad_group, creative, media, slot, course, 노출, 클릭, 전환, converted_clicks, spent, confirmed
    (naive("09:00"), K1, G1, C1, M, SLOT, None, 1, 0, 0, 0, 0, True),  # r10 코드 모르는 그룹
    (naive("10:00"), K1, G1, C1, M, SLOT, MLO, 1, 2, 0, 1, 99, True),  # r1·r2·r5(r4 거부), r2 는 r3 전환으로 converted, c1(받음 98)+c2
    (naive("11:00"), K1, G1, C1, M, SLOT, MLO, 1, 0, 0, 0, 0, True),  # r7 — 보관된 그룹도 그 코드
    (naive("11:00"), K1, G1, C1, M, SLOT, None, 2, 0, 0, 0, 0, True),  # r8 그룹 없음 · r9 결정 2판
    (naive("11:00"), K2, G2, C2, M, SLOT2, AIO, 1, 0, 0, 0, 7, True),  # r6 · c4
    (naive("12:00"), None, None, None, None, None, None, 1, 0, 0, 0, 5, True),  # r11 · c3 — 결정이 Silver 에 없음(차원 없음)
    (naive("13:00"), K1, G1, C1, M, SLOT, MLO, 0, 0, 1, 0, 0, True),  # r3 전환은 전환이 들어온 칸
]
COURSE = [  # kst_date, creative, course, 노출, 클릭, 전환, confirmed — 모두 KST 11-08(UTC 10~13시 = KST 19~22시)
    (date(2026, 11, 8), C1, MLO, 2, 2, 1, True), (date(2026, 11, 8), C1, None, 3, 0, 0, True), (date(2026, 11, 8), C2, AIO, 1, 0, 0, True),
    (date(2026, 11, 8), None, None, 1, 0, 0, True),
]


class Tables(unittest.TestCase):
    def test_builder_events(self):
        self.assertEqual(tables.DELAY_SECONDS, 330)  # 보류 300초 + 다시 판정 30초 = 5분 30초(계약 문서)
        lake = started(self)
        self.assertTrue(lake.gold().ok, lake.logs)
        later = pointers.read_events(spark, lake.r, "ads", pointers.RECONCILED).frame.where(F.col("occurred_at") >= naive("15:00"))
        self.assertEqual(sorted(r[0] for r in later.select("event_type").collect()), ["ad.charge", "ad.reaction", "ad.reaction", "ad.reaction_received"])  # 빈 시험이 아님
        self.assertEqual(lake.cells(tables.PERFORMANCE_HOURLY), sorted(PERF, key=repr))  # 15시 칸 없음
        self.assertEqual(lake.cells(tables.COURSE_DAILY), sorted(COURSE, key=repr))
        self.assertEqual(lake.record(tables.PERFORMANCE_HOURLY)["counts"], {"no_dimension": 2, "decision_v2": 1, "no_segment": 1, "unknown_code": 1})
        self.assertEqual(lake.record(tables.COURSE_DAILY)["counts"], {"no_dimension": 1, "decision_v2": 1, "no_segment": 1, "unknown_code": 1})
        self.assertEqual(lake.record(tables.PERFORMANCE_HOURLY)["cutoff_at"], "2026-11-08T16:00:00.000000Z")  # clock 의 outbox 시각
        self.assertEqual([c for c in spark.read.format("delta").load(control.table_path(lake.r, "ads", "performance_hourly", 1)).columns],
                         ["hour", *tables.DIMENSIONS, "impressions", "clicks", "conversions", "converted_clicks", "spent", "confirmed"])

    def test_console_rules(self):  # §7 — 콘솔(records/report.py) 규칙을 따로 쓴 계산과 같은 숫자(결정이 있는 사건 — 콘솔은 결정 범위로 센다)
        lake = started(self)
        self.assertTrue(lake.gold().ok, lake.logs)
        accepted = [spec for spec in REACTIONS.values() if spec[4] == "ACCEPTED" and spec[0] != 7]
        conversions = {(d, p) for d, kind, _, p, _ in accepted if kind == "CONVERSION"}
        decision_campaign = {1: K1, 2: K2, 3: K1, 4: K1, 5: K1, 6: K1}
        expected = {}
        for d, kind, _, person, _ in accepted:
            row = expected.setdefault(decision_campaign[d], dict.fromkeys(("impressions", "clicks", "conversions", "converted_clicks", "spent"), 0))
            row[{"IMPRESSION": "impressions", "CLICK": "clicks", "CONVERSION": "conversions"}[kind]] += 1
            row["converted_clicks"] += kind == "CLICK" and (d, person) in conversions
        for d, _, amount, campaign, *_ in CHARGES.values():
            if d != 7:
                expected[campaign]["spent"] += amount
        frame = spark.read.format("delta").load(control.table_path(lake.r, "ads", "performance_hourly", 1)).where(F.col("campaign_id").isNotNull())
        got = {r["campaign_id"]: {k: r[k] for k in expected[K1]} for r in frame.groupBy("campaign_id").agg(
            *[F.sum(m).alias(m) for m in ("impressions", "clicks", "conversions", "converted_clicks", "spent")]).collect()}
        self.assertEqual(got, expected)
        budget_day = frame.groupBy(F.to_date(F.col("hour") + F.expr("INTERVAL 9 HOURS")).alias("d")).agg(F.sum("impressions").alias("i")).collect()
        self.assertEqual([(r["d"], r["i"]) for r in budget_day], [(date(2026, 11, 8), 6)])  # 예산 일 = KST 날짜(오프셋 9) — 계약 문서의 묶기

    def test_conversion_after_the_click_corrects_the_click_hour(self):  # §3.1 — converted_clicks 는 클릭 칸, 전환이 나중에 오면 지난 칸이 바뀌고 정정 줄
        lake = Lake(self)
        lake.cycle(SEGMENTS + DECISIONS + reaction_pairs({"r1", "r2", "r5"}) + [clock(at("16:00"))])
        self.assertTrue(lake.gold().ok, lake.logs)
        self.assertIn((naive("10:00"), K1, G1, C1, M, SLOT, MLO, 1, 2, 0, 0, 0, True), lake.cells(tables.PERFORMANCE_HOURLY))
        lake.cycle([reaction(3, *REACTIONS["r3"]), clock(at("16:30"), n=98)])
        self.assertTrue(lake.gold().ok, lake.logs)
        lines = [r.asDict() for r in spark.read.format("delta").load(control.corrections_path(lake.r, "ads")).where(F.col("table_name") == "performance_hourly")
                 .orderBy("cell_time").collect()]
        self.assertEqual([(r["cell_time"], r["metrics_before"], r["metrics_after"]) for r in lines],
                         [(naive("10:00"), {"impressions": 1, "clicks": 2, "conversions": 0, "converted_clicks": 0, "spent": 0},
                           {"impressions": 1, "clicks": 2, "conversions": 0, "converted_clicks": 1, "spent": 0}),
                          (naive("13:00"), None, {"impressions": 0, "clicks": 0, "conversions": 1, "converted_clicks": 0, "spent": 0})])  # 이미 쓴 범위 안에 생긴 칸
        self.assertEqual({r["reason"] for r in lines}, {"silver_changed"})

    def test_doubt_hours_and_the_day(self):  # r2b C1·r3b C2 + §3.3 — 창 mismatch 회차에 "차원 없음"·코드 모르는 그룹 사건의 시간, 하루 표는 그 날 전체
        lake = started(self)
        lake.mismatch_window(at("14:00"))  # 14시 창 → 14·13시, 의심 시간 09시(r10 코드 모름) · 12시(r11·c3 차원 없음)
        self.assertTrue(lake.gold().ok, lake.logs)
        confirmed = {(r[0], r[6], r[1]): r[-1] for r in lake.cells(tables.PERFORMANCE_HOURLY)}
        self.assertEqual({k[0]: v for k, v in confirmed.items()},
                         {naive("09:00"): False, naive("10:00"): True, naive("11:00"): True, naive("12:00"): False, naive("13:00"): False})
        self.assertEqual({r[-1] for r in lake.cells(tables.COURSE_DAILY)}, {False})
        self.assertEqual(lake.record(tables.PERFORMANCE_HOURLY)["unconfirmed_hours"], 4)

    def test_two_codes_for_one_segment_writes_nothing(self):  # AG7 — 잇기가 부풀면 쓰지 않는다
        lake = started(self, extra=[segment(9, SEG_A, "xyz", version=3)])
        result = lake.gold()
        self.assertEqual(result, framework.Result(False, {}))
        self.assertTrue(any(line.startswith("gold alert check_mismatch table=creative_course_daily metric=impressions") for line in lake.logs), lake.logs)

    def test_charged_amount_must_be_an_integer(self):  # §2.2 — 돈 열이 형·범위 밖이면 쓰지 않는다
        payload = S.charge_payload(entry_id=uid(4, 50), campaign_id=K1, charge_policy="share/v1", created_at=at("10:30"), reaction_ref="x", decision_id=uid(1, 1),
                                   requested_amount=3, charged_amount="3", shortfall=0, budget_day=date(2026, 11, 8))
        lake = started(self, extra=[made("ad.charge", payload, at("10:31"))])
        with self.assertRaisesRegex(framework.GoldError, "performance_hourly: 페이로드에서 꺼낸 수 1 개가 형·범위를 벗어났다"):
            lake.gold()


class RealRows(unittest.TestCase):  # SL-8 실제 Bronze 행(e2e 덤프) — 반응·과금의 결정이 이 행들에 없어 "차원 없음" 칸
    def test_real_adapter_output(self):
        lake = Lake(self)
        real = [pair for pair, expected in sa.INPUT[:sa.MADE_START] if expected is None]
        lake.cycle(real + [clock(datetime(2026, 11, 7, 16, 0, tzinfo=UTC))])  # KST 11-07 끝(UTC 15:00) + 5분 30초 뒤
        self.assertTrue(lake.gold().ok, lake.logs)
        hour = datetime(2026, 11, 7, 2, 0)
        self.assertEqual(lake.cells(tables.PERFORMANCE_HOURLY), [(hour, None, None, None, None, None, None, 1, 1, 0, 0, 294, True)])  # 노출·클릭 인정, 과금 294
        self.assertEqual(lake.cells(tables.COURSE_DAILY), [(date(2026, 11, 7), None, None, 1, 1, 0, True)])
        self.assertEqual(lake.record(tables.PERFORMANCE_HOURLY)["counts"], {"no_dimension": 3, "decision_v2": 0, "no_segment": 0, "unknown_code": 0})


if __name__ == "__main__":
    unittest.main()
