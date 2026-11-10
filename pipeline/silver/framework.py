"""Silver 공통 틀 — 어댑터 인터페이스와 어댑터 결과 확인(계획 15 §2.1, SL-3 다시 나누기 §3 SL-3a, SL-3a 결정 D1).

어댑터(원천마다 `pipeline/silver/adapters/<domain>.py`)는 **Bronze DataFrame → (정상 DataFrame, 오류 DataFrame)** 변환이다.
- 입력: Bronze 표의 행(contracts.BRONZE_SCHEMA).
- 정상: contracts.OK_SCHEMA — Bronze 열 그대로 + 어댑터 열(contracts.ADAPTER_FIELDS). 나머지 공통 열은 공통 틀이 채운다(SL-3b·SL-3c).
- 오류: contracts.ADAPTER_ERROR_SCHEMA — Bronze 열 그대로 + error_code(contracts.ADAPTER_ERROR_CODES)·reason·event_id(모르면 null).
- 모든 Bronze 행은 정상·오류 가운데 정확히 한 번, Bronze 열을 바꾸지 않고 나온다(조용한 유실 0).
- **판정 순서**(앞에서 걸린 코드가 그 행의 코드다 — 설정 이상이 행 오류에 가려 멈추지 않는 일을 막는다):
  ① value 가 null(tombstone) → `not_insert`(멈춤, 15 §2.2)
  ② 봉투 엄격 검사(contracts.strict_reasons 의 `value` 사유) → `decode`. 통과한 행은 try_parse_json(Variant)으로 해석하고,
     Spark 가 해석하지 못하면(Variant 크기 한도, 중첩 1,001단부터 — 엄격 검사는 통과하고 try_parse_json 이 null, 2026-11-04 실측) 그것도 `decode`
  ③ 출처(source.db·schema·table 이 그 원천의 outbox 가 아님) → `foreign_source`, op 가 c·r 가 아니거나 after 가 없음 → `not_insert`(둘 다 멈춤)
  ④ 안쪽 JSON 문자열 엄격 검사(contracts.strict_reasons 의 `nested` 사유) → `decode`
  ⑤ contracts.extract 로 표대로 꺼냄·원천별 계약 → `contract`·`unknown_schema`
  ②와 ④는 UDF 한 번의 두 결과다(봉투는 한 번만 해석). ③을 ④보다 앞에 두는 것은 다른 표의 행이 같은 이름의 열에 JSON 이 아닌 문자열을
  들고 와도 decode(계속)가 아니라 foreign_source(멈춤)가 되게 하기 위해서다.
- 내용 해시(content_sha256)는 pipeline/common/hop_rules 규칙의 UDF 다(SL-4·SL-8).
- 어댑터는 UDF 를 거친 중간 결과를 cache 하고 정상·오류를 그 cache 에서 나눈다 — 그래야 UDF 가 배치마다 한 번 돈다(SL-3c). 공통 틀은 두 결과를 한 번 세어
  UDF 단계 시간을 재고, 배치가 끝나면 cache 를 모두 비운다(process_batch).
- `settings` 는 출처 dict `{"db","schema","table"}`(control.source_settings 가 잡 입구에서 검사 — SL-3c 결정 S1, SL-8 도 같음).
check_adapted 는 이 약속을 Spark 식으로 확인한다. 어긋나면 배치를 멈춘다(SV7 '설정 이상은 멈춤').
배치 판정(SL-3b)과 쓰기·멱등·정체성(SL-3c)은 아래 두 절이다. 잡 입구는 silver_job.py.
(SL-9 — 계획 temp/pipeline/38_silver-quarantine-sl9.md) 유령 격리: 판정은 목록 ID 의 접은 행을 본 표 전체 확인에서 빼 격리 표와 맞추고(quarantine_check),
쓰기는 할 일이 있을 때만 격리 MERGE(txn appId `quarantine`), 스트림 전 시작 옮기기는 move_quarantined 다.
"""
import json
import time
from collections import namedtuple
from datetime import datetime, timezone
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
    지문이 같다(2026-11-04 실측: key=null·value=b"x" 와 key=b"x"·value=null 이 같은 지문). Bronze 열은 Kafka 원천이 정한 자리라 그렇게 바뀔 길이 없다.
    dedupe_bronze 는 지문이 아니라 열 값을 그대로 비교한다(SL-3b 리뷰 참고 4)."""
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
    Spark 실행이 한 번 든다. **어댑터 결과(엄격 검사 UDF 를 거친 중간 결과)는 cache 되어 있어야 한다**(머리말 — 어댑터가 한다) — 아니면
    이 확인의 부분 질의마다, 그리고 뒤의 쓰기마다 어댑터 계산과 UDF 가 다시 돈다. 배치 비용의 UDF 단계 시간도 그 cache 를 채우는 시간으로 잰다(process_batch)."""
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
    반환: (정리한 Bronze DF, 줄인 줄 수). 부르는 쪽은 배치를 persist 해 둔다.
    보통 배치(위치 중복 없음)는 위치 세 열만 묶어 보고 **받은 DF 를 그대로** 돌려준다 — 창은 Bronze 전체 열을 위치로 셔플하므로, 돌려준 DF 를
    읽을 때마다 그 비용이 든다(SL-3b 리뷰 권고 1 실측: 29만 행 약 9.5초 → 1.7초). 창은 중복이 있는 드문 배치에서만 돈다.
    "같은 바이트"는 Kafka 8열 값을 그대로 비교한다(struct 의 min = max — 지문의 null 건너뛰기 한계가 없다, SL-3b 리뷰 참고 4)."""
    if not batch.groupBy(*POSITION).count().where(F.col("count") > 1).limit(1).count():
        return batch, 0
    by_position = Window.partitionBy(*POSITION)
    kafka = F.struct(*[F.col(name) for name in KAFKA_FIELDS])
    staged = (batch.withColumn("_differs", ~F.min(kafka).over(by_position).eqNullSafe(F.max(kafka).over(by_position)))
              .withColumn("_rank", F.row_number().over(by_position.orderBy("ingested_at", "pipeline_release", "ingest_date"))))
    summary = staged.agg(F.sum((F.col("_rank") > 1).cast("int")).alias("dropped"), F.max(F.col("_differs").cast("int")).alias("differs")).first()
    if summary["differs"]:
        found = staged.where(F.col("_differs")).select(*POSITION).distinct().limit(examples).collect()
        raise BronzeConflictError("Silver 배치를 멈춘다 — Bronze 의 같은 위치에 Kafka 바이트가 다른 줄이 있다(K4 — 같은 이름의 토픽을 다시 만들었는지 확인): "
                                  + ", ".join(f"{row.topic}/{row.partition}/{row.offset}" for row in found))
    return staged.where(F.col("_rank") == 1).drop("_differs", "_rank"), int(summary["dropped"] or 0)


def _judged(frame, *, existing_id=None, existing_date=None, acknowledged=False, ack_reason=None, ack_incident=None):
    """어댑터 오류 열을 가진 DF → 판정한 오류 행(contracts.JUDGED_ERROR_SCHEMA 순서)."""
    extra = {"existing_event_id": existing_id, "existing_event_date": existing_date, "acknowledged": F.lit(acknowledged),
             "ack_reason": ack_reason, "ack_incident": ack_incident}
    columns = [F.col(name) for name, _, _ in contracts.ADAPTER_ERROR_FIELDS]
    columns += [(F.lit(None) if extra[name] is None else extra[name]).cast(kind).alias(name) for name, kind, _ in contracts.JUDGED_EXTRA_FIELDS]
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


def full_check(folded, silver, conflict_ids, broadcast=True):
    """매 배치 전체 확인(15 §2.1 4단계 (a)~(d) — 사용자 결정 C1). `silver`: Silver 표 전체의 투영(event_id·event_date·content_sha256),
    `conflict_ids`: 오류 표 `error_code = 'conflict'` 의 event_id(관리 Step 으로 풀린 ID 는 부르는 쪽이 뺀다).
    배치의 event_id 를 broadcast 해 두 표에 semi-join 한다 — Silver 쪽은 한 번 훑고 shuffle 하지 않는다(broadcast 크기는 배치 크기에 묶인다).
    `broadcast=False` 면 힌트 없이 semi-join 한다(배치 고유 ID 가 BROADCAST_LIMIT 를 넘을 때 — SL-3c 결정 S3, 판정은 같다).
    반환: (MERGE 할 접은 행 DF = 판정이 없는 접은 행, 충돌 판정 DF — event_id·reason·existing_event_id·existing_event_date).
    MERGE 쪽은 판정 DF 를 빼는 꼴이라, 부르는 쪽이 판정 DF 를 persist 하면 Silver 를 한 번만 훑는다(judge)."""
    ids = folded.select("event_id")
    ids = F.broadcast(ids) if broadcast else ids
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
    decided = joined.where(F.col("_reason").isNotNull()).select(
        "event_id", F.col("_reason").alias("reason"), F.when(F.col("_n").isNotNull(), F.col("event_id")).alias("existing_event_id"),
        F.col("_date").alias("existing_event_date"))
    merge = folded.join(decided.select("event_id"), "event_id", "left_anti").select(*[name for name, _, _ in contracts.MERGE_SOURCE_FIELDS])
    return merge, decided


def listed_frame(spark, lines):
    """격리 목록 줄(control.load_quarantine) → DataFrame(event_id + contracts.QUARANTINE_LIST_FIELDS). listed_at 은 세션 UTC 로 읽는다(부르는 쪽이 UTC 확인)."""
    rows = [(line["event_id"], line["reason"], line["incident"], line["listed_by"], line["listed_at"]) for line in lines]
    frame = spark.createDataFrame(rows, "event_id string, quarantine_reason string, quarantine_incident string, quarantine_listed_by string, listed_at string")
    return frame.select("event_id", "quarantine_reason", "quarantine_incident", "quarantine_listed_by",
                        F.to_timestamp("listed_at", "yyyy-MM-dd'T'HH:mm:ss'Z'").alias("quarantine_listed_at"))


def quarantine_check(listed_rows, quarantined, conflict_ids):
    """격리 목록 ID 의 접은 행 판정(SL-9 — 계획 38 §2.3 ②). `listed_rows`: 목록 ID 의 접은 행(작음), `quarantined`: 격리 표 투영
    (event_id·event_date·content_sha256), `conflict_ids`: 오류 표 conflict ID. 오류 표 conflict 에 있음 → conflict((a)와 같음), 격리 표에 없음 → 넣음,
    같은 해시 → copies, 다른 해시 → conflict(격리 표 그대로). 반환: (격리 MERGE 원본 = 판정이 없는 행, 충돌 판정 DF — full_check 와 같은 열)."""
    ids = F.broadcast(listed_rows.select("event_id"))
    existing = (quarantined.select("event_id", "event_date", "content_sha256").join(ids, "event_id", "left_semi").groupBy("event_id")
                .agg(F.min("event_date").alias("_date"), F.min("content_sha256").alias("_sha"), F.max("content_sha256").alias("_sha_max")))
    known = conflict_ids.select("event_id").join(ids, "event_id", "left_semi").distinct().withColumn("_known", F.lit(True))
    joined = listed_rows.join(existing, "event_id", "left").join(known, "event_id", "left")
    reason = (F.when(F.col("_known"), F.lit("이미 conflict 로 오류 표에 있는 event_id 의 새 사본"))
              .when(F.col("_sha").isNotNull() & ((F.col("_sha") != F.col("_sha_max")) | (F.col("_sha") != F.col("content_sha256"))),
                    F.lit("격리 목록 ID 의 새 사본 — 격리 표의 그 행과 내용이 다르다")))
    decided = joined.withColumn("_reason", reason).where(F.col("_reason").isNotNull()).select(
        "event_id", F.col("_reason").alias("reason"), F.when(F.col("_sha").isNotNull(), F.col("event_id")).alias("existing_event_id"),
        F.col("_date").alias("existing_event_date"))
    return listed_rows.join(decided.select("event_id"), "event_id", "left_anti"), decided


BROADCAST_LIMIT = 1_000_000  # 배치 고유 event_id 가 이만큼까지 broadcast(15 §2.1b 실측 범위). 넘으면 broadcast 없이 — SL-3c 결정 S3
Judgement = namedtuple("Judgement", "stop merge errors stops mismatches ids broadcast check_seconds cached quarantine")


def judge(ok, errors, lines, bronze_table, silver, conflict_ids, broadcast_limit=BROADCAST_LIMIT, quarantine_lines=(), quarantined=None):
    """배치 판정(15 §2.1 2~4단계, 쓰기 없음). 입력은 check_adapted 를 지난 어댑터 결과(dedupe_bronze 를 지난 배치에서 나온 것).
    반환 Judgement:
    - stop: 넘기지 못한 멈춤 줄이 있으면 참 — 그때는 접기·전체 확인을 하지 않고 merge·errors 는 None, stops 에 진단 행(D4).
    - merge: MERGE 원본(contracts.MERGE_SOURCE_SCHEMA, silver_written_at·silver_release 는 쓸 때 채움).
    - errors: 오류 표 행(contracts.JUDGED_ERROR_SCHEMA) — 어댑터 오류, 넘긴 멈춤 줄, 충돌 사본(배치 안 충돌, (a)~(c) — 그 ID 의 이번 배치 사본 모두).
      contracts.error_rows 로 배치 열을 붙여 쓴다.
    - stops: 진단 행(stop 이 거짓이면 빈 DF). mismatches: 행 수가 다른 범위 줄.
    - ids: 접은 행 수(= broadcast 크기), broadcast: 전체 확인을 broadcast 로 했나, check_seconds: 전체 확인(4단계) 시간(드라이버 단조 시계).
    - cached: 판정이 persist 한 DF(접은 행·충돌 판정) — 부르는 쪽이 쓰기를 마친 뒤 unpersist 한다(SL-3b 리뷰 참고 2).
    - quarantine: (SL-9) 격리 MERGE 원본(MERGE_SOURCE 열 + 목록 열) 또는 None(목록이 빔). 목록 ID 의 접은 행은 본 표 전체 확인에 들어가지 않는다 —
      격리 '목록'과 맞춘다(격리 '표'와 맞추면 새 세대·Bronze 재생에서 유령이 (d)로 본 표에 되살아남 — 15 §9 37). `quarantined`: 격리 표 투영."""
    _require_utc(ok.sparkSession)
    adapter_errors, stops, mismatches = acknowledge(errors, lines, bronze_table)
    if stops.limit(1).count():
        return Judgement(True, None, None, stops, mismatches, None, None, None, (), None)
    folded, in_batch = fold(ok)
    folded = folded.persist()  # 접기 창(정상 행 셔플)이 broadcast 크기 세기·전체 확인·MERGE 원본에서 다시 돌지 않게
    main, listed = folded, None
    if quarantine_lines:
        listed = listed_frame(ok.sparkSession, quarantine_lines)
        main = folded.join(F.broadcast(listed.select("event_id")), "event_id", "left_anti")
    ids = main.count()
    use_broadcast = ids <= broadcast_limit
    started = time.monotonic()
    merge, decided = full_check(main, silver, conflict_ids, broadcast=use_broadcast)
    quarantine = None
    if listed is not None:
        to_quarantine, held = quarantine_check(folded.join(F.broadcast(listed.select("event_id")), "event_id", "left_semi"), quarantined, conflict_ids)
        decided, quarantine = decided.unionByName(held), to_quarantine.join(listed, "event_id")
    decided = decided.persist()  # Silver 투영·오류 표를 한 번만 훑는다(MERGE 원본과 충돌 행이 함께 씀)
    decided.count()
    check_seconds = time.monotonic() - started
    in_batch_rows = _judged(in_batch.select(*_BRONZE_NAMES, F.lit("conflict").alias("error_code"),
                                            F.lit("배치 안에서 같은 event_id 의 내용이 다르다").alias("reason"), "event_id"))
    decided_rows = ok.join(decided, "event_id").select(*_BRONZE_NAMES, F.lit("conflict").alias("error_code"), "reason", "event_id",
                                                        "existing_event_id", "existing_event_date")
    decided_rows = _judged(decided_rows, existing_id=F.col("existing_event_id"), existing_date=F.col("existing_event_date"))
    return Judgement(False, merge, adapter_errors.unionByName(in_batch_rows).unionByName(decided_rows), stops, mismatches,
                     ids, use_broadcast, check_seconds, (folded, decided), quarantine)


# ---------------------------------------------------------------- 쓰기·멱등·정체성(SL-3c)

TXN_CONF = ("spark.databricks.delta.write.txnAppId", "spark.databricks.delta.write.txnVersion", "spark.databricks.delta.commitInfo.userMetadata")
RETENTION = "delta.setTransactionRetentionDuration"  # 설정되면 오래된 멱등 기록(SetTransaction)이 지워진다 — 그런 표에서는 돌지 않는다
MERGE_SQL = """MERGE INTO delta.`{path}` t USING silver_merge_source s
  ON t.event_date = s.event_date AND t.event_id = s.event_id
