"""pipeline/silver 유령 격리(SL-9 — 계획 temp/pipeline/38_silver-quarantine-sl9.md §4 시험 1~18, 결정 D1~D8) — Spark 시험.

Silver 는 실제 잡 경로(silver_job.run — 시작 옮기기 + readStream + availableNow + foreachBatch, file:// Delta)로 만든다. 어댑터는 합성(spark_framework)과
실제 메타버스 어댑터(시험 17) 둘이다. 표를 일부러 고칠 때는 Delta SQL·append 로 한다. 되돌리기 SQL(시험 18)은 runbooks/silver.md 에서 글자 그대로 꺼낸다.
"""
import json
import re
import shutil
import textwrap
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from pyspark.sql import SparkSession, functions as F

from pipeline.silver import contracts, control, framework, reconcile
from pipeline.silver.adapters import metaverse
from metaverse import spark_adapter as mv  # 시험 실행기(run_spark_tests)는 pipeline/tests 를 뿌리로 모듈을 찾는다
from silver import spark_framework as synthetic
from silver import spark_job as job
from silver import spark_reconcile as rec

E = synthetic.EIDS
T0 = job.T0
RELEASE = job.RELEASE
W05 = rec.W05
AFTER_T0, BEFORE_T0 = "2026-11-04T06:00:00Z", "2026-11-04T05:00:00Z"  # Bronze ingested_at(T0 = 05:55:37) 뒤·앞
SILVER = [name for name, _, _ in contracts.SILVER_FIELDS]
RUNBOOK = Path(__file__).resolve().parents[2] / "runbooks" / "silver.md"


def setUpModule():
    global spark
    spark = SparkSession.builder.getOrCreate()
    job.spark = synthetic.spark = mv.spark = rec.spark = spark  # 빌려 쓰는 도우미 모듈의 전역


def listed(*ids, at=AFTER_T0):
    lines = [{"event_id": i, "reason": "PITR 뒤 outbox 에 없음", "incident": "INC-9", "listed_by": "운영자", "listed_at": at} for i in ids]
    return control.load_quarantine(json.dumps({"domain": "metaverse", "lines": lines}), "metaverse")


class Lake(rec.Lake):
    def q(self):
        return {r["event_id"]: r for r in spark.read.format("delta").load(self.settings.quarantine).collect()}

    def moves(self):
        return [line.rsplit(" seconds=", 1)[0] for line in self.logs if line.startswith("silver quarantine moved=")]

    def quarantine_row(self):
        return self.rows()[("quarantine", None)]


def silver_part(row):
    return {name: row[name] for name in SILVER}


