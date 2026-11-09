"""Gold 잡 입구(계획 temp/pipeline/41_ads-gold-plan.md r3d §2.1): `gold_job.py --domain ads`.

- 설정은 환경변수다(기본값 없음 — control.job_settings): GOLD_LAKE_ROOT(Silver 와 같은 뿌리), GOLD_CODE_ROOT(gold_release). s3a 뿌리면 S3 넷
  (pipeline/silver/control.s3_settings — 키는 파일에서 읽어 conf 로만). 설정 오류는 Spark 전에 rc 2.
- 원천의 표 목록은 `pipeline.gold.tables.<원천>` 모듈의 TABLES·DELAY_SECONDS(판정 지연 상한 — 광고는 보류 300초 + 다시 판정 30초).
- 세션은 Silver 잡과 같은 설정(silver_job.session — Delta 확장·세션 시간대 UTC).
- 운영은 광고 Silver 예약 + 10분, ScheduledSparkApplication `concurrencyPolicy: Forbid`(§2.1, Q1). 로컬 정기 실행은 두지 않는다.
- 종료 코드: 0 = 썼다, 3 = 확인(AG7)이 어긋나 쓰지 않았다, 2 = 설정, 그 밖(포인터·대사 표·돈 열 범위 등 예외)은 1.
"""
import argparse
import importlib
import os
import sys

from pipeline.gold import control, framework
from pipeline.silver import control as silver_control, silver_job

CHECK_RC = 3


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(description="Gold 잡 — 원천 하나")
    parser.add_argument("--domain", required=True)
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    try:
        settings = control.job_settings(environ, args.domain)
        s3 = silver_control.s3_settings(environ, (settings.root,))
        release = control.gold_release(settings.code_root)
    except (ValueError, FileNotFoundError) as error:
        print(f"gold error {error}", file=sys.stderr)
        return 2
    source = importlib.import_module(f"pipeline.gold.tables.{settings.domain}")
    spark = silver_job.session(f"gold-{settings.domain}", s3)
    log = lambda line: print(line, flush=True)  # noqa: E731
    try:
        log(f"gold start domain={settings.domain} release={release} tables={[t.name for t in source.TABLES]}")
        result = framework.run(spark, settings.root, settings.domain, source.TABLES, release, delay_seconds=source.DELAY_SECONDS, log=log)
    finally:
        spark.stop()
    return 0 if result.ok else CHECK_RC


if __name__ == "__main__":
    sys.exit(main())
