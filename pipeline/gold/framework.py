"""Gold 공통 틀(계획 temp/pipeline/41_ads-gold-plan.md r3d §2 — 결정 AG1~AG10, 리뷰 r3c C3·권고 1~3, 리뷰 r3d 권고 1). 원천 하나·회차 하나.

순서(§2.1 — 확인이 쓰기 앞):
  1. `read_events(..., RECONCILED)`(AG9 (나)) — 포인터 세대의 마지막 대사가 확인한 본 표 판과 그 대사 결과 표 판을 고정한다.
  2. 잃은 정정 채우기 — 표마다 "정정이 쓰인 마지막 Gold 판 뒤의 모든 판"을 차례로(권고 1). 같은 판은 Delta txn 으로 두 번 쓰지 않는다.
  3. 기준 시각(control.cutoff)과 미확정 시간(control.unconfirmed + C1·C2 의심 시간)을 정한다.
  4. 표마다 통째로 계산한다(AG3 (가)) — 끝난 칸만(C3), 행마다 확정 열(AG5 (가)), KST 하루 표는 그 날 안에 미확정 시간이 있으면 그 날 모두 거짓.
  5. 확인(AG7 (가)): 표마다 지표 합 = Silver 를 다른 단순 식으로 센 값. 어긋나면 아무것도 쓰지 않고 Result.ok 거짓(잡 rc 3).
     페이로드에서 꺼낸 수가 형·범위를 벗어나면(`_valid` 거짓) GoldError — 쓰지 않는다(§2.2).
  6. 표마다 덮어쓰기(새 판 하나, mergeSchema — 열 추가는 같은 판, 형 변경은 Delta 가 거부), 커밋 userMetadata = 회차 기록(§2.3).
  7. 그 판의 정정을 쓴다(2와 같은 함수).
표(Table): 이름·판·칸 단위(HOURLY 열 `hour` / KST_DAILY 열 `kst_date`)·키 열·지표 열·build·totals. build(frame) → Built(events, doubt, counts):
  events 는 사건 하나에 한 행 — 열 `at`(그 사건의 칸 시각, AG2 (가) Silver occurred_at), 키 열, 지표 열(bigint — 그 사건의 몫), 있으면 `_valid`.
  doubt 은 열 `at` 의 DataFrame 또는 None — "차원 없음"·"코드 모르는 그룹" 칸에 센 사건(r2b C1·r3b C2: 창 mismatch 가 있는 회차에 그 시간 미확정).
  totals(frame, finished) → {지표: 합} — finished(at 열) 은 그 사건이 이번에 쓰는 칸인가.
"""
import json
import operator
from collections import namedtuple
from functools import reduce
from datetime import datetime, timezone

from pyspark.sql import functions as F, types as T

from pipeline.gold import control
from pipeline.silver import contracts, control as silver_control, framework as silver_framework, pointers

HOURLY, KST_DAILY = "hour", "kst_date"  # 칸 단위 = Gold 표의 시간 열 이름
Table = namedtuple("Table", "name version grain keys metrics build totals")
Built = namedtuple("Built", "events doubt counts")
Result = namedtuple("Result", "ok records")
_US = 1_000_000
_HOUR_US = control.HOUR * _US
RECONCILE_COLUMNS = ("kind", "window_start", "status", "reconciled_at")
CORRECTIONS_SCHEMA = T.StructType([T.StructField(name, kind) for name, kind in (
    ("table_name", T.StringType()), ("cell_time", T.TimestampType()), ("cell_key", T.MapType(T.StringType(), T.StringType())),
    ("metrics_before", T.MapType(T.StringType(), T.LongType())), ("metrics_after", T.MapType(T.StringType(), T.LongType())),
    ("confirmed_before", T.BooleanType()), ("confirmed_after", T.BooleanType()), ("reason", T.StringType()),
    ("gold_version_before", T.LongType()), ("gold_version_after", T.LongType()),
    ("silver_generation_before", T.IntegerType()), ("silver_generation_after", T.IntegerType()),
    ("silver_version_before", T.LongType()), ("silver_version_after", T.LongType()), ("run_at", T.TimestampType()))])


class GoldError(RuntimeError):
    """회차를 쓰지 않고 멈춘다(rc 1) — 사유를 적는다."""


