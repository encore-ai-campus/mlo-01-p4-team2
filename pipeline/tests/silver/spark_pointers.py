"""pipeline/silver/pointers.py 원천별 세대 포인터(SL-7 — 계획 temp/pipeline/40_silver-union-pointer-sl7.md r3 §4 시험 1~10·7a·7b, 결정 D1·D3·D5·D6) — Spark 시험.

메타버스 세대는 실제 잡 경로(silver_job.run + reconcile, file:// Delta, 합성 어댑터 — spark_reconcile.Lake)로 만든다. 합성 원천(`ads` 이름 — 본 표 +
합성 대사 결과 커밋)은 원천 사이 포인터가 닿지 않는 시험과 열 모양 거부 시험에만 쓴다. 실제 광고 어댑터 출력(SL-8 시험 경로)은 시험 11(계획 §7).
"""
import contextlib
import io
import json
import re
import shutil
import unittest
from unittest import mock

from pyspark.sql import SparkSession, functions as F

from pipeline.silver import contracts, control, framework, pointers, reconcile
from ads import spark_adapter as ads_e2e
from silver import spark_framework as synthetic  # 시험 실행기(run_spark_tests)는 pipeline/tests 를 뿌리로 모듈을 찾는다
from silver import spark_job as job
from silver import spark_reconcile as rec

E = synthetic.EIDS
RELEASE = job.RELEASE
SILVER = [name for name, _, _ in contracts.SILVER_FIELDS]
BY, WHY = "운영자", "SL-7 시험"


def setUpModule():
    global spark
    spark = SparkSession.builder.getOrCreate()
    job.spark = synthetic.spark = rec.spark = ads_e2e.spark = spark  # 빌려 쓰는 도우미 모듈의 전역


class Lake(rec.Lake):
    """임시 뿌리 하나 = 버킷 하나. 메타버스 세대는 `generation(n)` 으로 바꿔 가며 같은 Bronze 를 읽는다."""

    def __init__(self, test):
        super().__init__(test)
        self.base = self.settings

    def generation(self, n, bronze=None):
        self.settings = self.base._replace(generation=n, bronze=bronze or self.base.bronze, **control.silver_paths(str(self.root), "metaverse", n)._asdict())
        return self.settings

    def cycle(self, n, quarantine=(), bronze=None):
        """세대 n 을 한 회차(잡 + 대사) 돈다. 반환: 대사 Result."""
        self.generation(n, bronze)
        self.run(quarantine=quarantine)
        return self.reconcile(quarantine)

    def set(self, domain, n, **kwargs):
        self.logs.clear()
        return pointers.set_generation(spark, str(self.root), domain, n, by=kwargs.pop("by", BY), reason=kwargs.pop("reason", WHY), log=self.logs.append, **kwargs)

    def view(self, domain="metaverse"):
        return pointers.read_events(spark, str(self.root), domain)

    def pointer(self, domain="metaverse"):
        return control.pointer_path(str(self.root), domain)

    def pointer_rows(self, domain="metaverse"):
        return [r.asDict() for r in spark.read.format("delta").load(self.pointer(domain)).collect()]

    def fake(self, domain, n, frame=None, *, bronze_table="/lake/bronze/ads/outbox_events", bronze_version=5):
        """합성 원천 세대: 본 표(기본은 메타버스 g1 행의 domain·event_id 를 바꾼 것)와 대사 결과 커밋 하나(요약만 — 열은 보지 않는다)."""
        paths = control.silver_paths(str(self.root), domain, n)
        if frame is None:
            frame = (spark.read.format("delta").load(control.silver_paths(str(self.root), "metaverse", 1).silver)
                     .withColumn("domain", F.lit(domain)).withColumn("event_id", F.concat(F.lit("a"), F.substring("event_id", 2, 35))))
        frame.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(paths.silver)
        self.summary(paths.reconcile, {"status": "match", "silver_version": self.version(paths.silver), "bronze_version": bronze_version, "checkpoint_id": "c-1",
                                       "silver_release": RELEASE, "bronze_table": bronze_table})
        return paths

    def summary(self, path, summary):
        spark.range(1).write.format("delta").mode("overwrite").option("overwriteSchema", "true").option("userMetadata", json.dumps(summary)).save(path)


