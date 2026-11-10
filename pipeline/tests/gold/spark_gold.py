"""pipeline/gold 공통 틀(계획 temp/pipeline/41_ads-gold-plan.md r3d §7 "틀" 시험 + 리뷰 r3c C3·권고 1~3, 리뷰 r3d 권고 1) — Spark 시험.

Silver 쪽은 실제 표 모양으로 직접 만든다: 본 표(contracts.SILVER_SCHEMA), 대사 결과 표(reconcile.RESULT_SCHEMA + 커밋 요약), 포인터는 실제
pointers.set_generation. 소비자 표는 아직 없으므로(코드 Step ②) 시험 안에서만 쓰는 표 둘(시간 표·KST 하루 표)로 틀을 본다.
"""
import json
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from pyspark.sql import SparkSession, functions as F

from pipeline.gold import control, framework, gold_job
from pipeline.silver import contracts, control as silver_control, pointers, reconcile

UTC = timezone.utc
DAY0 = datetime(2026, 11, 12, tzinfo=UTC)
RELEASE = "gold@test00000000"
DELAY = 330


def at(text):
    """"10:20" 또는 "10:20:00.000001" → 2026-11-12 UTC 시각, "+1 15:00" 은 다음 날(날 수는 여러 자리)."""
    days = 0
    if text.startswith("+"):
        days, text = text[1:].split(" ", 1)
        days = int(days)
    fmt = "%H:%M:%S.%f" if "." in text else "%H:%M:%S" if text.count(":") == 2 else "%H:%M"
    clock = datetime.strptime(text, fmt)
    return DAY0 + timedelta(days=days, hours=clock.hour, minutes=clock.minute, seconds=clock.second, microseconds=clock.microsecond)


def naive(moment):
    return moment.astimezone(UTC).replace(tzinfo=None)  # 실행기 TZ=UTC — collect 의 naive 값과 맞춘다


def setUpModule():
    global spark
    spark = SparkSession.builder.getOrCreate()


# ---------------------------------------------------------------- 시험 안에서만 쓰는 표(배포하지 않음 — §2.4)

def _events(frame):
    v = F.try_parse_json("payload")
    amount, ok = contracts.field(v, "$.amount", "bigint")
    charge = F.col("event_type") == "charge"
    return frame.select(F.col("occurred_at").alias("at"), "event_type", F.get_json_object("payload", "$.group").alias("group"),
                        F.lit(1).cast("bigint").alias("events"), F.when(charge, amount).otherwise(F.lit(0)).cast("bigint").alias("amount"),
                        F.when(charge, ok).otherwise(F.lit(True)).alias("_valid"))


def hourly_build(frame):
    events = _events(frame)
    doubt = events.where(F.col("group").isNull()).select("at")  # "차원 없음" 자리(C1)
    return framework.Built(events, doubt, {"no_group": doubt.count()})


def hourly_totals(frame, finished):  # Gold 코드와 다른 단순 식(AG7)
    done = frame.where(finished(F.col("occurred_at")))
    amount = done.where(F.col("event_type") == "charge").agg(F.sum(F.get_json_object("payload", "$.amount").cast("bigint"))).first()[0]
    return {"events": done.count(), "amount": amount or 0}


def daily_build(frame):
    return framework.Built(_events(frame).select("at", "event_type", "events", "_valid"), None, {})


def daily_totals(frame, finished):
    return {"events": frame.where(finished(F.col("occurred_at"))).count()}


HOURLY = framework.Table("events_hourly", 1, framework.HOURLY, (("event_type", "string"), ("group", "string")), ("events", "amount"), hourly_build, hourly_totals)
DAILY = framework.Table("events_kst", 1, framework.KST_DAILY, (("event_type", "string"),), ("events",), daily_build, daily_totals)
TABLES = (HOURLY, DAILY)


# ---------------------------------------------------------------- Silver 쪽(본 표·대사 결과·포인터)

