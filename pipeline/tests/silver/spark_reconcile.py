"""pipeline/silver/reconcile.py — Spark 시험: Bronze ↔ Silver 대사(SL-6 — 계획 temp/pipeline/35_silver-reconcile-sl6.md §4 시험 1~17, 결정 D1~D10).

Silver 는 실제 잡 경로(silver_job.run — readStream + availableNow + foreachBatch, file:// Delta)로 만든다. 어댑터는 합성(spark_framework)과
실제 메타버스 어댑터(끝에서 끝·꺼내는 식 — 시험 3·6·17) 둘이다. 표를 일부러 고칠 때는 Delta SQL(DELETE·UPDATE·append)로 한다.
"""
import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pyspark.sql import SparkSession, functions as F

from pipeline.common import hop_rules
from pipeline.silver import contracts, control, framework, reconcile, silver_job
from pipeline.silver.adapters import metaverse
from metaverse import spark_adapter as mv  # 시험 실행기(run_spark_tests)는 pipeline/tests 를 뿌리로 모듈을 찾는다
from silver import spark_framework as synthetic
from silver import spark_job as job

E = synthetic.EIDS
T0 = job.T0
RELEASE = job.RELEASE
NEXT_DAY = "2026-11-05T01:00:00.000001Z"


def setUpModule():
    global spark
    spark = SparkSession.builder.getOrCreate()
    job.spark = synthetic.spark = mv.spark = spark  # 빌려 쓰는 도우미 모듈의 전역(그 모듈의 setUpModule 이 아직 돌지 않았을 수 있다)


class Lake(job.Lake):
    """spark_job.Lake + tombstone(None)·어댑터 고르기·대사."""

    def add(self, *values):
        rows = [(b"k", None if v is None else bytearray(v), [], job.TOPIC, 0, self.offset + i, T0, 0, T0, "bronze@x", T0.date()) for i, v in enumerate(values)]
        self.offset += len(values)
        spark.createDataFrame(rows, contracts.BRONZE_SCHEMA).coalesce(1).write.format("delta").mode("append").save(self.settings.bronze)

    def run(self, lines=(), adapt=job.adapt, quarantine=()):
        return silver_job.run(spark, self.settings, adapt, list(lines), RELEASE, quarantine=list(quarantine), log=self.logs.append)

    def reconcile(self, quarantine=()):
        self.logs.clear()
        return reconcile.reconcile(spark, self.settings, release=RELEASE, quarantine=list(quarantine), log=self.logs.append)

    def rows(self):
        return {(r["kind"], r["window_start"]): r for r in spark.read.format("delta").load(self.settings.reconcile).collect()}

    def windows(self):
        return {k[1]: r for k, r in self.rows().items() if k[0] == "window"}

    def sql(self, statement):
        spark.sql(statement.format(silver=f"delta.`{self.settings.silver}`", errors=f"delta.`{self.settings.errors}`", bronze=f"delta.`{self.settings.bronze}`",
                                   quarantine=f"delta.`{self.settings.quarantine}`"))