WHEN MATCHED AND t.content_sha256 = s.content_sha256 THEN UPDATE SET t.copies = t.copies + s.copies
WHEN NOT MATCHED THEN INSERT *"""  # 15 §2.1. 해시가 다른 같은 ID 는 전체 확인이 먼저 막는다
# (SL-9) 배치 격리 MERGE — 같은 해시면 copies 만(quarantined_at 은 처음 넣을 때 그대로), 다른 해시는 판정이 먼저 뺀다(quarantine_check)
QUARANTINE_MERGE_SQL = """MERGE INTO delta.`{path}` t USING silver_merge_source s ON t.event_id = s.event_id
WHEN MATCHED AND t.content_sha256 = s.content_sha256 THEN UPDATE SET t.copies = t.copies + s.copies
WHEN NOT MATCHED THEN INSERT *"""
# 시작 옮기기(계획 38 §2.3 ①) — 둘 다 txn 없이 키로 멱등, 할 일이 없으면 커밋도 없다(38 §1.3 ④⑥). Delta 4.3.1 은 DELETE 의 하위 질의를 받지 않는다(①)
MOVE_INSERT_SQL = "MERGE INTO delta.`{path}` t USING silver_quarantine_source s ON t.event_id = s.event_id WHEN NOT MATCHED THEN INSERT *"
MOVE_DELETE_SQL = "MERGE INTO delta.`{path}` t USING silver_quarantine_source s ON t.event_id = s.event_id WHEN MATCHED THEN DELETE"


class SilverStop(RuntimeError):
    """넘기지 못한 멈춤 줄이 있어 배치를 멈췄다 — 진단 행은 오류 표에 남겼다(D4 `stops` appId). 넘김 목록에 줄을 더하거나 원인을 고친 뒤 다시 돈다."""


class QuarantineError(RuntimeError):
    """시작 옮기기(SL-9)가 앞조건·확인에서 어긋났다 — 스트림 전에 멈춘다(rc 1). 사람이 두 표를 본다(runbook 유령 격리 절)."""


def _jvm(spark):
    return spark.sparkContext._jvm


def table_state(spark, path):
    """Delta 표의 (판, txn appId 집합, RETENTION 설정 여부). 없는 표는 (-1, set(), False).
    Python delta 패키지가 없어 py4j 로 DeltaLog 스냅샷을 읽는다 — Delta 4.3.1 의 `DeltaLog.update(boolean, Option, Option)`·
    `Snapshot.setTransactions()`(javap 확인). 내부 API 라 jar 판을 올릴 때 시험(spark_job)이 잡는다(15 §2.1)."""
    none = _jvm(spark).scala.Option.empty()
    snapshot = _jvm(spark).org.apache.spark.sql.delta.DeltaLog.forTable(spark._jsparkSession, path).update(False, none, none)
    txns = snapshot.setTransactions()
    app_ids = {txns.apply(i).appId() for i in range(txns.length())}
    return snapshot.version(), app_ids, bool(snapshot.version() >= 0 and snapshot.metadata().configuration().contains(RETENTION))


def checkpoint_state(spark, checkpoint):
    """Spark checkpoint 의 (metadata 식별자 또는 None, 끝난 배치(commits/ 의 숫자 파일)가 있나). Hadoop FS 로 읽어 file://·s3a:// 모두 같다."""
    jvm = _jvm(spark)
    root = jvm.org.apache.hadoop.fs.Path(checkpoint)
    fs = root.getFileSystem(spark.sparkContext._jsc.hadoopConfiguration())
    metadata, commits = jvm.org.apache.hadoop.fs.Path(root, "metadata"), jvm.org.apache.hadoop.fs.Path(root, "commits")
    ident = None
    if fs.exists(metadata):
        stream = fs.open(metadata)
        try:
            ident = control.checkpoint_id(jvm.org.apache.commons.io.IOUtils.toString(stream, "UTF-8"))
        finally:
            stream.close()
    finished = bool(fs.exists(commits) and any(status.getPath().getName().isdigit() for status in fs.listStatus(commits)))
    return ident, finished


