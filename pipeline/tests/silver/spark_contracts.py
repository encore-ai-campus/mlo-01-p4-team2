"""pipeline/silver/contracts.py — Spark 시험(run_spark_tests.py 로 돈다): 표 모양, 엄격 검사 UDF, 값 꺼내기, 오류 행."""
import json
import unittest
from datetime import date, datetime, timezone

from pyspark.sql import SparkSession, functions as F

from pipeline.silver import contracts, strict

INT39 = "123456789012345678901234567890123456789"
NESTED = (("after", "raw_data"), ("after", "payload_json"))


def setUpModule():
    global spark
    spark = SparkSession.builder.getOrCreate()


def columns(schema):
    return [(f.name, f.dataType.simpleString()) for f in schema.fields]


def envelope(**after):
    return json.dumps({"op": "c", "after": after}, ensure_ascii=False).encode("utf-8")


class Schemas(unittest.TestCase):
    def test_common_columns_split_between_adapter_and_framework(self):
        names = [name for name, _, _ in contracts.SILVER_FIELDS]
        adapter = [name for name, _, _ in contracts.ADAPTER_FIELDS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(adapter) | set(contracts.FRAMEWORK_FIELDS), set(names))
        self.assertFalse(set(adapter) & set(contracts.FRAMEWORK_FIELDS))
        self.assertEqual({name for name, _, null in contracts.SILVER_FIELDS if null},
                         {"actor_user_uuid", "actor_subject_id", "actor_person_id", "session_uuid", "aggregate_id", "envelope", "source_meta", "src_lsn", "src_txid"})

    def test_frame_shapes(self):
        bronze = [("key_binary", "binary"), ("value_binary", "binary"), ("headers", "array<struct<key:string,value:binary>>"), ("topic", "string"),
                  ("partition", "int"), ("offset", "bigint"), ("kafka_timestamp", "timestamp"), ("kafka_timestamp_type", "int"),
                  ("ingested_at", "timestamp"), ("pipeline_release", "string"), ("ingest_date", "date")]
        self.assertEqual(columns(contracts.BRONZE_SCHEMA), bronze)
        self.assertEqual(columns(contracts.OK_SCHEMA), bronze + [(n, k) for n, k, _ in contracts.ADAPTER_FIELDS])
        self.assertEqual(columns(contracts.ADAPTER_ERROR_SCHEMA), bronze + [("error_code", "string"), ("reason", "string"), ("event_id", "string")])
        self.assertEqual([n for n, _ in columns(contracts.ERROR_SCHEMA)][len(bronze):],
                         ["error_code", "reason", "event_id", "existing_event_id", "existing_event_date", "acknowledged", "ack_reason", "ack_incident",
                          "silver_release", "detected_at", "batch_id"])
        self.assertEqual(set(contracts.ERROR_CODES), {"decode", "contract", "unknown_schema", "conflict", "not_insert", "foreign_source"})
        self.assertNotIn("conflict", contracts.ADAPTER_ERROR_CODES)


