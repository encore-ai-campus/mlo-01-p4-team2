"""pipeline/silver/adapters/ads.py — Spark 시험: 광고 어댑터(판정 순서 ①~⑤·꺼낸 값·종류별 열·내용 해시), 공통 틀 확인(check_adapted),
대사(SL-6 reconcile — 광고 SOURCES 줄)와 끝에서 끝(SL-8 계획 3판 §4.3).

입력은 실제 Bronze 행(rows.REAL — e2e·lab 덤프, 대상 그룹 전 8쌍)과 빌더 합성 행(rows.MADE — 결정 F (가)), 그리고 그것을 바꾼 것이다.
"""
import json
import unittest
from datetime import date, datetime, timezone

from pyspark.sql import SparkSession, functions as F

from pipeline.silver import contracts, control, framework, reconcile, silver_job
from pipeline.silver.adapters import ads, ads_rules as rules
from ads import rows  # 시험 실행기(run_spark_tests)는 pipeline/tests 를 뿌리로 모듈을 찾는다
from silver import spark_job as job

T0 = datetime(2026, 11, 9, 5, 0, 0, tzinfo=timezone.utc)
TOPIC = "ads.e2e.outbox.events"
E2E = rows.E2E


def setUpModule():
    global spark
    spark = SparkSession.builder.getOrCreate()
    job.spark = spark  # 빌려 쓰는 Lake 의 전역(그 모듈의 setUpModule 이 아직 돌지 않았을 수 있다)


def dumped(doc):
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


def changed(name="DECISION", *, payload=None, **kwargs):
    """실제 행(key, value)을 바꾼 것. payload 는 페이로드 객체를 고치는 함수(결과를 JSON 문자열로 넣음)."""
    doc = rows.bronze_doc(name, **kwargs)
    if payload is not None:
        doc["after"]["payload"] = json.dumps(payload(json.loads(doc["after"]["payload"])), ensure_ascii=False)
    return rows.REAL[name]["key"], dumped(doc)


def real(name):
    return rows.REAL[name]["key"], rows.REAL[name]["value"]


def bronze(pairs):
    data = [(None if k is None else bytearray(k.encode()), None if v is None else bytearray(v if isinstance(v, bytes) else v.encode()), [], TOPIC, 0, i,
             T0, 0, T0, "bronze_ads_outbox@0123456789ab", date(2026, 11, 9)) for i, (k, v) in enumerate(pairs)]
    return spark.createDataFrame(data, contracts.BRONZE_SCHEMA)