class StartMove(unittest.TestCase):  # 시험 1~5
    def test_empty_list(self):  # 1 — 격리 표는 만들어지고 다른 커밋은 없다
        lake = Lake(self)
        lake.add(synthetic.value(E[0]))
        lake.run()
        result = lake.reconcile()
        self.assertTrue(result.ok)
        self.assertEqual([op for op, _ in lake.history(lake.settings.quarantine)], ["CREATE TABLE"])
        self.assertEqual(lake.moves(), [])  # 빈 목록은 아무것도 읽지 않는다
        row = lake.quarantine_row()
        self.assertEqual((row["status"], row["list_ids"], row["overlap_ids"], row["list_not_in_bronze"], row["republished_ids"]), ("match", 0, 0, 0, 0))

    def test_move_then_rerun(self):  # 2·3
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[0], op="r", lsn=None), synthetic.value(E[1], created=rec.NEXT_DAY))
        lake.run()
        before = silver_part(lake.silver()[E[0]])
        lake.logs.clear()
        lake.run(quarantine=listed(E[0]))
        self.assertEqual(lake.moves(), ["silver quarantine moved=1 already=0 pending=0"])
        self.assertEqual(set(lake.silver()), {E[1]})
        row = lake.q()[E[0]]
        self.assertEqual(silver_part(row), before)  # 본 표 행 그대로(copies 2 포함)
        self.assertEqual((row["quarantine_reason"], row["quarantine_incident"], row["quarantine_listed_by"], row["quarantine_listed_at"]),
                         ("PITR 뒤 outbox 에 없음", "INC-9", "운영자", rec.hour(AFTER_T0)))
        self.assertEqual(row["quarantine_release"], RELEASE)
        self.assertGreater(row["quarantined_at"], row["silver_written_at"])
        result = lake.reconcile(listed(E[0]))
        self.assertTrue(result.ok)  # Silver ∪ 격리
        self.assertEqual((lake.windows()[W05]["quarantine_ids"], lake.windows()[W05]["silver_copies"]), (1, 2))
        self.assertEqual((lake.quarantine_row()["list_ids"], result.summary["list_ids"], result.summary["overlap"]), (1, 1, 0))
        versions = (lake.version(lake.settings.silver), lake.version(lake.settings.quarantine))
        lake.logs.clear()
        lake.run(quarantine=listed(E[0]))  # 3 — 같은 목록으로 다시: 두 표 모두 커밋 없음
        self.assertEqual(lake.moves(), ["silver quarantine moved=0 already=1 pending=0"])
        self.assertEqual((lake.version(lake.settings.silver), lake.version(lake.settings.quarantine)), versions)

    def test_crash_between_insert_and_delete(self):  # 4 — 격리 표에만 넣고 죽은 상태
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[1]))
        lake.run()
        with mock.patch.object(framework, "MOVE_DELETE_SQL", "SELECT 1"), self.assertRaisesRegex(framework.QuarantineError, "두 표에 남은 ID"):
            lake.run(quarantine=listed(E[0]))
        self.assertEqual((set(lake.silver()), set(lake.q())), ({E[0], E[1]}, {E[0]}))
        result = lake.reconcile(listed(E[0]))
        self.assertEqual((result.ok, result.summary["overlap"], lake.windows()[W05]["status"], lake.quarantine_row()["status"]), (False, 1, "mismatch", "mismatch"))
        self.assertIn(f"silver alert quarantine_overlap ids=1 examples=['{E[0]}']", lake.logs)
        lake.logs.clear()
        lake.run(quarantine=listed(E[0]))  # 다음 실행: 넣기는 건너뛰고 앞조건을 지나 지운다
        self.assertEqual(lake.moves(), ["silver quarantine moved=1 already=0 pending=0"])
        self.assertEqual((set(lake.silver()), set(lake.q())), ({E[1]}, {E[0]}))
        self.assertTrue(lake.reconcile(listed(E[0])).ok)

    def test_list_before_silver(self):  # 5 — 어디에도 없으면 pending, 뒤 배치 사본이 격리 표로
        lake = Lake(self)
        lake.add(synthetic.value(E[1]))
        lake.run(quarantine=listed(E[0]))
        self.assertEqual(lake.moves(), ["silver quarantine moved=0 already=0 pending=1"])
        self.assertEqual([op for op, _ in lake.history(lake.settings.quarantine)], ["CREATE TABLE"])  # 할 일이 없는 배치는 격리 MERGE 를 하지 않는다(D3 (가))
        lake.add(synthetic.value(E[0]))
        lake.run(quarantine=listed(E[0]))
        self.assertEqual((set(lake.silver()), set(lake.q())), ({E[1]}, {E[0]}))
        self.assertTrue(lake.reconcile(listed(E[0])).ok)


