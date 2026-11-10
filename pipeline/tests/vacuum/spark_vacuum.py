"""pipeline/vacuum 옛 판 파일 정리 잡(계획 temp/pipeline/45_vacuum-plan.md r2 §3.7) — Spark 시험.

광고 lake 하나를 실제 잡 입구 셋(silver_job.main · pointers.main set · gold_job.main 두 번 — 셋 다 silver_job.session)으로 모듈에서 한 번 만들고,
정리 시험은 그 사본에서 돈다. 시계는 운영 코드 분기 없이 다룬다:
(ㄱ) 실제 30일 속성 표에 Delta 가 모르는 파일·옛 판 파일의 수정 시각을 os.utime 으로 옮기고, (ㄴ) 시험이 만든 표에 짧은 속성을 주고 vacuum_table 에 그 값을 넘긴다.
입구가 시험 공용 세션에 얹는 설정(표 속성 기본값·VACUUM 로그)은 매번 지워, 다음 회차가 늘 같은 세션 상태에서 시작한다.
"""
import contextlib
import io
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from pyspark.sql import SparkSession
from pyspark.errors import AnalysisException

from pipeline.gold import control as gold_control, framework as gold_framework, gold_job
from pipeline.gold.tables import ads as tables
from pipeline.silver import control as silver_control, framework as silver_framework, pointers, silver_job
from pipeline.vacuum import vacuum_job
from ads import spark_adapter as sa  # 시험 실행기(run_spark_tests)는 pipeline/tests 를 뿌리로 모듈을 찾는다
from gold import spark_ads as ga
from silver import spark_job as job

REPO = Path(__file__).resolve().parents[3]
DEFAULT = "spark.databricks.delta.properties.defaults.deletedFileRetentionDuration"
RETENTION = silver_control.DELETED_FILE_RETENTION
DAY = 86400
# VACUUM 이 _delta_log 에 새로 남기는 것(리뷰 r1 C1): _last_vacuum_info 하나 — 커밋이 아니다. 점 파일은 Hadoop 로컬 파일 시스템의 체크섬 짝이다(file:// 만, S3 에는 없다)
NEW_IN_LOG = ["._last_vacuum_info.crc", "_last_vacuum_info"]
_MODULE_CLEANUP = []


class _Module:
    """모듈 준비에는 시험 객체가 없다 — 빌려 쓰는 Lake 의 정리를 tearDownModule 로."""

    def addCleanup(self, *args):
        _MODULE_CLEANUP.append(args)


def _clean_session():
    """입구가 시험 공용 세션에 얹은 설정을 모듈 시작 때 값으로 되돌린다(없던 것은 지움 — 셔플 파티션처럼 실행기가 정한 값은 그 값으로)."""
    for key, value in ORIGINAL.items():
        if value is None:
            spark.conf.unset(key)
        else:
            spark.conf.set(key, value)


def setUpModule():
    global spark, BUILT, RCS, OUT, ORIGINAL
    spark = SparkSession.builder.getOrCreate()
    spark.conf.unset(DEFAULT)  # 앞 모듈의 입구 호출(예: spark_pointers 의 pointers.main)이 공용 세션에 남긴 값 — 이 모듈은 "기본값 없음"에서 시작한다
    ORIGINAL = {key: spark.conf.get(key, None) for key in (DEFAULT, *vacuum_job.SESSION)}
    sa.spark = job.spark = ga.spark = spark
    lake = sa.Lake(_Module())
    BUILT = str(lake.root)
    lake.add_pairs(ga.SEGMENTS + ga.DECISIONS + ga.reaction_pairs() + ga.charge_pairs() + [ga.clock(ga.at("16:00"))])
    for name in ("ack.json", "quarantine.json"):
        (lake.root / name).write_text('{"domain": "ads", "lines": []}')
    silver_env = {"SILVER_LAKE_ROOT": BUILT, "SILVER_BRONZE_TABLE": lake.settings.bronze, "SILVER_SOURCE_DB": sa.E2E["db"], "SILVER_SOURCE_SCHEMA": sa.E2E["schema"],
                  "SILVER_SOURCE_TABLE": sa.E2E["table"], "SILVER_ACKNOWLEDGED": f"{BUILT}/ack.json", "SILVER_QUARANTINE": f"{BUILT}/quarantine.json",
                  "SILVER_CODE_ROOT": str(REPO), "SILVER_MAX_FILES_PER_TRIGGER": "100", "SILVER_MAX_BYTES_PER_TRIGGER": "1g"}
    out = io.StringIO()
    with mock.patch.object(SparkSession, "stop"), contextlib.redirect_stdout(out):
        try:
            RCS = (silver_job.main(["--domain", "ads", "--generation", "1"], silver_env),
                   pointers.main(["set", "--domain", "ads", "--generation", "1", "--by", "시험", "--reason", "DEP3-VAC"], {"SILVER_LAKE_ROOT": BUILT}),
                   *(gold_job.main(["--domain", "ads"], {"GOLD_LAKE_ROOT": BUILT, "GOLD_CODE_ROOT": str(REPO)}) for _ in range(2)))  # 두 번째 Gold = 옛 판 파일
        finally:
            _clean_session()
    OUT = out.getvalue()


