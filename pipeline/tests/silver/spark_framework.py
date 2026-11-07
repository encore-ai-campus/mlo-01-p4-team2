"""pipeline/silver/framework.py — Spark 시험: 어댑터 인터페이스(Bronze DF → 정상 DF, 오류 DF)·판정 순서와 어댑터 결과 확인(check_adapted).

합성 어댑터는 인터페이스와 판정 순서 ①~⑤(framework 머리말)를 따르는 가장 작은 예다(메타버스·광고 어댑터는 SL-4·SL-8).
내용 해시는 시험용 sha2 다(실제는 hop_rules 규칙의 UDF).
"""
import json
import unittest
from datetime import date, datetime, timedelta, timezone

from pyspark.sql import SparkSession, functions as F

from pipeline.silver import contracts, framework

T0 = datetime(2026, 11, 4, 5, 55, 37, tzinfo=timezone.utc)
TOPIC = "metaverse.outbox.events"
OUTBOX = "metaverse_lab.public.eventing_eventoutbox"
EIDS = ["6f1c2b9e-3d4a-4b5c-8d6e-7f8091a2b3c4", "0a1b2c3d-4e5f-4a6b-9c7d-8e9f0a1b2c3d", "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", "2b3c4d5e-6f70-4a8b-9cad-1e2f3a4b5c6d"]
NESTED = (("after", "raw_data"),)
SPEC = [  # (열, 경로, 형, NULL 허용)
    ("event_id", "$.after.event_uuid", "string", False), ("event_type", "$.after.event_type", "string", False),
    ("schema_version", "$.after.version", "string", False), ("occurred_at", "$.after.occurred_at", "timestamp", False),
    ("produced_at", "$.after.created_at", "timestamp", False), ("actor_user_uuid", "$.after.user_uuid", "string", True),
    ("payload", "$.after.raw_data", "string", False), ("src_lsn", "$.source.lsn", "bigint", True), ("src_txid", "$.source.txId", "bigint", True),
    ("first_op", "$.op", "string", False),
]


def setUpModule():
    global spark
    spark = SparkSession.builder.getOrCreate()


def value(event_id, *, op="c", raw='{"u":"x"}', created="2026-11-04T05:55:37.628609Z", lsn=123, table="eventing_eventoutbox"):
    return json.dumps({"op": op, "source": {"db": "metaverse_lab", "schema": "public", "table": table, "lsn": lsn, "txId": 9 if lsn else None},
                       "after": {"event_uuid": event_id, "event_type": "post_created", "version": "1.0", "occurred_at": "2026-11-04T05:55:37.622806Z",
                                 "created_at": created, "user_uuid": EIDS[3], "raw_data": raw}}).encode("utf-8")


def bronze(values):
    rows = [(b"k", None if v is None else bytearray(v), [], TOPIC, 0, i, T0, 0, T0, "bronze_ads_outbox@b4b69424d588", date(2026, 11, 4)) for i, v in enumerate(values)]
    return spark.createDataFrame(rows, contracts.BRONZE_SCHEMA)


def adapt(frame, *, tombstone_code="not_insert"):
    """합성 어댑터. 판정 순서 ①~⑤ — 앞에서 걸린 코드가 그 행의 코드다."""
    staged = frame.withColumn("_r", contracts.strict_reasons(F.col("value_binary"), NESTED))
    staged = staged.withColumn("_v", F.when(F.col("value_binary").isNotNull() & F.col("_r.value").isNull(), F.try_parse_json(F.col("value_binary").cast("string"))))
    values, problem = contracts.extract(F.col("_v"), SPEC)
    source = F.concat_ws(".", *[contracts.field(F.col("_v"), f"$.source.{name}", "string")[0] for name in ("db", "schema", "table")])
    staged = staged.select("*", *values, problem.alias("_problem"), source.alias("_source"))
    rules = [  # (조건, 코드, 사유)
        (F.col("value_binary").isNull(), F.lit(tombstone_code), F.lit("value 가 null(tombstone)")),  # ①
        (F.col("_r.value").isNotNull() | F.col("_v").isNull(), F.lit("decode"), F.coalesce(F.col("_r.value"), F.lit("Spark 가 해석하지 못함"))),  # ②
        (F.col("_source") != OUTBOX, F.lit("foreign_source"), F.concat(F.lit("다른 출처: "), F.col("_source"))),  # ③ 출처
        (~F.coalesce(F.col("first_op").isin("c", "r"), F.lit(False)), F.lit("not_insert"), F.concat(F.lit("op "), F.coalesce(F.col("first_op"), F.lit("없음")))),  # ③ op
        (F.col("_r.nested").isNotNull(), F.lit("decode"), F.col("_r.nested")),  # ④
        (F.col("_problem").isNotNull(), F.lit("contract"), F.concat(F.lit("계약: "), F.col("_problem"))),  # ⑤
    ]
    code, reason = F.lit(None).cast("string"), F.lit(None).cast("string")
    for condition, rule_code, rule_reason in reversed(rules):
        code, reason = F.when(condition, rule_code).otherwise(code), F.when(condition, rule_reason).otherwise(reason)
    staged = staged.withColumn("_code", code).withColumn("_why", reason).cache()  # 어댑터 결과를 persist(UDF 가 다시 돌지 않게 — framework.check_adapted)
    extra = {"domain": F.lit("metaverse"), "actor_subject_id": F.lit(None), "actor_person_id": F.lit(None), "session_uuid": F.lit(None),
             "aggregate_id": F.lit(None), "envelope": F.lit(None), "source_meta": F.lit(None), "content_sha256": F.sha2(F.col("payload"), 256)}
    ok = staged.where(F.col("_code").isNull()).select(*[F.col(name) for name, _, _ in contracts.BRONZE_FIELDS],
                                                       *[(extra[name] if name in extra else F.col(name)).cast(kind).alias(name) for name, kind, _ in contracts.ADAPTER_FIELDS])
    errors = contracts.adapter_errors(staged.where(F.col("_code").isNotNull()), F.col("_code"), F.col("_why"), F.col("event_id"))
    return ok, errors