def _ddl(fields):
    return ", ".join(f"`{name}` {kind}{'' if nullable else ' NOT NULL'}" for name, kind, nullable in fields)


def prepare(spark, settings):
    """잡 시작 검사(스트림 쿼리 전, SL-3c 결정 S2): 한 프로세스 한 쿼리, RETENTION, 시작 검사 ①~④, 세 표 만들기(없을 때만 — (SL-9) 격리 표 포함).
    반환: checkpoint 식별자(없으면 None — 첫 배치에서 metadata 를 읽는다)."""
    if spark.streams.active:
        raise RuntimeError("한 프로세스에 스트림 쿼리는 하나다 — 이미 도는 쿼리가 있다")
    ident, finished = checkpoint_state(spark, settings.checkpoint)
    tables = ((settings.silver, contracts.SILVER_FIELDS, " PARTITIONED BY (event_date)"),  # SV4: produced_at 의 UTC 날짜
              (settings.errors, contracts.ADAPTER_ERROR_FIELDS + contracts.ERROR_EXTRA_FIELDS, ""),
              (settings.quarantine, contracts.QUARANTINE_FIELDS, ""))  # 드물고 작아 나누지 않는다(계획 38 §2.2)
    states = [table_state(spark, path) for path, _, _ in tables]
    if any(retention for _, _, retention in states):
        raise control.IdentityError(f"표 속성에 {RETENTION} 이 있다 — 멱등 기록이 지워질 수 있어 돌지 않는다")
    control.start_check(states[0][1], states[1][1], states[2][1], domain=settings.domain, generation=settings.generation, checkpoint=ident,
                        finished=finished)
    for (path, fields, extra), (version, _, _) in zip(tables, states):
        if version < 0:
            spark.sql(f"CREATE TABLE IF NOT EXISTS delta.`{path}` ({_ddl(fields)}) USING delta{extra}")
            continue
        actual = [(f.name, f.dataType.simpleString()) for f in spark.read.format("delta").load(path).schema.fields]
        if actual != [(name, contracts._TYPES[kind].simpleString()) for name, kind, _ in fields]:
            raise control.IdentityError(f"{path} 의 열이 이 판의 표 모양과 다르다 — 새 세대로 돌린다: {actual}")
    return ident


