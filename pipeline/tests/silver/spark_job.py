"""pipeline/silver/silver_job.py·framework 쓰기 경로(SL-3c) — Spark 시험: 실제 readStream(Delta) + availableNow + foreachBatch, file:// Delta 표.

합성 어댑터는 spark_framework 의 것(판정 순서 ①~⑤, 중간 결과 cache)이다. 출처 설정은 control.source_settings 모양으로 넘긴다.
SL-2 ⑥⑧⑨ 회귀, 멱등(같은 배치 재실행), 멈춤 진단(D4), 시작 검사 ①~④, 두 전체 확인 경로, 읽기 상한(파일 수·바이트 — SL-5), Delta·checkpoint 형식 고정,
잡 입구가 Spark 전에 보는 설정(S3 설정·키 파일 — SL-5).
"""
import json
import shutil
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pyspark.errors import StreamingQueryException
from pyspark.sql import SparkSession, functions as F

from pipeline.silver import contracts, control, framework, silver_job
from silver import spark_framework as synthetic  # 시험 실행기(run_spark_tests)는 pipeline/tests 를 뿌리로 모듈을 찾는다

T0 = datetime(2026, 11, 4, 5, 55, 37, tzinfo=timezone.utc)
TOPIC = synthetic.TOPIC
E = synthetic.EIDS
RELEASE = "silver@000000000000"
SOURCE = {"db": "metaverse_lab", "schema": "public", "table": "eventing_eventoutbox"}


def setUpModule():
    global spark
    spark = SparkSession.builder.getOrCreate()


def adapt(frame, settings):
    assert settings == SOURCE  # 잡 입구가 검사한 출처 dict 를 그대로 받는다(SL-3c 결정 S1)
    return synthetic.adapt(frame)


class Lake:
    """임시 폴더 하나 = 버킷 하나(15 §5.5 (가) 경로)."""

    def __init__(self, test, *, max_files=100, max_bytes="1g"):
        folder = tempfile.mkdtemp(prefix="sl3c-")
        test.addCleanup(shutil.rmtree, folder, True)
        self.root = Path(folder)
        env = {"SILVER_LAKE_ROOT": folder, "SILVER_BRONZE_TABLE": f"{folder}/bronze/metaverse/outbox_events", "SILVER_SOURCE_DB": SOURCE["db"],
               "SILVER_SOURCE_SCHEMA": SOURCE["schema"], "SILVER_SOURCE_TABLE": SOURCE["table"], "SILVER_ACKNOWLEDGED": f"{folder}/ack.json",
               "SILVER_QUARANTINE": f"{folder}/quarantine.json", "SILVER_CODE_ROOT": folder, "SILVER_MAX_FILES_PER_TRIGGER": str(max_files), "SILVER_MAX_BYTES_PER_TRIGGER": max_bytes}
        self.settings = control.job_settings(env, "metaverse", 1)
        self.offset, self.logs = 0, []

    def add(self, *values):
        """Bronze 에 한 커밋(파일 하나)으로 행을 더한다. 값은 synthetic.value(...) 바이트."""
        rows = [(b"k", bytearray(v), [], TOPIC, 0, self.offset + i, T0, 0, T0, "bronze@x", T0.date()) for i, v in enumerate(values)]
        self.offset += len(values)
        spark.createDataFrame(rows, contracts.BRONZE_SCHEMA).coalesce(1).write.format("delta").mode("append").save(self.settings.bronze)

    def run(self, lines=(), **kwargs):
        return silver_job.run(spark, self.settings, adapt, list(lines), RELEASE, log=self.logs.append, **kwargs)

    def silver(self):
        return {r["event_id"]: r for r in spark.read.format("delta").load(self.settings.silver).collect()}

    def errors(self):
        return sorted((r["offset"], r["error_code"], r["acknowledged"], r["batch_id"]) for r in spark.read.format("delta").load(self.settings.errors).collect())

    def version(self, path):
        return framework.table_state(spark, path)[0]

    def history(self, path):
        return [(r["operation"], r["userMetadata"]) for r in spark.sql(f"DESCRIBE HISTORY delta.`{path}`").orderBy("version").collect()]