class BatchCopies(unittest.TestCase):  # 시험 6~9
    def test_new_generation_first_batch(self):  # 6 — 빈 표 + 목록: 첫 배치에서 바로 격리 표로
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[1]))
        lake.run(quarantine=listed(E[0]))
        self.assertEqual((set(lake.silver()), set(lake.q())), ({E[1]}, {E[0]}))
        row = lake.q()[E[0]]
        self.assertEqual((row["copies"], row["quarantined_at"], row["silver_written_at"], row["quarantine_release"]),
                         (1, row["silver_written_at"], lake.silver()[E[1]]["silver_written_at"], RELEASE))  # 배치 시각 하나
        operation, metadata = lake.history(lake.settings.quarantine)[-1]
        self.assertEqual((operation, json.loads(metadata)["quarantine_rows"]), ("MERGE", 1))
        ident = framework.checkpoint_state(spark, lake.settings.checkpoint)[0]
        self.assertEqual(framework.table_state(spark, lake.settings.quarantine)[1], {control.txn_app_id("metaverse", 1, "quarantine", ident)})
        self.assertIn("silver quarantine batch=0 rows=1", lake.logs)
        self.assertTrue(lake.reconcile(listed(E[0])).ok)

    def test_same_hash_copy_and_rerun(self):  # 7 — copies + 1, quarantined_at 그대로, 같은 배치를 다시 돌면(txn) 그대로
        lake = Lake(self)
        lake.add(synthetic.value(E[0]))
        lake.run(quarantine=listed(E[0]))
        first = lake.q()[E[0]]["quarantined_at"]
        lake.add(synthetic.value(E[0]))
        real = framework.append_errors
        with mock.patch.object(framework, "append_errors", side_effect=RuntimeError("일부러 죽음")):
            job.stopped(self, lake, "일부러 죽음", quarantine=listed(E[0]))
        self.assertEqual((lake.q()[E[0]]["copies"], lake.q()[E[0]]["quarantined_at"]), (2, first))
        merged = lake.version(lake.settings.quarantine)
        with mock.patch.object(framework, "append_errors", side_effect=real) as append:
            lake.run(quarantine=listed(E[0]))
        self.assertEqual(append.call_count, 1)  # 같은 batch 1 이 다시 돌았다
        self.assertEqual((lake.version(lake.settings.quarantine), lake.q()[E[0]]["copies"]), (merged, 2))  # 격리 MERGE 건너뜀
        self.assertEqual(lake.silver(), {})
        self.assertTrue(lake.reconcile(listed(E[0])).ok)

    def test_different_hash_is_conflict(self):  # 8 — 다른 해시 → 오류 표 conflict, 격리 표 그대로, 그 뒤 사본은 (a)
        lake = Lake(self)
        lake.add(synthetic.value(E[0]))
        lake.run(quarantine=listed(E[0]))
        lake.add(synthetic.value(E[0], raw='{"u":"y"}'))
        lake.run(quarantine=listed(E[0]))
        lake.add(synthetic.value(E[0]))
        lake.run(quarantine=listed(E[0]))
        self.assertEqual((lake.q()[E[0]]["copies"], lake.silver()), (1, {}))
        reasons = sorted((r["offset"], r["error_code"], r["reason"], r["existing_event_id"], r["existing_event_date"])
                         for r in spark.read.format("delta").load(lake.settings.errors).collect())
        self.assertEqual(reasons, [(1, "conflict", "격리 목록 ID 의 새 사본 — 격리 표의 그 행과 내용이 다르다", E[0], date(2026, 11, 4)),
                                   (2, "conflict", "이미 conflict 로 오류 표에 있는 event_id 의 새 사본", E[0], date(2026, 11, 4))])
        self.assertTrue(lake.reconcile(listed(E[0])).ok)  # 오류 행은 위치로 맞음

    def test_republished_op_r(self):  # 9 — listed_at 뒤에 들어온 op r 만 센다(경보만, rc 0)
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[1]))  # ingested_at = T0(모든 행)
        lake.run(quarantine=listed(E[0], E[1]))
        lake.add(synthetic.value(E[0], op="r", lsn=None), synthetic.value(E[0], op="r", lsn=None))  # E0 의 op r 사본 둘
        lake.run(quarantine=listed(E[0], E[1]))
        self.assertEqual((lake.q()[E[0]]["copies"], lake.q()[E[1]]["copies"]), (3, 1))
        result = lake.reconcile(listed(E[0], E[1], at=BEFORE_T0))
        self.assertEqual((result.ok, result.summary["republished"], lake.quarantine_row()["status"], lake.quarantine_row()["republished_ids"]),
                         (True, 1, "match", 1))  # ID 수로 센다 — E0 은 op r 사본이 둘이어도 1(사본으로 세면 2), E1 은 op c 사본뿐이라 0(op 조건이 없으면 E1 도 셈)
        self.assertIn(f"silver alert quarantined_republished ids=1 incidents=['INC-9'] examples=['{E[0]}']", lake.logs)
        result = lake.reconcile(listed(E[0], E[1], at=AFTER_T0))  # 유령이 되기 전 스냅숏 사본 — 세지 않음
        self.assertEqual((result.ok, result.summary["republished"]), (True, 0))
        self.assertFalse(any("quarantined_republished" in line for line in lake.logs))