def _floor_div(value, divisor):
    """정수 바닥 나눗셈(실수 나눗셈의 경계 오차 없이 — 나머지를 뺀 정확한 배수를 나눈다)."""
    return ((value - F.pmod(value, F.lit(divisor))) / F.lit(divisor)).cast("bigint")


def _hour(at):
    """칸 시각(timestamp 열) → epoch 시간(정수). 세션 시간대와 무관하다."""
    return _floor_div(F.unix_micros(at), _HOUR_US)


def _day(hour):
    """epoch 시간 열 → KST 날짜의 epoch 일 번호(control.kst_day 와 같은 식)."""
    return _floor_div(hour * F.lit(control.HOUR) + F.lit(control.KST_OFFSET), control.DAY)


def _end_us(grain, hour):
    """그 사건이 든 칸의 끝(epoch 마이크로초): 시간 칸은 다음 시, KST 하루 칸은 다음 KST 자정."""
    if grain == HOURLY:
        return (hour + F.lit(1)) * F.lit(_HOUR_US)
    return ((_day(hour) + F.lit(1)) * F.lit(control.DAY) - F.lit(control.KST_OFFSET)) * F.lit(_US)


def finished(grain, hour, cutoff_us, delay_seconds):
    """끝난 칸인가(C3): 칸 끝 + 판정 지연 상한 ≤ 기준 시각. 기준 시각이 없으면(사건 없음) 거짓."""
    if cutoff_us is None:
        return F.lit(False)
    return (_end_us(grain, hour) + F.lit(delay_seconds * _US)) <= F.lit(cutoff_us)


def _txn_versions(spark, path):
    """Delta 표의 txn {appId: version}(없는 표는 {}). silver framework.table_state 와 같은 py4j 스냅샷 읽기."""
    jvm = silver_framework._jvm(spark)
    none = jvm.scala.Option.empty()
    snapshot = jvm.org.apache.spark.sql.delta.DeltaLog.forTable(spark._jsparkSession, path).update(False, none, none)
    if snapshot.version() < 0:
        return {}
    txns = snapshot.setTransactions()
    return {txns.apply(i).appId(): txns.apply(i).version() for i in range(txns.length())}


def _records(spark, path, latest, after):
    """Gold 표 판 (after, latest] 가운데 회차 기록이 있는 판 [(판, 기록)] 오름차순 — `latest` 는 그 표의 지금 판이어야 한다(이력은 최신부터 읽음).
    기록 = 커밋 userMetadata 의 dict(키 `gold_release` 가 있는 것). VACUUM 같은 다른 커밋은 건너뛴다."""
    found = []
    for row in spark.sql(f"DESCRIBE HISTORY delta.`{path}` LIMIT {latest - after}").collect():
        try:
            record = json.loads(row["userMetadata"] or "")
        except ValueError:
            continue
        if isinstance(record, dict) and "gold_release" in record and row["version"] > after:
            found.append((row["version"], record))
    return sorted(found, key=lambda item: item[0])


def last_record(spark, path):
    """Gold 표의 마지막 회차 (판, 기록) 또는 None(표 없음·기록 없음)."""
    latest = silver_framework.table_state(spark, path)[0]
    span = 1
    while latest >= 0:
        found = _records(spark, path, latest, max(latest - span, -1))
        if found:
            return found[-1]
        if latest - span < 0:
            return None
        span *= 2
    return None


def app_id(domain, table):
    """Gold 표 하나의 Delta txn appId — 정정 표(그 표의 정정이 쓰인 마지막 Gold 판)와 Gold 표 자신(회차 쓰기)에 쓴다. 표가 달라 겹치지 않는다."""
    return f"gold:{domain}:{table.name}:v{table.version}"


def _cell_frame(spark, path, version, table):
    frame = spark.read.format("delta").option("versionAsOf", version).load(path)
    return frame.select(table.grain, *[name for name, _ in table.keys], *[F.col(m) if m in frame.columns else F.lit(None).cast("bigint").alias(m) for m in table.metrics],
                        "confirmed")


def _cell_start(grain, column):
    """칸 시작(UTC timestamp) — 정정 줄의 cell_time."""
    if grain == HOURLY:
        return column
    return F.timestamp_seconds(F.unix_date(column).cast("bigint") * F.lit(control.DAY) - F.lit(control.KST_OFFSET))