class Lake:
    def __init__(self, test):
        self.root = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.root)
        self.r, self.logs, self.offset = str(self.root), [], 0

    def paths(self, g=1):
        return silver_control.silver_paths(self.r, "ads", g)

    def add(self, *events, g=1):
        rows = []
        for eid, kind, occurred, produced, payload in events:
            self.offset += 1
            rows.append((eid, "ads", kind, "1", occurred, produced, None, None, None, None, None, json.dumps(payload), None, None,
                         "ads.outbox", 0, self.offset, None, None, "c", 1, produced, produced, "silver@x", produced.date(), "h"))
        spark.createDataFrame(rows, contracts.SILVER_SCHEMA).write.format("delta").mode("append").save(self.paths(g).silver)

    def delete(self, eid, g=1):
        spark.sql(f"DELETE FROM delta.`{self.paths(g).silver}` WHERE event_id = '{eid}'")

    def version(self, path):
        return spark.sql(f"DESCRIBE HISTORY delta.`{path}` LIMIT 1").first()["version"]

    def reconcile(self, windows=(), unplaced="match", errors="match", quarantine="match", reconciled=None, g=1, **summary):
        """대사 결과 커밋 하나(창 줄 = [(창 시작, status)]). 요약의 silver_version 은 지금 본 표 판."""
        rows = [{"kind": "window", "window_start": naive(start), "window_end": naive(start + timedelta(hours=1)), "status": status} for start, status in windows]
        rows += [{"kind": kind, "status": status} for kind, status in (("errors", errors), ("unplaced", unplaced), ("quarantine", quarantine))]
        stamp = naive(reconciled or at("+30 00:00"))  # 기본은 사건보다 한참 뒤 — 기준 시각은 본 표의 최대 produced_at
        frame = spark.createDataFrame([tuple({**r, "reconciled_at": stamp}.get(f.name) for f in reconcile.RESULT_SCHEMA.fields) for r in rows], reconcile.RESULT_SCHEMA)
        statuses = {r["status"] for r in rows}
        worst = "mismatch" if "mismatch" in statuses else "copies_mismatch" if "copies_mismatch" in statuses else "match"
        meta = {"status": worst, "silver_version": self.version(self.paths(g).silver), "bronze_version": 7, "checkpoint_id": "c-1", "silver_release": "silver@x",
                "bronze_table": "/lake/bronze/ads", **summary}
        frame.write.format("delta").mode("overwrite").option("overwriteSchema", "true").option("userMetadata", json.dumps(meta)).save(self.paths(g).reconcile)

    def point(self, g=1):
        pointers.set_generation(spark, self.r, "ads", g, by="시험", reason="Gold 틀", log=lambda line: None)

    def run(self, tables=TABLES, release=RELEASE, now=None):
        self.logs.clear()
        return framework.run(spark, self.r, "ads", tables, release, delay_seconds=DELAY, log=self.logs.append, now=now or at("+3 00:00"))

    def gold_path(self, table=HOURLY):
        return control.table_path(self.r, "ads", table.name, table.version)

    def gold(self, table=HOURLY, version=None):
        reader = spark.read.format("delta")
        frame = (reader if version is None else reader.option("versionAsOf", version)).load(self.gold_path(table))
        return sorted((tuple(r) for r in frame.collect()), key=repr)

    def record(self, table=HOURLY, version=None):
        version = self.version(self.gold_path(table)) if version is None else version
        return json.loads(spark.sql(f"DESCRIBE HISTORY delta.`{self.gold_path(table)}`").where(F.col("version") == version).first()["userMetadata"])

    def corrections(self, table=HOURLY):
        path = control.corrections_path(self.r, "ads")
        rows = [r.asDict() for r in spark.read.format("delta").load(path).where(F.col("table_name") == table.name).collect()]
        return sorted(rows, key=lambda r: (r["gold_version_after"], r["cell_time"], sorted(r["cell_key"].items(), key=repr)))

    def done(self, table=HOURLY):
        return framework._txn_versions(spark, control.corrections_path(self.r, "ads")).get(framework.app_id("ads", table), -1)


def ev(eid, occurred, produced=None, kind="impression", group="g1", **payload):
    body = dict(payload)
    if group is not None:  # group 이 없으면 "차원 없음" 칸
        body["group"] = group
    return eid, kind, at(occurred), at(produced or occurred), body


def clock(produced):
    """기준 시각을 정하는 사건(produced_at 이 최대 — 그 시간 칸은 아직 끝나지 않음)."""
    return ev(f"clock-{produced}", produced, kind="clock", group="clock")


def started(test, *events, windows=()):
    lake = Lake(test)
    lake.add(*events)
    lake.reconcile()
    lake.point()
    if windows:
        lake.reconcile(windows)
    return lake


