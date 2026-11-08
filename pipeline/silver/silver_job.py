"""Silver 잡 입구(계획 15 §1·§2 SL-3c·§7): `silver_job.py --domain metaverse --generation 1`.

Bronze Delta 를 readStream(`maxFilesPerTrigger` — SL-3c 결정 S3, `maxBytesPerTrigger` — SL-5 D3)·`trigger(availableNow=True)` 로 한 번 돌고, foreachBatch 마다 framework.process_batch 를 부른다.
- 설정은 환경변수다(기본값 없음 — control.job_settings). 넘김 목록 파일·코드 뿌리(silver_release)도 설정으로 받는다.
  s3a 경로면 S3 연결 설정 넷도 필요하다(control.s3_settings — 키는 파일에서 읽어 conf 로만).
- 잡 코드가 Delta 확장과 `spark.sql.session.timeZone=UTC` 를 스스로 정한다 — 운영 SparkApplication 에는 제출 스크립트가 없다.
- 시작 전에 framework.prepare(한 프로세스 한 쿼리·시작 검사 ①~④·표 만들기)를 지나야 한다.
- 로컬 lock 은 래퍼 run-silver.sh(lockf), 예약은 launchd 견본 local.pipeline.silver-<원천>.plist(SL-5 — 15 §9 33). 운영은 ScheduledSparkApplication `concurrencyPolicy: Forbid`.
"""
import argparse
import importlib
import os
import sys
from pathlib import Path

from pyspark.sql import SparkSession

from pipeline.silver import control, framework


def run(spark, settings, adapt, lines, release, *, log=print, broadcast_limit=framework.BROADCAST_LIMIT):
    """스트림 쿼리 하나를 끝까지 돈다(availableNow). 배치가 실패하면(멈춤·결함) awaitTermination 이 예외를 낸다. 반환: 끝난 쿼리."""
    spark.conf.set("spark.sql.session.timeZone", "UTC")  # event_date = produced_at 의 UTC 날짜(배치 세션은 쿼리 시작 때 이 세션을 복제한다)
    known = framework.prepare(spark, settings)
    state = {"id": None}

    def checkpoint():
        if state["id"] is None:  # 첫 배치 — 처음 도는 쿼리는 시작하며 metadata 를 만든다(첫 foreachBatch 전, 실측)
            ident = framework.checkpoint_state(spark, settings.checkpoint)[0]
            if ident is None or (known is not None and ident != known):
                raise control.IdentityError(f"checkpoint 식별자가 시작 때와 다르다: 시작 {known!r} · 지금 {ident!r}")
            state["id"] = ident
        return state["id"]

    ctx = framework.BatchContext(settings, adapt, lines, release, log, broadcast_limit, checkpoint)
    query = (spark.readStream.format("delta").option("maxFilesPerTrigger", settings.max_files).option("maxBytesPerTrigger", settings.max_bytes)
             .load(settings.bronze)
             .writeStream.option("checkpointLocation", settings.checkpoint).trigger(availableNow=True)
             .foreachBatch(lambda frame, batch_id: framework.process_batch(ctx, frame, batch_id)).start())
    query.awaitTermination()
    return query


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(description="Silver 잡 — 원천 하나·세대 하나")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--generation", type=int, required=True)
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    settings = control.job_settings(environ, args.domain, args.generation)  # Spark 전에 검사
    s3 = control.s3_settings(environ, settings)
    lines = control.load_acknowledged(Path(settings.acknowledged).read_bytes(), settings.domain)
    release = control.silver_release(settings.code_root)
    adapter = importlib.import_module(f"pipeline.silver.adapters.{settings.domain}")
    builder = (SparkSession.builder.appName(f"silver-{settings.domain}-g{settings.generation}")
               .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
               .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
               .config("spark.sql.session.timeZone", "UTC"))
    if s3 is not None:  # 자격 증명은 파일에서 읽어 conf 로만 넘긴다(명령행·로그에 남기지 않는다 — Bronze s3a_session 과 같음)
        builder = (builder.config("spark.hadoop.fs.s3a.endpoint", s3["endpoint"]).config("spark.hadoop.fs.s3a.endpoint.region", s3["region"])
                   .config("spark.hadoop.fs.s3a.path.style.access", "true")
                   .config("spark.hadoop.fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")
                   .config("spark.hadoop.fs.s3a.access.key", Path(s3["access_key_file"]).read_text().strip())
                   .config("spark.hadoop.fs.s3a.secret.key", Path(s3["secret_key_file"]).read_text().strip()))
    spark = builder.getOrCreate()
    try:
        print(f"silver start domain={settings.domain} generation={settings.generation} release={release} acknowledged_lines={len(lines)}", flush=True)
        run(spark, settings, adapter.adapt, lines, release, log=lambda line: print(line, flush=True))
    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