class ReconcileChecks(unittest.TestCase):  # 시험 10~13·16
    def test_manual_overlap(self):  # 10
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[1]))
        lake.run()
        lines = framework.listed_frame(spark, listed(E[0]))
        copy = spark.read.format("delta").load(lake.settings.silver).where(F.col("event_id") == E[0]).join(lines, "event_id").select(
            *SILVER, *[name for name, _, _ in contracts.QUARANTINE_LIST_FIELDS], F.current_timestamp().alias("quarantined_at"), F.lit(RELEASE).alias("quarantine_release"))
        copy.write.format("delta").mode("append").save(lake.settings.quarantine)
        result = lake.reconcile(listed(E[0]))
        row = lake.quarantine_row()
        self.assertEqual((result.ok, result.summary["status"], lake.windows()[W05]["status"], row["status"], row["overlap_ids"], row["extra_examples"]),
                         (False, "mismatch", "mismatch", "mismatch", 1, [E[0]]))
        self.assertIn(f"silver alert reconcile_mismatch kind=quarantine overlap=1 list_not_in_bronze=0 missing=[] extra=['{E[0]}']", lake.logs)
        self.assertIn(f"silver alert quarantine_overlap ids=1 examples=['{E[0]}']", lake.logs)
        self.assertEqual(result.summary["duplicates"], 0)  # 겹침은 본 표 안 중복이 아니다(중복은 본 표 안에서만 센다)
        self.assertFalse(any("silver_duplicates" in line for line in lake.logs))

    def test_list_id_not_in_bronze(self):  # 11 — D4 (가)
        lake = Lake(self)
        lake.add(synthetic.value(E[0]))
        lake.run(quarantine=listed(E[3]))
        self.assertEqual(lake.moves(), ["silver quarantine moved=0 already=0 pending=1"])
        result = lake.reconcile(listed(E[3]))
        row = lake.quarantine_row()
        self.assertEqual((result.ok, result.summary["list_not_in_bronze"], row["status"], row["missing_examples"], lake.windows()[W05]["status"]),
                         (False, 1, "mismatch", [E[3]], "match"))
        self.assertIn(f"silver alert reconcile_mismatch kind=quarantine overlap=0 list_not_in_bronze=1 missing=['{E[3]}'] extra=[]", lake.logs)
        self.assertTrue(lake.reconcile(listed()).ok)  # 목록을 고치면 match

    def test_error_table_id_counts_as_in_bronze(self):  # 11 보강 — 오류 표 event_id 도 Bronze 쪽(계획 38 §2.4)
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[1], created="2026-11-04T05:10:00.0000001Z"))  # E1 contract(7자리) — 오류 표에만
        lake.run()
        place = reconcile.place
        with mock.patch.object(reconcile, "place", side_effect=lambda frame, source: place(frame, source).withColumn(  # Bronze 쪽 ID 를 가린다
                "id", F.when(F.col("id") == E[1], F.lit(None)).otherwise(F.col("id")))):
            result = lake.reconcile(listed(E[1]))
        self.assertEqual(result.summary["list_not_in_bronze"], 0)

    def test_deleted_quarantine_table(self):  # 12 — 시작 검사는 지나고(D3 (가)) 대사가 잡는다
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[1]))
        lake.run(quarantine=listed(E[0]))  # 배치가 격리 표에 txn 기록을 남긴다
        self.assertEqual(len(framework.table_state(spark, lake.settings.quarantine)[1]), 1)
        shutil.rmtree(lake.settings.quarantine)
        lake.run(quarantine=listed(E[0]))
        result = lake.reconcile(listed(E[0]))
        self.assertEqual((result.ok, lake.windows()[W05]["status"], lake.windows()[W05]["missing_examples"]), (False, "mismatch", [E[0]]))

    def test_foreign_checkpoint_in_quarantine_table(self):  # 13
        lake = Lake(self)
        lake.add(synthetic.value(E[0]))
        lake.run()
        lines = framework.listed_frame(spark, listed(E[0]))
        row = spark.read.format("delta").load(lake.settings.silver).join(lines, "event_id").select(
            *SILVER, *[name for name, _, _ in contracts.QUARANTINE_LIST_FIELDS], F.current_timestamp().alias("quarantined_at"), F.lit(RELEASE).alias("quarantine_release"))
        old = control.txn_app_id("metaverse", 1, "quarantine", "0aabe9c8-6b77-4b20-9e52-3360a8f0b86c")
        row.write.format("delta").mode("append").option("txnAppId", old).option("txnVersion", 0).save(lake.settings.quarantine)
        with self.assertRaisesRegex(control.IdentityError, "다른 checkpoint"):
            lake.run()

    def test_result_table_from_sl6_shape(self):  # 16 — SL-6 모양 결과 표 위에 덮어쓴다(overwriteSchema)
        lake = Lake(self)
        lake.add(synthetic.value(E[0]))
        lake.run()
        added = {"quarantine_ids", "list_ids", "list_not_in_bronze", "overlap_ids", "republished_ids", "quarantine_version"}
        old = reconcile.T.StructType([f for f in reconcile.RESULT_SCHEMA.fields if f.name not in added])
        spark.createDataFrame([], old).write.format("delta").save(lake.settings.reconcile)
        self.assertTrue(lake.reconcile().ok)
        self.assertEqual(spark.read.format("delta").load(lake.settings.reconcile).schema, reconcile.RESULT_SCHEMA)
        self.assertEqual({r["quarantine_version"] for r in lake.rows().values()}, {lake.version(lake.settings.quarantine)})