def _hour_of_cell(grain, column):
    """칸 시작의 epoch 시간 — finished 로 정정의 범위(직전 판이 이미 쓴 칸)를 가린다."""
    return _hour(_cell_start(grain, column))


def diff(spark, path, table, before, after):
    """정정 줄(AG4 (가) + r3d): 직전 회차 판이 이미 쓴 범위(그 기준 시각에 끝난 칸) 안에서 값·확정이 바뀌었거나 생기거나 없어진 칸마다 한 줄."""
    (vb, rb), (va, ra) = before, after
    keys = [table.grain, *[name for name, _ in table.keys]]
    old, new = _cell_frame(spark, path, vb, table).alias("o"), _cell_frame(spark, path, va, table).alias("n")
    on = [F.col(f"o.{k}").eqNullSafe(F.col(f"n.{k}")) for k in keys]
    joined = old.join(new, on, "full_outer").withColumn("_present_o", F.col("o.confirmed").isNotNull()).withColumn("_present_n", F.col("n.confirmed").isNotNull())
    cell = {k: F.when(F.col("_present_o"), F.col(f"o.{k}")).otherwise(F.col(f"n.{k}")) for k in keys}  # confirmed 는 null 이 아니다 — 있는 쪽 표시
    changed = reduce(operator.or_, [~F.col("_present_o"), ~F.col("_present_n"), ~F.col("o.confirmed").eqNullSafe(F.col("n.confirmed")),
                                    *[~F.col(f"o.{m}").eqNullSafe(F.col(f"n.{m}")) for m in table.metrics]])
    in_range = finished(table.grain, _hour_of_cell(table.grain, cell[table.grain]), rb["cutoff_us"], rb["delay_seconds"])

    def metrics(side):
        return F.when(F.col(f"_present_{side}"), F.create_map(*[x for m in table.metrics for x in (F.lit(m), F.col(f"{side}.{m}"))]))

    lines = joined.where(changed & in_range).select(
        F.lit(table.name).alias("table_name"), _cell_start(table.grain, cell[table.grain]).alias("cell_time"),
        F.create_map(*[x for name, _ in table.keys for x in (F.lit(name), cell[name].cast("string"))]).alias("cell_key") if table.keys
        else F.create_map().cast("map<string,string>").alias("cell_key"),
        metrics("o").alias("metrics_before"), metrics("n").alias("metrics_after"),
        F.col("o.confirmed").alias("confirmed_before"), F.col("n.confirmed").alias("confirmed_after"), F.lit(control.reason(rb, ra)).alias("reason"),
        F.lit(vb).cast("bigint").alias("gold_version_before"), F.lit(va).cast("bigint").alias("gold_version_after"),
        F.lit(rb["silver_generation"]).cast("int").alias("silver_generation_before"), F.lit(ra["silver_generation"]).cast("int").alias("silver_generation_after"),
        F.lit(rb["silver_version"]).cast("bigint").alias("silver_version_before"), F.lit(ra["silver_version"]).cast("bigint").alias("silver_version_after"),
        F.timestamp_micros(F.lit(ra["run_at_us"])).alias("run_at"))
    return lines.select(*[F.col(f.name).cast(f.dataType) for f in CORRECTIONS_SCHEMA.fields])


