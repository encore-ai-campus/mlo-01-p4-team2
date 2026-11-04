"""Silver 공통 틀 — 어댑터 인터페이스와 어댑터 결과 확인(계획 15 §2.1, SL-3 다시 나누기 §3 SL-3a, SL-3a 결정 D1).

어댑터(원천마다 `pipeline/silver/adapters/<domain>.py`)는 **Bronze DataFrame → (정상 DataFrame, 오류 DataFrame)** 변환이다.
- 입력: Bronze 표의 행(contracts.BRONZE_SCHEMA).
- 정상: contracts.OK_SCHEMA — Bronze 열 그대로 + 어댑터 열(contracts.ADAPTER_FIELDS). 나머지 공통 열은 공통 틀이 채운다(SL-3b·SL-3c).
- 오류: contracts.ADAPTER_ERROR_SCHEMA — Bronze 열 그대로 + error_code(contracts.ADAPTER_ERROR_CODES)·reason·event_id(모르면 null).
- 모든 Bronze 행은 정상·오류 가운데 정확히 한 번, Bronze 열을 바꾸지 않고 나온다(조용한 유실 0).
- **판정 순서**(앞에서 걸린 코드가 그 행의 코드다 — 설정 이상이 행 오류에 가려 멈추지 않는 일을 막는다):
  ① value 가 null(tombstone) → `not_insert`(멈춤, 15 §2.2)
  ② 봉투 엄격 검사(contracts.strict_reasons 의 `value` 사유) → `decode`. 통과한 행은 try_parse_json(Variant)으로 해석하고,
     Spark 가 해석하지 못하면(Variant 크기 한도 등) 그것도 `decode`
  ③ 출처(source.db·schema·table 이 그 원천의 outbox 가 아님) → `foreign_source`, op 가 c·r 가 아니거나 after 가 없음 → `not_insert`(둘 다 멈춤)
  ④ 안쪽 JSON 문자열 엄격 검사(contracts.strict_reasons 의 `nested` 사유) → `decode`
  ⑤ contracts.extract 로 표대로 꺼냄·원천별 계약 → `contract`·`unknown_schema`
  ②와 ④는 UDF 한 번의 두 결과다(봉투는 한 번만 해석). ③을 ④보다 앞에 두는 것은 다른 표의 행이 같은 이름의 열에 JSON 이 아닌 문자열을
  들고 와도 decode(계속)가 아니라 foreign_source(멈춤)가 되게 하기 위해서다.
- 내용 해시(content_sha256)는 pipeline/common/hop_rules 규칙의 UDF 다(SL-4·SL-8).
check_adapted 는 이 약속을 Spark 식으로 확인한다. 어긋나면 배치를 멈춘다(SV7 '설정 이상은 멈춤').
"""
from collections import namedtuple
from functools import reduce

from pyspark.sql import Window, functions as F

from pipeline.silver import contracts, control

POSITION = ("topic", "partition", "offset")  # Bronze 행 하나를 가리키는 위치
FIRST_COPY = ("ingested_at", "topic", "partition", "offset")  # D2: 먼저 적재된 사본이 첫 사본
KAFKA_FIELDS = ("key_binary", "value_binary", "headers", "topic", "partition", "offset", "kafka_timestamp", "kafka_timestamp_type")  # B1: Kafka 기록의 바이트
_BRONZE_NAMES = [name for name, _, _ in contracts.BRONZE_FIELDS]
_JUDGED_NAMES = [name for name, _, _ in contracts.JUDGED_ERROR_FIELDS]


class AdapterContractError(RuntimeError):
    """어댑터 결과 확인에서 결함이 나왔다 — 배치를 멈춘다(사유에 원인 쪽이 적힌다: 어댑터 결함 또는 Bronze 위치 중복)."""


class BronzeConflictError(RuntimeError):
    """Bronze 의 같은 위치에 Kafka 바이트가 다른 줄이 있다(K4 — 같은 이름의 토픽을 다시 만들어 offset 이 겹침) — 원천 설정 이상이라 멈춘다(B1)."""


def _same_columns(frame, schema, label):
    expected = [(f.name, f.dataType.simpleString()) for f in schema.fields]
    actual = [(f.name, f.dataType.simpleString()) for f in frame.schema.fields]
    if actual != expected:  # 이름·순서·형(NULL 허용은 Spark 식이 늘 참으로 둘 수 있어 행 검사로 본다)
        raise AdapterContractError(f"{label} 결과의 열이 다르다:\n  기대 {expected}\n  실제 {actual}")


