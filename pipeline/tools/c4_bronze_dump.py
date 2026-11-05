"""C4: Bronze Delta 행을 NDJSON으로 내놓는다(위치·value 원문·적재 시각). 해석은 c4_reconcile.py 가 한다.

사용: c4_bronze_dump.py <out.ndjson> [--since ISO]
- --since: 원천 생성 시각(after.created_at)이 이 시각 이후인 행만(생성 시각이 없는 행은 남긴다). 적재 날짜 파티션으로 먼저 줄인다(시계 차이에 하루 여유).
- 드라이버로 한꺼번에 모으지 않고 파티션 단위로 흘려 쓴다(행이 수십만이어도 드라이버 메모리에 다 올리지 않는다)."""
import argparse
import json
from datetime import UTC, datetime, timedelta

from pyspark.sql import functions as F

from bronze_ads_outbox import TABLE, s3a_session

spark = s3a_session("c4_bronze_dump")
spark.sparkContext.setLogLevel("WARN")
ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--since", help="원천 생성 시각이 이 ISO 시각 이후인 행만")
a = ap.parse_args()
version = spark.sql(f"DESCRIBE HISTORY delta.`{TABLE}` LIMIT 1").collect()[0]["version"]
df = spark.read.format("delta").option("versionAsOf", version).load(TABLE)
if a.since:
    since = datetime.fromisoformat(a.since).astimezone(UTC)  # c4_reconcile 과 같게 UTC 로(ingest_date 는 UTC 날짜)
    created = F.to_timestamp(F.get_json_object(F.col("value_binary").cast("string"), "$.after.created_at"))
    # 적재 날짜는 하루 여유(Spark 와 원천 서버의 시계가 달라도 빠지지 않게). 생성 시각이 없는 행(삭제·다른 표·툼스톤)은 남겨 대사의 검사가 본다
    df = df.filter(F.col("ingest_date") >= F.lit(since.date() - timedelta(days=1))).filter(created.isNull() | (created >= F.lit(since)))
rows = df.select("topic", "partition", "offset", F.col("key_binary").cast("string").alias("key"), F.col("value_binary").cast("string").alias("value"),
                 F.date_format("ingested_at", "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'").alias("ingested_at"))
n = 0
with open(a.out, "w", encoding="utf-8") as f:
    for r in rows.toLocalIterator():
        f.write(json.dumps(r.asDict(), ensure_ascii=False) + "\n")
        n += 1
print(f"C4_DUMP rows={n} delta_version={version} since={a.since}", flush=True)
