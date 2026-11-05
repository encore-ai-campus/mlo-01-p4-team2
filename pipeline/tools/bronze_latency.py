"""Bronze 단계별 지연 분포(설계 §11.2 J6·E-0): 원천 생성(after.created_at) → Kafka(타임스탬프) → Bronze 적재(ingested_at).

사용: spark-submit-ads.sh bronze_latency.py --since ISO --until ISO
- 원천 생성 시각이 [since, until) 인 Bronze 행만(적재 날짜 파티션으로 먼저 줄인다 — 시계 차이에 하루 여유). 같은 event_id 가 여러 번이면 처음 적재된 행만 센다.
- 단계마다 p50·p95·p99·최대(초), 분당 생성 건수의 최소·평균·최대, 적재 배치 수.
"""
import argparse
import json
from datetime import UTC, datetime, timedelta

from pyspark.sql import Window, functions as F

from bronze_ads_outbox import TABLE, s3a_session

ap = argparse.ArgumentParser()
ap.add_argument("--since", required=True)
ap.add_argument("--until", required=True)
a = ap.parse_args()
since, until = datetime.fromisoformat(a.since).astimezone(UTC), datetime.fromisoformat(a.until).astimezone(UTC)  # ingest_date 는 UTC 날짜

spark = s3a_session("bronze_latency")
spark.sparkContext.setLogLevel("WARN")
v = F.col("value_binary").cast("string")
rows = (spark.read.format("delta").load(TABLE).filter(F.col("ingest_date") >= F.lit(since.date() - timedelta(days=1)))  # 시계 차이에 하루 여유
        .select(F.get_json_object(v, "$.after.event_id").alias("event_id"),
                F.to_timestamp(F.get_json_object(v, "$.after.created_at")).alias("created_at"), "kafka_timestamp", "ingested_at")
        .filter((F.col("created_at") >= F.lit(since)) & (F.col("created_at") < F.lit(until))))
first = rows.withColumn("n", F.row_number().over(Window.partitionBy("event_id").orderBy("ingested_at"))).filter("n = 1").drop("n")
sec = lambda end, start: (F.unix_micros(end) - F.unix_micros(start)) / 1e6  # noqa: E731
stages = {"created_to_kafka_s": sec("kafka_timestamp", "created_at"), "kafka_to_bronze_s": sec("ingested_at", "kafka_timestamp"),
          "created_to_bronze_s": sec("ingested_at", "created_at")}
aggs = []
for name, expr in stages.items():
    aggs += [F.percentile_approx(expr, [0.5, 0.95, 0.99], 10000).alias(f"{name}_pct"), F.max(expr).alias(f"{name}_max")]
r = first.agg(F.count("*").alias("events"), F.countDistinct("ingested_at").alias("batches"), *aggs).collect()[0].asDict()
per_min = first.groupBy(F.date_trunc("minute", "created_at")).count().agg(F.min("count"), F.avg("count"), F.max("count")).collect()[0]
out = {"since": a.since, "until": a.until, "table": TABLE, "events": r["events"], "batches": r["batches"],
       "events_per_minute": {"min": per_min[0], "avg": round(per_min[1], 1) if per_min[1] is not None else None, "max": per_min[2]}}
for name in stages:
    p50, p95, p99 = r[f"{name}_pct"] or [None] * 3
    out[name] = {"p50": p50, "p95": p95, "p99": p99, "max": r[f"{name}_max"]}
print("BRONZE_LATENCY " + json.dumps(out, ensure_ascii=False, default=str), flush=True)