def tearDownModule():
    for function, *args in _MODULE_CLEANUP:
        function(*args)


def eight(root):
    """잡 입구 셋이 만든 광고 표 여덟: Silver 넷·포인터·Gold 둘·정정."""
    paths = silver_control.silver_paths(root, "ads", 1)
    return [*paths[:4], silver_control.pointer_path(root, "ads"), *(gold_control.table_path(root, "ads", t.name, t.version) for t in tables.TABLES),
            gold_control.corrections_path(root, "ads")]


def copy_lake(test):
    folder = tempfile.mkdtemp(prefix="dep3-vac-")
    test.addCleanup(shutil.rmtree, folder, True)
    shutil.copytree(BUILT, folder, dirs_exist_ok=True)  # copy2 — 수정 시각이 그대로다(Delta 커밋 시각은 로그 파일의 수정 시각)
    return folder


def age(path, days):
    moment = time.time() - days * DAY
    os.utime(path, (moment, moment))


def orphan(table, name, days):
    """표 폴더에 Delta 가 모르는 파일(실패한 쓰기 흉내)을 수정 시각 `days` 일 전으로."""
    path = Path(table) / f"part-{name}.snappy.parquet"
    path.write_bytes(b"orphan")
    age(path, days)
    return path


def table(path, retention=RETENTION, versions=1):
    """시험이 만드는 작은 Delta 표(덮어쓰기 `versions` 번). `retention` 이 None 이면 속성 없이(이 코드 전에 만든 표)."""
    if retention is not None:
        spark.conf.set(DEFAULT, retention)
    try:
        for i in range(versions):
            spark.range(i, i + 3).write.format("delta").mode("overwrite").option("txnAppId", "t").option("txnVersion", i).save(path)
    finally:
        _clean_session()


def state(path):
    """정리 전후로 같아야 하는 것: 판·txn·이력 줄 수·최신 행(collect — count 는 파일 통계로 답할 수 있다)과 _delta_log 목록."""
    rows = sorted(map(repr, spark.read.format("delta").load(path).collect()))
    return (silver_framework.table_state(spark, path)[0], gold_framework._txn_versions(spark, path), spark.sql(f"DESCRIBE HISTORY delta.`{path}`").count(),
            rows), sorted(os.listdir(Path(path) / "_delta_log"))


def vacuum(folder, environ=None):
    out = io.StringIO()
    with mock.patch.object(SparkSession, "stop"), contextlib.redirect_stdout(out):
        try:
            rc = vacuum_job.main(["--domain", "ads"], {"VACUUM_LAKE_ROOT": folder} if environ is None else environ)
        finally:
            _clean_session()
    return rc, out.getvalue().splitlines()


class Built(unittest.TestCase):
    def test_entries_stamp_the_retention_on_every_table(self):  # ① Planner 요청 1 — 입구 셋으로 만든 표 여덟
        self.assertEqual(RCS, (0, 0, 0, 0), OUT)
        for path in eight(BUILT):
            with self.subTest(path=path):
                props = spark.sql(f"DESCRIBE DETAIL delta.`{path}`").first()["properties"]
                self.assertEqual(props.get("delta.deletedFileRetentionDuration"), "interval 30 days")
                self.assertNotIn("delta.logRetentionDuration", props)  # 로그 보존은 기본(30일) — 지운 파일 보존과 같아 호환 검사를 지난다
        self.assertIsNone(spark.conf.get(DEFAULT, None))
        self.assertEqual(len([line for line in OUT.splitlines() if line.startswith("gold table ")]), 4)