def lake_with_g1(test, *values):
    lake = Lake(test)
    lake.add(*(values or (synthetic.value(E[0]), synthetic.value(E[1]), synthetic.value(E[2], created=rec.NEXT_DAY))))
    test.assertTrue(lake.cycle(1).ok)
    return lake


def silver_rows(path, version=None):
    reader = spark.read.format("delta")
    return sorted(map(tuple, (reader if version is None else reader.option("versionAsOf", version)).load(path).collect()))


class Read(unittest.TestCase):  # 시험 1~5·8·9
    def test_no_pointer_table(self):  # 1
        lake = lake_with_g1(self)
        with self.assertRaisesRegex(pointers.PointerError, f"^포인터 표가 없다: {re.escape(lake.pointer())} "):
            lake.view()
        logs = []
        pointers.show(spark, str(lake.root), "metaverse", log=logs.append)
        self.assertEqual(logs, [f"silver pointer table=none path={lake.root}/silver/metaverse/pointer"])

    def test_one_domain(self):  # 2 — 실제 잡 경로 세대 → set → 읽기 = 본 표
        lake = lake_with_g1(self)
        summary = json.loads(lake.history(lake.settings.reconcile)[-1][1])
        self.assertEqual(summary["bronze_table"], lake.settings.bronze)  # (D5) 대사 요약의 새 키
        row = lake.set("metaverse", 1)
        silver_version = lake.version(lake.settings.silver)
        self.assertEqual({k: row[k] for k in row if k != "set_at"},
                         {"domain": "metaverse", "generation": 1, "set_by": BY, "reason": WHY, "reconcile_version": lake.version(lake.settings.reconcile),
                          "silver_version": silver_version, "silver_release": RELEASE, "checkpoint_id": framework.checkpoint_state(spark, lake.settings.checkpoint)[0],
                          "bronze_table": lake.settings.bronze, "bronze_version": lake.version(lake.settings.bronze)})
        self.assertEqual([{k: v for k, v in r.items() if k != "set_at"} for r in lake.pointer_rows()], [{k: v for k, v in row.items() if k != "set_at"}])
        self.assertEqual(lake.pointer(), f"{lake.root}/silver/metaverse/pointer")
        history = lake.history(lake.pointer())
        self.assertEqual([op for op, _ in history], ["WRITE"])
        self.assertEqual(json.loads(history[-1][1]), {"domain": "metaverse", "from_generation": None, "to_generation": 1, "set_by": BY, "reason": WHY, "bronze_changed": None})
        self.assertEqual(lake.logs, [f"silver pointer domain=metaverse from=None to=1 reconcile_version={row['reconcile_version']} silver_version={silver_version} "
                                     f"bronze_version={row['bronze_version']} bronze_changed=no by={BY}"])
        view = lake.view()
        self.assertEqual(view.frame.columns, SILVER)
        self.assertEqual(view.frame.schema, spark.read.format("delta").load(lake.settings.silver).schema)
        self.assertEqual(sorted(map(tuple, view.frame.collect())), silver_rows(lake.settings.silver))
        self.assertEqual(view.frame.count(), 3)
        self.assertEqual(view[1:], (lake.version(lake.pointer()), "metaverse", 1, lake.settings.silver, silver_version, None))  # LATEST — 대사 판 없음

    def test_domains_do_not_touch(self):  # 3 — 원천마다 포인터 표(D6)
        lake = lake_with_g1(self)
        lake.set("metaverse", 1)
        before = (lake.version(lake.pointer()), lake.history(lake.pointer()), lake.pointer_rows())
        ads = lake.fake("ads", 1)
        lake.set("ads", 1)
        lake.set("ads", 1, reason="다시")
        self.assertEqual((lake.version(lake.pointer()), lake.history(lake.pointer()), lake.pointer_rows()), before)
        self.assertEqual(lake.pointer("ads"), f"{lake.root}/silver/ads/pointer")
        self.assertEqual([op for op, _ in lake.history(lake.pointer("ads"))], ["WRITE", "WRITE"])
        self.assertEqual(lake.view()[1:], (before[0], "metaverse", 1, lake.settings.silver, lake.version(lake.settings.silver), None))
        view = lake.view("ads")
        self.assertEqual((view.generation, view.path, {r["domain"] for r in view.frame.collect()}), (1, ads.silver, {"ads"}))

    def test_column_check(self):  # 4 — 열 하나 더·형 하나 다름·순서 다름·이름만 다름
        lake = lake_with_g1(self)
        lake.set("metaverse", 1)
        table = lake.settings.silver
        good = spark.read.format("delta").load(table).localCheckpoint()  # 덮어쓰기 전 행(아래에서 같은 표를 덮어씀)
        swapped = SILVER[:]
        swapped[1], swapped[2] = swapped[2], swapped[1]
        for label, frame, needle in (("extra", good.withColumn("note", F.lit("x")), r"더 있음 \[\('note', 'string'\)\]"),
                                     ("type", good.withColumn("src_partition", F.col("src_partition").cast("bigint")), r"\('src_partition', 'bigint'\)"),
                                     ("order", good.select(*swapped), "순서 다름"),
                                     ("rename", good.withColumnRenamed("aggregate_id", "aggregate_key"),
                                      r"더 있음 \[\('aggregate_key', 'string'\)\], 없음 \[\('aggregate_id', 'string'\)\]")):
            with self.subTest(label):
                frame.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(table)
                with self.assertRaisesRegex(pointers.PointerError, f"^{re.escape(table)} 의 열이 이 판의 표 모양과 다르다.*{needle}"):
                    lake.view()

    def test_pointer_table_checks(self):  # 5 — 포인터 표 열 모양·한 행·그 원천 행
        lake = lake_with_g1(self)
        lake.set("metaverse", 1)
        path = lake.pointer()
        version = lake.version(path)

        def refused(needle):
            for call in (lambda: lake.view(), lambda: lake.set("metaverse", 1)):
                with self.assertRaisesRegex(pointers.PointerError, needle):
                    call()
            spark.sql(f"RESTORE TABLE delta.`{path}` TO VERSION AS OF {version}")

        spark.sql(f"ALTER TABLE delta.`{path}` ADD COLUMNS (note string)")
        refused(f"^{re.escape(path)} 의 열이 이 판의 표 모양과 다르다\\(더 있음 \\[\\('note', 'string'\\)\\]")
        spark.sql(f"INSERT INTO delta.`{path}` SELECT * FROM delta.`{path}`")  # 덮어쓰기가 아닌 쓰기로 두 행
        refused(f"^{re.escape(path)} 판 {lake.version(path)} 은 원천 metaverse 의 한 행이어야 한다: \\['metaverse', 'metaverse'\\]")  # 판은 RESTORE 뒤 INSERT
        spark.sql(f"UPDATE delta.`{path}` SET domain = 'ads'")  # 다른 원천 행
        refused("은 원천 metaverse 의 한 행이어야 한다: \\['ads'\\]")
        self.assertEqual(lake.view().generation, 1)

    def test_quarantined_rows_are_not_read(self):  # 8 — 본 표만(G11)
        lake = lake_with_g1(self)
        lines = [{"event_id": E[0], "reason": "PITR", "incident": "INC-9", "listed_by": BY, "listed_at": "2026-11-09T06:00:00Z"}]
        quarantine = control.load_quarantine(json.dumps({"domain": "metaverse", "lines": lines}), "metaverse")
        self.assertTrue(lake.cycle(1, quarantine).ok)
        self.assertEqual({r["event_id"] for r in spark.read.format("delta").load(lake.settings.quarantine).collect()}, {E[0]})
        lake.set("metaverse", 1)
        self.assertEqual(sorted(r["event_id"] for r in lake.view().frame.collect()), sorted(E[1:3]))

    def test_versions_are_pinned(self):  # 9 — 읽은 뒤 끼어든 쓰기, 판을 정한 뒤 읽기 전에 끼어든 쓰기
        lake = lake_with_g1(self)
        lake.set("metaverse", 1)
        before = lake.version(lake.settings.silver)
        view = lake.view()
        lake.add(synthetic.value(E[3]))
        self.assertTrue(lake.cycle(1).ok)
        self.assertEqual((view.frame.count(), view.version), (3, before))
        self.assertEqual(lake.view().frame.count(), 4)
        pointer_before = lake.version(lake.pointer())
        self.assertTrue(lake.cycle(2).ok)
        lake.set("metaverse", 2)
        state = framework.table_state
        pinned = {lake.pointer(): pointer_before, control.silver_paths(str(lake.root), "metaverse", 1).silver: before}
        with mock.patch.object(framework, "table_state", side_effect=lambda session, path: (pinned.get(path, state(session, path)[0]), *state(session, path)[1:])):
            view = lake.view()
            self.assertEqual((view.pointer_version, view.generation, view.version, view.frame.count()), (pointer_before, 1, before, 3))