class StrictUdf(unittest.TestCase):
    CASES = [  # (이름, 바이트, 통과하나) — 2026-11-04 실측 경우 + 큰 수 셋 + 안쪽 JSON 문자열
        ("ok", b'{"b":1,"a":{"y":"\xea\xb0\x80","x":2.5},"c":[1,1.0,1e2]}', True),
        ("dup_top", b'{"a":1,"a":2}', False), ("dup_nested", b'{"o":{"b":1,"b":2}}', False),
        ("nan", b'{"a":NaN}', False), ("inf", b'{"a":Infinity}', False), ("ninf", b'{"a":-Infinity}', False), ("overflow", b'{"a":1e999}', False),
        ("nan_text_ok", b'{"a":"NaN"}', True), ("top_array", b"[1]", False), ("top_number", b"1", False), ("top_string", b'"x"', False),
        ("top_null", b"null", False), ("bad_utf8", b'{"a":"\xff"}', False), ("lone_surrogate", b'{"a":"\\ud800"}', False),
        ("broken", b"{", False), ("trailing", b'{"a":1} x', False), ("empty", b"", False), ("big_int30", b'{"a":123456789012345678901234567890}', True),
        ("int39", b'{"a":' + INT39.encode() + b"}", True), ("int1000_jsonb", b'{"a":1' + b"0" * 999 + b"}", True), ("int4301", b'{"a":1' + b"0" * 4300 + b"}", False),
        ("underflow_1e-400", b'{"a":1e-400}', True), ("underflow_jsonb", b'{"a":0.' + b"0" * 399 + b"1}", True),
        ("nested_ok", envelope(raw_data='{"u":"x"}', payload_json='{"raw_data":{}}'), True),
        ("nested_dup", envelope(raw_data='{"u":1,"u":2}'), False), ("nested_nan", envelope(payload_json='{"a":NaN}'), False),
        ("nested_array", envelope(raw_data="[1]"), False), ("nested_trailing", envelope(raw_data='{"a":1} x'), False),
        ("nested_null_and_object", json.dumps({"after": {"raw_data": None, "payload_json": {"a": 1}}}).encode(), True),
    ]

    def test_udf_matches_the_python_check_and_expectations(self):
        frame = spark.createDataFrame([(name, bytearray(value)) for name, value, _ in self.CASES], "name string, v binary")
        got = {row["name"]: (row["r"]["value"], row["r"]["nested"]) for row in frame.select("name", contracts.strict_reasons(F.col("v"), NESTED).alias("r")).collect()}
        for name, value, passes in self.CASES:
            with self.subTest(name=name):
                self.assertEqual(got[name], strict.strict_reasons(value, NESTED))
                self.assertEqual(got[name] == (None, None), passes, got[name])
                self.assertEqual(got[name][1] is not None, name.startswith("nested_") and not passes)  # 안쪽 사유는 봉투가 정상일 때만

    def test_text_column_and_null_input(self):
        frame = spark.createDataFrame([("ok", '{"a":1}'), ("dup", '{"a":1,"a":1}'), ("none", None)], "name string, s string")
        got = {row["name"]: row["r"] for row in frame.select("name", contracts.strict_reasons(F.col("s")).alias("r")).collect()}
        self.assertEqual((got["ok"]["value"], got["ok"]["nested"]), (None, None))
        self.assertIn("겹친다", got["dup"]["value"])
        self.assertIn("바이트·문자열이 아니다", got["none"]["value"])  # tombstone — 어댑터는 그 전에 not_insert 로 보낸다(순서 ①)
        self.assertEqual(columns(frame.select(contracts.strict_reasons(F.col("s")).alias("r")).schema), [("r", "struct<value:string,nested:string>")])


DOC = {
    "s": "abc", "n": 1, "o": {"x": 1}, "nul": None, "raw": '{"b":1,  "a":2}', "sn": "12", "t": True, "nl": "abc\n",
    "ts": "2026-11-04T05:55:37.622806Z", "ts0": "2026-11-04T05:55:37Z", "ts1": "2026-11-04T05:55:37.6Z", "ts7": "2026-11-04T05:55:37.6228061Z",
    "tsoff": "2026-11-04T14:55:37.622806+09:00", "tsnz": "2026-11-04T05:55:37.622806", "tsbad": "2026-02-30T00:00:00Z",
    "tssp": "2026-11-04 05:55:37Z", "tsnl": "2026-11-04T05:55:37Z\n",
    "tsu": "2026-11-04T05:55:37.622806+00:00", "tsu0": "2026-11-04T05:55:37+00:00", "tsu1": "2026-11-04T05:55:37.6+00:00",  # SL-3d +00:00 표기
    "tsu7": "2026-11-04T05:55:37.6228061+00:00", "tsum": "2026-11-04T05:55:37.622806-00:00", "tsubad": "2026-02-30T00:00:00+00:00",
    "tsunl": "2026-11-04T05:55:37+00:00\n", "tsu24": "2026-11-04T24:00:00+00:00", "tsunc": "2026-11-04T05:55:37+0000", "tsusp": "2026-11-04 05:55:37+00:00",
}
NUMBERS = {"big": "9223372036854775807", "under": "-9223372036854775809", "half": "1.5", "one0": "1.0", "e2": "1e2", "i39": INT39, "i1000": "1" + "0" * 999}
TEXT = json.dumps(DOC, ensure_ascii=False)[:-1] + "".join(f',"{k}":{v}' for k, v in NUMBERS.items()) + "}"