class Failures(unittest.TestCase):  # 시험 14
    def test_before_after_mismatch(self):
        lake = Lake(self)
        lake.add(synthetic.value(E[0]))
        lake.run()
        lake.add(synthetic.value(E[1]))
        with mock.patch.object(framework, "MOVE_INSERT_SQL", "SELECT 1"), \
                mock.patch.object(framework, "MOVE_DELETE_SQL", f"DELETE FROM delta.`{{path}}` WHERE event_id = '{E[0]}'"), \
                self.assertRaisesRegex(framework.QuarantineError, "확인이 어긋났다"):
            lake.run(quarantine=listed(E[0]))
        self.assertNotIn(E[1], lake.silver())  # 스트림을 돌지 않았다

    def test_different_rows_in_both_tables(self):  # r2 권고 2 — 지우기 전에 멈춘다
        lake = Lake(self)
        lake.add(synthetic.value(E[0]))
        lake.run(quarantine=listed(E[0]))
        back = spark.read.format("delta").load(lake.settings.quarantine).select(*SILVER).withColumn("copies", F.lit(2))
        back.write.format("delta").mode("append").save(lake.settings.silver)  # 되돌리기를 순서 없이 하고 손으로 고친 꼴
        with self.assertRaisesRegex(framework.QuarantineError, "다른 행으로"):
            lake.run(quarantine=listed(E[0]))
        self.assertEqual((lake.silver()[E[0]]["copies"], lake.q()[E[0]]["copies"]), (2, 1))  # 본 표 행이 그대로 남았다