def catch_up(spark, root, domain, table, log=print):
    """그 표의 정정이 쓰이지 않은 회차 판을 차례로 쓴다(§2.1 잃지 않기 — 권고 1~3). 첫 회차 판은 견줄 판이 없어 빈 줄로 표시만. 반환: 쓴 줄 수."""
    path = control.table_path(root, domain, table.name, table.version)
    latest = silver_framework.table_state(spark, path)[0]
    target = control.corrections_path(root, domain)
    ident = app_id(domain, table)
    done = _txn_versions(spark, target).get(ident, -1)
    if done > latest:  # (GOLD-1 리뷰 권고 1) 정정이 쓰인 판보다 표가 앞 — 표 폴더를 지우고 다시 만들었다(없거나 판이 0부터). 회차 시작에서 멈춰 아무것도 쓰지 않는다
        raise GoldError(f"{path} 의 지금 판 {latest}{'(표 없음)' if latest < 0 else ''} 이 정정 기록의 마지막 판 {done}({ident})보다 앞이다 — "
                        f"표를 다시 만들지 말고 새 표 판(v{table.version + 1})으로 만든다(runbooks/gold.md)")
    if latest < 0 or done == latest:
        return 0
    found = _records(spark, path, latest, done - 1 if done >= 0 else -1)  # 정정이 쓰인 마지막 판(견줄 앞 판)부터
    base = next((item for item in found if item[0] == done), None)
    if done >= 0 and base is None:
        raise GoldError(f"{path} 판 {done}(정정이 쓰인 마지막 판)에 회차 기록이 없다")
    written = 0
    for version, record in (item for item in found if item[0] > done):
        lines = diff(spark, path, table, base, (version, record)) if base else spark.createDataFrame([], CORRECTIONS_SCHEMA)
        lines = lines.localCheckpoint()
        count = lines.count()
        lines.write.format("delta").mode("append").option("txnAppId", ident).option("txnVersion", version).save(target)
        log(f"gold corrections table={table.name} gold_version_before={base[0] if base else None} gold_version_after={version} lines={count}")
        written += count
        base = (version, record)
    return written


def _cells(table, events, cutoff_us, delay_seconds, unconfirmed, hours):
    """사건 행 → 끝난 칸만 모은 Gold 행(시간 열, 키, 지표 합, confirmed)."""
    keyed = events.withColumn("_h", _hour(F.col("at")))
    keyed = keyed.where(finished(table.grain, F.col("_h"), cutoff_us, delay_seconds))
    if table.grain == HOURLY:
        time, bad = F.col("_h"), sorted(hours)
    else:
        time, bad = _day(F.col("_h")), sorted({control.kst_day(h) for h in hours})
    keys = [name for name, _ in table.keys]
    grouped = keyed.groupBy(time.alias("_t"), *keys).agg(*[F.sum(m).cast("bigint").alias(m) for m in table.metrics])
    confirmed = F.lit(False) if unconfirmed else ~F.col("_t").isin(bad) if bad else F.lit(True)
    stamp = (F.timestamp_seconds(F.col("_t") * F.lit(control.HOUR)) if table.grain == HOURLY
             else F.date_add(F.lit("1970-01-01").cast("date"), F.col("_t").cast("int")))
    return grouped.select(stamp.alias(table.grain), *[F.col(k).cast(contracts._TYPES[kind]) for k, kind in table.keys], *table.metrics, confirmed.alias("confirmed"))