class Extract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = spark.createDataFrame([(TEXT,)], "s string").select(F.try_parse_json("s").alias("v"))

    def get(self, path, kind, nullable=False):
        value, ok = contracts.field(F.col("v"), path, kind, nullable)
        row = self.frame.select(value.alias("value"), ok.alias("ok")).first()
        return row["value"], row["ok"]

    def check(self, kind, table):
        for path, nullable, expected in table:
            with self.subTest(kind=kind, path=path, nullable=nullable):
                self.assertEqual(self.get(path, kind, nullable), expected)

    def test_strings_are_taken_only_from_json_strings(self):
        self.check("string", [
            ("$.s", False, ("abc", True)), ("$.raw", False, ('{"b":1,  "a":2}', True)),  # 안쪽 JSON 문자열은 원문 그대로(공백·키 순서)
            ("$.sn", False, ("12", True)), ("$.nl", False, ("abc\n", True)),
            ("$.n", False, (None, False)), ("$.o", False, (None, False)), ("$.t", False, (None, False)), ("$.i39", False, (None, False)),
            ("$.nul", False, (None, False)), ("$.nul", True, (None, True)), ("$.missing", False, (None, False)), ("$.missing", True, (None, False)),
        ])

    def test_integers_never_change(self):
        self.check("bigint", [
            ("$.big", False, (9223372036854775807, True)), ("$.n", False, (1, True)),
            ("$.one0", False, (1, True)),  # Variant 는 1.0 을 DECIMAL(1,0) 으로 읽는다 — 수학적으로 같은 정수(JCS 도 1)
            ("$.under", False, (None, False)), ("$.half", False, (None, False)), ("$.e2", False, (None, False)), ("$.i39", False, (None, False)),
            ("$.i1000", False, (None, False)), ("$.sn", False, (None, False)), ("$.t", False, (None, False)), ("$.o", False, (None, False)),
            ("$.nul", True, (None, True)), ("$.nul", False, (None, False)),
        ])

    def test_timestamps_are_utc_z_with_at_most_microseconds(self):
        self.check("timestamp", [
            ("$.ts", False, (datetime(2026, 11, 4, 5, 55, 37, 622806), True)), ("$.ts0", False, (datetime(2026, 11, 4, 5, 55, 37), True)),
            ("$.ts1", False, (datetime(2026, 11, 4, 5, 55, 37, 600000), True)),
            ("$.ts7", False, (None, False)), ("$.tsoff", False, (None, False)), ("$.tsnz", False, (None, False)), ("$.tsbad", False, (None, False)),
            ("$.tssp", False, (None, False)), ("$.tsnl", False, (None, False)), ("$.n", False, (None, False)), ("$.nul", True, (None, True)),
            ("$.tsu", False, (None, False)),  # +00:00 표기는 이 형이 아니다(SL-3d 형)
        ])

    def test_timestamps_with_utc_offset(self):  # SL-3d — 광고 SL-8 결정 B (가): +00:00 만, 소수 6자리 이하, 실제 날짜
        self.check("timestamp_utc_offset", [
            ("$.tsu", False, (datetime(2026, 11, 4, 5, 55, 37, 622806), True)), ("$.tsu0", False, (datetime(2026, 11, 4, 5, 55, 37), True)),
            ("$.tsu1", False, (datetime(2026, 11, 4, 5, 55, 37, 600000), True)),
            ("$.ts", False, (None, False)), ("$.ts0", False, (None, False)),  # Z 는 받지 않는다(광고 페이로드에 없음)
            ("$.tsoff", False, (None, False)), ("$.tsum", False, (None, False)), ("$.tsunc", False, (None, False)), ("$.tsnz", False, (None, False)),
            ("$.tsu7", False, (None, False)), ("$.tsubad", False, (None, False)), ("$.tsu24", False, (None, False)), ("$.tsunl", False, (None, False)),
            ("$.tsusp", False, (None, False)), ("$.n", False, (None, False)), ("$.nul", False, (None, False)), ("$.nul", True, (None, True)),
            ("$.missing", True, (None, False)),
        ])

    def test_result_types_and_bad_specs(self):
        selected = self.frame.select(*[contracts.field(F.col("v"), "$.s", kind)[0].alias(kind) for kind in contracts.KINDS])
        self.assertEqual(columns(selected.schema), [("string", "string"), ("bigint", "bigint"), ("timestamp", "timestamp"), ("timestamp_utc_offset", "timestamp")])
        for path, kind in (("after.s", "string"), ("$.a-b", "string"), ("$.s", "double"), ("$", "string")):
            with self.subTest(path=path, kind=kind):
                with self.assertRaises(ValueError):
                    contracts.field(F.col("v"), path, kind)

    def test_extract_reports_failing_columns_in_spec_order(self):
        spec = [("a", "$.s", "string", False), ("b", "$.n", "string", False), ("c", "$.nul", "bigint", True), ("d", "$.missing", "timestamp", True)]
        values, problem = contracts.extract(F.col("v"), spec)
        row = self.frame.select(*values, problem.alias("problem")).first()
        self.assertEqual((row["a"], row["b"], row["c"], row["d"], row["problem"]), ("abc", None, None, None, "b,d"))
        values, problem = contracts.extract(F.col("v"), spec[:1] + spec[2:3])
        self.assertIsNone(self.frame.select(*values, problem.alias("problem")).first()["problem"])