def _row_defects(frame, checks):
    """checks: [(결함 조건 열, 사유)] → 결함이 하나라도 있는 행을 (위치, defect 사유) 로."""
    reasons = F.concat_ws("; ", *[F.when(condition, F.lit(reason)) for condition, reason in checks])
    return frame.select(*POSITION, reasons.alias("defect")).where(F.col("defect") != "")


def _keyed(frame):
    """위치 + Bronze 열 지문. 지문은 어댑터가 Bronze 열을 바꾼 것을 잡는 용도라 64비트 xxhash64 로 충분하다(보안 경계가 아님).
    한계(SL-3a 리뷰 r2 참고 3 — 기록만): xxhash64 는 null 열을 건너뛰고 해시하므로, null 인 열과 값이 있는 열 사이에서 같은 바이트가 자리만 바꾸면
    지문이 같다. Bronze 열은 Kafka 원천이 정한 자리라 그렇게 바뀔 길이 없다. dedupe_bronze 의 Kafka 바이트 지문도 같다."""
    return frame.select(*POSITION, F.xxhash64(*[F.col(name) for name in _BRONZE_NAMES]).alias("_bronze_fp"))


def defects(bronze, ok, errors, domain):
    """어댑터 결과의 결함 행(위치, defect). 모양(열 이름·순서·형)이 다르면 실행 전에 AdapterContractError."""
    _same_columns(ok, contracts.OK_SCHEMA, "정상")
    _same_columns(errors, contracts.ADAPTER_ERROR_SCHEMA, "오류")
    ok_checks = [(F.col(name).isNull(), f"{name} 이(가) null") for name, _, nullable in contracts.ADAPTER_FIELDS if not nullable]
    ok_checks += [
        (F.col("domain") != F.lit(domain), f"domain 이 {domain} 가 아니다"),
        (~F.col("event_id").rlike(contracts.UUID_RE), "event_id 가 소문자 UUID 가 아니다"),
        (~F.col("content_sha256").rlike(contracts.SHA256_RE), "content_sha256 이 소문자 16진 64자가 아니다"),
        (~F.col("first_op").isin("c", "r"), "정상 결과의 op 는 c·r 뿐이다(그 밖은 not_insert 오류)"),
        (F.col("value_binary").isNull(), "value 가 null 인 행(tombstone)이 정상 결과에 있다"),
    ]
    error_checks = [
        (~F.coalesce(F.col("error_code").isin(*contracts.ADAPTER_ERROR_CODES), F.lit(False)), "어댑터가 낼 수 없는 오류 코드"),
        (F.coalesce(F.trim(F.col("reason")) == "", F.lit(True)), "오류 사유가 비었다"),
        (F.col("event_id").isNotNull() & ~F.col("event_id").rlike(contracts.UUID_RE), "오류의 event_id 가 소문자 UUID 가 아니다"),
        (F.col("value_binary").isNull() & ~F.coalesce(F.col("error_code") == "not_insert", F.lit(False)), "value 가 null 인 행(tombstone)은 not_insert 다(순서 ①)"),
    ]
    keys = list(POSITION) + ["_bronze_fp"]
    source, results = _keyed(bronze), _keyed(ok).unionByName(_keyed(errors))
    conservation = [
        source.groupBy(*POSITION).count().where(F.col("count") > 1)
        .select(*POSITION, F.lit("Bronze 에 같은 위치가 둘 이상 있다 — 어댑터 결함이 아니다(설계 01 S3·감시 M7, RB-4 를 확인). 배치는 dedupe_bronze(B1)를 먼저 거쳐야 한다").alias("defect")),
        source.join(results, keys, "left_anti").select(*POSITION, F.lit("Bronze 행이 결과에 없거나 Bronze 열이 바뀌었다").alias("defect")),
        results.groupBy(*POSITION).count().where(F.col("count") > 1).select(*POSITION, F.lit("Bronze 행 하나가 결과에 두 번 이상 나온다").alias("defect")),
        results.join(source, keys, "left_anti").select(*POSITION, F.lit("결과에 Bronze 에 없는 행이 있다(위치 또는 Bronze 열이 다름)").alias("defect")),
    ]
    return reduce(lambda a, b: a.unionByName(b), [_row_defects(ok, ok_checks), _row_defects(errors, error_checks), *conservation])