def hour(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")  # 실행기 TZ=UTC — collect 의 naive 값과 맞춘다


W05 = hour("2026-11-04T05:00:00Z")


class Reference(unittest.TestCase):  # 시험 1 — 창 식·ids_sha256 식이 공용 규칙(hop_rules)과 같다
    def test_window_matches_hop_rules(self):
        stamps = ["2026-11-04T05:59:59.999999Z", "2026-11-04T06:00:00Z", "2026-11-04T23:59:59.999999Z", "2028-02-29T12:30:00.5Z", "2026-12-31T23:59:59.999999Z"]
        frame = synthetic.bronze([synthetic.value(E[0], created=text) for text in stamps])
        got = [r["w"] for r in reconcile.place(frame, hop_rules.METAVERSE).orderBy("offset")
               .select(F.date_format("window", "yyyy-MM-dd'T'HH:mm:ss'Z'").alias("w")).collect()]
        moments = [hop_rules._utc_text_time(text) for text in stamps]
        self.assertEqual(got, [hop_rules.format_time(hop_rules.window_start(m, hop_rules.HOUR)) for m in moments])
        for m in moments:  # N2 — 밀리초로 내린 문서(normal_document)도 같은 창
            doc = hop_rules.normal_document(E[0], "x", 1, m, {})
            self.assertEqual(hop_rules.window_start(hop_rules._produced(doc), hop_rules.HOUR), hop_rules.window_start(m, hop_rules.HOUR))

    def test_ids_sha256_matches_hop_rules(self):  # 같은 값은 이름 하나·서로 다른 ID 일 때만 나온다(summarize 는 중복까지 넣은 목록)
        now = datetime(2027, 1, 1, tzinfo=timezone.utc)
        for ids in ([E[0]], E, sorted(E, reverse=True)):
            docs = [hop_rules.normal_document(i, "x", 1, T0, {}) for i in ids]
            (line,), _ = hop_rules.summarize(docs, now, 0, size=hop_rules.HOUR)
            got = spark.createDataFrame([(i,) for i in ids], "id string").agg(reconcile._sha(reconcile._ids("id")).alias("s"), F.size(reconcile._ids("id")).alias("n")).first()
            self.assertEqual((got["n"], got["s"]), (line["count"], line["ids_sha256"]))
        empty = spark.range(1).select(reconcile._sha(F.array().cast("array<string>"))).first()[0]
        self.assertEqual(empty, hop_rules.EMPTY_SHA256)


class OffsetFormat(unittest.TestCase):  # 시험 2 — 판 V = Silver 가 처리한 행(속성 변경·OPTIMIZE 판 포함, 계획 35 §1.2)
    def test_processed_version(self):
        lake = Lake(self)
        lake.add(synthetic.value(E[0]))
        lake.add(synthetic.value(E[1]))
        lake.run()
        cases = [("WRITE", None), ("SET TBLPROPERTIES", "ALTER TABLE {bronze} SET TBLPROPERTIES ('x.note' = '1')"), ("OPTIMIZE", "OPTIMIZE {bronze}")]
        for label, statement in cases:
            with self.subTest(label):
                if statement:
                    lake.sql(statement)
                    lake.run()
                version = reconcile.processed_version(spark, lake.settings.checkpoint)
                self.assertEqual(version, lake.version(lake.settings.bronze))
                self.assertEqual(spark.read.format("delta").option("versionAsOf", version).load(lake.settings.bronze).count(), 2)
                self.assertTrue(lake.reconcile().ok)
        self.assertEqual(lake.history(lake.settings.bronze)[-1][0], "OPTIMIZE")

    def test_no_batches(self):
        lake = Lake(self)
        self.assertIsNone(reconcile.processed_version(spark, lake.settings.checkpoint))
        self.assertEqual(lake.reconcile(), reconcile.Result(True, {"status": "skipped"}))
        self.assertEqual(lake.logs, ["silver reconcile status=skipped reason=no_batches"])


def mv_rows():
    """실제 행(서로 다른 ID) + 새 7종 만든 행(ID 를 새로) + 오류 셋(decode·대문자 ID contract·tombstone 넘김) — 끝에서 끝 입력."""
    values = [mv.REAL["POINT_C"].encode(), mv.REAL["POINT_R"].encode(), mv.REAL["FILL"].encode(), mv.REAL["MOVE"].encode(), mv.REAL["CONTENT"].encode()]
    values += [mv.REAL[key].encode() for key in mv.KINDS]
    values += [mv.changed(event_type=t, topic="raw.event", raw={**mv.POINT_ONLY, **fields}, manifest={"event_type": t}, outbox_uuid=f"00000000-0000-4000-8000-{i:012d}")
               for i, (t, fields) in enumerate(mv.NEW_KINDS.items())]
    values += [b'{"op":"c"', mv.changed("FILL", outbox_uuid=json.loads(mv.REAL["FILL"])["after"]["outbox_uuid"].upper()), None]
    return values


def tombstone_ack(offset):
    line = {"topic": job.TOPIC, "partition": 0, "offset": offset, "error_code": "not_insert", "reason": "확인함", "incident": "INC-1",
            "acknowledged_by": "운영자", "acknowledged_at": "2026-11-04T06:00:00Z"}
    return control.load_acknowledged(json.dumps({"domain": "metaverse", "lines": [line]}), "metaverse")


class EndToEnd(unittest.TestCase):  # 시험 3·4·5 — 실제 메타버스 어댑터, 여러 창
    @classmethod
    def setUpClass(cls):
        cls.lake = Lake(SimpleNamespace(addCleanup=cls.addClassCleanup))
        values = mv_rows()
        cls.lake.add(*values)
        cls.lake.run(lines=tombstone_ack(len(values) - 1), adapt=metaverse.adapt)
        cls.result = cls.lake.reconcile()
        cls.logs = list(cls.lake.logs)

    def test_all_match(self):
        self.assertTrue(self.result.ok)
        rows = self.lake.rows()
        windows = self.lake.windows()
        self.assertGreater(len(windows), 1)  # 실제 행의 created_at 이 여러 시에 걸친다
        self.assertEqual({r["status"] for r in rows.values()}, {"match"})
        self.assertEqual(sum(r["bronze_rows"] for r in windows.values()), sum(r["silver_copies"] for r in windows.values()))
        self.assertEqual(sum(r["silver_ids"] for r in windows.values()), len(self.lake.silver()))
        errors = rows[("errors", None)]
        self.assertEqual((errors["error_rows"], errors["errors_not_in_bronze"], errors["errors_repeated"]), (3, 0, 0))  # decode·contract·넘긴 tombstone
        self.assertEqual(rows[("unplaced", None)]["bronze_rows"], 0)
        listed = rows[("quarantine", None)]  # (SL-9) 빈 목록 — 셈 모두 0
        self.assertEqual([listed[k] for k in ("list_ids", "list_not_in_bronze", "overlap_ids", "republished_ids")], [0, 0, 0, 0])
        self.assertEqual({r["quarantine_ids"] for r in windows.values()}, {0})

    def test_record_columns_and_summary(self):
        rows = list(self.lake.rows().values())
        ident = framework.checkpoint_state(spark, self.lake.settings.checkpoint)[0]
        run = {(r["bronze_version"], r["silver_version"], r["errors_version"], r["quarantine_version"], r["checkpoint_id"], r["silver_release"]) for r in rows}
        self.assertEqual(run, {(self.lake.version(self.lake.settings.bronze), self.lake.version(self.lake.settings.silver),
                                self.lake.version(self.lake.settings.errors), self.lake.version(self.lake.settings.quarantine), ident, RELEASE)})
        self.assertEqual(spark.read.format("delta").load(self.lake.settings.reconcile).schema, reconcile.RESULT_SCHEMA)
        operation, metadata = self.lake.history(self.lake.settings.reconcile)[-1]
        summary = json.loads(metadata)
        self.assertEqual((operation, summary["status"], summary["bronze_rows"], summary["checkpoint_id"]), ("WRITE", "match", len(mv_rows()), ident))
        self.assertEqual(metadata, hop_rules.canonical_json(summary))
        self.assertEqual(len(self.logs), 1)
        self.assertTrue(self.logs[0].startswith("silver reconcile status=match windows="))


class Mismatches(unittest.TestCase):  # 시험 4~8·10~13 — 합성 어댑터, 표를 일부러 고친다
    def lake(self, *values, lines=()):
        lake = Lake(self)
        lake.add(*values)
        lake.run(lines=lines)
        return lake

    def test_deleted_and_extra_silver_rows(self):  # 4·5
        lake = self.lake(synthetic.value(E[0]), synthetic.value(E[1]), synthetic.value(E[2], created=NEXT_DAY))
        self.assertTrue(lake.reconcile().ok)
        lake.sql(f"DELETE FROM {{silver}} WHERE event_id = '{E[1]}'")
        result = lake.reconcile()
        row = lake.windows()[W05]
        self.assertEqual((result.ok, row["status"], row["missing_examples"], row["extra_examples"]), (False, "mismatch", [E[1]], []))
        self.assertTrue(any(line.startswith("silver alert reconcile_mismatch kind=window window=2026-11-04T05:00:00Z ") and f"missing=['{E[1]}']" in line
                            for line in lake.logs))
        self.assertEqual(lake.windows()[hour("2026-11-05T01:00:00Z")]["status"], "match")
        extra = spark.read.format("delta").load(lake.settings.silver).where(F.col("event_id") == E[0]).withColumn("event_id", F.lit(E[3]))
        extra.write.format("delta").mode("append").save(lake.settings.silver)
        lake.reconcile()
        self.assertEqual((lake.windows()[W05]["missing_examples"], lake.windows()[W05]["extra_examples"]), ([E[1]], [E[3]]))

    def test_r1_same_window_error_copy_does_not_hide_a_lost_silver_row(self):  # 6 — 기존 행과 충돌 (c), contract 사본
        lake = self.lake(synthetic.value(E[0]), synthetic.value(E[1]), synthetic.value(E[1], created="2026-11-04T05:10:00.0000001Z"))  # E1 contract(7자리), 같은 창
        lake.add(synthetic.value(E[0], raw='{"u":"y"}'))  # 다음 배치 — 같은 날짜·다른 내용 = 기존 행과 충돌 (c)
        lake.run()
        self.assertEqual(sorted((o, c) for o, c, _, _ in lake.errors()), [(2, "contract"), (3, "conflict")])
        self.assertTrue(lake.reconcile().ok)
        lake.sql(f"DELETE FROM {{silver}} WHERE event_id IN ('{E[0]}', '{E[1]}')")
        self.assertFalse(lake.reconcile().ok)
        row = lake.windows()[W05]
        self.assertEqual((row["status"], row["missing_examples"]), ("mismatch", sorted([E[0], E[1]])))

    def test_error_positions(self):  # 7 — 오류 표 줄: Bronze 에 없음 → mismatch, 겹침 → copies_mismatch
        lake = self.lake(synthetic.value(E[0]), b"not json")
        errors = spark.read.format("delta").load(lake.settings.errors).where(F.col("offset") == 1).limit(1).localCheckpoint()  # 한 줄로 고정
        errors.write.format("delta").mode("append").save(lake.settings.errors)  # 같은 오류 행 한 줄 더
        result = lake.reconcile()
        row = lake.rows()[("errors", None)]
        self.assertEqual((result.ok, row["status"], row["errors_repeated"], row["errors_not_in_bronze"], row["extra_examples"]),
                         (False, "copies_mismatch", 1, 0, [f"{job.TOPIC}:0:1"]))
        self.assertIn("silver alert reconcile_copies kind=errors not_in_bronze=0 repeated=1", lake.logs[0])
        self.assertEqual(result.summary["status"], "copies_mismatch")  # 부풂만 — 요약도 copies_mismatch(D5)
        errors.withColumn("offset", F.lit(99).cast("bigint")).write.format("delta").mode("append").save(lake.settings.errors)
        result = lake.reconcile()
        row = lake.rows()[("errors", None)]
        self.assertEqual((row["status"], row["errors_not_in_bronze"], row["extra_examples"]), ("mismatch", 1, [f"{job.TOPIC}:0:1", f"{job.TOPIC}:0:99"]))
        self.assertEqual(result.summary["status"], "mismatch")

    def test_unplaced(self):  # 8 — 오류 표에 없는데 창·ID 를 꺼낼 수 없는 Bronze 행
        lake = self.lake(synthetic.value(E[0]), b"not json")
        self.assertTrue(lake.reconcile().ok)
        lake.sql("DELETE FROM {errors} WHERE offset = 1")
        self.assertFalse(lake.reconcile().ok)
        row = lake.rows()[("unplaced", None)]
        self.assertEqual((row["status"], row["bronze_rows"], row["missing_examples"]), ("mismatch", 1, [f"{job.TOPIC}:0:1"]))

    def test_cross_date_conflict_keeps_silver_unique(self):  # 9 — 15 §4 ⑧
        lake = self.lake(synthetic.value(E[0]))
        lake.add(synthetic.value(E[0], created=NEXT_DAY))
        lake.run()
        self.assertEqual([c for _, c, _, _ in lake.errors()], ["conflict"])
        result = lake.reconcile()
        self.assertEqual((result.ok, result.summary["duplicates"], set(lake.windows())), (True, 0, {W05}))  # 다음 날 사본은 오류 표에서 위치로 맞음(창 줄 없음)
        self.assertEqual(lake.rows()[("errors", None)]["error_rows"], 1)

    def test_stop_then_acknowledged_rerun(self):  # 10 — 진단 줄(acknowledged=false)은 뺀다
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[1], table="auth_user"))
        job.stopped(self, lake, "Silver 배치 0 를 멈춘다")
        lake.run(lines=job.ack(1, "foreign_source"))
        self.assertEqual(lake.errors(), [(1, "foreign_source", False, 0), (1, "foreign_source", True, 0)])
        self.assertTrue(lake.reconcile().ok)

    def test_silver_duplicate(self):  # 11 — 두 번째 그물
        lake = self.lake(synthetic.value(E[0]))
        dup = spark.read.format("delta").load(lake.settings.silver).withColumn("produced_at", F.lit(datetime(2026, 11, 5, 1, 0, tzinfo=timezone.utc)))
        dup.withColumn("event_date", F.lit(date(2026, 11, 5))).write.format("delta").mode("append").save(lake.settings.silver)
        result = lake.reconcile()
        windows = lake.windows()
        self.assertEqual((result.ok, result.summary["duplicates"]), (False, 1))
        self.assertEqual({w: (r["status"], r["silver_duplicate_ids"]) for w, r in windows.items()},
                         {W05: ("mismatch", 1), hour("2026-11-05T01:00:00Z"): ("mismatch", 1)})
        self.assertIn(f"silver alert silver_duplicates ids=1 examples=['{E[0]}']", lake.logs)

    def test_copies(self):  # 12 — D5: 부풂 copies_mismatch, 부족 mismatch
        lake = self.lake(synthetic.value(E[0]), synthetic.value(E[0], op="r", lsn=None))
        lake.sql(f"UPDATE {{silver}} SET copies = copies + 1 WHERE event_id = '{E[0]}'")
        result = lake.reconcile()
        self.assertEqual((result.ok, lake.windows()[W05]["status"], result.summary["copies_mismatched"], result.summary["status"]),
                         (False, "copies_mismatch", 1, "copies_mismatch"))
        self.assertIn("silver reconcile status=copies_mismatch ", lake.logs[-1])
        lake.sql(f"UPDATE {{silver}} SET copies = 1 WHERE event_id = '{E[0]}'")
        result = lake.reconcile()
        self.assertEqual((result.ok, lake.windows()[W05]["status"], result.summary["mismatched"], result.summary["status"]), (False, "mismatch", 1, "mismatch"))

    def test_bronze_double_load(self):  # 계획 §2.2 경우별 표 — 배치 안 이중 적재는 위치로 줄어 match, 배치를 넘으면 정상·오류 행 모두 copies_mismatch
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), b"not json")
        lake.offset = 0
        lake.add(synthetic.value(E[0]), b"not json")  # 같은 위치·같은 바이트를 다른 커밋으로 — 한 배치(dedupe_bronze)
        lake.run()
        self.assertTrue(lake.reconcile().ok)
        self.assertEqual((lake.windows()[W05]["bronze_rows"], lake.windows()[W05]["silver_copies"]), (1, 1))
        lake.offset = 0
        lake.add(synthetic.value(E[0]), b"not json")  # 다음 배치에 다시(15 B1 한계)
        lake.run()
        result = lake.reconcile()
        window, errors = lake.windows()[W05], lake.rows()[("errors", None)]
        self.assertEqual((result.ok, result.summary["status"], window["status"], window["bronze_rows"], window["silver_copies"]),
                         (False, "copies_mismatch", "copies_mismatch", 1, 2))
        self.assertEqual((errors["status"], errors["errors_repeated"]), ("copies_mismatch", 1))

    def test_bronze_after_silver_is_not_counted(self):  # 13 — 판 V 고정
        lake = self.lake(synthetic.value(E[0]))
        lake.add(synthetic.value(E[1]))
        self.assertTrue(lake.reconcile().ok)
        self.assertEqual(sum(r["bronze_rows"] for r in lake.windows().values()), 1)

    def test_pending_batch_is_refused(self):  # 14 — 처리 중인 배치가 남은 checkpoint
        lake = self.lake(synthetic.value(E[0]))
        offsets = lake.root / "checkpoints/silver/metaverse/events/g1/offsets"
        (offsets / "1").write_text((offsets / "0").read_text())
        with self.assertRaisesRegex(ValueError, "처리 중인 배치"):
            lake.reconcile()

    def test_overwrite_and_read_versions(self):  # 15
        lake = self.lake(synthetic.value(E[0]), synthetic.value(E[1], created=NEXT_DAY), b"not json")
        lake.reconcile()
        lake.reconcile()
        self.assertEqual(spark.read.format("delta").load(lake.settings.reconcile).count(), 2 + 3)  # 창 둘 + errors·unplaced·quarantine(rows() 는 dict 라 겹친 행을 못 센다)
        self.assertEqual([op for op, _ in lake.history(lake.settings.reconcile)], ["WRITE", "WRITE"])
        recorded = {(r["silver_version"], r["errors_version"], r["quarantine_version"]) for r in lake.rows().values()}
        self.assertEqual(recorded, {(lake.version(lake.settings.silver), lake.version(lake.settings.errors), lake.version(lake.settings.quarantine))})
        pinned = {path: lake.version(path) for path in (lake.settings.silver, lake.settings.errors, lake.settings.quarantine)}
        lake.sql(f"DELETE FROM {{silver}} WHERE event_id = '{E[0]}'")  # 판을 정한 뒤 끼어든 쓰기 — 대사는 정한 판을 읽고 그 판을 적는다
        lake.sql("DELETE FROM {errors} WHERE offset = 2")
        lines = framework.listed_frame(spark, [{"event_id": E[1], "reason": "r", "incident": "INC-9", "listed_by": "운영자", "listed_at": "2026-11-09T06:00:00Z"}])
        spark.read.format("delta").load(lake.settings.silver).join(lines, "event_id").select(  # (SL-9) 격리 표에도 끼어든 쓰기 — 읽으면 겹침
            *[name for name, _, _ in contracts.SILVER_FIELDS + contracts.QUARANTINE_LIST_FIELDS], F.current_timestamp().alias("quarantined_at"),
            F.lit(RELEASE).alias("quarantine_release")).write.format("delta").mode("append").save(lake.settings.quarantine)
        state = framework.table_state
        with mock.patch.object(framework, "table_state", side_effect=lambda session, path: (pinned[path], *state(session, path)[1:])):
            self.assertTrue(lake.reconcile().ok)
        self.assertEqual({(r["silver_version"], r["errors_version"], r["quarantine_version"]) for r in lake.rows().values()}, {tuple(pinned.values())})

    def test_requires_utc(self):  # 16
        lake = self.lake(synthetic.value(E[0]))
        spark.conf.set("spark.sql.session.timeZone", "Asia/Seoul")
        try:
            with self.assertRaisesRegex(ValueError, "UTC"):
                lake.reconcile()
        finally:
            spark.conf.set("spark.sql.session.timeZone", "UTC")


