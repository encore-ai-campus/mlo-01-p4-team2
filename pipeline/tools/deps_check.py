"""의존 jar 세트(extraClassPath) 확인: 클래스 출처, RustFS s3a Delta 쓰기·읽기, Bronze 표 읽기, Kafka 배치 읽기. 시험 경로는 끝나면 지운다."""
import time
from bronze_ads_outbox import BUCKET, KAFKA, TABLE, s3a_session

spark = s3a_session("deps_check")
jvm = spark.sparkContext._jvm


def origin(cls):
    c = jvm.java.lang.Class.forName(cls)
    src = c.getProtectionDomain().getCodeSource()
    return src.getLocation().toString() if src else "(bootstrap)"


for cls in ("org.apache.hadoop.fs.s3a.S3AFileSystem", "org.apache.spark.sql.delta.DeltaLog",
            "org.apache.spark.sql.kafka010.KafkaSourceProvider", "software.amazon.awssdk.services.s3.S3Client",
            "com.fasterxml.jackson.databind.ObjectMapper"):
    print(f"DEPS_ORIGIN {cls} {origin(cls)}", flush=True)
print("DEPS_JACKSON", jvm.com.fasterxml.jackson.databind.cfg.PackageVersion.VERSION.toString(), flush=True)

path = f"s3a://{BUCKET}/_verify/deps_check_{int(time.time())}"
spark.range(0, 1000, numPartitions=4).selectExpr("id", "id * 2 AS v").write.format("delta").save(path)
back = spark.read.format("delta").load(path)
print(f"DEPS_WRITE path={path} rows={back.count()} sum_v={back.agg({'v': 'sum'}).collect()[0][0]}", flush=True)
fs = jvm.org.apache.hadoop.fs.Path(path).getFileSystem(spark.sparkContext._jsc.hadoopConfiguration())
print(f"DEPS_CLEANUP deleted={fs.delete(jvm.org.apache.hadoop.fs.Path(path), True)} exists_after={fs.exists(jvm.org.apache.hadoop.fs.Path(path))}", flush=True)

bronze = spark.read.format("delta").load(TABLE)
print(f"DEPS_BRONZE rows={bronze.count()} distinct_offsets={bronze.select('partition', 'offset').distinct().count()}", flush=True)
kafka = (spark.read.format("kafka").option("kafka.bootstrap.servers", KAFKA).option("subscribe", "ads.outbox.events")
         .option("startingOffsets", "earliest").option("endingOffsets", "latest").option("kafka.isolation.level", "read_committed").load())
print(f"DEPS_KAFKA rows={kafka.count()}", flush=True)
print("DEPS_APP", spark.sparkContext.applicationId, flush=True)
spark.stop()