H = lambda text, group="g1", kind="impression", events=1, amount=0, confirmed=True: (naive(at(text)), kind, group, events, amount, confirmed)  # noqa: E731
D = lambda day, kind="impression", events=1, confirmed=True: (DAY0.date() + timedelta(days=day), kind, events, confirmed)  # noqa: E731


class FirstRun(unittest.TestCase):
    def test_writes_finished_cells_and_record(self):  # 끝난 칸만(C3), 실행 기록(§2.3), 첫 판은 정정 줄 없이 표시만
        lake = started(self, ev("a", "10:20"), ev("b", "10:40"), ev("c", "11:10"), clock("12:30"))
        result = lake.run()
        self.assertTrue(result.ok)
        self.assertCountEqual(lake.gold(), [H("10:00", events=2), H("11:00")])
        self.assertCountEqual(lake.gold(DAILY), [])  # KST 11-12 은 UTC 15:00 + 330초에 끝난다 — 기준 시각 12:30 이라 아직
        record = lake.record()
        silver = lake.paths().silver
        expected_cutoff = int(at("12:30").timestamp() * 1_000_000)
        self.assertEqual({k: record[k] for k in ("domain", "table", "table_version", "grain", "gold_release", "silver_generation", "silver_version", "reconcile_version",
                                                 "delay_seconds", "cutoff_us", "cutoff_at", "cutoff_max_produced_us", "cutoff_previous_us", "unconfirmed_everything",
                                                 "unconfirmed_hours", "cells", "cells_unconfirmed", "counts")},
                         {"domain": "ads", "table": "events_hourly", "table_version": 1, "grain": "hour", "gold_release": RELEASE, "silver_generation": 1,
                          "silver_version": lake.version(silver), "reconcile_version": lake.version(lake.paths().reconcile), "delay_seconds": DELAY,
                          "cutoff_us": expected_cutoff, "cutoff_at": "2026-11-12T12:30:00.000000Z", "cutoff_max_produced_us": expected_cutoff,
                          "cutoff_previous_us": None, "unconfirmed_everything": False, "unconfirmed_hours": 0, "cells": 2, "cells_unconfirmed": 0,
                          "counts": {"no_group": 0}})
        self.assertEqual(record["cutoff_reconciled_us"], int(at("+30 00:00").timestamp() * 1_000_000))
        self.assertEqual((lake.corrections(), lake.done(), lake.done(DAILY)), ([], 0, 0))
        self.assertIn("gold corrections table=events_hourly gold_version_before=None gold_version_after=0 lines=0", lake.logs)
        self.assertTrue(lake.run().ok)  # 빈 표(하루 표)도 회차마다 판 하나 — 기록이 빠지지 않는다
        self.assertEqual((lake.version(lake.gold_path(DAILY)), lake.record(DAILY)["cells"], lake.done(DAILY)), (1, 0, 1))

    def test_empty_silver(self):  # 사건이 없으면 기준 시각이 없다 — 빈 표
        lake = started(self)
        self.assertTrue(lake.run().ok)
        self.assertEqual((sorted(lake.gold(), key=repr), lake.record()["cutoff_us"]), ([], None))

    def test_no_pointer(self):
        lake = Lake(self)
        with self.assertRaisesRegex(pointers.PointerError, "포인터 표가 없다"):
            lake.run()

    def test_reconcile_table_without_result_columns(self):
        lake = started(self, ev("a", "10:20"))
        meta = json.loads(spark.sql(f"DESCRIBE HISTORY delta.`{lake.paths().reconcile}` LIMIT 1").first()["userMetadata"])
        spark.range(1).write.format("delta").mode("overwrite").option("overwriteSchema", "true").option("userMetadata", json.dumps(meta)).save(lake.paths().reconcile)
        with self.assertRaisesRegex(framework.GoldError, r"대사 결과 열 \['kind', 'window_start', 'status', 'reconciled_at'\]"):
            lake.run()


