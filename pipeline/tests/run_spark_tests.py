"""Spark 시험 실행기 — pipeline/tests 아래 spark_*.py 를 한 SparkSession 에서 돈다. 실패가 있거나 시험이 0개면 종료 코드 1.

빠른 단위 시험(클러스터 코어를 쓰지 않음):
    SPARK_MASTER='local[1]' pipeline/silver/spark-submit-silver.sh pipeline/tests/run_spark_tests.py
spark_*.py 는 `python -m unittest discover pipeline/tests` 의 기본 패턴(test*.py)에 걸리지 않는다(pyspark 가 없는 환경에서 돌기 때문).
"""
import os
import sys
import time
import unittest
from pathlib import Path

os.environ["TZ"] = "UTC"  # collect 가 timestamp 를 이 프로세스 시간대의 naive datetime 으로 돌려준다 — 시험 비교를 UTC 로
time.tzset()

from pyspark.sql import SparkSession  # noqa: E402


def main():
    spark = (SparkSession.builder.appName("pipeline_spark_tests").config("spark.sql.session.timeZone", "UTC")
             .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")  # 잡(silver_job.main)과 같은 Delta 설정 — spark_job 의 file:// 표
             .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
             .config("spark.sql.shuffle.partitions", "2").getOrCreate())
    spark.sparkContext.setLogLevel("WARN")  # INFO 로그가 시험 결과 줄 사이에 끼지 않게
    here = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(here), pattern="spark_*.py", top_level_dir=str(here))
    print(f"spark={spark.version} master={spark.sparkContext.master} ansi={spark.conf.get('spark.sql.ansi.enabled')} tests={suite.countTestCases()}", flush=True)
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
    spark.stop()
    return 0 if result.wasSuccessful() and suite.countTestCases() else 1


if __name__ == "__main__":
    sys.exit(main())