def ack(offset, code):
    line = {"topic": TOPIC, "partition": 0, "offset": offset, "error_code": code, "reason": "확인함", "incident": "INC-1",
            "acknowledged_by": "운영자", "acknowledged_at": "2026-11-04T06:00:00Z"}
    return control.load_acknowledged(json.dumps({"domain": "metaverse", "lines": [line]}), "metaverse")


def stopped(test, lake, needle, **kwargs):
    with test.assertRaises(StreamingQueryException) as caught:
        lake.run(**kwargs)
    test.assertIn(needle, str(caught.exception))


class FirstRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lake = Lake(SimpleNamespace(addCleanup=cls.addClassCleanup))  # 정리는 클래스 끝에
        cls.lake.add(synthetic.value(E[0]), synthetic.value(E[0], op="r", lsn=None), synthetic.value(E[1]), b"not json")
        cls.query = cls.lake.run()

    def test_silver_rows(self):
        rows = self.lake.silver()
        self.assertEqual({k: (r["copies"], r["first_op"], r["src_offset"]) for k, r in rows.items()}, {E[0]: (2, "c", 0), E[1]: (1, "c", 2)})
        self.assertEqual({(r["silver_release"], r["event_date"]) for r in rows.values()}, {(RELEASE, date(2026, 11, 4))})
        self.assertTrue(all(r["silver_written_at"] is not None for r in rows.values()))

    def test_error_rows(self):
        self.assertEqual(self.lake.errors(), [(3, "decode", False, 0)])
        row = spark.read.format("delta").load(self.lake.settings.errors).first()
        self.assertEqual((row["silver_release"], row["reason"] != ""), (RELEASE, True))

    def test_idempotency_records_and_checkpoint_format(self):  # 내부 API(DeltaLog)·checkpoint metadata 형식 고정 — 판을 올리면 여기서 잡힌다
        metadata = (self.lake.root / "checkpoints/silver/metaverse/events/g1/metadata").read_text()
        self.assertEqual(json.loads(metadata), {"id": str(self.query.id)})
        ident = control.checkpoint_id(metadata)
        self.assertEqual(framework.checkpoint_state(spark, self.lake.settings.checkpoint), (ident, True))
        _, silver_ids, retention = framework.table_state(spark, self.lake.settings.silver)
        self.assertEqual((silver_ids, retention), ({control.txn_app_id("metaverse", 1, "events", ident)}, False))
        self.assertEqual(framework.table_state(spark, self.lake.settings.errors)[1], {control.txn_app_id("metaverse", 1, "errors", ident)})
        self.assertEqual(framework.table_state(spark, str(self.lake.root / "absent")), (-1, set(), False))

    def test_cost_record(self):
        operation, metadata = self.lake.history(self.lake.settings.silver)[-1]
        record = json.loads(metadata)
        self.assertEqual((operation, record["batch_id"], record["bronze_rows"], record["ok_rows"], record["adapter_error_rows"]), ("MERGE", 0, 4, 3, 1))
        self.assertEqual((record["bronze_duplicates"], record["check"]["broadcast"], record["check"]["ids"], record["silver_release"]), (0, True, 2, RELEASE))
        self.assertEqual({key for key in record if key.endswith("_seconds")}, {"dedupe_seconds", "udf_seconds", "check_adapted_seconds", "pre_write_seconds"})
        lines = [line.split(" batch=")[0] for line in self.lake.logs]
        self.assertEqual(lines, ["silver udf", "silver check", "silver batch"])
        self.assertIn("silver_files=0 silver_rows=0 error_files=0 error_rows=0 ids=2 broadcast=true", self.lake.logs[1])

    def test_session_settings_do_not_leak(self):  # 멱등 설정·userMetadata 는 배치 세션에만 두고 지운다(C2·SL-2 ④⑦)
        for key in framework.TXN_CONF:
            self.assertIsNone(spark.conf.get(key, None))
        self.assertNotIn("silver_merge_source", [table.name for table in spark.catalog.listTables()])  # 임시 보기도 남지 않는다