def check_adapted(bronze, ok, errors, domain, examples=5):
    """어댑터 결과를 확인하고 그대로 돌려준다(ok, errors). 결함이 있으면 예시와 함께 AdapterContractError.
    Spark 실행이 한 번 든다. 부르는 쪽(SL-3c)은 **어댑터 결과(엄격 검사 UDF 를 거친 중간 결과)를 persist** 해 둔다 — Bronze 배치만 persist 하면
    이 확인의 부분 질의마다, 그리고 뒤의 쓰기마다 어댑터 계산과 UDF 가 다시 돈다. 배치 비용의 UDF 단계 시간도 그 persist 를 기준으로 잰다."""
    found = defects(bronze, ok, errors, domain).limit(examples).collect()
    if found:
        raise AdapterContractError("Silver 배치를 멈춘다 — 어댑터 결과 확인에서 결함: "
                                   + "; ".join(f"{row.topic}/{row.partition}/{row.offset}: {row.defect}" for row in found))
    return ok, errors


# ---------------------------------------------------------------- 배치 판정(SL-3b — 쓰기 없음. 쓰기·멱등은 SL-3c)

def _require_utc(spark):
    if spark.conf.get("spark.sql.session.timeZone") != "UTC":  # event_date = produced_at 의 UTC 날짜(to_date 는 세션 시간대를 쓴다)
        raise ValueError("Silver 판정은 spark.sql.session.timeZone=UTC 에서만 한다")


def dedupe_bronze(batch, examples=5):
    """Bronze 배치의 같은 위치 줄을 정리한다(B1 — Planner 결정 2026-11-04, 어댑터 앞에서 부른다).
    같은 Kafka 바이트(KAFKA_FIELDS)면 같은 Kafka 기록을 두 번 적재한 것(RB-4 실수)이라 먼저 적재된 줄(D2) 하나만 남긴다 — Bronze 원본은 그대로이고,
    줄인 수는 부르는 쪽이 배치 비용 기록과 경보 줄에 남긴다. 바이트가 다르면 BronzeConflictError(멈춤).
    배치를 넘는 이중 적재(옛 배치에서 이미 처리한 위치)는 Silver 만으로 보이지 않아 copies + 1 이 된다 — 안전망은 감시 M7 이다.
    반환: (정리한 Bronze DF, 줄인 줄 수). Spark 실행이 한 번 든다(부르는 쪽은 배치를 persist 해 둔다)."""
    by_position = Window.partitionBy(*POSITION)
    staged = (batch.withColumn("_kafka_fp", F.xxhash64(*[F.col(name) for name in KAFKA_FIELDS]))
              .withColumn("_kinds", F.size(F.collect_set("_kafka_fp").over(by_position)))
              .withColumn("_rank", F.row_number().over(by_position.orderBy("ingested_at", "pipeline_release", "ingest_date"))))
    summary = staged.agg(F.sum((F.col("_rank") > 1).cast("int")).alias("dropped"), F.max("_kinds").alias("kinds")).first()
    if summary["kinds"] and summary["kinds"] > 1:
        found = staged.where(F.col("_kinds") > 1).select(*POSITION).distinct().limit(examples).collect()
        raise BronzeConflictError("Silver 배치를 멈춘다 — Bronze 의 같은 위치에 Kafka 바이트가 다른 줄이 있다(K4 — 같은 이름의 토픽을 다시 만들었는지 확인): "
                                  + ", ".join(f"{row.topic}/{row.partition}/{row.offset}" for row in found))
    return staged.where(F.col("_rank") == 1).drop("_kafka_fp", "_kinds", "_rank"), int(summary["dropped"] or 0)


def _judged(frame, *, existing_id=None, existing_date=None, acknowledged=False, ack_reason=None, ack_incident=None):
    """어댑터 오류 열을 가진 DF → 판정한 오류 행(contracts.JUDGED_ERROR_SCHEMA 순서)."""
    extra = {"existing_event_id": existing_id, "existing_event_date": existing_date, "acknowledged": F.lit(acknowledged),
             "ack_reason": ack_reason, "ack_incident": ack_incident}
    columns = [F.col(name) for name, _, _ in contracts.ADAPTER_ERROR_FIELDS]
    columns += [(F.lit(None) if extra[name] is None else extra[name]).cast(kind).alias(name) for name, kind, _ in contracts.ERROR_EXTRA_FIELDS[:5]]
    return frame.select(*columns)