class Boundaries(unittest.TestCase):
    def test_thirty_days_rows_versions_and_other_sources(self):  # ②③⑤⑦ — 실제 30일 속성
        folder = copy_lake(self)
        paths = eight(folder)
        for path in paths:
            orphan(path, "old", 31)
            orphan(path, "new", 29)
        gold_paths = paths[5:7]
        stale = []
        for path in gold_paths:  # 두 번째 Gold 가 지운 표시를 남긴 옛 판 파일 — 수정 시각이 31일 전이어도 지운 표시가 새것이라 남는다
            live = {Path(f).name for f in spark.read.format("delta").load(path).inputFiles()}
            stale += [f for f in Path(path).glob("*.parquet") if f.name not in live and not f.name.startswith("part-old") and not f.name.startswith("part-new")]
        self.assertTrue(stale)
        for path in stale:
            age(path, 31)
        g2 = silver_control.silver_paths(folder, "ads", 2).silver
        old_table = gold_control.table_path(folder, "ads", "old_table", 1)  # 리뷰 권고 1 — 표 목록(TABLES)에 없는 Gold 표 폴더
        other = silver_control.silver_paths(folder, "metaverse", 1).silver  # ⑤ 같은 뿌리의 다른 원천
        for path in (g2, old_table, other):
            table(path)
            orphan(path, "old", 31)
        unknown = [f"{folder}/silver/ads/events/g01", f"{folder}/silver/ads/events/tmp", f"{folder}/gold/ads/Bad-Name", f"{folder}/gold/ads/performance_hourly/v01"]
        for path in unknown:
            Path(path).mkdir()
            orphan(path, "old", 31)
        before = {path: state(path) for path in paths + [other]}
        previous = {path: sorted(map(repr, spark.read.format("delta").option("versionAsOf", before[path][0][0] - 1).load(path).collect())) for path in gold_paths}

        rc, lines = vacuum(folder)
        self.assertEqual(rc, 0, lines)
        self.assertEqual(lines[0], f'vacuum start domain=ads retention="interval 30 days" tables={8 + 4 + 1}')
        self.assertEqual(sorted(line for line in lines if "reason=unknown_name" in line), sorted(f"vacuum skip path={p} reason=unknown_name" for p in unknown))
        g2_rest = silver_control.silver_paths(folder, "ads", 2)[1:4]
        self.assertEqual(sorted(line for line in lines if "reason=no_table" in line), sorted(f"vacuum skip path={p} reason=no_table" for p in g2_rest))
        cleaned = sorted(line.split(" seconds=")[0] for line in lines if line.startswith("vacuum table "))
        self.assertEqual(cleaned, sorted(f"vacuum table path={p} files=1" for p in paths + [g2, old_table]))
        self.assertRegex(lines[-1], r"^vacuum end domain=ads deleted_files=10 tables=13 refused=0 errors=0 seconds=\d+\.\d{3}$")
        for path in paths + [g2, old_table]:
            with self.subTest(path=path):
                self.assertFalse((Path(path) / "part-old.snappy.parquet").exists())  # 30일 전 — 지움
                if path in paths:
                    self.assertTrue((Path(path) / "part-new.snappy.parquet").exists())  # 30일 안 — 남김
        self.assertTrue(all(path.exists() for path in stale))
        for path in unknown + [other]:  # 규칙 밖 이름·다른 원천은 열지 않는다
            self.assertTrue((Path(path) / "part-old.snappy.parquet").exists(), path)
        for path in paths + [other]:  # ③ 판·txn·이력·최신 행 그대로, _delta_log 에 새로 생긴 것은 _last_vacuum_info 뿐(리뷰 r1 C1)
            with self.subTest(path=path):
                values, files = state(path)
                self.assertEqual(values, before[path][0])
                expected = before[path][1] if path == other else sorted(before[path][1] + NEW_IN_LOG)
                self.assertEqual(files, expected)
        for path in gold_paths:  # 30일 안의 옛 판은 읽힌다
            self.assertEqual(sorted(map(repr, spark.read.format("delta").option("versionAsOf", before[path][0][0] - 1).load(path).collect())), previous[path])

        out = io.StringIO()  # ③ 정리 뒤 Gold 회차 — 정정 채우기·회차 기록이 그대로 돈다
        with mock.patch.object(SparkSession, "stop"), contextlib.redirect_stdout(out):
            try:
                self.assertEqual(gold_job.main(["--domain", "ads"], {"GOLD_LAKE_ROOT": folder, "GOLD_CODE_ROOT": str(REPO)}), 0, out.getvalue())
            finally:
                _clean_session()
        done = gold_framework._txn_versions(spark, gold_control.corrections_path(folder, "ads"))
        for t, path in zip(tables.TABLES, gold_paths):
            latest = silver_framework.table_state(spark, path)[0]
            self.assertEqual((latest, done[gold_framework.app_id("ads", t)]), (before[path][0][0] + 1, latest))
        self.assertIn("lines=0", out.getvalue())


