"""C3 대조: Bronze Delta ↔ Kafka(read_committed) 위치별 바이트 비교, 중복, 배치·지연, Delta 이력."""
import json

from pyspark.sql import functions as F

from bronze_ads_outbox import KAFKA, TABLE, s3a_session

spark = s3a_session("c3_check")
spark.sparkContext.setLogLevel("WARN")
POS = ["topic", "partition", "offset"]
b = spark.read.format("delta").load(TABLE)
k = (spark.read.format("kafka").option("kafka.bootstrap.servers", KAFKA).option("subscribe", "ads.outbox.events")
     .option("startingOffsets", "earliest").option("endingOffsets", "latest").option("kafka.isolation.level", "read_committed")
     .option("includeHeaders", "true").load())
bb = b.select(*POS, F.col("key_binary").alias("bk"), F.col("value_binary").alias("bv"), F.col("headers").alias("bh"),
              F.col("kafka_timestamp").alias("bts"), F.col("kafka_timestamp_type").alias("btt"), F.lit(1).alias("in_b"))
kk = k.select(*POS, F.col("key").alias("kk"), F.col("value").alias("kv"), F.col("headers").alias("kh"),
              F.col("timestamp").alias("kts"), F.col("timestampType").alias("ktt"), F.lit(1).alias("in_k"))
j = bb.join(kk, POS, "full_outer").cache()
both = j.filter("in_b = 1 and in_k = 1")
r = {
    "bronze_rows": b.count(),
    "bronze_distinct_positions": b.select(*POS).distinct().count(),
    "kafka_rows": k.count(),
    "only_bronze": j.filter("in_k is null").count(),
    "only_kafka_pending": j.filter("in_b is null").count(),
    "key_mismatch": both.filter(~F.col("bk").eqNullSafe(F.col("kk"))).count(),
    "value_mismatch": both.filter(~F.col("bv").eqNullSafe(F.col("kv"))).count(),
    "headers_mismatch": both.filter(~F.col("bh").eqNullSafe(F.col("kh"))).count(),
    "timestamp_mismatch": both.filter(~F.col("bts").eqNullSafe(F.col("kts")) | ~F.col("btt").eqNullSafe(F.col("ktt"))).count(),
}
ev = b.select("ingested_at", "ingest_date", "pipeline_release",
              F.get_json_object(F.col("value_binary").cast("string"), "$.after.event_id").alias("event_id"),
              F.to_timestamp(F.get_json_object(F.col("value_binary").cast("string"), "$.after.created_at")).alias("produced_at"),
              F.col("kafka_timestamp"))
r["distinct_event_ids"] = ev.select("event_id").distinct().count()
lat = (F.unix_micros("ingested_at") - F.unix_micros("produced_at")) / 1e6
UTC = "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"
r["batches"] = [row.asDict() for row in ev.groupBy(F.date_format("ingested_at", UTC).alias("ingested_at_utc")).agg(
    F.count("*").alias("rows"), F.round(F.min(lat), 3).alias("min_latency_s"), F.round(F.max(lat), 3).alias("max_latency_s"),
    F.round(F.max((F.unix_micros("ingested_at") - F.unix_micros("kafka_timestamp")) / 1e6), 3).alias("max_kafka_to_bronze_s"),
).orderBy("ingested_at_utc").collect()]
r["ingest_dates"] = [x[0] for x in b.select("ingest_date").distinct().collect()]
r["releases"] = [x[0] for x in b.select("pipeline_release").distinct().collect()]
r["schema"] = b.schema.simpleString()
r["max_offset_bronze"] = {x[0]: x[1] for x in b.groupBy("partition").agg(F.max("offset")).collect()}
r["max_offset_kafka"] = {x[0]: x[1] for x in k.groupBy("partition").agg(F.max("offset")).collect()}
h = spark.sql(f"DESCRIBE HISTORY delta.`{TABLE}`").select("version", F.date_format("timestamp", UTC).alias("timestamp"), "operation", "operationParameters", "operationMetrics").orderBy("version")
r["history"] = [{"version": x.version, "timestamp": x.timestamp, "operation": x.operation, "epochId": (x.operationParameters or {}).get("epochId"),
                 "numOutputRows": (x.operationMetrics or {}).get("numOutputRows"), "numFiles": (x.operationMetrics or {}).get("numAddedFiles")} for x in h.collect()]
print("C3_REPORT " + json.dumps(r, ensure_ascii=False, default=str), flush=True)
