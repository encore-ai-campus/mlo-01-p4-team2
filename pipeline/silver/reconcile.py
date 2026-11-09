"""Bronze ↔ Silver 대사(SV8 — 계획 15 §2 SL-6 6판 r9, 계획 temp/pipeline/35_silver-reconcile-sl6.md 결정 D1~D10).

Silver 잡(`silver_job.main`)이 스트림이 성공한 뒤 같은 세션에서 부른다(D2). 결과 표에 쓰는 쪽은 Silver 잡 하나다(lockf·`concurrencyPolicy: Forbid`).
- 범위(D1): Silver checkpoint 의 마지막 끝난 배치 offset → Bronze `versionAsOf V`(= Silver 가 처리한 행 — control.processed_bronze_version).
  Silver·오류 표는 판을 먼저 읽고(framework.table_state) 그 판으로 읽는다 — 기록하는 판이 실제로 읽은 판이다.
- 맞추는 법(D10 — Bronze 행 하나는 오류 표 한 줄이 되거나 Silver 행 하나의 copies 하나가 된다):
  ① 오류 표(진단 줄 = 멈춤 코드 + acknowledged=false 는 뺌)는 Bronze 위치로. 위치가 Bronze(V)에 없으면 mismatch, 오류 표 안에 두 번 이상이면
     copies_mismatch(부풂 — 배치를 넘는 이중 적재·오류 append 멱등 깨짐·진단 줄 빼기 실패. 행을 잃는 일이 아니다).
  ② 오류 표에 위치가 없는 Bronze 행은 UTC 시 창마다 Silver 와: 고유 ID 수·ids_sha256 이 같고, 고유 위치 수 = copies 합
     (D5: 위치 수 > copies 합 = Bronze 행이 어디에도 없음 → mismatch, 위치 수 < copies 합 = 부풂 → copies_mismatch — 경보만, Gold 확정은 막지 않음).
  ③ 오류 표에 없는데 창·ID 를 꺼낼 수 없는 Bronze 행 → mismatch. 어댑터가 정상으로 받는 행은 이 식으로 늘 꺼내진다 — 해석은 어댑터와 같은 식
     (try_parse_json(value_binary 문자열)), 값은 같은 도우미(contracts.field)·같은 경로(원천 설정 hop_rules 의 id·time — 시험이 어댑터 경로와 같음을 본다).
  합집합("Silver ∪ 오류")이 아니라 나누는 까닭: 같은 창에 X 의 오류 사본이 있으면 합집합은 Silver 에서 빠진 X 를 가린다(계획 35 리뷰 R1).
- 두 번째 그물(D4): Silver 안 event_id 중복 0 을 매 회차. 중복 ID 가 놓인 창은 mismatch.
- ids_sha256 = sha2(concat_ws('\\n', array_sort(collect_set(id))), 256) — hop_rules 의 정의(오름차순, '\\n', 끝 줄바꿈 없음, 0건은 빈 바이트의 해시)와
  같은 값임을 시험이 본다(이름 하나·서로 다른 ID 일 때). 창은 date_trunc('HOUR') — 세션 시간대 UTC 에서만 돈다.
- 기록(D6): Delta 표 `<lake>/silver/<원천>/reconcile/g<N>` 을 회차마다 덮어쓴다(창마다 한 행 + errors 줄 + unplaced 줄 — 표 이력이 회차 이력).
  커밋 userMetadata = 회차 요약의 hop_rules.canonical_json. 예시: 창 줄은 ID(missing = Bronze 에만, extra = Silver 에만),
  errors 줄은 위치(extra = Bronze 에 없거나 겹친 오류 행), unplaced 줄은 위치(missing). 로그 줄 `silver reconcile`·`silver alert reconcile_mismatch`·
  `silver alert reconcile_copies`·`silver alert silver_duplicates`. 요약 status 는 가장 무거운 줄(mismatch > copies_mismatch > match).
  불일치가 하나라도 있으면(copies_mismatch 포함) Result.ok 가 거짓 — 잡은 rc 3(D7).
"""
import time
from collections import namedtuple
from datetime import datetime, timezone

from pyspark.sql import functions as F, types as T

from pipeline.common import hop_rules
from pipeline.silver import contracts, control, framework

