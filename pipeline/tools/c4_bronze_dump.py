"""C4: Bronze Delta 행을 NDJSON으로 내놓는다(위치·value 원문·적재 시각). 해석은 c4_reconcile.py 가 한다."""
import json
import sys

from pyspark.sql import functions as F

from bronze_ads_outbox import TABLE, s3a_session

spark = s3a_session("c4_bronze_dump")
spark.sparkContext.setLogLevel("WARN")
version = spark.sql(f"DESCRIBE HISTORY delta.`{TABLE}` LIMIT 1").collect()[0]["version"]
# ponytail: 드라이버로 모두 모은다(실험 규모). 운영 규모면 (창, 이름) 집계를 Spark 안에서 한다
rows = spark.read.format("delta").option("versionAsOf", version).load(TABLE).select(
    "topic", "partition", "offset", F.col("key_binary").cast("string").alias("key"), F.col("value_binary").cast("string").alias("value"),
    F.date_format("ingested_at", "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'").alias("ingested_at")).collect()
with open(sys.argv[1], "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r.asDict(), ensure_ascii=False) + "\n")
print(f"C4_DUMP rows={len(rows)} delta_version={version}", flush=True)