def run(spark, root, domain, tables, release, *, delay_seconds, log=print, now=None):
    """한 회차(모듈 설명). 반환 Result(ok — 확인이 모두 맞아 썼나, records — {표: 회차 기록}). 확인이 어긋나면 아무것도 쓰지 않는다."""
    silver_framework._require_utc(spark)
    if not 0 <= delay_seconds < control.HOUR:  # "직전 시간"까지만 미확정으로 두는 규칙(AG5 (가))의 앞조건
        raise ValueError(f"판정 지연 상한은 0 이상 3600 미만 초다: {delay_seconds!r}")
    run_at = now or datetime.now(timezone.utc)
    view = pointers.read_events(spark, root, domain, pointers.RECONCILED)
    reconcile_path = silver_control.silver_paths(root, domain, view.generation).reconcile
    reconciled = spark.read.format("delta").option("versionAsOf", view.reconcile_version).load(reconcile_path)
    lacking = [c for c in RECONCILE_COLUMNS if c not in reconciled.columns]
    if lacking:
        raise GoldError(f"{reconcile_path} 판 {view.reconcile_version} 에 대사 결과 열 {lacking} 이(가) 없다")
    rows = reconciled.select("kind", _hour(F.col("window_start")).alias("h"), "status", F.unix_micros("reconciled_at").alias("r")).collect()
    unconfirmed = control.unconfirmed((r["kind"], r["h"], r["status"]) for r in rows)
    reconciled_us = max((r["r"] for r in rows if r["r"] is not None), default=None)
    for table in tables:
        catch_up(spark, root, domain, table, log=log)
    previous = {table.name: last_record(spark, control.table_path(root, domain, table.name, table.version)) for table in tables}
    previous_us = max((p[1]["cutoff_us"] for p in previous.values() if p and p[1].get("cutoff_us") is not None), default=None)
    frame = view.frame.persist()
    built = {}
    try:
        max_produced_us = frame.agg(F.max(F.unix_micros("produced_at"))).first()[0]
        cutoff_us = control.cutoff(max_produced_us, reconciled_us, previous_us)
        for table in tables:
            result = table.build(frame)
            built[table.name] = result._replace(events=result.events.persist())
            if "_valid" in result.events.columns:
                bad = built[table.name].events.where(~F.coalesce(F.col("_valid"), F.lit(False))).count()
                if bad:
                    raise GoldError(f"{table.name}: 페이로드에서 꺼낸 수 {bad} 개가 형·범위를 벗어났다 — 쓰지 않는다")
        hours = set(unconfirmed.hours)
        if unconfirmed.any_window:  # r2b C1·r3b C2 — 창 mismatch 가 있는 회차의 "차원 없음"·"코드 모르는 그룹" 사건 시간
            for result in built.values():
                if result.doubt is not None:
                    hours.update(r[0] for r in result.doubt.select(_hour(F.col("at"))).distinct().collect())
        cells, sums, mismatches = {}, {}, []
        for table in tables:
            events = built[table.name].events.drop("_valid") if "_valid" in built[table.name].events.columns else built[table.name].events
            cells[table.name] = _cells(table, events, cutoff_us, delay_seconds, unconfirmed.everything, hours).localCheckpoint()
            sums[table.name] = cells[table.name].agg(*[F.coalesce(F.sum(m), F.lit(0)).cast("bigint").alias(m) for m in table.metrics]).first().asDict()
            expected = table.totals(frame, lambda at, grain=table.grain: finished(grain, _hour(at), cutoff_us, delay_seconds))
            for metric in table.metrics:
                if sums[table.name][metric] != expected.get(metric):
                    mismatches.append((table.name, metric, sums[table.name][metric], expected.get(metric)))
        if mismatches:
            for name, metric, gold, silver in mismatches:
                log(f"gold alert check_mismatch table={name} metric={metric} gold={gold} silver={silver}")
            return Result(False, {})
        records = {}
        for table in tables:
            path = control.table_path(root, domain, table.name, table.version)
            out = cells[table.name]
            record = {"domain": domain, "table": table.name, "table_version": table.version, "grain": table.grain, "gold_release": release,
                      "run_at": run_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "run_at_us": int(run_at.timestamp() * _US),
                      "silver_generation": view.generation, "silver_version": view.version, "silver_pointer_version": view.pointer_version,
                      "reconcile_version": view.reconcile_version, "delay_seconds": delay_seconds, "cutoff_us": cutoff_us,
                      "cutoff_at": None if cutoff_us is None else datetime.fromtimestamp(cutoff_us / _US, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                      "cutoff_max_produced_us": max_produced_us, "cutoff_reconciled_us": reconciled_us, "cutoff_previous_us": previous_us,
                      "unconfirmed_everything": unconfirmed.everything, "unconfirmed_hours": len(hours), "cells": out.count(),
                      "cells_unconfirmed": out.where(~F.col("confirmed")).count(), "counts": built[table.name].counts}
            # txn 은 회차마다 커밋 하나를 남기려고 둔다 — 빈 표에 빈 결과를 덮어쓰면 Delta 가 커밋을 건너뛰어 회차 기록이 빠진다(실측). 판 = 지금 판 + 1
            (out.write.format("delta").mode("overwrite").option("mergeSchema", "true").option("txnAppId", app_id(domain, table))
             .option("txnVersion", silver_framework.table_state(spark, path)[0] + 1)
             .option("userMetadata", json.dumps(record, ensure_ascii=False, sort_keys=True)).save(path))
            records[table.name] = record
            log(f"gold table table={table.name} version=v{table.version} cells={record['cells']} unconfirmed={record['cells_unconfirmed']} "
                f"cutoff_at={record['cutoff_at']} silver_generation={view.generation} silver_version={view.version}")
            catch_up(spark, root, domain, table, log=log)
        return Result(True, records)
    finally:
        frame.unpersist()
        for result in built.values():
            result.events.unpersist()