SOURCES = {"metaverse": hop_rules.METAVERSE}  # 원천 → 창·ID 를 꺼낼 after 열(설정의 id·time). 광고는 SL-8 이 hop_rules.ADS 로 더한다
EXAMPLES = 10  # 줄마다 예시 수, 불일치 경보 줄 수
MATCH, MISMATCH, COPIES = "match", "mismatch", "copies_mismatch"
POSITION = framework.POSITION
_STRINGS = T.ArrayType(T.StringType())
RESULT_SCHEMA = T.StructType([T.StructField(name, kind) for name, kind in (
    ("kind", T.StringType()), ("window_start", T.TimestampType()), ("window_end", T.TimestampType()), ("status", T.StringType()),
    ("bronze_ids", T.LongType()), ("bronze_ids_sha256", T.StringType()), ("bronze_rows", T.LongType()),
    ("silver_ids", T.LongType()), ("silver_ids_sha256", T.StringType()), ("silver_copies", T.LongType()), ("silver_duplicate_ids", T.LongType()),
    ("error_rows", T.LongType()), ("errors_not_in_bronze", T.LongType()), ("errors_repeated", T.LongType()),
    ("missing_examples", _STRINGS), ("extra_examples", _STRINGS),
    ("bronze_version", T.LongType()), ("silver_version", T.LongType()), ("errors_version", T.LongType()),
    ("checkpoint_id", T.StringType()), ("silver_release", T.StringType()), ("reconciled_at", T.TimestampType()))])
Result = namedtuple("Result", "ok summary")


def processed_version(spark, checkpoint):
    """Silver checkpoint(Hadoop FS — file://·s3a:// 같음)의 offsets·commits 로 처리를 끝낸 Bronze 판 V(없으면 None). 판정은 control.processed_bronze_version."""
    jvm = framework._jvm(spark)
    root = jvm.org.apache.hadoop.fs.Path(checkpoint)
    fs = root.getFileSystem(spark.sparkContext._jsc.hadoopConfiguration())

    def ids(name):
        folder = jvm.org.apache.hadoop.fs.Path(root, name)
        return [int(s.getPath().getName()) for s in fs.listStatus(folder) if s.getPath().getName().isdigit()] if fs.exists(folder) else []

    offsets, commits = ids("offsets"), ids("commits")
    text = ""
    if commits and max(commits) in offsets:
        stream = fs.open(jvm.org.apache.hadoop.fs.Path(root, f"offsets/{max(commits)}"))
        try:
            text = jvm.org.apache.commons.io.IOUtils.toString(stream, "UTF-8")
        finally:
            stream.close()
    return control.processed_bronze_version(offsets, commits, text)


def _position():
    return F.concat_ws(":", *POSITION)


def _ids(column):
    return F.array_sort(F.collect_set(column))


def _sha(ids):
    return F.sha2(F.concat_ws("\n", ids), 256)


def place(bronze, source):
    """Bronze 행 → (위치 열, id, window). 해석은 어댑터와 같은 식, 꺼내기는 contracts.field(어댑터와 같은 도우미). 꺼낼 수 없으면 null."""
    staged = bronze.withColumn("_v", F.try_parse_json(F.col("value_binary").cast("string")))  # 한 번만 해석(아래 두 꺼내기가 같은 값을 쓴다)
    ident = contracts.field(F.col("_v"), f"$.after.{source['id']}", "string")[0]
    stamp = contracts.field(F.col("_v"), f"$.after.{source['time']}", "timestamp")[0]
    return staged.select(*POSITION, F.lower(ident).alias("id"), F.date_trunc("HOUR", stamp).alias("window"))


def _examples(frame):
    return [r[0] for r in frame.orderBy(*POSITION).limit(EXAMPLES).select(_position()).collect()]