class Pinned(unittest.TestCase):
    def test_same_silver_same_gold(self):  # AG3 + C3 ② — 같은 판이면 다른 시각에 돌아도 같은 칸, 정정 줄 없음
        lake = started(self, ev("a", "10:20"), ev("b", "+1 01:00"), clock("+1 16:00"))
        self.assertTrue(lake.run(now=at("+1 16:20")).ok)
        first = (lake.gold(), lake.gold(DAILY))
        self.assertTrue(lake.run(now=at("+5 09:00")).ok)
        self.assertEqual((lake.gold(), lake.gold(DAILY)), first)
        self.assertEqual(first[1], [D(0), D(1)])  # KST 11-12(UTC 10:20) · 11-13(UTC +1 01:00) — 둘 다 +1 15:05:30 이전에 끝남
        self.assertEqual((lake.corrections(), lake.done()), ([], 1))
        self.assertNotEqual(lake.record(version=0)["run_at"], lake.record(version=1)["run_at"])

    def test_reads_the_reconciled_version(self):  # AG9 (나) — 대사가 확인하지 않은 본 표 쓰기는 Gold 에 없다
        lake = started(self, ev("a", "10:20"), clock("12:30"))
        checked = lake.version(lake.paths().silver)
        lake.add(ev("late", "10:30"))
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(), [H("10:00")])
        self.assertEqual(lake.record()["silver_version"], checked)
        lake.reconcile()
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(), [H("10:00", events=2)])


class Finished(unittest.TestCase):  # C3 ① — 칸 끝 + 330초 앞뒤 경계
    def test_hour_boundary(self):
        lake = started(self, ev("a", "10:20"), clock("11:05:29.999999"))
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(), [])
        lake.add(clock("11:05:30"))
        lake.reconcile()
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(), [H("10:00")])
        self.assertEqual(lake.corrections(), [])  # 범위로 처음 들어온 칸은 정정이 아니다

    def test_kst_day_boundary(self):  # KST 11-12 = UTC 11-11 15:00 ~ 11-12 15:00
        lake = started(self, ev("a", "14:59:59.999999"), ev("b", "15:00"), clock("15:05:29.999999"))
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(DAILY), [])
        lake.add(clock("15:05:30"))
        lake.reconcile()
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(DAILY), [D(0)])  # 15:00 사건은 KST 11-13 — 아직

    def test_cutoff_is_capped_and_never_moves_back(self):  # 앞선 produced_at 은 reconciled_at 으로 막고, 행이 빠져도 기준은 뒤로 가지 않는다(r3d 권고 1)
        lake = started(self, ev("a", "10:20"), ev("b", "11:10"), clock("12:30"), clock("+400 00:00"))
        lake.reconcile(reconciled=at("12:30"))
        self.assertTrue(lake.run().ok)
        self.assertEqual(lake.record()["cutoff_at"], "2026-11-12T12:30:00.000000Z")
        self.assertCountEqual(lake.gold(), [H("10:00"), H("11:00")])
        lake.delete("clock-+400 00:00")
        lake.delete("clock-12:30")  # 격리 등으로 최대 행이 빠짐 — 후보 기준 시각은 11:10
        lake.reconcile()
        self.assertTrue(lake.run().ok)
        record = lake.record()
        self.assertEqual((record["cutoff_at"], record["cutoff_max_produced_us"], record["cutoff_previous_us"]),
                         ("2026-11-12T12:30:00.000000Z", int(at("11:10").timestamp() * 1_000_000), int(at("12:30").timestamp() * 1_000_000)))
        self.assertCountEqual(lake.gold(), [H("10:00"), H("11:00")])
        self.assertEqual(lake.corrections(), [])


