"""옛 판 파일 정리 잡 입구(계획 temp/pipeline/45_vacuum-plan.md r2 — DEP3-VAC): `vacuum_job.py --domain ads`.

- 행은 지우지 않는다. 표가 쓰지 않는 옛 판 데이터 파일 가운데 표 속성 `delta.deletedFileRetentionDuration`(30일 — Silver control.DELETED_FILE_RETENTION)이
  지난 것만 Delta VACUUM(FULL)이 지운다(14 Q4). Delta 4.3.1 은 RETAIN(0 아님)을 무시하고 이 속성을 쓰므로 RETAIN 을 주지 않는다(계획 §1 ①).
  속성이 30일이 아닌 표(이 코드 전에 만든 표)는 지우지 않고 거부한다 — 기본 7일로 지우는 일이 없게.
- 표에 커밋을 쓰지 않는다: `spark.databricks.delta.vacuum.logging.enabled=false`(운영 s3a 의 기본과 같은 값을 적어 file:// 시험도 같은 길 — §1 ③).
  VACUUM 이 `_delta_log/_last_vacuum_info` 하나는 덮어쓰지만 커밋이 아니고, 쓰지 못해도 VACUUM 은 계속된다(§1 ⑪ — 운영 정리 키에는 쓰기가 없다).
- 지운 수 = 바로 앞 `VACUUM … DRY RUN` 의 후보 수(VACUUM 결과에는 수가 없다 — §1 ⑨).
- 운영은 ScheduledSparkApplication 하루 한 번(`45 3 * * *` UTC), `concurrencyPolicy: Forbid`. 로컬 정기 실행은 두지 않는다.
- 종료 코드: 0 = 모든 표를 치웠거나 표가 없음, 1 = 거부·오류가 하나라도 있음(나머지 표는 끝까지 치운다), 2 = 설정(Spark 전).
"""
import argparse
import json
import os
import sys
import time

from pipeline.silver import control as silver_control, framework as silver_framework, silver_job
from pipeline.vacuum import control

RETENTION_PROPERTY = "delta.deletedFileRetentionDuration"
# 표를 치우기 전 세션 설정. 첫 줄은 동작(커밋을 쓰지 않음 — 모듈 설명), 나머지 둘은 속도 조정값이다(동작 같음):
# VACUUM 은 폴더 나열을 parallelPartitionDiscovery.parallelism(기본 10000)개로 나눠, 파일 몇 개인 표도 태스크 약 9만 개·local[1] 약 20초였다
# (8·8 이면 약 2초 — 계획 45 코드 Step 측정). ponytail: 운영 executor 코어가 늘면 함께 올린다.
SESSION = {"spark.databricks.delta.vacuum.logging.enabled": "false", "spark.sql.sources.parallelPartitionDiscovery.parallelism": "8",
           "spark.sql.shuffle.partitions": "8"}


def _children(spark, path):
    """폴더 바로 아래 폴더 이름(없는 폴더면 빈 목록). Hadoop FS 로 읽어 file://·s3a:// 가 같다."""
    jvm = spark.sparkContext._jvm
    folder = jvm.org.apache.hadoop.fs.Path(path)
    fs = folder.getFileSystem(spark.sparkContext._jsc.hadoopConfiguration())
    if not fs.exists(folder):
        return []
    return sorted(status.getPath().getName() for status in fs.listStatus(folder) if status.isDirectory())


def targets(spark, root, domain):
    """(정리할 표 경로 목록, 규칙 밖 이름의 폴더 경로 목록) — control 모듈 설명."""
    unknown, numbers = [], set()
    for parent in control.silver_parents(root, domain):
        found = control.generations(_children(spark, parent))
        numbers.update(found.numbers)
        unknown += [f"{parent}/{name}" for name in found.unknown]
    base = control.gold_dir(root, domain)
    tables = control.gold_tables(root, domain, _children(spark, base))
    unknown += [f"{base}/{name}" for name in tables.unknown]
    gold = {}
    for name in tables.numbers:
        folder = control.table_dir(root, domain, name)
        found = control.table_versions(_children(spark, folder))
        gold[name] = found.numbers
        unknown += [f"{folder}/{child}" for child in found.unknown]
    return control.targets(root, domain, sorted(numbers), gold), unknown


def vacuum_table(spark, path, retention):
    """표 하나를 치운다. 반환 ("table", 지운 수) · ("refused", 표의 속성 값 또는 None) · ("skip", None — Delta 표가 없음).
    `retention`: 표 속성이 이 글자와 같아야 치운다(잡은 늘 Silver control.DELETED_FILE_RETENTION)."""
    for key, value in SESSION.items():
        spark.conf.set(key, value)
    if silver_framework.table_state(spark, path)[0] < 0:
        return "skip", None
    value = spark.sql(f"DESCRIBE DETAIL delta.`{path}`").first()["properties"].get(RETENTION_PROPERTY)
    if value != retention:
        return "refused", value
    files = spark.sql(f"VACUUM delta.`{path}` DRY RUN").count()
    spark.sql(f"VACUUM delta.`{path}`").collect()
    return "table", files


def run(spark, root, domain, log=print):
    """원천 하나의 정리 한 회차. 반환: 거부·오류가 없었나."""
    started = time.monotonic()
    paths, unknown = targets(spark, root, domain)
    retention = silver_control.DELETED_FILE_RETENTION
    log(f"vacuum start domain={domain} retention={json.dumps(retention)} tables={len(paths)}")
    for path in unknown:
        log(f"vacuum skip path={path} reason=unknown_name")
    deleted = refused = errors = 0
    for path in paths:
        begun = time.monotonic()
        try:
            kind, value = vacuum_table(spark, path, retention)
        except Exception as error:  # 한 표의 문제로 다른 표의 정리를 멈추지 않는다 — rc 1
            errors += 1
            log(f"vacuum error path={path} error={type(error).__name__}: {(str(error).splitlines() or [''])[0]}")
            continue
        if kind == "skip":
            log(f"vacuum skip path={path} reason=no_table")
        elif kind == "refused":
            refused += 1
            log(f"vacuum refused path={path} retention={json.dumps(value)}")
        else:
            deleted += value
            log(f"vacuum table path={path} files={value} seconds={time.monotonic() - begun:.3f}")
    log(f"vacuum end domain={domain} deleted_files={deleted} tables={len(paths)} refused={refused} errors={errors} "
        f"seconds={time.monotonic() - started:.3f}")
    return not refused and not errors


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(description="옛 판 파일 정리 — 원천 하나")
    parser.add_argument("--domain", required=True)
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    try:
        settings = control.job_settings(environ, args.domain)
    except ValueError as error:
        print(f"vacuum error {error}", file=sys.stderr)
        return 2
    spark = silver_job.session(f"vacuum-{settings.domain}", settings.s3)
    try:
        ok = run(spark, settings.root, settings.domain, log=lambda line: print(line, flush=True))
    finally:
        spark.stop()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