DECISION_ID = json.loads(rows.REAL["DECISION"]["value"])["after"]["event_id"]
OLD_KEY = rows.REAL["LAB_OLD_KEY"]["key"]
UPPER_KEY = rows.REAL["DECISION"]["key"].replace(DECISION_ID, DECISION_ID.upper())
E2E_REAL = [name for name in rows.REAL if not name.startswith("LAB_")]
INPUT = [(real(name), None) for name in E2E_REAL]  # 0~10 실제 행(e2e 8쌍 — 판 2 결정, 반응 둘의 subject_id null·str, 광고그룹 CREATED)
INPUT += [
    (changed("LAB_BULK", source={"db": E2E["db"]}), None),  # 11 결정 subject_id null(일괄 슬롯, lab 행 — 출처만 e2e 로)
    (changed(op="r", source={"lsn": None, "txId": None}), None),  # 12 재발행 사본(op r)
    (changed("REACTION", op="r", source={"lsn": None, "txId": None}), None),  # 13
]
MADE_START = len(INPUT)  # 14~22 합성 행(결정 3판 다섯, 대상 그룹 판 둘, 광고그룹 대상 둘)
INPUT += [(rows.made(kind, payload), None) for kind, payload in rows.MADE.values()]
INPUT += [
    ((rows.REAL["DECISION"]["key"], None), ("not_insert", "tombstone", DECISION_ID)),  # 23 ①
    ((OLD_KEY, None), ("not_insert", "tombstone", None)),  # 24 E-R1 C 전 키 → 모름
    ((UPPER_KEY, None), ("not_insert", "tombstone", DECISION_ID)),  # 25 대문자 키 → 소문자(리뷰 조건 C3)
    (("k", '{"op":"c"'), ("decode", "JSON 문서가 아니다", None)),  # 26 ②
    (("k", rows.REAL["DECISION"]["value"].replace('"op":"c"', '"op":"c","op":"c"')), ("decode", "JSON 키가 겹친다", None)),  # 27 ②
    (real("LAB_OLD_KEY"), ("foreign_source", "다른 출처: ads_pipeline_lab.public.events_outbox", json.loads(rows.REAL["LAB_OLD_KEY"]["value"])["after"]["event_id"])),  # 28 ③
    (changed(op="u"), ("not_insert", "op u", DECISION_ID)),  # 29 ③ op
    (changed(op="d", after=None), ("not_insert", "op d", None)),  # 30
    (changed(after=None), ("not_insert", "after 가 없다", None)),  # 31 op c + after null
    ((rows.REAL["DECISION"]["key"], dumped(rows.bronze_doc(payload='{"a": 1, "a": 2}'))), ("decode", "after.payload: JSON 키가 겹친다", DECISION_ID)),  # 32 ④
    ((rows.REAL["DECISION"]["key"], dumped(rows.bronze_doc(payload="not json"))), ("decode", "after.payload", DECISION_ID)),  # 33 ④
    (changed(schema_version=1), ("unknown_schema", '["ad.decision","1"]', DECISION_ID)),  # 34 ⑤-1
    (changed(schema_version=4), ("unknown_schema", '["ad.decision","4"]', DECISION_ID)),  # 35
    (changed(event_name="ad.unknown"), ("unknown_schema", '["ad.unknown","2"]', DECISION_ID)),  # 36
    (changed(schema_version=4, payload=lambda d: {"x": 1}), ("unknown_schema", '["ad.decision","4"]', DECISION_ID)),  # 37 새 판은 꺼내기 실패보다 먼저
    (changed(payload=lambda d: {k: v for k, v in d.items() if k != "decided_at"}), ("contract", "꺼낼 수 없는 열: occurred_at", DECISION_ID)),  # 38 ⑤-2
    (changed(payload=lambda d: {**d, "decided_at": "2026-11-07T02:51:59.336703Z"}), ("contract", "꺼낼 수 없는 열: occurred_at", DECISION_ID)),  # 39 Z
    (changed(payload=lambda d: {**d, "decided_at": "2026-11-07T11:51:59.336703+09:00"}), ("contract", "꺼낼 수 없는 열: occurred_at", DECISION_ID)),  # 40
    (changed(payload=lambda d: {**d, "decided_at": "2026-11-07T02:51:59.3367031+00:00"}), ("contract", "꺼낼 수 없는 열: occurred_at", DECISION_ID)),  # 41 7자리
    (changed(schema_version="2"), ("contract", "꺼낼 수 없는 열: schema_version", DECISION_ID)),  # 42
    (changed(payload=lambda d: {**d, "subject_id": 7}), ("contract", "꺼낼 수 없는 열: actor_subject_id", DECISION_ID)),  # 43
    (changed("REACTION", payload=lambda d: {**d, "person_id": ["x"]}), ("contract", "꺼낼 수 없는 열: actor_person_id",
                                                                     json.loads(rows.REAL["REACTION"]["value"])["after"]["event_id"])),  # 44
    (changed(created_at=None), ("contract", "꺼낼 수 없는 열: produced_at", DECISION_ID)),  # 45
    (changed(event_id=DECISION_ID.upper()), ("contract", "event_id 가 소문자 UUID 가 아니다", None)),  # 46 ⑤-3
    (changed(payload=lambda d: {**d, "note": 1}), ("contract", '계약: payload 필드가 계약과 다르다(빠짐 [], 더 있음 ["note"])', DECISION_ID)),  # 47 ⑤-4
    (changed(aggregate_id="01a1b3eb-85f5-704b-9de3-9f5f4098b4ec"), ("contract", "계약: aggregate_id 가 payload.decision_id 와 다르다", DECISION_ID)),  # 48
    (changed(schema_version=3), ("contract", "계약: payload 필드가 계약과 다르다(빠짐 [", DECISION_ID)),  # 49 2판 모양에 판 3
]
D3_ON_ID = json.loads(rows.made(*rows.MADE["D3_ON"])[1])["after"]["event_id"]
SEG_ID = json.loads(rows.made(*rows.MADE["SEG_FIRST"])[1])["after"]["event_id"]
INPUT += [
    (rows.made(*rows.MADE["D3_ON"], schema_version=2), ("contract", "계약: payload 필드가 계약과 다르다(빠짐 [], 더 있음 [", D3_ON_ID)),  # 50 3판 모양에 판 2
    (rows.made(*rows.MADE["SEG_FIRST"], schema_version=2), ("unknown_schema", '["ad.segment_changed","2"]', SEG_ID)),  # 51
    (rows.made(*rows.MADE["SEG_FIRST"], aggregate_id=rows.MADE["SEG_FIRST"][1]["segment_version_id"]),
     ("contract", "계약: aggregate_id 가 payload.segment_id 와 다르다", SEG_ID)),  # 52 대상 키 = segment_id(api.py)
    (rows.made(*rows.MADE["D3_ON"], payload_text=json.dumps({**rows.MADE["D3_ON"][1], "segment_active": "yes"})), None),  # 53 값의 형은 보지 않음(§7 5)
]
SAME_ID_OTHER_CONTENT = 53  # D3_ON 과 event_id 가 같고 내용이 다르다 — 끝에서 끝 입력에서는 뺀다(충돌이 됨)