class Confirmed(unittest.TestCase):  # AG5 (가) + r2b C1 + r3b C2 + §3.3
    def test_window_and_the_hour_before(self):  # 10:58 수신·11:02 판정 반응이 빠짐 → 11시 창 mismatch → 10시 칸도 거짓
        lake = started(self, ev("a", "09:20"), ev("b", "10:20"), ev("c", "11:20"), ev("d", "12:20"), clock("14:00"), windows=[(at("11:00"), "mismatch")])
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(), [H("09:00"), H("10:00", confirmed=False), H("11:00", confirmed=False), H("12:00")])
        self.assertEqual(lake.record()["unconfirmed_hours"], 2)
        lake.reconcile([(at("11:00"), "match")])  # 다음 회차 대사가 맞으면 다시 참(같은 Silver 판) — 정정 줄에 확정 전후
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(), [H("09:00"), H("10:00"), H("11:00"), H("12:00")])
        lines = lake.corrections()
        self.assertEqual([(r["cell_time"], r["confirmed_before"], r["confirmed_after"], r["metrics_before"], r["metrics_after"], r["reason"]) for r in lines],
                         [(naive(at(t)), False, True, {"events": 1, "amount": 0}, {"events": 1, "amount": 0}, "silver_changed") for t in ("10:00", "11:00")])

    def test_kst_midnight_hour_marks_the_day_before(self):  # §3.3 — KST 00시대(UTC 15시) 미확정 → 전날도 거짓
        lake = started(self, ev("a", "01:00"), ev("b", "15:20"), ev("c", "+1 16:00"), clock("+2 16:00"), windows=[(at("15:00"), "mismatch")])
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(DAILY), [D(0, confirmed=False), D(1, confirmed=False), D(2)])  # 01:00 = KST 11-12, 15:20 = 11-13, +1 16:00 = 11-14
        lake.reconcile([(at("+1 15:00"), "mismatch")])  # KST 11-14 00시대 → 11-14 와 전날 11-13
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(DAILY), [D(0), D(1, confirmed=False), D(2, confirmed=False)])

    def test_unplaced_is_everything_and_other_rows_are_not(self):
        lake = started(self, ev("a", "10:20"), clock("12:30"), windows=[])
        lake.reconcile(errors="mismatch", quarantine="mismatch")  # 오류-Bronze·목록-Bronze — 본 표 숫자에 닿지 않는다(리뷰 r2 참고 ①)
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(), [H("10:00")])
        lake.reconcile(unplaced="mismatch")
        self.assertTrue(lake.run().ok)
        self.assertEqual((sorted(lake.gold(), key=repr), lake.record()["unconfirmed_everything"]), ([H("10:00", confirmed=False)], True))

    def test_doubt_hours_only_with_a_window_mismatch(self):  # C1·C2 — "차원 없음" 칸에 센 사건의 시간
        lake = started(self, ev("a", "09:20", group=None), ev("b", "09:40"), ev("c", "12:20"), ev("d", "15:20"), clock("17:00"))
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(), [H("09:00", group=None), H("09:00"), H("12:00"), H("15:00")])
        self.assertEqual(lake.record()["counts"], {"no_group": 1})
        lake.reconcile([(at("15:00"), "mismatch")])
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(), [H("09:00", group=None, confirmed=False), H("09:00", confirmed=False), H("12:00"), H("15:00", confirmed=False)])
        self.assertEqual(lake.record()["unconfirmed_hours"], 3)


class Check(unittest.TestCase):  # AG7 (가) — 확인이 쓰기 앞
    def test_inflated_join_writes_nothing(self):
        lake = started(self, ev("a", "10:20"), clock("12:30"))
        self.assertTrue(lake.run().ok)
        before = (lake.version(lake.gold_path()), lake.version(lake.gold_path(DAILY)), lake.version(control.corrections_path(lake.r, "ads")))

        def doubled(frame):
            built = hourly_build(frame)
            return built._replace(events=built.events.unionByName(built.events))  # 결정이 두 번 잡힌 잇기
        bad = HOURLY._replace(build=doubled)
        lake.add(ev("b", "10:30"))
        lake.reconcile()
        result = lake.run(tables=(bad, DAILY))
        self.assertEqual(result, framework.Result(False, {}))
        self.assertEqual((lake.version(lake.gold_path()), lake.version(lake.gold_path(DAILY)), lake.version(control.corrections_path(lake.r, "ads"))), before)
        self.assertIn("gold alert check_mismatch table=events_hourly metric=events gold=4 silver=2", lake.logs)

    def test_money_out_of_range_writes_nothing(self):  # §2.2 — 조용히 바꾸지 않는다
        lake = started(self, ev("a", "10:20"), clock("12:30"))
        self.assertTrue(lake.run().ok)
        before = lake.version(lake.gold_path())
        for eid, amount in (("x", 10 ** 30), ("y", "12"), ("z", 1.5)):
            with self.subTest(amount=amount):
                lake.add(ev(eid, "10:30", kind="charge", amount=amount))
                lake.reconcile()
                with self.assertRaisesRegex(framework.GoldError, "events_hourly: 페이로드에서 꺼낸 수 1 개가 형·범위를 벗어났다"):
                    lake.run()
                self.assertEqual(lake.version(lake.gold_path()), before)
                lake.delete(eid)
        lake.add(ev("ok", "10:30", kind="charge", amount=7))
        lake.reconcile()
        self.assertTrue(lake.run().ok)
        self.assertIn(H("10:00", kind="charge", amount=7), lake.gold())