class Reconciled(unittest.TestCase):  # 계획 41 r3d AG9 (나) — read_events 판 인자(Gold 가 마지막 대사가 확인한 판을 읽는다)
    def test_reconciled_version(self):
        lake = lake_with_g1(self)
        lake.set("metaverse", 1)
        checked, reconcile_version = lake.version(lake.settings.silver), lake.version(lake.settings.reconcile)
        lake.add(synthetic.value(E[3]))
        lake.run()  # 대사 전 — 본 표는 앞섰고 마지막 대사는 그대로
        latest = lake.version(lake.settings.silver)
        self.assertGreater(latest, checked)
        view = pointers.read_events(spark, str(lake.root), "metaverse", pointers.RECONCILED)
        self.assertEqual((view.generation, view.version, view.reconcile_version, view.frame.count()), (1, checked, reconcile_version, 3))
        self.assertEqual(sorted(map(tuple, view.frame.collect())), silver_rows(lake.settings.silver, checked))
        default = lake.view()  # LATEST(기본) 은 그대로 — 대사 판이 없다
        self.assertEqual((default.version, default.reconcile_version, default.frame.count()), (latest, None, 4))
        self.assertEqual(pointers.read_events(spark, str(lake.root), "metaverse", pointers.LATEST).version, latest)
        self.assertTrue(lake.reconcile().ok)  # 다음 대사가 따라오면 그 판
        view = pointers.read_events(spark, str(lake.root), "metaverse", pointers.RECONCILED)
        self.assertEqual((view.version, view.reconcile_version, view.frame.count()), (latest, lake.version(lake.settings.reconcile), 4))

    def test_reconciled_follows_the_pointer_generation(self):  # 세대와 그 세대 대사 판이 같은 포인터 판에서 나온다
        lake = lake_with_g1(self)
        lake.set("metaverse", 1)
        self.assertTrue(lake.cycle(2).ok)
        g2 = (lake.version(lake.settings.silver), lake.version(lake.settings.reconcile))
        lake.set("metaverse", 2)
        view = pointers.read_events(spark, str(lake.root), "metaverse", pointers.RECONCILED)
        self.assertEqual((view.generation, view.version, view.reconcile_version, view.path), (2, *g2, lake.settings.silver))

    def test_reconciled_refusals(self):
        lake = lake_with_g1(self)
        row = lake.set("metaverse", 1)
        good = json.loads(lake.history(lake.settings.reconcile)[-1][1])

        def refused(needle):
            with self.assertRaisesRegex(pointers.PointerError, needle):
                pointers.read_events(spark, str(lake.root), "metaverse", pointers.RECONCILED)
            self.assertEqual(lake.view().version, lake.version(lake.settings.silver))  # LATEST 는 대사 표를 보지 않는다

        lake.summary(lake.settings.reconcile, {k: v for k, v in good.items() if k != "checkpoint_id"})
        refused(r"대사 요약에 \['checkpoint_id'\] 이\(가\) 없다$")
        lake.summary(lake.settings.reconcile, {**good, "silver_version": str(good["silver_version"])})
        refused(r"대사 요약에 \['silver_version'\] 이\(가\) 없다$")
        lake.summary(lake.settings.reconcile, {**good, "checkpoint_id": "other"})
        refused(re.escape(f"대사 요약의 checkpoint 'other' ≠ 포인터 행의 {row['checkpoint_id']!r}"))
        lake.summary(lake.settings.reconcile, {**good, "silver_version": lake.version(lake.settings.silver) + 1})
        refused("판 범위")
        lake.summary(lake.settings.reconcile, {**good, "silver_version": -1})
        refused("판 범위")
        shutil.rmtree(lake.settings.reconcile.removeprefix("file://"))
        refused(f"^포인터 g1 의 대사 결과 표가 없다: {re.escape(lake.settings.reconcile)}$")
        with self.assertRaisesRegex(ValueError, "판 인자는 LATEST·RECONCILED 다: 3"):
            pointers.read_events(spark, str(lake.root), "metaverse", 3)


