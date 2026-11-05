"""RB-4·RB-5 시작 위치 계산(Spark, 읽기만): 새 Bronze checkpoint 에 줄 startingOffsets JSON.

사용: pipeline/bronze/spark-submit-ads.sh pipeline/runbooks/rb_offsets.py   (BRONZE_TABLE·BRONZE_TOPIC·KAFKA_BOOTSTRAP 은 Bronze 와 같은 환경 변수)
- 파티션마다 시작 = max(Bronze 표의 max(offset) + 1, Kafka 에 남은 가장 이른 offset). Bronze 에 행이 없는 파티션은 -2(earliest).
  - RB-4(checkpoint 상실·질의 모양 변경, 설계 S3·B5): Bronze 가 Kafka 위치를 원본대로 갖고 있어 이어 쓸 자리를 다시 만든다.
  - RB-5(보존 만료, 설계 K5): 지워진 구간이 있으면 시작 = 가장 이른 offset. 그 구간은 `EXPIRED` 줄로 알리고 RB-0(outbox 재발행)으로 메운다.
  - Spark 는 Kafka 에 없는 offset(보존 밖)을 주면 failOnDataLoss=true 로 멈추므로 두 값 중 큰 쪽을 쓴다.
- 같은 토픽인지 확인(K4: 토픽을 지우고 다시 만들면 offset 이 0부터 다시 쓰인다): 파티션마다 Bronze 의 마지막 위치(max offset)에 있는 레코드를
  Kafka 의 같은 위치에서 읽어 key·value 바이트를 비교한다. 다르거나 없으면, 또는 Bronze 위치가 Kafka 끝보다 앞서면 RB-3 으로 멈춘다.
  그 위치가 보존 밖이면 비교할 수 없어 `UNVERIFIED` 줄로 알린다 — 그때 K4 여부는 사건 사실(토픽 삭제·재생성, topic id)로 정한다.
- 출력: `RB_STARTING_OFFSETS <json>` 한 줄(→ BRONZE_STARTING_OFFSETS), 지워진 구간마다 `EXPIRED partition=… from=… to=…`,
  비교하지 못한 파티션마다 `UNVERIFIED partition=… offset=…`.
"""
import json

from pyspark.sql import functions as F

from bronze_ads_outbox import KAFKA, TABLE, TOPIC, s3a_session

spark = s3a_session("rb_offsets")
spark.sparkContext.setLogLevel("WARN")
bronze_next = {int(r["partition"]): int(r["m"]) + 1 for r in
               spark.read.format("delta").load(TABLE).filter(F.col("topic") == TOPIC).groupBy("partition").agg(F.max("offset").alias("m")).collect()}

# 파티션 목록·가장 이른 offset 은 Kafka 에 직접 묻는다(kafka-clients 는 의존 jar 세트에 있어 드라이버 classpath 에 있다)
jvm = spark.sparkContext._jvm
props = jvm.java.util.Properties()
props.setProperty("bootstrap.servers", KAFKA)
admin = jvm.org.apache.kafka.clients.admin.Admin.create(props)
try:
    described = admin.describeTopics(jvm.java.util.Collections.singletonList(TOPIC)).allTopicNames().get().get(TOPIC)
    parts = sorted(int(p.partition()) for p in described.partitions())

    def list_offsets(spec):
        request = jvm.java.util.HashMap()
        for p in parts:
            request.put(jvm.org.apache.kafka.common.TopicPartition(TOPIC, p), spec)
        result = admin.listOffsets(request).all().get()
        return {int(tp.partition()): int(result.get(tp).offset()) for tp in result.keySet()}
    OffsetSpec = jvm.org.apache.kafka.clients.admin.OffsetSpec
    earliest, latest = list_offsets(OffsetSpec.earliest()), list_offsets(OffsetSpec.latest())
finally:
    admin.close()

missing = set(bronze_next) - set(parts)
reset = sorted(p for p in bronze_next if p in latest and bronze_next[p] > latest[p])
if missing or reset:  # 토픽이 다시 만들어져 offset 이 0부터 다시 시작했다(K4) — 같은 토픽으로 이어 쓸 수 없다
    raise SystemExit(f"Bronze 위치가 토픽 {TOPIC} 와 맞지 않는다(없는 파티션 {sorted(missing)}, Kafka 끝보다 앞선 파티션 {reset}) → RB-3(새 토픽 이름)")
# 위치만으로는 다시 만든 토픽이 옛 위치를 넘은 뒤를 못 가린다 — 마지막 위치의 레코드가 같은지 본다
last = {p: n - 1 for p, n in bronze_next.items() if n - 1 >= earliest[p]}
for p in sorted(set(bronze_next) - set(last)):
    print(f"UNVERIFIED partition={p} offset={bronze_next[p] - 1} — Kafka 보존 밖이라 같은 레코드인지 비교할 수 없다(K4 여부는 사건 사실로 정한다)", flush=True)
if last:
    raw = lambda x: None if x is None else bytes(x)  # noqa: E731
    b_rows = (spark.read.format("delta").load(TABLE).filter(F.col("topic") == TOPIC)
              .filter(F.expr(" or ".join(f"(partition = {p} and offset = {o})" for p, o in last.items())))
              .select("partition", "key_binary", "value_binary").collect())
    k_rows = (spark.read.format("kafka").option("kafka.bootstrap.servers", KAFKA).option("assign", json.dumps({TOPIC: sorted(last)}))
              .option("startingOffsets", json.dumps({TOPIC: {str(p): o for p, o in last.items()}}))
              .option("endingOffsets", json.dumps({TOPIC: {str(p): o + 1 for p, o in last.items()}}))
              .option("kafka.isolation.level", "read_committed").load().select("partition", "offset", "key", "value").collect())
    bronze_last = {int(r["partition"]): (raw(r["key_binary"]), raw(r["value_binary"])) for r in b_rows}
    kafka_last = {int(r["partition"]): (raw(r["key"]), raw(r["value"])) for r in k_rows if int(r["offset"]) == last[int(r["partition"])]}
    differ = sorted(p for p in last if kafka_last.get(p) is None or kafka_last[p] != bronze_last.get(p))
    if differ:
        raise SystemExit(f"Bronze 마지막 위치의 레코드가 토픽 {TOPIC} 의 같은 위치와 다르다(파티션 {differ}) — 토픽이 다시 만들어졌다(K4) → RB-3(새 토픽 이름)")
    print(f"SAME_TOPIC partitions={sorted(last)} — 마지막 위치의 key·value 가 Kafka 와 같다", flush=True)
starting = {}
for p in parts:
    if p not in bronze_next:
        starting[str(p)] = -2
        continue
    starting[str(p)] = max(bronze_next[p], earliest[p])
    if earliest[p] > bronze_next[p]:
        print(f"EXPIRED partition={p} from={bronze_next[p]} to={earliest[p] - 1}", flush=True)
print("RB_STARTING_OFFSETS " + json.dumps({TOPIC: starting}), flush=True)