def merge_events(session, path, merge, app_id, batch_id, metadata, sql=MERGE_SQL):
    """Silver MERGE(15 §2.1 SQL — (SL-9) 격리 MERGE 는 `sql=QUARANTINE_MERGE_SQL`). 멱등 설정 둘과 userMetadata 를 **배치 세션**에 두고
    (C2 — 바깥 세션 설정은 foreachBatch 안 MERGE 에 보이지 않음), 성공·실패 모두 finally 로 셋 다 지운다(SL-2 ④ 남으면 다른 표에 엉뚱한 기록,
    ⑦ 하나만 남으면 다음 DML 오류)."""
    merge.createOrReplaceTempView("silver_merge_source")
    try:
        for key, value in zip(TXN_CONF, (app_id, str(batch_id), json.dumps(metadata, ensure_ascii=False, sort_keys=True))):
            session.conf.set(key, value)
        session.sql(sql.format(path=path))
    finally:
        for key in TXN_CONF:
            session.conf.unset(key)
        session.catalog.dropTempView("silver_merge_source")


def append_errors(rows, path, app_id, batch_id):
    """오류 표 append — 쓰기 옵션 멱등(`txnAppId`·`txnVersion` = batchId, SL-2 ⑤). 같은 (appId, version)은 다시 쓰지 않는다."""
    rows.write.format("delta").mode("append").option("txnAppId", app_id).option("txnVersion", batch_id).save(path)