class Set(unittest.TestCase):  # 시험 6·7·7a·7b·10
    def test_switch_and_roll_back(self):  # 6 — 15 끝 조건: 포인터를 옛 세대로 돌리면 읽기도 따라간다
        lake = lake_with_g1(self)
        lake.set("metaverse", 1)
        self.assertTrue(lake.cycle(2).ok)
        g1, g2 = (control.silver_paths(str(lake.root), "metaverse", n).silver for n in (1, 2))
        self.assertNotEqual(silver_rows(g1), silver_rows(g2))  # silver_written_at 이 다르다
        lake.set("metaverse", 2, reason="새 세대")
        view = lake.view()
        self.assertEqual((view.generation, view.path, sorted(map(tuple, view.frame.collect()))), (2, g2, silver_rows(g2)))
        lake.set("metaverse", 1, reason="되돌리기", by="당번")
        view = lake.view()
        self.assertEqual((view.generation, view.path, sorted(map(tuple, view.frame.collect()))), (1, g1, silver_rows(g1)))
        self.assertEqual([(r["domain"], r["generation"], r["set_by"], r["reason"]) for r in lake.pointer_rows()], [("metaverse", 1, "당번", "되돌리기")])
        history = lake.history(lake.pointer())
        self.assertEqual([op for op, _ in history], ["WRITE", "WRITE", "WRITE"])
        self.assertEqual([(m["from_generation"], m["to_generation"], m["set_by"], m["reason"]) for m in (json.loads(m) for _, m in history)],
                         [(None, 1, BY, WHY), (1, 2, BY, "새 세대"), (2, 1, "당번", "되돌리기")])

    def assert_refused(self, lake, needles, **kwargs):
        domain = kwargs.pop("domain", "metaverse")
        version = lake.version(lake.pointer(domain))
        with self.assertRaises(pointers.PointerError) as refused:
            lake.set(domain, kwargs.pop("n", 1), **kwargs)
        for needle in needles:
            self.assertRegex(str(refused.exception), needle)
        self.assertEqual(lake.version(lake.pointer(domain)), version)
        return str(refused.exception)

    def test_refusals(self):  # 7
        lake = Lake(self)
        lake.add(synthetic.value(E[0]), synthetic.value(E[1]))
        self.assertEqual(self.assert_refused(lake, ["본 표가 없다", "대사 결과 표가 없다"]).count(" / "), 1)  # 사유를 모두 적는다
        lake.generation(1)
        lake.run()
        self.assert_refused(lake, ["^포인터를 바꾸지 않는다\\(domain=metaverse generation=1\\): 대사 결과 표가 없다"])
        lake.reconcile()
        lake.sql(f"DELETE FROM {{silver}} WHERE event_id = '{E[0]}'")  # 대사 뒤 본 표에 쓰기
        self.assert_refused(lake, [f"마지막 대사가 읽은 본 표 판 {lake.version(lake.settings.silver) - 1} ≠ 지금 판 {lake.version(lake.settings.silver)}"])
        self.assertFalse(lake.reconcile().ok)  # Bronze 의 E0 가 Silver 에 없다
        self.assertEqual(self.assert_refused(lake, ["마지막 대사\\(판 1\\)가 mismatch 다"]).count(" / "), 0)
        lake.summary(lake.settings.reconcile, {"status": "match"})  # 요약 키가 모자람(옛 판 대사 등)
        self.assert_refused(lake, [r"대사 요약에 \['silver_version', 'bronze_version', 'checkpoint_id', 'silver_release', 'bronze_table'\] 이\(가\) 없다"])
        spark.range(1).write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(lake.settings.reconcile)  # 요약 없는 커밋
        self.assert_refused(lake, ["대사 요약에 \\['status'"])
        bad = lake.fake("ads", 1, spark.read.format("delta").load(lake.settings.silver).withColumn("note", F.lit("x")))
        self.assert_refused(lake, [f"{re.escape(bad.silver)} 의 열이 이 판의 표 모양과 다르다\\(더 있음 \\[\\('note', 'string'\\)\\]"], domain="ads")
        for bad_args in ({"by": " "}, {"reason": ""}, {"bronze_changed": ""}):
            with self.subTest(bad_args=bad_args), self.assertRaises(ValueError):
                lake.set("metaverse", 1, **bad_args)

    def test_catch_up(self):  # 7a (D5) — 예약을 내린 옛 세대로는 되돌리지 않는다, 따라잡은 회차 뒤에는 된다
        lake = lake_with_g1(self)
        lake.set("metaverse", 1)
        lake.add(synthetic.value(E[3]))
        self.assertTrue(lake.cycle(2).ok)
        lake.set("metaverse", 2)
        g2_bronze = lake.version(lake.settings.bronze)
        lake.generation(1)
        self.assert_refused(lake, [f"따라잡지 않았다: 마지막 대사의 Bronze 판 {g2_bronze - 1} < 지금 포인터 g2 의 {g2_bronze}\\(같은 Bronze 표\\)"])
        self.assert_refused(lake, ["따라잡지 않았다", "--bronze-changed 는 따라잡음을 견줄 수 없을 때만"], bronze_changed="급함")
        self.assertTrue(lake.cycle(1).ok)  # 예약을 올려 따라잡음
        lake.set("metaverse", 1, reason="되돌리기")
        self.assertEqual(lake.view().frame.count(), 4)
        lake.set("metaverse", 1, reason="같은 세대 다시")  # 같은 판끼리는 지난다(사유·시각 기록)
        self.assertEqual(len(lake.history(lake.pointer())), 4)

    def test_bronze_changed(self):  # 7b (D5) — 견줄 수 없을 때만 우회, 이유는 커밋에
        lake = lake_with_g1(self)
        self.assert_refused(lake, ["--bronze-changed 는 따라잡음을 견줄 수 없을 때만"], bronze_changed="처음")  # 처음 쓰는 포인터는 따라잡음을 건너뜀
        lake.set("metaverse", 1)
        moved = f"{lake.root}/bronze/metaverse/outbox_events_v2"
        spark.read.format("delta").load(lake.base.bronze).write.format("delta").save(moved)
        self.assertTrue(lake.cycle(2, bronze=moved).ok)
        self.assert_refused(lake, [f"따라잡음을 견줄 수 없다: 지금 포인터 g1 의 대사 Bronze 표 '{re.escape(lake.base.bronze)}' 판 \\d+ · 대상 Bronze 표 '{re.escape(moved)}'"], n=2)
        lake.set("metaverse", 2, bronze_changed="MVR-3a Bronze 경로 바뀜")
        self.assertEqual(json.loads(lake.history(lake.pointer())[-1][1])["bronze_changed"], "MVR-3a Bronze 경로 바뀜")
        self.assertIn("bronze_changed=yes", lake.logs[0])
        self.assertEqual(lake.pointer_rows()[0]["bronze_table"], moved)
        self.assertTrue(lake.cycle(3, bronze=moved + "/").ok)  # 끝 '/' 만 다르면 같은 표
        self.assert_refused(lake, ["--bronze-changed 는 따라잡음을 견줄 수 없을 때만"], n=3, bronze_changed="필요 없음")
        lake.set("metaverse", 3)
        current = control.silver_paths(str(lake.root), "metaverse", 3).reconcile
        lake.summary(current, {k: v for k, v in json.loads(lake.history(current)[-1][1]).items() if k != "bronze_table"})  # 지금 세대 요약에 키 없음(이 코드 전 회차)
        self.assert_refused(lake, ["따라잡음을 견줄 수 없다: 지금 포인터 g3 의 대사 Bronze 표 None 판 \\d+ "], n=2)
        lake.set("metaverse", 2, bronze_changed="키 없음")

    def test_command_line(self):  # 10
        lake = lake_with_g1(self)
        environ = {"SILVER_LAKE_ROOT": str(lake.root)}
        argv = ["set", "--domain", "metaverse", "--generation", "1", "--by", BY, "--reason", WHY]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(SparkSession, "stop"), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(pointers.main(argv, environ), 0)
            self.assertEqual(pointers.main(["show", "--domain", "metaverse"], environ), 0)
            self.assertEqual(pointers.main(["set", "--domain", "metaverse", "--generation", "2", "--by", BY, "--reason", WHY], environ), 1)
            self.assertEqual(pointers.main(argv[:4] + ["0"] + argv[5:], environ), 2)
            self.assertEqual(pointers.main(["show", "--domain", "metaverse"], {}), 2)
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("silver pointer domain=metaverse from=None to=1 "), lines)
        row = lake.pointer_rows()[0]
        self.assertRegex(lines[1], f"^silver pointer version=0 domain=metaverse generation=1 set_at=\\d{{4}}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\d\\.\\d{{6}}Z set_by={BY} "
                                   f"reason={WHY} reconcile_version={row['reconcile_version']} .* bronze_version={row['bronze_version']}$")
        self.assertEqual(len(lines), 3)
        self.assertRegex(lines[2], '^silver pointer history version=0 at=\\S+Z operation=WRITE metadata=\\{.*"to_generation": 1')
        self.assertRegex(err.getvalue(), "^silver pointer refused 포인터를 바꾸지 않는다\\(domain=metaverse generation=2\\): 본 표가 없다")
        self.assertIn("silver pointer error 포인터: generation 은 1 이상의 정수다: '0'", err.getvalue())
        self.assertIn("silver pointer error 포인터 도구 설정이 없다(기본값 없음): SILVER_LAKE_ROOT", err.getvalue())
        self.assertFalse(spark.sparkContext._jsc.sc().isStopped())