class Rerun(unittest.TestCase):
    def test_no_new_rows_no_new_batch_then_new_rows(self):
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[1]))
        lake.run()
        before = lake.version(lake.settings.silver)
        lake.run()
        self.assertEqual(lake.version(lake.settings.silver), before)  # 새 Bronze 가 없으면 배치가 없다
        lake.add(synthetic.value(E[1], op="r", lsn=None), synthetic.value(E[2]))
        lake.run()
        self.assertEqual({k: r["copies"] for k, r in lake.silver().items()}, {E[0]: 1, E[1]: 2, E[2]: 1})
        self.assertEqual([json.loads(m)["batch_id"] for op, m in lake.history(lake.settings.silver) if op == "MERGE"], [0, 1])

    def test_crash_between_merge_and_error_append(self):  # SL-2 ⑥ — 바깥 세션에 멱등을 두면(⑧) 다시 돈 MERGE 가 copies 를 두 배로 만든다
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[0], op="r", lsn=None), b"not json")
        real = framework.append_errors
        with mock.patch.object(framework, "append_errors", side_effect=RuntimeError("일부러 죽음")):
            stopped(self, lake, "일부러 죽음")
        self.assertEqual({k: r["copies"] for k, r in lake.silver().items()}, {E[0]: 2})  # MERGE 는 커밋됨
        self.assertEqual(lake.errors(), [])
        merged = lake.version(lake.settings.silver)
        with mock.patch.object(framework, "append_errors", side_effect=real) as append:
            lake.run()
        self.assertEqual(append.call_count, 1)  # 같은 batch 0 이 다시 돌았다
        self.assertEqual((lake.version(lake.settings.silver), {k: r["copies"] for k, r in lake.silver().items()}), (merged, {E[0]: 2}))  # MERGE 건너뜀
        self.assertEqual(lake.errors(), [(2, "decode", False, 0)])

    def test_merge_failure_clears_both_settings(self):  # SL-2 ⑦ — 실패해도 둘 다(그리고 userMetadata) 지운다
        lake = Lake(self)
        source = spark.createDataFrame([], contracts.SILVER_SCHEMA)
        with self.assertRaises(Exception):
            framework.merge_events(spark, str(lake.root / "missing-table"), source, "silver:metaverse:g1:events:x", 0, {"batch_id": 0})
        for key in framework.TXN_CONF:
            self.assertIsNone(spark.conf.get(key, None))
        spark.createDataFrame([(1,)], "a int").write.format("delta").save(str(lake.root / "next-table"))  # 다음 쓰기가 멱등 오류 없이 된다
        self.assertEqual(framework.table_state(spark, str(lake.root / "next-table"))[1], set())  # 엉뚱한 기록도 없다(SL-2 ④)

    def test_batch_without_ok_rows_still_merges(self):  # 시작 검사 ④의 근거 — 끝난 배치는 늘 events appId 를 남긴다
        lake = Lake(self)
        lake.add(b"not json")
        lake.run()
        _, ids, _ = framework.table_state(spark, lake.settings.silver)
        self.assertEqual([op for op, _ in lake.history(lake.settings.silver)], ["CREATE TABLE", "MERGE"])
        self.assertEqual((lake.silver(), len(ids)), ({}, 1))