def _silver_columns(frame):
    return frame.select(*[name for name, _, _ in contracts.SILVER_FIELDS])


def _run_sql(spark, frame, sql):
    frame.createOrReplaceTempView("silver_quarantine_source")
    try:
        spark.sql(sql)
    finally:
        spark.catalog.dropTempView("silver_quarantine_source")


def move_quarantined(spark, settings, lines, release, log=print):
    """시작 옮기기(SL-9 — 계획 38 §2.3 ①, prepare 뒤·스트림 전, 표 하나에 쓰는 쪽은 잡 하나). `lines`: control.load_quarantine 결과.
    본 표에 남은 목록 ID 를 격리 표로 옮긴다: 전 값(목록 ID 에 한정한 (본 ∪ 격리)의 (ID 집합, copies 합) — Silver 열의 서로 다른 행, D7) →
    지우기 전 앞조건(두 표에 다 있는 목록 ID 는 Silver 열이 같아야 함 — 4와 6 사이에서 죽은 상태뿐) → 격리 표 NOT MATCHED 넣기 → 본 표 MATCHED 지우기 →
    확인(본 ∩ (격리 ∩ 목록) = ∅, 전 = 뒤). 어긋나면 QuarantineError(rc 1). 어디에도 없는 목록 ID 는 "아직"(멈추지 않음 — 15 §9 37).
    반환: {"moved", "already", "pending"}."""
    if not lines:
        return {"moved": 0, "already": 0, "pending": 0}
    _require_utc(spark)
    started = time.monotonic()
    listed = listed_frame(spark, lines)
    ids = F.broadcast(listed.select("event_id"))

    def read(path):  # 쓰기 뒤에는 새로 읽는다
        return spark.read.format("delta").load(path)

    def found(path):
        return {row[0] for row in read(path).select("event_id").join(ids, "event_id", "left_semi").distinct().collect()}

    def totals():
        rows = _silver_columns(read(settings.silver)).join(ids, "event_id", "left_semi").unionByName(
            _silver_columns(read(settings.quarantine)).join(ids, "event_id", "left_semi")).distinct()
        row = rows.agg(F.sort_array(F.collect_set("event_id")).alias("ids"), F.sum("copies").alias("copies")).first()
        return list(row["ids"]), row["copies"] or 0

    in_main, in_quarantine = found(settings.silver), found(settings.quarantine)
    counts = {"moved": len(in_main), "already": len(in_quarantine - in_main),
              "pending": len({line["event_id"] for line in lines} - in_main - in_quarantine)}
    if in_main:
        before = totals()
        both = in_main & in_quarantine
        if both:
            pick = F.broadcast(spark.createDataFrame([(i,) for i in sorted(both)], "event_id string"))
            ours, theirs = (_silver_columns(read(path)).join(pick, "event_id", "left_semi") for path in (settings.silver, settings.quarantine))
            if ours.exceptAll(theirs).limit(1).count() or theirs.exceptAll(ours).limit(1).count():
                raise QuarantineError(f"본 표와 격리 표에 같은 목록 ID 가 다른 행으로 있다 — 지우지 않고 멈춘다(되돌리기 순서·손으로 고친 표를 본다): {sorted(both)[:10]}")
        moved_at = datetime.now(timezone.utc)
        source = _silver_columns(read(settings.silver)).join(ids, "event_id", "left_semi").join(listed, "event_id").select(
            *[F.col(name) for name, _, _ in contracts.SILVER_FIELDS + contracts.QUARANTINE_LIST_FIELDS],
            F.lit(moved_at).cast("timestamp").alias("quarantined_at"), F.lit(release).alias("quarantine_release"))
        _run_sql(spark, source, MOVE_INSERT_SQL.format(path=settings.quarantine))
        _run_sql(spark, read(settings.quarantine).select("event_id").join(ids, "event_id", "left_semi").distinct(), MOVE_DELETE_SQL.format(path=settings.silver))
        left = found(settings.silver) & found(settings.quarantine)
        after = totals()
        if left or after != before:
            raise QuarantineError(f"격리 옮기기 확인이 어긋났다 — 두 표에 남은 ID {sorted(left)[:10]}, 전 (ID {len(before[0])}·copies {before[1]}) "
                                  f"뒤 (ID {len(after[0])}·copies {after[1]})")
    log(f"silver quarantine moved={counts['moved']} already={counts['already']} pending={counts['pending']} seconds={round(time.monotonic() - started, 3)}")
    return counts