def python_result(key, value):
    return rules.adapter_result(value if isinstance(value, str) or value is None else value.decode(), key)


def utc_naive(text):
    return datetime.fromisoformat(text).astimezone(timezone.utc).replace(tzinfo=None)  # 실행기 TZ=UTC — collect 의 naive 값과 맞춘다


class Adapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bronze = bronze([pair for pair, _ in INPUT])
        cls.ok, cls.errors = framework.check_adapted(cls.bronze, *ads.adapt(cls.bronze, E2E), ads.DOMAIN)
        cls.rows = {r["offset"]: r for r in cls.ok.collect()}

    def test_order_and_codes(self):
        found = {r["offset"]: (r["error_code"], r["reason"], r["event_id"]) for r in self.errors.collect()}
        self.assertEqual(sorted(found), [i for i, (_, expected) in enumerate(INPUT) if expected])
        self.assertEqual(sorted(self.rows), [i for i, (_, expected) in enumerate(INPUT) if not expected])
        for offset, (code, needle, event_id) in ((i, e) for i, (_, e) in enumerate(INPUT) if e):
            with self.subTest(offset=offset, code=code):
                self.assertEqual((found[offset][0], found[offset][2]), (code, event_id))
                self.assertIn(needle, found[offset][1])

    def test_every_pair_has_a_row(self):  # 10쌍마다 정상 행 — 실제 8쌍 + 합성 2쌍(계획 결정 F)
        pairs = {(r["event_type"], int(r["schema_version"])) for r in self.rows.values()}
        self.assertEqual(pairs, set(rules.CONTRACTS))
        real_pairs = {(r["event_type"], int(r["schema_version"])) for i, r in self.rows.items() if i < MADE_START}
        self.assertEqual(real_pairs, set(rules.CONTRACTS) - {("ad.decision", 3), ("ad.segment_changed", 1)})

    def test_values_come_out_unchanged(self):
        row, value = self.rows[0], json.loads(rows.REAL["DECISION"]["value"])
        a = value["after"]
        self.assertEqual({name: row[name] for name, _, _ in contracts.ADAPTER_FIELDS}, {
            "event_id": DECISION_ID, "domain": "ads", "event_type": "ad.decision", "schema_version": "2",
            "occurred_at": datetime(2026, 11, 7, 2, 51, 59, 336703), "produced_at": datetime(2026, 11, 7, 2, 51, 59, 336703),
            "actor_user_uuid": None, "actor_subject_id": "63e4f876-3eda-456f-974b-e4203c414b1e", "actor_person_id": None, "session_uuid": None,
            "aggregate_id": a["aggregate_id"], "payload": a["payload"], "envelope": None, "source_meta": None,
            "src_lsn": value["source"]["lsn"], "src_txid": value["source"]["txId"], "first_op": "c",
            "content_sha256": python_result(*real("DECISION"))[1]})

    def test_kind_columns(self):  # 종류별 열 — occurred_at 은 (종류, 판)의 필드, actor 는 그 종류에서만
        for offset, row in self.rows.items():
            payload = json.loads(row["payload"])
            line = rules.CONTRACTS[(row["event_type"], int(row["schema_version"]))]
            with self.subTest(offset=offset, kind=row["event_type"]):
                self.assertEqual(row["occurred_at"], utc_naive(payload[line[2]]))
                self.assertEqual(row["actor_subject_id"], payload.get("subject_id") if row["event_type"] in ads.SUBJECT_KINDS else None)
                self.assertEqual(row["actor_person_id"], payload.get("person_id") if row["event_type"] in ads.PERSON_KINDS else None)
                self.assertEqual(row["aggregate_id"], payload[line[1]])
        reaction = json.loads(self.rows[3]["payload"])  # 결정 A — 반응은 received_at(매체 occurred_at 과 다른 행)
        self.assertNotEqual(reaction["occurred_at"], reaction["received_at"])
        self.assertEqual(self.rows[3]["occurred_at"], utc_naive(reaction["received_at"]))
        self.assertEqual(self.rows[3]["actor_person_id"], reaction["person_id"])
        self.assertIsNone(self.rows[11]["actor_subject_id"])  # 일괄 슬롯 결정
        made = {name: self.rows[MADE_START + i] for i, name in enumerate(rows.MADE)}
        self.assertEqual(made["SEG_FIRST"]["occurred_at"], datetime(2026, 11, 9, 1, 2, 4))  # 소수 0자리 +00:00
        self.assertEqual(made["SEG_FIRST"]["aggregate_id"], rows.SEG_A)
        self.assertIsNone(made["SEG_FIRST"]["actor_subject_id"])
        self.assertIsNone(made["D3_NO_SUBJECT"]["actor_subject_id"])
        self.assertEqual([made[n]["schema_version"] for n in ("D3_ON", "D3_NO_GROUP", "D3_NO_AD")], ["3"] * 3)

    def test_content_hash(self):
        for offset in self.rows:  # UDF 경로(Spark 가 바꾼 문자열) = 순수 함수 경로
            with self.subTest(offset=offset):
                self.assertEqual(self.rows[offset]["content_sha256"], python_result(*INPUT[offset][0])[1])
        self.assertEqual(self.rows[12]["content_sha256"], self.rows[0]["content_sha256"])  # 재발행 사본 → 같은 해시(copies)
        self.assertEqual((self.rows[12]["first_op"], self.rows[12]["src_lsn"], self.rows[12]["src_txid"]), ("r", None, None))

    def test_settings(self):
        for settings in ({"db": "ads_pipeline_e2e", "schema": "public"}, None, {**E2E, "table": ""}, {**E2E, "extra": "x"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                ads.adapt(self.bronze, settings)
        ok, errors = ads.adapt(self.bronze.where(F.col("offset") == 0), rows.LAB)
        self.assertEqual((ok.count(), [r["error_code"] for r in errors.collect()]), (0, ["foreign_source"]))


class SameExtractionAsReconcile(unittest.TestCase):  # 대사(SL-6)가 꺼내는 id·창 = 어댑터의 event_id·produced_at 시(계획 §0a)
    def test_paths_and_values(self):
        spec = {name: (path, kind) for name, path, kind, _ in ads.ENVELOPE_SPEC}
        source = reconcile.SOURCES["ads"]
        self.assertEqual((spec["event_id"], spec["produced_at"]), ((f"$.after.{source['id']}", "string"), (f"$.after.{source['time']}", "timestamp")))
        frame = bronze([pair for pair, _ in INPUT])
        ok, _ = ads.adapt(frame, E2E)
        got = reconcile.place(frame, source).join(ok.select("offset", "event_id", F.date_trunc("HOUR", "produced_at").alias("w")), "offset")
        self.assertEqual(got.count(), ok.count())
        self.assertEqual(got.where(F.col("id").isNull() | (F.col("id") != F.col("event_id")) | (F.col("window") != F.col("w"))).count(), 0)


class Lake(job.Lake):
    """spark_job.Lake 를 광고 원천으로(--domain ads, 출처 e2e)."""

    def __init__(self, test):
        super().__init__(test)
        folder = str(self.root)
        env = {"SILVER_LAKE_ROOT": folder, "SILVER_BRONZE_TABLE": f"{folder}/bronze/ads_outbox_events", "SILVER_SOURCE_DB": E2E["db"],
               "SILVER_SOURCE_SCHEMA": E2E["schema"], "SILVER_SOURCE_TABLE": E2E["table"], "SILVER_ACKNOWLEDGED": f"{folder}/ack.json",
               "SILVER_QUARANTINE": f"{folder}/quarantine.json", "SILVER_CODE_ROOT": folder, "SILVER_MAX_FILES_PER_TRIGGER": "100", "SILVER_MAX_BYTES_PER_TRIGGER": "1g"}
        self.settings = control.job_settings(env, "ads", 1)

    def run(self):
        return silver_job.run(spark, self.settings, ads.adapt, [], job.RELEASE, log=self.logs.append)

    def add_pairs(self, pairs):
        data = [(None if k is None else bytearray(k.encode()), None if v is None else bytearray(v.encode()), [], TOPIC, 0, self.offset + i, T0, 0, T0,
                 "bronze@x", T0.date()) for i, (k, v) in enumerate(pairs)]
        self.offset += len(pairs)
        spark.createDataFrame(data, contracts.BRONZE_SCHEMA).coalesce(1).write.format("delta").mode("append").save(self.settings.bronze)


class EndToEnd(unittest.TestCase):  # 실제 잡 경로(silver_job.run) → 대사 match — 광고 SOURCES 줄
    def test_match(self):
        lake = Lake(self)
        good = [pair for i, (pair, expected) in enumerate(INPUT) if expected is None and i != SAME_ID_OTHER_CONTENT]  # 실제 행·사본·합성 행
        lake.add_pairs(good + [(rows.REAL["DECISION"]["key"], '{"op":"c"')])  # 오류 행 하나(decode — 멈춤 아님)
        lake.run()
        silver = lake.silver()
        self.assertEqual(len(silver), len({json.loads(v)["after"]["event_id"] for _, v in good}))
        self.assertEqual(silver[DECISION_ID]["copies"], 2)  # 원본 + op r 사본
        self.assertEqual([e[1] for e in lake.errors()], ["decode"])
        result = reconcile.reconcile(spark, lake.settings, release=job.RELEASE, quarantine=[], log=lake.logs.append)
        self.assertTrue(result.ok, lake.logs)
        statuses = {r["status"] for r in spark.read.format("delta").load(lake.settings.reconcile).collect()}
        self.assertEqual(statuses, {"match"})


if __name__ == "__main__":
    unittest.main()