class Records(unittest.TestCase):
    def test_bronze_duplicates_are_counted_and_alerted(self):  # B1 — 같은 위치·같은 바이트 두 번 적재(RB-4 실수)
        lake = Lake(self)
        lake.add(synthetic.value(E[0]))
        lake.offset = 0
        lake.add(synthetic.value(E[0]))
        lake.run()
        self.assertEqual({k: r["copies"] for k, r in lake.silver().items()}, {E[0]: 1})
        self.assertEqual(json.loads(lake.history(lake.settings.silver)[-1][1])["bronze_duplicates"], 1)
        self.assertEqual(lake.logs[0], "silver alert bronze_duplicates batch=0 dropped=1")

    def test_job_sets_utc_itself(self):  # 운영 SparkApplication 에는 제출 스크립트가 없다 — event_date 는 UTC 날짜
        lake = Lake(self)
        lake.add(synthetic.value(E[0], created="2026-11-04T23:30:00.000001Z"))
        spark.conf.set("spark.sql.session.timeZone", "Asia/Seoul")
        try:
            lake.run()
        finally:
            spark.conf.set("spark.sql.session.timeZone", "UTC")
        self.assertEqual(lake.silver()[E[0]]["event_date"], date(2026, 11, 4))  # 한국 날짜면 11-05

    def test_main_checks_settings_before_spark(self):  # 설정·넘김 목록·어댑터를 Spark 세션 전에 본다(세션을 만들지 않으므로 이 시험 세션도 그대로)
        lake = Lake(self)
        env = {"SILVER_LAKE_ROOT": str(lake.root), "SILVER_BRONZE_TABLE": lake.settings.bronze, "SILVER_SOURCE_DB": "metaverse_lab",
               "SILVER_SOURCE_SCHEMA": "public", "SILVER_SOURCE_TABLE": "eventing_eventoutbox", "SILVER_ACKNOWLEDGED": str(lake.root / "ack.json"),
               "SILVER_QUARANTINE": str(lake.root / "quarantine.json"), "SILVER_CODE_ROOT": str(Path(__file__).resolve().parents[3]), "SILVER_MAX_FILES_PER_TRIGGER": "10",  # 저장소 뿌리(control 은 zip 에서 온다)
               "SILVER_MAX_BYTES_PER_TRIGGER": "1g"}
        with self.assertRaisesRegex(ValueError, "SILVER_MAX_FILES_PER_TRIGGER"):
            silver_job.main(["--domain", "metaverse", "--generation", "1"], {k: v for k, v in env.items() if k != "SILVER_MAX_FILES_PER_TRIGGER"})
        with self.assertRaises(FileNotFoundError):
            silver_job.main(["--domain", "metaverse", "--generation", "1"], env)
        s3a = {**env, "SILVER_LAKE_ROOT": "s3a://no-such-bucket"}  # SL-5 — s3a 면 S3 설정 넷이 필요하고, 키 파일도 Spark 전에 읽는다
        with self.assertRaisesRegex(ValueError, "S3_ENDPOINT, S3_REGION, S3_ACCESS_KEY_FILE, S3_SECRET_KEY_FILE"):
            silver_job.main(["--domain", "metaverse", "--generation", "1"], s3a)
        (lake.root / "ack.json").write_text(json.dumps({"domain": "metaverse", "lines": []}))
        with self.assertRaisesRegex(FileNotFoundError, "quarantine.json"):  # (SL-9) 격리 목록도 Spark 전에 읽는다
            silver_job.main(["--domain", "metaverse", "--generation", "1"], env)
        (lake.root / "quarantine.json").write_text(json.dumps({"domain": "ads", "lines": []}))
        with self.assertRaisesRegex(ValueError, "격리 목록"):
            silver_job.main(["--domain", "metaverse", "--generation", "1"], env)
        (lake.root / "quarantine.json").write_text(json.dumps({"domain": "metaverse", "lines": []}))
        keys = {"S3_ENDPOINT": "http://127.0.0.1:1", "S3_REGION": "r", "S3_ACCESS_KEY_FILE": str(lake.root / "no-access-key"),
                "S3_SECRET_KEY_FILE": str(lake.root / "no-secret-key")}
        with self.assertRaisesRegex(FileNotFoundError, "no-access-key"):
            silver_job.main(["--domain", "metaverse", "--generation", "1"], {**s3a, **keys})
        (lake.root / "ack.json").write_text(json.dumps({"domain": "nosuch", "lines": []}))
        (lake.root / "quarantine.json").write_text(json.dumps({"domain": "nosuch", "lines": []}))
        with self.assertRaises(ModuleNotFoundError):
            silver_job.main(["--domain", "nosuch", "--generation", "1"], env)
        self.assertFalse(spark.sparkContext._jsc.sc().isStopped())