class Judge(unittest.TestCase):  # 시험 15 — 목록 ID 행은 본 표 전체 확인에 들어가지 않는다(격리 '목록'으로 맞춤 — 격리 표가 비어도)
    def test_list_routes_before_full_check(self):
        frame = synthetic.bronze([synthetic.value(E[0]), synthetic.value(E[1])])
        ok, errors = synthetic.adapt(frame)
        silver = spark.createDataFrame([(E[0], date(2026, 11, 4), "0" * 64)], "event_id string, event_date date, content_sha256 string")  # 다른 해시
        empty = spark.createDataFrame([], "event_id string, event_date date, content_sha256 string")
        judgement = framework.judge(ok, errors, [], frame, silver, empty.select("event_id"), quarantine_lines=listed(E[0]), quarantined=empty)
        try:
            self.assertEqual([r["event_id"] for r in judgement.merge.collect()], [E[1]])
            self.assertEqual([r["event_id"] for r in judgement.quarantine.collect()], [E[0]])
            self.assertEqual(judgement.errors.where(F.col("error_code") == "conflict").count(), 0)
        finally:
            for cached in judgement.cached:
                cached.unpersist()


class RealAdapter(unittest.TestCase):  # 시험 17 — 실제 메타버스 어댑터 끝에서 끝
    def test_move_real_row(self):
        lake = Lake(self)
        values = rec.mv_rows()
        lake.add(*values)
        ack = rec.tombstone_ack(len(values) - 1)
        lake.run(lines=ack, adapt=metaverse.adapt)
        ghost = json.loads(mv.REAL["POINT_C"])["after"]["outbox_uuid"]  # op c·r 두 사본
        before = silver_part(lake.silver()[ghost])
        lake.logs.clear()
        lake.run(lines=ack, adapt=metaverse.adapt, quarantine=listed(ghost))
        self.assertEqual(lake.moves(), ["silver quarantine moved=1 already=0 pending=0"])
        self.assertEqual((silver_part(lake.q()[ghost]), before["copies"]), (before, 2))
        result = lake.reconcile(listed(ghost))
        self.assertTrue(result.ok)
        self.assertEqual({r["status"] for r in lake.rows().values()}, {"match"})
        self.assertEqual(sum(r["quarantine_ids"] for r in lake.windows().values()), 1)


class ReverseSql(unittest.TestCase):  # 시험 18 — runbook 되돌리기 SQL 두 문장을 글자 그대로
    def statements(self):
        blocks = [textwrap.dedent(b).strip() for b in re.findall(r"```sql\n(.*?)```", RUNBOOK.read_text(encoding="utf-8"), re.S)]
        merges = [b for b in blocks if b.startswith("MERGE INTO")]
        self.assertEqual([b.split("\n", 1)[0] for b in merges], ["MERGE INTO delta.`<본 표>` t", "MERGE INTO delta.`<격리 표>` t"])
        return merges

    def test_reverse(self):
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[0], op="r", lsn=None), synthetic.value(E[1]))
        lake.run()
        before = silver_part(lake.silver()[E[0]])
        lake.run(quarantine=listed(E[0], E[1]))
        self.assertEqual((lake.silver(), set(lake.q())), ({}, {E[0], E[1]}))
        for statement in self.statements():  # 순서 1 → 2. 목록에서는 E0 줄을 뺀다(관리 Step 2단계)
            spark.sql(statement.replace("<본 표>", lake.settings.silver).replace("<격리 표>", lake.settings.quarantine).replace("<ID 목록>", f"'{E[0]}'"))
        row = silver_part(lake.silver()[E[0]])
        self.assertEqual({k: v for k, v in row.items() if k != "silver_written_at"}, {k: v for k, v in before.items() if k != "silver_written_at"})
        self.assertGreater(row["silver_written_at"], before["silver_written_at"])  # D8 (가) — 본 표에 들어온 시각
        self.assertEqual(set(lake.q()), {E[1]})
        lake.logs.clear()
        lake.run(quarantine=listed(E[1]))
        self.assertEqual(lake.moves(), ["silver quarantine moved=0 already=1 pending=0"])
        self.assertTrue(lake.reconcile(listed(E[1])).ok)


if __name__ == "__main__":
    unittest.main()