def compare(spark, bronze, silver, errors, source):
    """대사 계산(쓰기 없음). 반환: (결과 DataFrame — RESULT_SCHEMA 의 앞 16열, 회차 수치 dict). 세 입력은 판을 고정해 읽은 DataFrame 이다."""
    kept = errors.where(~(F.col("error_code").isin(*control.STOP_CODES) & ~F.col("acknowledged")))  # 진단 줄(D4)은 뺀다
    error_positions = kept.select(*POSITION).persist()
    bronze_positions = bronze.select(*POSITION).distinct()
    repeated = error_positions.groupBy(*POSITION).count().where(F.col("count") > 1).drop("count")
    missing_from_bronze = error_positions.distinct().join(bronze_positions, list(POSITION), "left_anti")
    rest = place(bronze.join(error_positions.distinct(), list(POSITION), "left_anti"), source).persist()  # ② ③ — 오류 표에 위치가 없는 Bronze 행
    silver_rows = silver.select("event_id", F.date_trunc("HOUR", "produced_at").alias("window"), "copies").persist()
    try:
        counts = {"error_rows": error_positions.count(), "errors_not_in_bronze": missing_from_bronze.count(), "errors_repeated": repeated.count(),
                  "bronze_rows": bronze_positions.count()}
        error_examples = sorted(_examples(missing_from_bronze) + _examples(repeated))[:EXAMPLES]
        unplaced = rest.where(F.col("id").isNull() | F.col("window").isNull())
        counts["unplaced"] = unplaced.select(*POSITION).distinct().count()
        unplaced_examples = _examples(unplaced.select(*POSITION).distinct())
        placed = rest.where(F.col("id").isNotNull() & F.col("window").isNotNull())
        duplicate_ids = silver_rows.groupBy("event_id").count().where(F.col("count") > 1).select("event_id")
        counts["duplicates"] = duplicate_ids.count()
        counts["duplicate_examples"] = [r[0] for r in duplicate_ids.orderBy("event_id").limit(EXAMPLES).collect()]
        windows = (placed.groupBy("window").agg(_ids("id").alias("b_set"))
                   .join(placed.select("window", *POSITION).distinct().groupBy("window").agg(F.count(F.lit(1)).alias("b_rows")), "window", "full")
                   .join(silver_rows.groupBy("window").agg(_ids("event_id").alias("s_set"), F.sum("copies").alias("s_copies")), "window", "full")
                   .join(silver_rows.join(duplicate_ids, "event_id").groupBy("window").agg(F.countDistinct("event_id").alias("dups")), "window", "full"))
        empty = F.array().cast(_STRINGS)
        b_set, s_set = F.coalesce(F.col("b_set"), empty), F.coalesce(F.col("s_set"), empty)
        zero = F.lit(0).cast("bigint")
        windows = windows.select(
            F.lit("window").alias("kind"), F.col("window").alias("window_start"), (F.col("window") + F.expr("INTERVAL 1 HOUR")).alias("window_end"),
            F.size(b_set).cast("bigint").alias("bronze_ids"), _sha(b_set).alias("bronze_ids_sha256"), F.coalesce(F.col("b_rows").cast("bigint"), zero).alias("bronze_rows"),
            F.size(s_set).cast("bigint").alias("silver_ids"), _sha(s_set).alias("silver_ids_sha256"),
            F.coalesce(F.col("s_copies").cast("bigint"), zero).alias("silver_copies"), F.coalesce(F.col("dups").cast("bigint"), zero).alias("silver_duplicate_ids"),
            F.lit(None).cast("bigint").alias("error_rows"), F.lit(None).cast("bigint").alias("errors_not_in_bronze"), F.lit(None).cast("bigint").alias("errors_repeated"),
            F.slice(F.array_except(b_set, s_set), 1, EXAMPLES).alias("missing_examples"), F.slice(F.array_except(s_set, b_set), 1, EXAMPLES).alias("extra_examples"))
        same = (F.col("bronze_ids") == F.col("silver_ids")) & (F.col("bronze_ids_sha256") == F.col("silver_ids_sha256"))
        windows = windows.withColumn("status", F.when(~same | (F.col("bronze_rows") > F.col("silver_copies")) | (F.col("silver_duplicate_ids") > 0), MISMATCH)
                                     .when(F.col("bronze_rows") < F.col("silver_copies"), COPIES).otherwise(MATCH))
        error_status = MISMATCH if counts["errors_not_in_bronze"] else COPIES if counts["errors_repeated"] else MATCH
        extra = spark.createDataFrame([
            ("errors", None, None, error_status, None, None, None, None, None, None, None,
             counts["error_rows"], counts["errors_not_in_bronze"], counts["errors_repeated"], [], error_examples),
            ("unplaced", None, None, MISMATCH if counts["unplaced"] else MATCH, None, None, counts["unplaced"], None, None, None, None,
             None, None, None, unplaced_examples, [])], T.StructType(RESULT_SCHEMA.fields[:16]))
        result = windows.unionByName(extra).localCheckpoint()  # 아래 세기·쓰기가 다시 계산하지 않게(입력 persist 를 풀어도 된다)
    finally:
        for frame in (error_positions, rest, silver_rows):
            frame.unpersist()
    return result, counts