class ShortRetention(unittest.TestCase):
    def test_old_version_files_go_when_the_tombstone_expires(self):  # ④ (ㄴ) 시험 표의 짧은 속성 — 옛 판 파일 지움, 최신 행·판·txn 그대로
        folder = tempfile.mkdtemp(prefix="dep3-vac-")
        self.addCleanup(shutil.rmtree, folder, True)
        path = f"{folder}/t"
        table(path, retention="interval 1 seconds", versions=3)
        (values, files) = state(path)
        self.assertEqual(len(list(Path(path).glob("*.parquet"))), 3)
        time.sleep(2)
        try:
            self.assertEqual(vacuum_job.vacuum_table(spark, path, "interval 1 seconds"), ("table", 2))
        finally:
            _clean_session()
        self.assertEqual(len(list(Path(path).glob("*.parquet"))), 1)
        self.assertEqual(state(path), (values, sorted(files + NEW_IN_LOG)))
        with self.assertRaisesRegex(AnalysisException, "DELTA_UNSUPPORTED_TIME_TRAVEL_BEYOND_DELETED_FILE_RETENTION_DURATION"):
            spark.read.format("delta").option("versionAsOf", 0).load(path).collect()


class Refusals(unittest.TestCase):
    def test_tables_without_thirty_days_are_left_alone(self):  # ⑥ 이 코드 전에 만든 표(속성 없음)·기본 7일 표는 거부, 다른 표는 치움, rc 1
        folder = tempfile.mkdtemp(prefix="dep3-vac-")
        self.addCleanup(shutil.rmtree, folder, True)
        paths = silver_control.silver_paths(folder, "ads", 1)
        table(paths.silver, retention=None)
        table(paths.errors, retention="interval 1 week")
        table(paths.reconcile)
        for path in paths[:3]:
            orphan(path, "old", 31)
        rc, lines = vacuum(folder)
        self.assertEqual(rc, 1, lines)
        self.assertIn(f"vacuum refused path={paths.silver} retention=null", lines)
        self.assertIn(f'vacuum refused path={paths.errors} retention="interval 1 week"', lines)
        self.assertTrue((Path(paths.silver) / "part-old.snappy.parquet").exists())
        self.assertTrue((Path(paths.errors) / "part-old.snappy.parquet").exists())
        self.assertFalse((Path(paths.reconcile) / "part-old.snappy.parquet").exists())
        self.assertRegex(lines[-1], r"^vacuum end domain=ads deleted_files=1 tables=6 refused=2 errors=0 ")

    def test_an_error_does_not_stop_the_other_tables(self):  # rc 1, 나머지 표는 끝까지
        folder = tempfile.mkdtemp(prefix="dep3-vac-")
        self.addCleanup(shutil.rmtree, folder, True)
        paths = silver_control.silver_paths(folder, "ads", 1)
        table(paths.silver)
        table(paths.errors)
        for path in paths[:2]:
            orphan(path, "old", 31)
        real = vacuum_job.vacuum_table

        def broken(session, path, retention):
            if path == paths.silver:
                raise RuntimeError("일부러\n둘째 줄")
            return real(session, path, retention)

        with mock.patch.object(vacuum_job, "vacuum_table", side_effect=broken):
            rc, lines = vacuum(folder)
        self.assertEqual(rc, 1, lines)
        self.assertIn(f"vacuum error path={paths.silver} error=RuntimeError: 일부러", lines)
        self.assertFalse((Path(paths.errors) / "part-old.snappy.parquet").exists())
        self.assertRegex(lines[-1], r"^vacuum end domain=ads deleted_files=1 tables=6 refused=0 errors=1 ")


class Entry(unittest.TestCase):
    def test_empty_root_and_settings(self):  # ⑧
        folder = tempfile.mkdtemp(prefix="dep3-vac-")
        self.addCleanup(shutil.rmtree, folder, True)
        rc, lines = vacuum(folder)
        self.assertEqual(rc, 0, lines)
        self.assertEqual(lines[:3], ['vacuum start domain=ads retention="interval 30 days" tables=2', f"vacuum skip path={folder}/silver/ads/pointer reason=no_table",
                                     f"vacuum skip path={folder}/gold/ads/corrections reason=no_table"])
        self.assertRegex(lines[3], r"^vacuum end domain=ads deleted_files=0 tables=2 refused=0 errors=0 ")
        err = io.StringIO()
        with mock.patch.object(silver_job, "session") as make, contextlib.redirect_stderr(err):
            for environ in ({}, {"VACUUM_LAKE_ROOT": " /lake"}, {"VACUUM_LAKE_ROOT": "s3a://bucket"}):
                self.assertEqual(vacuum_job.main(["--domain", "ads"], environ), 2)
            self.assertEqual(vacuum_job.main(["--domain", "Ads"], {"VACUUM_LAKE_ROOT": "/lake"}), 2)
            make.assert_not_called()
        self.assertEqual(len(err.getvalue().splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