BatchContext = namedtuple("BatchContext", "settings adapt lines quarantine release log broadcast_limit checkpoint")


def _table_size(session, path):
    """(파일 수, 행 수) — DESCRIBE DETAIL 과 Delta 통계로 센다(count 는 LocalTableScan, 다시 훑지 않음 — 2026-11-04 실측)."""
    return session.sql(f"DESCRIBE DETAIL delta.`{path}`").first().numFiles, session.read.format("delta").load(path).count()


def process_batch(ctx, batch, batch_id):
    """foreachBatch 한 번(15 §2.1b 순서): dedupe_bronze → 어댑터 → check_adapted → judge → (멈춤이면 진단 append 뒤 SilverStop)
    → MERGE(빈 배치도 — 시작 검사 ④) → 오류 append. 쓰기는 모두 batch_df.sparkSession 에서 한다(C2).
    비용 기록(15 §2.1 Ads ③·SL-3b 리뷰 참고 2): 단계 시간·표 크기를 로그 줄과 MERGE 커밋 userMetadata 에 남긴다."""
    session, settings, clock = batch.sparkSession, ctx.settings, time.monotonic
    _require_utc(session)
    ident = ctx.checkpoint()
    app = {table: control.txn_app_id(settings.domain, settings.generation, table, ident) for table in control.TXN_TABLES}
    record = {"batch_id": batch_id, "silver_release": ctx.release}
    started = clock()
    try:
        batch = batch.persist()
        record["bronze_rows"] = batch.count()
        moment = clock()
        kept, record["bronze_duplicates"] = dedupe_bronze(batch)
        record["dedupe_seconds"] = round(clock() - moment, 3)
        if record["bronze_duplicates"]:
            ctx.log(f"silver alert bronze_duplicates batch={batch_id} dropped={record['bronze_duplicates']}")
        moment = clock()
        ok, errors = ctx.adapt(kept, settings.source)  # 어댑터는 UDF 를 거친 중간 결과를 cache 한다(머리말) — 두 결과를 세면 UDF 가 한 번 돈다
        record["ok_rows"], record["adapter_error_rows"] = ok.count(), errors.count()
        record["udf_seconds"] = round(clock() - moment, 3)
        ctx.log(f"silver udf batch={batch_id} rows={record['bronze_rows']} seconds={record['udf_seconds']}")
        moment = clock()
        check_adapted(kept, ok, errors, settings.domain)
        record["check_adapted_seconds"] = round(clock() - moment, 3)
        (silver_files, silver_rows), (error_files, error_rows) = _table_size(session, settings.silver), _table_size(session, settings.errors)
        error_table = session.read.format("delta").load(settings.errors)
        judgement = judge(ok, errors, ctx.lines, session.read.format("delta").load(settings.bronze),
                          session.read.format("delta").load(settings.silver).select("event_id", "event_date", "content_sha256"),
                          error_table.where(F.col("error_code") == "conflict").select("event_id"), broadcast_limit=ctx.broadcast_limit,
                          quarantine_lines=ctx.quarantine, quarantined=session.read.format("delta").load(settings.quarantine))
        detected = datetime.now(timezone.utc)
        if judgement.stop:
            append_errors(contracts.error_rows(judgement.stops, silver_release=ctx.release, detected_at=detected, batch_id=batch_id),
                          settings.errors, app["stops"], batch_id)
            ctx.log(f"silver stop batch={batch_id} mismatches={len(judgement.mismatches)}")
            raise SilverStop(f"Silver 배치 {batch_id} 를 멈춘다 — 넘기지 못한 멈춤 줄(foreign_source·not_insert)이 있다. 오류 표 진단 줄(acknowledged=false)을 본다"
                             + "".join(f"; 범위 줄 행 수가 다르다 {line['topic']}/{line['partition']}/{line['start']}~{line['end']}: 적힌 {line['row_count']} · 센 {counted}"
                                       for line, counted in judgement.mismatches))
        record["check"] = {"seconds": round(judgement.check_seconds, 3), "ids": judgement.ids, "broadcast": judgement.broadcast,
                           "silver_files": silver_files, "silver_rows": silver_rows, "error_files": error_files, "error_rows": error_rows}
        ctx.log(f"silver check batch={batch_id} silver_files={silver_files} silver_rows={silver_rows} error_files={error_files} "
                f"error_rows={error_rows} ids={judgement.ids} broadcast={str(judgement.broadcast).lower()} seconds={record['check']['seconds']}")
        written = {"silver_written_at": F.lit(detected).cast("timestamp"), "silver_release": F.lit(ctx.release)}
        merge = judgement.merge.select(*[written.get(name, F.col(name)).alias(name) for name, _, _ in contracts.SILVER_FIELDS])
        quarantine = None
        if judgement.quarantine is not None:  # (SL-9) 목록 ID 의 사본 — 할 일이 있을 때만 격리 MERGE(D3 (가): 빈 MERGE 는 txn 때문에 커밋을 남긴다)
            moved = {**written, "quarantined_at": F.lit(detected).cast("timestamp"), "quarantine_release": F.lit(ctx.release)}
            quarantine = judgement.quarantine.select(*[moved.get(name, F.col(name)).alias(name) for name, _, _ in contracts.QUARANTINE_FIELDS])
            record["quarantine_rows"] = quarantine.count()
        record["pre_write_seconds"] = round(clock() - started, 3)  # 쓰기 앞까지(쓰기 시간은 커밋 기록·로그 줄의 전체 시간에서 뺀다)
        merge_events(session, settings.silver, merge, app["events"], batch_id, record)
        if record.get("quarantine_rows"):
            merge_events(session, settings.quarantine, quarantine, app["quarantine"], batch_id, record, sql=QUARANTINE_MERGE_SQL)
            ctx.log(f"silver quarantine batch={batch_id} rows={record['quarantine_rows']}")
        append_errors(contracts.error_rows(judgement.errors, silver_release=ctx.release, detected_at=detected, batch_id=batch_id),
                      settings.errors, app["errors"], batch_id)
        ctx.log(f"silver batch batch={batch_id} bronze_rows={record['bronze_rows']} ok_rows={record['ok_rows']} "
                f"adapter_error_rows={record['adapter_error_rows']} seconds={round(clock() - started, 3)}")
    finally:
        session.catalog.clearCache()  # 이 배치의 persist·어댑터 cache 를 모두 푼다(한 프로세스 한 쿼리라 다른 쿼리의 cache 는 없다)