def acknowledge(errors, lines, bronze_table):
    """멈춤 확인(15 §2.1 2단계, C4·D3). `errors`: 어댑터 오류 DF. `lines`: control.load_acknowledged 결과. `bronze_table`: Bronze 표 **전체**
    (범위 줄의 실제 행 수를 센다 — D3, 그 줄이 이번 배치에서 맞았을 때만, 줄마다 offset 조건 count 한 번).
    반환: (오류 행 DF — 넘긴 멈춤 줄은 acknowledged·ack_reason·ack_incident 를 채움, 멈춤 코드가 아닌 오류는 그대로,
           진단 행 DF — 넘기지 못한 멈춤 줄(하나라도 있으면 그 배치는 멈춤 — D4 stops appId 로 쓴다),
           행 수가 다른 범위 줄 [(줄, 센 수)] — 그 줄은 넘기지 않는다)."""
    spark = errors.sparkSession
    table = spark.createDataFrame(
        [(i, line["topic"], line["partition"], line["start"], line["end"], line["error_code"], line["reason"], line["incident"], line["row_count"])
         for i, line in enumerate(lines)],
        "ack_index int, ack_topic string, ack_partition int, ack_start bigint, ack_end bigint, ack_code string, ack_reason string, ack_incident string, ack_row_count bigint")
    stop = errors.where(F.col("error_code").isin(*control.STOP_CODES))
    matched = stop.join(F.broadcast(table), (F.col("topic") == F.col("ack_topic")) & (F.col("partition") == F.col("ack_partition"))
                        & (F.col("error_code") == F.col("ack_code")) & F.col("offset").between(F.col("ack_start"), F.col("ack_end")), "left")
    mismatches, refused = [], []
    for row in matched.where(F.col("ack_row_count").isNotNull()).select("ack_index").distinct().collect():
        line = lines[row.ack_index]
        counted = bronze_table.where((F.col("topic") == line["topic"]) & (F.col("partition") == line["partition"])
                                     & F.col("offset").between(line["start"], line["end"])).count()
        if counted != line["row_count"]:
            mismatches.append((line, counted))
            refused.append(row.ack_index)
    accepted = F.col("ack_index").isNotNull() & ~F.col("ack_index").isin(*refused) if refused else F.col("ack_index").isNotNull()
    acked = _judged(matched.where(accepted), acknowledged=True, ack_reason=F.col("ack_reason"), ack_incident=F.col("ack_incident"))
    stops = _judged(matched.where(~accepted))
    return _judged(errors.where(~F.col("error_code").isin(*control.STOP_CODES))).unionByName(acked), stops, mismatches


def fold(ok):
    """배치 안 접기(15 §2.1 3단계). 반환: (접은 행 DF — contracts.MERGE_SOURCE_SCHEMA, 배치 안 충돌 사본 DF — 어댑터 정상 열).
    `event_id` 마다 내용 해시가 하나면 첫 사본(D2)의 값 + 출처 열 + copies = 사본 수 + event_date(produced_at 의 UTC 날짜)로 한 행을 만든다.
    둘 이상이면 그 ID 의 사본 모두가 충돌이다(Silver 에 넣지 않음)."""
    _require_utc(ok.sparkSession)
    by_id = Window.partitionBy("event_id")
    staged = (ok.withColumn("_copies", F.count(F.lit(1)).over(by_id))
              .withColumn("_hashes", F.size(F.collect_set("content_sha256").over(by_id)))
              .withColumn("_rank", F.row_number().over(by_id.orderBy(*FIRST_COPY))))
    renamed = {"src_topic": "topic", "src_partition": "partition", "src_offset": "offset", "copies": "_copies"}
    folded = staged.where((F.col("_hashes") == 1) & (F.col("_rank") == 1)).select(
        *[(F.to_date("produced_at") if name == "event_date" else F.col(renamed.get(name, name))).cast(kind).alias(name)
          for name, kind, _ in contracts.MERGE_SOURCE_FIELDS])
    return folded, staged.where(F.col("_hashes") > 1).drop("_copies", "_hashes", "_rank")


