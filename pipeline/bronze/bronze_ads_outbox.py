"""Bronze: Kafka `ads.outbox.events` → Delta(RustFS/S3) 원본 그대로, 1분마다. 상시 실행 쿼리 하나(checkpoint 경로당 하나만 띄운다).

JSON을 해석하지 않는다 — key·value 바이트, 헤더, 위치만 쌓는다(해석·중복 제거는 Silver).
"""
import hashlib
import os
import tempfile
from pathlib import Path

from pyspark.sql import SparkSession, functions as F
from pyspark.sql.streaming import StreamingQueryListener

BUCKET = os.environ.get("BRONZE_BUCKET", "ads-lake")
TABLE = os.environ.get("BRONZE_TABLE", f"s3a://{BUCKET}/bronze/ads_outbox_events")
CHECKPOINT = os.environ.get("BRONZE_CHECKPOINT", f"s3a://{BUCKET}/checkpoints/bronze_ads_outbox")
TOPIC = os.environ.get("BRONZE_TOPIC", "ads.outbox.events")  # 종단 시험은 시험 전용 토픽·표·checkpoint 를 쓴다(설계 §11.1)
TRIGGER = os.environ.get("BRONZE_TRIGGER", "1 minute")
KAFKA = os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:9092,127.0.0.1:9192,127.0.0.1:9292")  # 브로커 3대(로컬 3노드) — 한 대가 죽어도 새로 연결
# 설계 R6-3: 긴 정지 뒤 첫 배치가 적체 전부를 읽다 메모리가 모자라지 않게 상한을 둔다. 10,000은 임시값 — E-B4(P0-8) 실측으로 정한다
MAX_OFFSETS = os.environ.get("BRONZE_MAX_OFFSETS_PER_TRIGGER", "10000")
RELEASE = "bronze_ads_outbox@" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]


def s3a_session(app):
    """S3A·Delta 설정을 붙인 세션. 자격 증명은 파일에서 읽어 conf로만 넘긴다(명령행·로그에 남기지 않는다)."""
    key = Path(os.environ.get("S3_ACCESS_KEY_FILE", "/opt/rustfs/config/access-key")).read_text().strip()
    secret = Path(os.environ.get("S3_SECRET_KEY_FILE", "/opt/rustfs/config/secret-key")).read_text().strip()
    return (SparkSession.builder.appName(app)
            .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
            .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
            .config("spark.sql.warehouse.dir", str(Path(tempfile.gettempdir()) / "ads_pipeline_lab" / "spark-warehouse"))  # 현재 폴더에 빈 spark-warehouse/ 를 만들지 않게
            .config("spark.sql.session.timeZone", "UTC")  # ingest_date = ingested_at 의 UTC 날짜
            .config("spark.hadoop.fs.s3a.endpoint", os.environ.get("S3_ENDPOINT", "http://127.0.0.1:9000"))
            .config("spark.hadoop.fs.s3a.endpoint.region", os.environ.get("S3_REGION", "ap-northeast-2"))
            .config("spark.hadoop.fs.s3a.path.style.access", "true")
            .config("spark.hadoop.fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")
            .config("spark.hadoop.fs.s3a.access.key", key)
            .config("spark.hadoop.fs.s3a.secret.key", secret)
            .getOrCreate())


class ProgressLog(StreamingQueryListener):
    def onQueryStarted(self, event):
        print(f"bronze started id={event.id} run={event.runId} release={RELEASE}", flush=True)

    def onQueryProgress(self, event):
        p = event.progress
        print(f"bronze batch={p.batchId} rows={p.numInputRows} duration_ms={p.batchDuration} at={p.timestamp}", flush=True)

    def onQueryIdle(self, event):
        pass

    def onQueryTerminated(self, event):
        print(f"bronze terminated exception={event.exception}", flush=True)


if __name__ == "__main__":
    spark = s3a_session("bronze_ads_outbox")
    spark.streams.addListener(ProgressLog())
    query = (spark.readStream.format("kafka")
             .option("kafka.bootstrap.servers", KAFKA)
             .option("subscribe", TOPIC)
             .option("startingOffsets", "earliest")  # 첫 실행만. 이후는 checkpoint
             .option("kafka.isolation.level", "read_committed")
             .option("includeHeaders", "true")
             .option("maxOffsetsPerTrigger", MAX_OFFSETS)
             .option("failOnDataLoss", "true")  # 기본값을 드러내 둔다: 보존 만료 등으로 offset이 사라지면 건너뛰지 않고 멈춘다(설계 K5·RB-5)
             .load()
             .select(F.col("key").alias("key_binary"), F.col("value").alias("value_binary"), "headers", "topic", "partition", "offset",
                     F.col("timestamp").alias("kafka_timestamp"), F.col("timestampType").alias("kafka_timestamp_type"),
                     F.current_timestamp().alias("ingested_at"), F.lit(RELEASE).alias("pipeline_release"))
             .withColumn("ingest_date", F.to_date("ingested_at"))
             .writeStream.format("delta")
             .queryName("bronze_ads_outbox")
             .option("checkpointLocation", CHECKPOINT)
             .partitionBy("ingest_date")
             .trigger(processingTime=TRIGGER)
             .start(TABLE))
    query.awaitTermination()