class StopAndAcknowledge(unittest.TestCase):  # D4 시험 둘(15 §2.1): 진단 줄 = 멈춤 코드 + acknowledged=false, 넘김 뒤 다시 돈 배치가 오류를 모두 씀
    def test_stop_then_acknowledged_rerun(self):
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[1], table="auth_user"), b"not json")
        stopped(self, lake, "Silver 배치 0 를 멈춘다")
        self.assertEqual((lake.silver(), lake.errors()), ({}, [(1, "foreign_source", False, 0)]))
        stopped(self, lake, "Silver 배치 0 를 멈춘다")
        self.assertEqual(lake.errors(), [(1, "foreign_source", False, 0)])  # 거듭 멈춰도 진단 줄은 한 번(stops appId)
        lake.run(lines=ack(1, "foreign_source"))
        self.assertEqual(set(lake.silver()), {E[0]})
        self.assertEqual(lake.errors(), [(1, "foreign_source", False, 0), (1, "foreign_source", True, 0), (2, "decode", False, 0)])
        diagnostics = spark.read.format("delta").load(lake.settings.errors).where(F.col("error_code").isin(*control.STOP_CODES) & ~F.col("acknowledged"))
        self.assertEqual([r["offset"] for r in diagnostics.collect()], [1])
        ident = framework.checkpoint_state(spark, lake.settings.checkpoint)[0]
        self.assertEqual(framework.table_state(spark, lake.settings.errors)[1],
                         {control.txn_app_id("metaverse", 1, table, ident) for table in ("errors", "stops")})


class StartChecks(unittest.TestCase):  # SL-3c 결정 S2 ①~④, RETENTION, 한 쿼리, 표 모양
    def setUp(self):
        self.lake = Lake(self)
        self.lake.add(synthetic.value(E[0]))
        self.lake.run()
        self.versions = (self.lake.version(self.lake.settings.silver), self.lake.version(self.lake.settings.errors))

    def assert_refused(self, needle, error=control.IdentityError):
        with self.assertRaisesRegex(error, needle):
            self.lake.run()

    def test_deleted_checkpoint(self):  # ② — SL-2 ⑨: 그대로 돌면 copies 가 조용히 두 배
        shutil.rmtree(self.lake.settings.checkpoint)
        self.assert_refused("checkpoint 가 없는데")
        self.assertEqual((self.lake.version(self.lake.settings.silver), self.lake.version(self.lake.settings.errors)), self.versions)

    def test_other_checkpoint(self):  # ③
        other = Lake(self)
        other.add(synthetic.value(E[1]))
        other.run()
        self.lake.settings = self.lake.settings._replace(checkpoint=other.settings.checkpoint)
        self.assert_refused("다른 checkpoint")

    def test_deleted_silver_table(self):  # ④ — 그대로 돌면 지난 배치의 Bronze 가 빠진다
        shutil.rmtree(self.lake.settings.silver)
        self.assert_refused("Silver 표에 그 기록이 없다")

    def test_deleted_error_table(self):  # ④ — 그대로 돌면 지난 오류 행과 conflict 보호((a))가 사라진다(SL-3c 리뷰 R1 실측)
        shutil.rmtree(self.lake.settings.errors)
        self.assert_refused("오류 표에 그 기록이 없다")
        self.assertEqual(self.lake.version(self.lake.settings.errors), -1)  # 시작 검사가 표를 다시 만들기 전에 멈춘다

    def test_retention_property(self):
        spark.sql(f"ALTER TABLE delta.`{self.lake.settings.errors}` SET TBLPROPERTIES ('{framework.RETENTION}' = 'interval 7 days')")
        self.assert_refused(framework.RETENTION)

    def test_one_query_per_process(self):
        other = spark.readStream.format("rate").load().writeStream.format("noop").start()
        try:
            self.assert_refused("스트림 쿼리는 하나", RuntimeError)
        finally:
            other.stop()

    def test_table_shape(self):
        fresh = Lake(self)
        spark.createDataFrame([(1,)], "event_id int").write.format("delta").save(fresh.settings.silver)
        with self.assertRaisesRegex(control.IdentityError, "열이 이 판의 표 모양과 다르다"):
            fresh.run()