def full_check(folded, silver, conflict_ids):
    """매 배치 전체 확인(15 §2.1 4단계 (a)~(d) — 사용자 결정 C1). `silver`: Silver 표 전체의 투영(event_id·event_date·content_sha256),
    `conflict_ids`: 오류 표 `error_code = 'conflict'` 의 event_id(관리 Step 으로 풀린 ID 는 부르는 쪽이 뺀다).
    배치의 event_id 를 broadcast 해 두 표에 semi-join 한다 — Silver 쪽은 한 번 훑고 shuffle 하지 않는다(broadcast 크기는 배치 크기에 묶인다).
    반환: (MERGE 할 접은 행 DF, 충돌 판정 DF — event_id·reason·existing_event_id·existing_event_date)."""
    ids = F.broadcast(folded.select("event_id"))
    existing = (silver.select("event_id", "event_date", "content_sha256").join(ids, "event_id", "left_semi").groupBy("event_id")
                .agg(F.count(F.lit(1)).alias("_n"), F.min("event_date").alias("_date"), F.max("event_date").alias("_date_max"),
                     F.min("content_sha256").alias("_sha"), F.max("content_sha256").alias("_sha_max")))
    known = conflict_ids.select("event_id").join(ids, "event_id", "left_semi").distinct().withColumn("_known", F.lit(True))
    joined = folded.join(existing, "event_id", "left").join(known, "event_id", "left")
    reason = (F.when(F.col("_known"), F.lit("이미 conflict 로 오류 표에 있는 event_id 의 새 사본"))  # (a)
              .when(F.col("_n").isNull(), F.lit(None))  # (d) 새 ID
              .when((F.col("_n") > 1) | (F.col("_date") != F.col("_date_max")) | (F.col("_sha") != F.col("_sha_max")),
                    F.lit("Silver 안에 같은 event_id 가 둘 이상 있다(두 번째 그물 대상) — 사람이 정할 때까지 더 넣지 않는다"))
              .when(F.col("_date") != F.col("event_date"), F.concat(F.lit("같은 event_id 가 다른 event_date("), F.col("_date").cast("string"), F.lit(")에 이미 있다")))  # (b)
              .when(F.col("_sha") != F.col("content_sha256"), F.lit("같은 event_date 에 내용이 다른 같은 event_id 가 이미 있다")))  # (c) 다름, 같으면 null
    joined = joined.withColumn("_reason", reason)
    merge = joined.where(F.col("_reason").isNull()).select(*[name for name, _, _ in contracts.MERGE_SOURCE_FIELDS])
    decided = joined.where(F.col("_reason").isNotNull()).select(
        "event_id", F.col("_reason").alias("reason"), F.when(F.col("_n").isNotNull(), F.col("event_id")).alias("existing_event_id"),
        F.col("_date").alias("existing_event_date"))
    return merge, decided


Judgement = namedtuple("Judgement", "stop merge errors stops mismatches")


def judge(ok, errors, lines, bronze_table, silver, conflict_ids):
    """배치 판정(15 §2.1 2~4단계, 쓰기 없음). 입력은 check_adapted 를 지난 어댑터 결과(dedupe_bronze 를 지난 배치에서 나온 것).
    반환 Judgement:
    - stop: 넘기지 못한 멈춤 줄이 있으면 참 — 그때는 접기·전체 확인을 하지 않고 merge·errors 는 None, stops 에 진단 행(D4).
    - merge: MERGE 원본(contracts.MERGE_SOURCE_SCHEMA, silver_written_at·silver_release 는 쓸 때 채움).
    - errors: 오류 표 행(contracts.JUDGED_ERROR_SCHEMA) — 어댑터 오류, 넘긴 멈춤 줄, 충돌 사본(배치 안 충돌, (a)~(c) — 그 ID 의 이번 배치 사본 모두).
      contracts.error_rows 로 배치 열을 붙여 쓴다.
    - stops: 진단 행(stop 이 거짓이면 빈 DF). mismatches: 행 수가 다른 범위 줄."""
    _require_utc(ok.sparkSession)
    adapter_errors, stops, mismatches = acknowledge(errors, lines, bronze_table)
    if stops.limit(1).count():
        return Judgement(True, None, None, stops, mismatches)
    folded, in_batch = fold(ok)
    merge, decided = full_check(folded, silver, conflict_ids)
    in_batch_rows = _judged(in_batch.select(*_BRONZE_NAMES, F.lit("conflict").alias("error_code"),
                                            F.lit("배치 안에서 같은 event_id 의 내용이 다르다").alias("reason"), "event_id"))
    decided_rows = ok.join(decided, "event_id").select(*_BRONZE_NAMES, F.lit("conflict").alias("error_code"), "reason", "event_id",
                                                        "existing_event_id", "existing_event_date")
    decided_rows = _judged(decided_rows, existing_id=F.col("existing_event_id"), existing_date=F.col("existing_event_date"))
    return Judgement(False, merge, adapter_errors.unionByName(in_batch_rows).unionByName(decided_rows), stops, mismatches)