class SameExtractionAsAdapter(unittest.TestCase):  # 시험 17 — ③ "정상 행은 늘 꺼내진다"의 근거
    def test_paths_and_values(self):
        spec = {name: (path, kind) for name, path, kind, _ in metaverse.ENVELOPE_SPEC}
        source = reconcile.SOURCES["metaverse"]
        self.assertEqual((spec["event_id"], spec["produced_at"]), ((f"$.after.{source['id']}", "string"), (f"$.after.{source['time']}", "timestamp")))
        frame = mv.bronze([v for v, _ in mv.INPUT])
        ok, _ = metaverse.adapt(frame, mv.LAB)
        placed = reconcile.place(frame, source)
        got = placed.join(ok.select("offset", "event_id", F.date_trunc("HOUR", "produced_at").alias("w")), "offset")
        self.assertEqual(got.count(), ok.count())
        self.assertEqual(got.where(F.col("id").isNull() | (F.col("id") != F.col("event_id")) | (F.col("window") != F.col("w"))).count(), 0)


class ExitCode(unittest.TestCase):  # D7 — 대사 불일치면 rc 3, match 면 0(Silver 쓰기는 이미 끝남)
    def test_main(self):
        lake = Lake(self)
        (lake.root / "ack.json").write_text(json.dumps({"domain": "metaverse", "lines": []}))
        ghost = {"event_id": E[0], "reason": "r", "incident": "INC-9", "listed_by": "운영자", "listed_at": "2026-11-09T06:00:00Z"}
        (lake.root / "quarantine.json").write_text(json.dumps({"domain": "metaverse", "lines": [ghost]}))
        env = {"SILVER_LAKE_ROOT": str(lake.root), "SILVER_BRONZE_TABLE": lake.settings.bronze, "SILVER_SOURCE_DB": "metaverse_lab",
               "SILVER_SOURCE_SCHEMA": "public", "SILVER_SOURCE_TABLE": "eventing_eventoutbox", "SILVER_ACKNOWLEDGED": str(lake.root / "ack.json"),
               "SILVER_QUARANTINE": str(lake.root / "quarantine.json"), "SILVER_CODE_ROOT": str(Path(__file__).resolve().parents[3]),
               "SILVER_MAX_FILES_PER_TRIGGER": "10", "SILVER_MAX_BYTES_PER_TRIGGER": "1g"}
        for ok, rc in ((True, 0), (False, silver_job.MISMATCH_RC)):
            with self.subTest(ok=ok), mock.patch.object(silver_job, "SparkSession") as session, mock.patch.object(silver_job, "run") as run, \
                    mock.patch.object(reconcile, "reconcile", return_value=reconcile.Result(ok, {})) as called:
                self.assertEqual(silver_job.main(["--domain", "metaverse", "--generation", "1"], env), rc)
                self.assertEqual((run.call_count, called.call_count), (1, 1))
                self.assertEqual((run.call_args.kwargs["quarantine"], called.call_args.kwargs["quarantine"]), ([ghost], [ghost]))  # (SL-9) 같은 목록을 둘에
                session.builder.appName.return_value.config.return_value.config.return_value.config.return_value.getOrCreate.return_value.stop.assert_called_once()
        with mock.patch.object(silver_job, "SparkSession"), mock.patch.object(silver_job, "run", side_effect=RuntimeError("멈춤")), \
                mock.patch.object(reconcile, "reconcile") as called, self.assertRaisesRegex(RuntimeError, "멈춤"):
            silver_job.main(["--domain", "metaverse", "--generation", "1"], env)
        self.assertEqual(called.call_count, 0)  # 스트림이 실패하면 대사하지 않는다(rc 1 — 예외 그대로)


if __name__ == "__main__":
    unittest.main()