def reconcile(spark, settings, *, release, log=print):
    """한 회차(D1~D10). 반환: Result(ok — 모든 줄 match, summary — 회차 요약 dict). 끝난 배치가 없으면 쓰지 않고 ok."""
    framework._require_utc(spark)
    if settings.domain not in SOURCES:
        raise ValueError(f"대사 원천 설정이 없다: {settings.domain!r}(지금은 {sorted(SOURCES)})")
    started = time.monotonic()
    version = processed_version(spark, settings.checkpoint)
    if version is None:
        log("silver reconcile status=skipped reason=no_batches")
        return Result(True, {"status": "skipped"})
    ident = framework.checkpoint_state(spark, settings.checkpoint)[0]
    silver_version, errors_version = framework.table_state(spark, settings.silver)[0], framework.table_state(spark, settings.errors)[0]

    def read(path, at):
        return spark.read.format("delta").option("versionAsOf", at).load(path)

    result, counts = compare(spark, read(settings.bronze, version), read(settings.silver, silver_version), read(settings.errors, errors_version),
                             SOURCES[settings.domain])
    statuses = {(r["kind"], r["status"]): r["count"] for r in result.groupBy("kind", "status").count().collect()}
    bad = (result.where(F.col("status") != MATCH).orderBy("kind", "window_start")  # 창 시각은 세션(UTC)에서 글자로 — collect 는 프로세스 시간대의 naive 값
           .withColumn("window_text", F.date_format("window_start", "yyyy-MM-dd'T'HH:mm:ss'Z'")).collect())
    worst = MISMATCH if any(r["status"] == MISMATCH for r in bad) else COPIES if bad else MATCH  # 요약은 가장 무거운 줄(부풂만이면 copies_mismatch — D5)
    summary = {"status": worst, "bronze_version": version, "silver_version": silver_version, "errors_version": errors_version,
               "checkpoint_id": ident, "silver_release": release, "windows": sum(n for (kind, _), n in statuses.items() if kind == "window"),
               "mismatched": statuses.get(("window", MISMATCH), 0), "copies_mismatched": statuses.get(("window", COPIES), 0),
               **{key: counts[key] for key in ("duplicates", "unplaced", "error_rows", "errors_not_in_bronze", "errors_repeated", "bronze_rows")},
               "seconds": round(time.monotonic() - started, 3)}
    at = datetime.now(timezone.utc)
    run = {"bronze_version": version, "silver_version": silver_version, "errors_version": errors_version, "checkpoint_id": ident,
           "silver_release": release, "reconciled_at": at}
    columns = [F.col(f.name) if f.name not in run else F.lit(run[f.name]).cast(f.dataType).alias(f.name) for f in RESULT_SCHEMA.fields]
    (result.select(*columns).write.format("delta").mode("overwrite").option("userMetadata", hop_rules.canonical_json(summary))
     .save(settings.reconcile))
    for row in bad[:EXAMPLES]:
        log(_alert(row))
    if len(bad) > EXAMPLES:
        log(f"silver alert reconcile_more rows={len(bad) - EXAMPLES}")
    if counts["duplicates"]:
        log(f"silver alert silver_duplicates ids={counts['duplicates']} examples={counts['duplicate_examples']}")
    log("silver reconcile " + " ".join(f"{key}={summary[key]}" for key in (
        "status", "windows", "mismatched", "copies_mismatched", "duplicates", "unplaced", "errors_not_in_bronze", "errors_repeated",
        "bronze_version", "bronze_rows", "seconds")) + f" total_seconds={round(time.monotonic() - started, 3)}")
    return Result(not bad, summary)


def _alert(row):
    name = "reconcile_copies" if row["status"] == COPIES else "reconcile_mismatch"
    if row["kind"] == "window":
        return (f"silver alert {name} kind=window window={row['window_text']} "
                f"bronze_ids={row['bronze_ids']} silver_ids={row['silver_ids']} bronze_rows={row['bronze_rows']} silver_copies={row['silver_copies']} "
                f"duplicates={row['silver_duplicate_ids']} missing={row['missing_examples']} extra={row['extra_examples']}")
    if row["kind"] == "errors":
        return (f"silver alert {name} kind=errors not_in_bronze={row['errors_not_in_bronze']} repeated={row['errors_repeated']} "
                f"examples={row['extra_examples']}")
    return f"silver alert {name} kind=unplaced rows={row['bronze_rows']} examples={row['missing_examples']}"