INPUT = [  # (Bronze value, 기대 — None 이면 정상, 아니면 (코드, 사유에 든 말, event_id))
    (value(EIDS[0]), None),
    (value(EIDS[1], op="r", lsn=None), None),
    (value(EIDS[2]) + b" x", ("decode", "JSON 문서가 아니다", None)),  # ② 뒤따르는 내용
    (value(EIDS[2], created="2026-11-04T05:55:37.6228061Z"), ("contract", "계약: produced_at", EIDS[2])),  # ⑤ 소수 7자리 시각은 잘리지 않고 오류로
    (None, ("not_insert", "tombstone", None)),  # ① value null
    (value(EIDS[3], raw="not json", table="auth_user"), ("foreign_source", "다른 출처: metaverse_lab.public.auth_user", EIDS[3])),  # ③이 ④보다 먼저
    (value(EIDS[3], raw='{"a":1,"a":2}'), ("decode", "after.raw_data", EIDS[3])),  # ④ 안쪽 JSON 만 틀림
    (value(EIDS[1], op="u"), ("not_insert", "op u", EIDS[1])),  # ③ op
    (value(EIDS[0])[:-1] + b', "deep": ' + b"[" * 999 + b"]" * 999 + b"}", None),  # 1,000단(봉투 객체 + 배열 999) — Spark 가 해석한다
    (value(EIDS[0])[:-1] + b', "deep": ' + b"[" * 1000 + b"]" * 1000 + b"}", ("decode", "Spark 가 해석하지 못함", None)),  # ② 1,001단 — 엄격 검사(Python)는 통과, try_parse_json 은 null(2026-11-04 실측)
]


class Adapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bronze = bronze([v for v, _ in INPUT])
        cls.ok, cls.errors = adapt(cls.bronze)

    def assert_broken(self, ok, errors, needle, bronze_frame=None):
        with self.assertRaises(framework.AdapterContractError) as caught:
            framework.check_adapted(self.bronze if bronze_frame is None else bronze_frame, ok, errors, "metaverse")
        self.assertIn(needle, str(caught.exception))

    def test_good_adapter_passes_and_follows_the_order(self):
        ok, errors = framework.check_adapted(self.bronze, self.ok, self.errors, "metaverse")
        self.assertEqual([(r["offset"], r["event_id"], r["first_op"], r["src_lsn"], r["payload"]) for r in ok.orderBy("offset").collect()],
                         [(0, EIDS[0], "c", 123, '{"u":"x"}'), (1, EIDS[1], "r", None, '{"u":"x"}'), (8, EIDS[0], "c", 123, '{"u":"x"}')])
        found = {r["offset"]: (r["error_code"], r["reason"], r["event_id"]) for r in errors.collect()}
        self.assertEqual(sorted(found), [i for i, (_, expected) in enumerate(INPUT) if expected])
        for offset, (code, needle, event_id) in ((i, e) for i, (_, e) in enumerate(INPUT) if e):
            with self.subTest(offset=offset, code=code):
                self.assertEqual((found[offset][0], found[offset][2]), (code, event_id))
                self.assertIn(needle, found[offset][1])

    def test_tombstone_must_be_not_insert(self):  # 순서 ① — 어댑터가 틀려도 공통 틀이 잡는다(리뷰 R1)
        ok, errors = adapt(self.bronze, tombstone_code="decode")
        self.assert_broken(ok, errors, f"{TOPIC}/0/4: value 가 null 인 행(tombstone)은 not_insert 다")
        tombstone = self.bronze.where(F.col("offset") == 4)
        moved = self.ok.unionByName(tombstone.select(*[F.col(n) for n, _, _ in contracts.BRONZE_FIELDS],
                                                     *[F.lit("x" if n == "domain" else None).cast(k).alias(n) for n, k, _ in contracts.ADAPTER_FIELDS]))
        self.assert_broken(moved, self.errors.where(F.col("offset") != 4), "tombstone)이 정상 결과에 있다")

    def test_shape_is_checked_before_running(self):
        self.assert_broken(self.ok.drop("source_meta"), self.errors, "정상 결과의 열이 다르다")
        self.assert_broken(self.ok.withColumn("src_lsn", F.col("src_lsn").cast("string")), self.errors, "정상 결과의 열이 다르다")
        self.assert_broken(self.ok.select(*reversed(self.ok.columns)), self.errors, "정상 결과의 열이 다르다")
        self.assert_broken(self.ok, self.errors.drop("event_id"), "오류 결과의 열이 다르다")

    def test_ok_row_defects(self):
        cases = [("payload", F.lit(None).cast("string"), "payload 이(가) null"), ("event_id", F.upper("event_id"), "event_id 가 소문자 UUID"),
                 ("event_id", F.concat("event_id", F.lit("\n")), "event_id 가 소문자 UUID"), ("content_sha256", F.upper("content_sha256"), "content_sha256"),
                 ("first_op", F.lit("u"), "정상 결과의 op 는 c·r 뿐"), ("domain", F.lit("ads"), "domain 이 metaverse 가 아니다")]
        for name, column, needle in cases:
            with self.subTest(name=name, needle=needle):
                broken = self.ok.withColumn(name, F.when(F.col("offset") == 0, column).otherwise(F.col(name)))
                self.assert_broken(broken, self.errors, f"{TOPIC}/0/0: {needle}")  # 결함 행의 위치와 사유

    def test_error_row_defects(self):
        cases = [("error_code", F.lit("conflict"), "낼 수 없는 오류 코드"), ("error_code", F.lit(None).cast("string"), "낼 수 없는 오류 코드"),
                 ("reason", F.lit(" "), "사유가 비었다"), ("reason", F.lit(None).cast("string"), "사유가 비었다"),
                 ("event_id", F.lit("X"), "오류의 event_id")]
        for name, column, needle in cases:
            with self.subTest(name=name, needle=needle):
                self.assert_broken(self.ok, self.errors.withColumn(name, column), needle)

    def test_rows_are_neither_lost_nor_doubled(self):
        self.assert_broken(self.ok.where(F.col("offset") != 1), self.errors, f"{TOPIC}/0/1: Bronze 행이 결과에 없거나")
        self.assert_broken(self.ok.unionByName(self.ok.where(F.col("offset") == 0)), self.errors, "두 번 이상")
        self.assert_broken(self.ok, self.errors.unionByName(contracts.adapter_errors(self.bronze.where(F.col("offset") == 0), F.lit("decode"), F.lit("x"), F.lit(None))), "두 번 이상")
        self.assert_broken(self.ok.unionByName(self.ok.where(F.col("offset") == 0).withColumn("offset", F.lit(99).cast("bigint"))), self.errors,
                           "결과에 Bronze 에 없는 행이 있다")

    def test_bronze_columns_must_come_through_unchanged(self):  # 리뷰 S1 — 위치만이 아니라 Bronze 열 지문까지 맞춘다
        cases = [("value_binary", F.lit(b"{}")), ("ingested_at", F.col("ingested_at") + F.expr("INTERVAL 1 HOUR")), ("key_binary", F.lit(None).cast("binary")),
                 ("headers", F.array(F.struct(F.lit("h").alias("key"), F.lit(b"v").alias("value"))))]
        for name, column in cases:
            with self.subTest(name=name):
                broken = self.ok.withColumn(name, F.when(F.col("offset") == 0, column).otherwise(F.col(name)))
                self.assert_broken(broken, self.errors, f"{TOPIC}/0/0: Bronze 행이 결과에 없거나 Bronze 열이 바뀌었다")
        broken = self.errors.withColumn("value_binary", F.when(F.col("offset") == 2, F.lit(b"{}")).otherwise(F.col("value_binary")))
        self.assert_broken(self.ok, broken, f"{TOPIC}/0/2: Bronze 행이 결과에 없거나 Bronze 열이 바뀌었다")

    def test_duplicate_bronze_positions_are_reported_as_bronze_not_adapter(self):  # 리뷰 S2 — 설계 01 S3·감시 M7
        doubled = self.bronze.unionByName(self.bronze.where(F.col("offset") == 0))
        ok, errors = adapt(doubled)
        self.assert_broken(ok, errors, f"{TOPIC}/0/0: Bronze 에 같은 위치가 둘 이상 있다 — 어댑터 결함이 아니다", bronze_frame=doubled)


if __name__ == "__main__":
    unittest.main()