class RealAdsAdapter(unittest.TestCase):  # 11 — 계획 §7: 실제 광고 어댑터 출력(SL-8 끝에서 끝 시험의 잡 경로·입력)을 광고 포인터로 읽는다
    def test_ads_generation(self):
        lake = ads_e2e.Lake(self)
        lake.add_pairs([pair for i, (pair, expected) in enumerate(ads_e2e.INPUT) if expected is None and i != ads_e2e.SAME_ID_OTHER_CONTENT])
        lake.run()
        self.assertTrue(reconcile.reconcile(spark, lake.settings, release=RELEASE, quarantine=[], log=lake.logs.append).ok, lake.logs)
        pointers.set_generation(spark, str(lake.root), "ads", 1, by=BY, reason=WHY, log=lake.logs.append)
        view = pointers.read_events(spark, str(lake.root), "ads")
        self.assertEqual(view[1:], (0, "ads", 1, lake.settings.silver, lake.version(lake.settings.silver), None))
        self.assertEqual(view.frame.columns, SILVER)
        self.assertEqual(sorted(map(tuple, view.frame.collect())), silver_rows(lake.settings.silver))
        self.assertEqual({r["domain"] for r in view.frame.collect()}, {"ads"})
        self.assertGreater(view.frame.count(), 0)
        self.assertEqual(framework.table_state(spark, control.pointer_path(str(lake.root), "metaverse"))[0], -1)  # 다른 원천 포인터 표는 없다


if __name__ == "__main__":
    unittest.main()