class Corrections(unittest.TestCase):  # AG4 (가) + r3d — 범위 안의 바뀜·생김·없어짐, 이유 셋, 전후 판
    def test_changes_inside_the_written_range(self):
        lake = started(self, ev("a", "10:20"), ev("b", "10:40", group="g2"), ev("c", "11:20"), clock("12:30"))
        self.assertTrue(lake.run().ok)
        lake.add(ev("late", "10:50"), ev("new", "11:30", group="g3"), clock("13:30"))  # 늦은 판정 · 범위 안 새 칸 · 12시 칸은 이번에 처음 범위
        lake.delete("b")  # 격리 — g2 칸이 없어짐
        lake.reconcile()
        self.assertTrue(lake.run().ok)
        self.assertCountEqual(lake.gold(), [H("10:00", events=2), H("11:00"), H("11:00", group="g3"), H("12:00", kind="clock", group="clock")])
        lines = lake.corrections()
        silver = (lake.record(version=0)["silver_version"], lake.record(version=1)["silver_version"])
        self.assertEqual([(r["cell_time"], r["cell_key"], r["metrics_before"], r["metrics_after"], r["confirmed_before"], r["confirmed_after"]) for r in lines],
                         [(naive(at("10:00")), {"event_type": "impression", "group": "g1"}, {"events": 1, "amount": 0}, {"events": 2, "amount": 0}, True, True),
                          (naive(at("10:00")), {"event_type": "impression", "group": "g2"}, {"events": 1, "amount": 0}, None, True, None),
                          (naive(at("11:00")), {"event_type": "impression", "group": "g3"}, None, {"events": 1, "amount": 0}, None, True)])
        self.assertEqual({(r["reason"], r["gold_version_before"], r["gold_version_after"], r["silver_generation_before"], r["silver_generation_after"],
                           r["silver_version_before"], r["silver_version_after"], r["run_at"]) for r in lines},
                         {("silver_changed", 0, 1, 1, 1, *silver, naive(at("+3 00:00")))})

    def test_kst_day_cell_time(self):
        lake = started(self, ev("a", "01:00"), clock("16:00"))
        self.assertTrue(lake.run().ok)
        lake.add(ev("b", "02:00"))
        lake.reconcile()
        self.assertTrue(lake.run().ok)
        self.assertEqual([(r["cell_time"], r["cell_key"], r["metrics_before"], r["metrics_after"]) for r in lake.corrections(DAILY)],
                         [(naive(at("00:00") - timedelta(hours=9)), {"event_type": "impression"}, {"events": 1}, {"events": 2})])  # KST 11-12 00시 = UTC 11-11 15:00

    def test_reasons(self):  # AG8 (가) — 세대 > Gold 코드 > Silver
        lake = started(self, ev("a", "10:20"), clock("12:30"))
        self.assertTrue(lake.run().ok)
        lake.add(ev("b", "10:30"))
        lake.reconcile()
        self.assertTrue(lake.run(release="gold@other0000000").ok)
        lake.add(ev("a", "10:20"), ev("c", "10:25"), ev("d", "10:35"), clock("12:30"), g=2)
        lake.reconcile(g=2)
        lake.point(2)
        self.assertTrue(lake.run(release="gold@third0000000").ok)
        self.assertEqual([(r["gold_version_after"], r["reason"], r["silver_generation_before"], r["silver_generation_after"], r["metrics_after"]) for r in lake.corrections()],
                         [(1, "gold_release_changed", 1, 1, {"events": 2, "amount": 0}), (2, "generation_changed", 1, 2, {"events": 3, "amount": 0})])