EID = "6f1c2b9e-3d4a-4b5c-8d6e-7f8091a2b3c4"
T0 = datetime(2026, 11, 4, 5, 55, 37, tzinfo=timezone.utc)


def bronze_frame(n=2):
    rows = [(b"k", bytearray(b"{}"), [], "metaverse.outbox.events", 0, i, T0, 0, T0, "bronze_ads_outbox@b4b69424d588", date(2026, 11, 4)) for i in range(n)]
    return spark.createDataFrame(rows, contracts.BRONZE_SCHEMA)


class ErrorRows(unittest.TestCase):
    def test_adapter_errors_shape(self):
        errors = contracts.adapter_errors(bronze_frame(), F.lit("decode"), F.lit("이유"), F.lit(None))
        self.assertEqual(columns(errors.schema), columns(contracts.ADAPTER_ERROR_SCHEMA))
        self.assertEqual([(r["offset"], r["error_code"], r["event_id"]) for r in errors.orderBy("offset").collect()], [(0, "decode", None), (1, "decode", None)])

    def test_error_rows_defaults_and_batch_values(self):
        errors = contracts.adapter_errors(bronze_frame(1), F.lit("contract"), F.lit("계약: a"), F.lit(EID))
        rows = contracts.error_rows(errors, silver_release="silver@0123456789ab", detected_at=T0, batch_id=3)
        self.assertEqual(columns(rows.schema), columns(contracts.ERROR_SCHEMA))
        row = rows.first()
        self.assertEqual((row["error_code"], row["reason"], row["event_id"], row["existing_event_id"], row["existing_event_date"]), ("contract", "계약: a", EID, None, None))
        self.assertEqual((row["acknowledged"], row["ack_reason"], row["ack_incident"]), (False, None, None))
        self.assertEqual((row["silver_release"], row["detected_at"], row["batch_id"]), ("silver@0123456789ab", datetime(2026, 11, 4, 5, 55, 37), 3))
        self.assertEqual((row["topic"], row["offset"], bytes(row["value_binary"])), ("metaverse.outbox.events", 0, b"{}"))

    def test_error_rows_refuse_a_naive_detected_at(self):  # 시간대가 없으면 프로세스 지역 시간대로 읽혀 값이 밀린다(리뷰 R3)
        errors = contracts.adapter_errors(bronze_frame(1), F.lit("decode"), F.lit("이유"), F.lit(None))
        with self.assertRaisesRegex(ValueError, "시간대"):
            contracts.error_rows(errors, silver_release="s", detected_at=T0.replace(tzinfo=None), batch_id=0)
        with self.assertRaisesRegex(ValueError, "시간대"):
            contracts.error_rows(errors, silver_release="s", detected_at="2026-11-04T05:55:37Z", batch_id=0)

    def test_error_rows_keep_given_existing_and_ack_columns(self):
        errors = (contracts.adapter_errors(bronze_frame(1), F.lit("foreign_source"), F.lit("출처"), F.lit(None))
                  .withColumn("existing_event_id", F.lit(EID)).withColumn("existing_event_date", F.lit(date(2026, 11, 3)))
                  .withColumn("acknowledged", F.lit(True)).withColumn("ack_reason", F.lit("사유")).withColumn("ack_incident", F.lit("INC-7")))
        row = contracts.error_rows(errors, silver_release="s", detected_at=T0, batch_id=0).first()
        self.assertEqual((row["existing_event_id"], row["existing_event_date"], row["acknowledged"], row["ack_reason"], row["ack_incident"]),
                         (EID, date(2026, 11, 3), True, "사유", "INC-7"))


if __name__ == "__main__":
    unittest.main()