class Paths(unittest.TestCase):
    def test_read_limit_splits_batches(self):  # SL-3c 결정 S3 ① — availableNow 도 maxFilesPerTrigger 를 따른다
        lake = Lake(self, max_files=1)
        for event_id in E[:3]:
            lake.add(synthetic.value(event_id))
        lake.run()
        self.assertEqual([line.split()[2] for line in lake.logs if line.startswith("silver batch ")], ["batch=0", "batch=1", "batch=2"])
        self.assertEqual(set(lake.silver()), set(E[:3]))

    def test_byte_limit_splits_batches(self):  # SL-5 D3 (다) — 파일 수 상한이 넉넉해도 바이트 상한이 먼저 닿으면 끊는다(넘게 만드는 파일 하나까지 넣음)
        lake = Lake(self, max_files=100, max_bytes="1")
        for event_id in E[:3]:
            lake.add(synthetic.value(event_id))
        lake.run()
        self.assertEqual([line.split()[2] for line in lake.logs if line.startswith("silver batch ")], ["batch=0", "batch=1", "batch=2"])
        self.assertEqual(set(lake.silver()), set(E[:3]))
        lake = Lake(self, max_files=100, max_bytes="1g")  # 같은 입력, 넉넉한 바이트 상한 → 배치 하나
        for event_id in E[:3]:
            lake.add(synthetic.value(event_id))
        lake.run()
        self.assertEqual([line.split()[2] for line in lake.logs if line.startswith("silver batch ")], ["batch=0"])

    def test_broadcast_and_shuffle_paths_write_the_same(self):  # SL-3c 결정 S3 ② — 전체 확인 (b)(c)(d)·copies 가 두 경로에서 같다
        results = []
        for limit in (framework.BROADCAST_LIMIT, 0):
            lake = Lake(self)
            lake.add(synthetic.value(E[0]), synthetic.value(E[1]), synthetic.value(E[3]))
            lake.run(broadcast_limit=limit)
            lake.add(synthetic.value(E[0], op="r", lsn=None), synthetic.value(E[1], raw='{"u":"y"}'), synthetic.value(E[2]),  # 사본 · (c) · (d)
                     synthetic.value(E[3], created="2026-11-05T05:55:37.628609Z"))  # (b) 같은 내용(합성 해시는 raw_data 만), 다른 날짜
            lake.run(broadcast_limit=limit)
            flags = [json.loads(m)["check"]["broadcast"] for op, m in lake.history(lake.settings.silver) if op == "MERGE"]
            reasons = sorted((r["offset"], r["error_code"], r["reason"], r["existing_event_date"])
                             for r in spark.read.format("delta").load(lake.settings.errors).collect())
            results.append((flags, {k: r["copies"] for k, r in lake.silver().items()}, reasons))
        (flags_b, silver_b, errors_b), (flags_s, silver_s, errors_s) = results
        self.assertEqual((flags_b, flags_s), ([True, True], [False, False]))
        self.assertEqual(silver_b, {E[0]: 2, E[1]: 1, E[2]: 1, E[3]: 1})
        self.assertEqual(errors_b, [(4, "conflict", "같은 event_date 에 내용이 다른 같은 event_id 가 이미 있다", date(2026, 11, 4)),
                                    (6, "conflict", "같은 event_id 가 다른 event_date(2026-11-04)에 이미 있다", date(2026, 11, 4))])
        self.assertEqual((silver_s, errors_s), (silver_b, errors_b))


if __name__ == "__main__":
    unittest.main()