class Recovery(unittest.TestCase):  # §2.1 정정 줄 잃지 않기 + 권고 1 — 빠진 판을 차례로, 회복 중 죽어도 이어 씀, 같은 판을 두 번 쓰지 않음
    def test_missing_versions_are_filled_in_order(self):
        lake = started(self, ev("e0", "10:20"), clock("12:30"))
        self.assertTrue(lake.run().ok)
        for n in (1, 2):  # 두 회차가 정정을 쓰지 못함(회복도 돌지 않음 — 빠진 판이 둘)
            lake.add(ev(f"e{n}", "10:20"))
            lake.reconcile()
            with mock.patch.object(framework, "catch_up", lambda *args, **kwargs: 0):
                self.assertTrue(lake.run().ok)
        self.assertEqual((lake.version(lake.gold_path()), lake.done()), (2, 0))
        real_diff, calls = framework.diff, []

        def dies_on_second(*args, **kwargs):
            calls.append(args[4][0])
            if len(calls) == 2:
                raise RuntimeError("회복 중에 죽음")
            return real_diff(*args, **kwargs)
        lake.add(ev("e3", "10:20"))
        lake.reconcile()
        with mock.patch.object(framework, "diff", dies_on_second), self.assertRaisesRegex(RuntimeError, "회복 중에 죽음"):
            lake.run()
        self.assertEqual((calls, lake.version(lake.gold_path()), lake.done()), ([1, 2], 2, 1))
        self.assertTrue(lake.run().ok)
        lines = [(r["gold_version_before"], r["gold_version_after"], r["metrics_before"]["events"], r["metrics_after"]["events"]) for r in lake.corrections()]
        self.assertEqual(lines, [(0, 1, 1, 2), (1, 2, 2, 3), (2, 3, 3, 4)])
        self.assertEqual((lake.done(), lake.done(DAILY)), (3, 3))
        self.assertTrue(lake.run().ok)  # 바뀐 것이 없는 회차도 표시는 남는다(빈 커밋 — 다음 회차가 다시 견주지 않음)
        self.assertEqual((len(lake.corrections()), lake.done()), (3, 4))


    def test_recreated_table_is_refused(self):  # GOLD-1 리뷰 권고 1 — 표 폴더를 지우고 다시 만들어 판이 정정 기록보다 앞서면 조용히 건너뛰지 않는다
        lake = started(self, ev("a", "10:20"), clock("12:30"))
        for _ in range(3):
            self.assertTrue(lake.run().ok)
        self.assertEqual(lake.done(), 2)
        shutil.rmtree(lake.gold_path())
        lake.add(ev("b", "10:30"))
        lake.reconcile()
        with self.assertRaisesRegex(framework.GoldError, r"events_hourly/v1 의 지금 판 -1\(표 없음\) 이 정정 기록의 마지막 판 2\(gold:ads:events_hourly:v1\)보다 앞이다 — "
                                                         r"표를 다시 만들지 말고 새 표 판\(v2\)으로"):
            lake.run()
        self.assertFalse(Path(lake.gold_path()).exists())  # 회차 시작에서 멈춰 아무것도 쓰지 않았다
        spark.range(1).write.format("delta").save(lake.gold_path())  # 손으로 다시 만든 표(판 0)도 같다
        with self.assertRaisesRegex(framework.GoldError, r"지금 판 0 이 정정 기록의 마지막 판 2"):
            lake.run()


class JobEntry(unittest.TestCase):
    def test_main(self):
        env = {"GOLD_LAKE_ROOT": "/lake", "GOLD_CODE_ROOT": str(Path(__file__).resolve().parents[3])}
        self.assertEqual(gold_job.main(["--domain", "ads"], {}), 2)
        package, module = types.ModuleType("pipeline.gold.tables"), types.ModuleType("pipeline.gold.tables.ads")
        module.TABLES, module.DELAY_SECONDS = TABLES, DELAY
        session = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"pipeline.gold.tables": package, "pipeline.gold.tables.ads": module}), \
                mock.patch.object(gold_job.silver_job, "session", return_value=session) as make, mock.patch.object(gold_job.framework, "run") as run:
            for result, rc in ((framework.Result(True, {}), 0), (framework.Result(False, {}), 3)):
                run.return_value = result
                self.assertEqual(gold_job.main(["--domain", "ads"], env), rc)
            make.assert_called_with("gold-ads", None)
            self.assertEqual(run.call_args.args[1:5], ("/lake", "ads", TABLES, control.gold_release(env["GOLD_CODE_ROOT"])))
            self.assertEqual(run.call_args.kwargs["delay_seconds"], DELAY)
            self.assertEqual(session.stop.call_count, 2)


if __name__ == "__main__":
    unittest.main()
