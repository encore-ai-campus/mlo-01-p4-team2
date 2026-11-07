"""pipeline/silver/framework.py 배치 판정(SL-3b) — Spark 시험(쓰기 없음): Bronze 이중 적재 정리(B1), 멈춤 확인(C4·D3), 접기(D2), 전체 확인 (a)~(d)."""
import json
import unittest
from datetime import date, datetime, timedelta, timezone

from pyspark.sql import SparkSession, functions as F

from pipeline.silver import contracts, control, framework

T0 = datetime(2026, 11, 4, 5, 55, 37, tzinfo=timezone.utc)
D0 = date(2026, 11, 4)
TOPIC = "metaverse.outbox.events"
SHA_A, SHA_B = "a" * 64, "b" * 64


def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def setUpModule():
    global spark
    spark = SparkSession.builder.getOrCreate()


def columns(frame_or_schema):
    schema = getattr(frame_or_schema, "schema", frame_or_schema)
    return [(f.name, f.dataType.simpleString()) for f in schema.fields]


def bronze_fields(offset, *, topic=TOPIC, partition=0, ingested=T0, value=b"v", release="bronze@x"):
    return (b"k", bytearray(value), [], topic, partition, offset, T0, 0, ingested, release, ingested.date())


def ok_row(offset, event_id, sha=SHA_A, *, topic=TOPIC, partition=0, ingested=T0, produced=T0, op="c"):
    adapter = {"event_id": event_id, "domain": "metaverse", "event_type": "post_created", "schema_version": "1.0", "occurred_at": produced,
               "produced_at": produced, "actor_user_uuid": None, "actor_subject_id": None, "actor_person_id": None, "session_uuid": None,
               "aggregate_id": None, "payload": "{}", "envelope": None, "source_meta": None, "src_lsn": None if op == "r" else 100,
               "src_txid": None, "first_op": op, "content_sha256": sha}
    return bronze_fields(offset, topic=topic, partition=partition, ingested=ingested) + tuple(adapter[name] for name, _, _ in contracts.ADAPTER_FIELDS)


def error_row(offset, code, *, event_id=None, topic=TOPIC, partition=0):
    return bronze_fields(offset, topic=topic, partition=partition) + (code, f"사유 {code}", event_id)


def oks(*rows):
    return spark.createDataFrame(list(rows), contracts.OK_SCHEMA)


def errs(*rows):
    return spark.createDataFrame(list(rows), contracts.ADAPTER_ERROR_SCHEMA)


def silver(*rows):
    return spark.createDataFrame(list(rows), "event_id string, event_date date, content_sha256 string")


def ids(*values):
    return spark.createDataFrame([(v,) for v in values], "event_id string")


def bronze(*rows):
    return spark.createDataFrame(list(rows), contracts.BRONZE_SCHEMA)


def ack_lines(*lines):
    return control.load_acknowledged(json.dumps({"domain": "metaverse", "lines": list(lines)}), "metaverse")


POINT = {"topic": TOPIC, "partition": 0, "offset": 12, "error_code": "foreign_source", "reason": "커넥터 표 목록 오류", "incident": "INC-7",
         "acknowledged_by": "운영자", "acknowledged_at": "2026-11-04T06:00:00Z"}
RANGE = {"topic": TOPIC, "partition": 1, "offset_start": 10, "offset_end": 20, "row_count": 3, "error_code": "not_insert", "reason": "TRUNCATE",
         "incident": "INC-8", "acknowledged_by": "운영자", "acknowledged_at": "2026-11-04T06:00:00Z"}


class DedupeBronze(unittest.TestCase):
    def test_same_kafka_bytes_keep_the_first_ingested_row(self):
        frame = bronze(bronze_fields(1, ingested=T0 + timedelta(hours=2), release="bronze@new"), bronze_fields(1, ingested=T0), bronze_fields(2),
                       bronze_fields(1, ingested=T0 + timedelta(hours=1)))
        kept, dropped = framework.dedupe_bronze(frame)
        self.assertEqual(dropped, 2)
        self.assertEqual(sorted((r["offset"], r["ingested_at"], r["pipeline_release"]) for r in kept.collect()),
                         [(1, T0.replace(tzinfo=None), "bronze@x"), (2, T0.replace(tzinfo=None), "bronze@x")])
        self.assertEqual(columns(kept), columns(contracts.BRONZE_SCHEMA))

    def test_identical_rows_and_no_duplicates(self):
        kept, dropped = framework.dedupe_bronze(bronze(bronze_fields(1), bronze_fields(1), bronze_fields(2, partition=1), bronze_fields(2)))
        self.assertEqual((kept.count(), dropped), (3, 1))
        frame = bronze(bronze_fields(1), bronze_fields(1, topic=TOPIC + ".g2"))
        kept, dropped = framework.dedupe_bronze(frame)
        self.assertIs(kept, frame)  # 위치 중복이 없으면 받은 DF 그대로 — 창(Bronze 전체 열 셔플)을 거치지 않는다(SL-3b 리뷰 권고 1)
        self.assertEqual((kept.count(), dropped), (2, 0))

    def test_same_position_with_other_kafka_bytes_stops(self):  # K4 — 같은 이름의 토픽을 다시 만들어 offset 이 겹침
        with self.assertRaisesRegex(framework.BronzeConflictError, f"{TOPIC}/0/1"):
            framework.dedupe_bronze(bronze(bronze_fields(1), bronze_fields(1, value=b"w", ingested=T0 + timedelta(hours=1)), bronze_fields(2)))

    def test_bytes_moved_between_null_and_value_columns_stop(self):  # xxhash64 는 null 을 건너뛰어 두 줄의 지문이 같다 — 값을 그대로 비교한다(참고 4)
        moved = (None, bytearray(b"x")), (bytearray(b"x"), None)
        rows = [key_value + bronze_fields(1)[2:] for key_value in moved]
        self.assertEqual(bronze(*rows).select(F.xxhash64("key_binary", "value_binary").alias("fp")).distinct().count(), 1)
        with self.assertRaises(framework.BronzeConflictError):
            framework.dedupe_bronze(bronze(*rows))


class Acknowledge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = ack_lines(POINT, RANGE, RANGE | {"partition": 2, "offset_start": 0, "offset_end": 5, "row_count": 99})
        # 파티션 1 의 10~20 에는 Bronze 행이 셋(10·12·15 — EOS 표식 때문에 offset 이 이어지지 않음), 25 는 범위 밖
        cls.table = bronze(*[bronze_fields(o, partition=1) for o in (10, 12, 15, 25)], bronze_fields(12), bronze_fields(13))

    def judge_errors(self, rows, lines=None):
        out, stops, mismatches = framework.acknowledge(errs(*rows), self.lines if lines is None else lines, self.table)
        return ({(r["partition"], r["offset"]): r for r in out.collect()}, {(r["partition"], r["offset"]): r for r in stops.collect()}, mismatches)

    def test_listed_point_and_range_rows_are_acknowledged_and_others_pass(self):
        out, stops, mismatches = self.judge_errors([error_row(12, "foreign_source"), error_row(10, "not_insert", partition=1, event_id=uid(1)),
                                                    error_row(15, "not_insert", partition=1), error_row(13, "decode")])
        self.assertEqual((stops, mismatches), ({}, []))
        self.assertEqual({k: (r["acknowledged"], r["ack_incident"], r["ack_reason"]) for k, r in out.items()},
                         {(0, 12): (True, "INC-7", "커넥터 표 목록 오류"), (1, 10): (True, "INC-8", "TRUNCATE"), (1, 15): (True, "INC-8", "TRUNCATE"),
                          (0, 13): (False, None, None)})
        self.assertEqual(out[(1, 10)]["event_id"], uid(1))
        self.assertEqual(out[(0, 12)]["error_code"], "foreign_source")  # 코드는 그대로

    def test_unlisted_stop_rows_become_diagnostics(self):
        rows = [error_row(13, "foreign_source"), error_row(12, "foreign_source", partition=1), error_row(12, "foreign_source", topic="other"),
                error_row(12, "not_insert"), error_row(21, "not_insert", partition=1)]
        out, stops, mismatches = framework.acknowledge(errs(*rows), self.lines, self.table)
        self.assertEqual((out.count(), mismatches), (0, []))
        # 위치·토픽·파티션·코드 어느 하나가 넘김 줄과 다르면 넘기지 않는다
        self.assertEqual(sorted((r["topic"], r["partition"], r["offset"], r["error_code"], r["acknowledged"]) for r in stops.collect()),
                         sorted([(TOPIC, 0, 13, "foreign_source", False), (TOPIC, 1, 12, "foreign_source", False), ("other", 0, 12, "foreign_source", False),
                                 (TOPIC, 0, 12, "not_insert", False), (TOPIC, 1, 21, "not_insert", False)]))

    def test_range_count_mismatch_refuses_the_line(self):
        lines = ack_lines(POINT, RANGE | {"row_count": 4})
        out, stops, mismatches = self.judge_errors([error_row(10, "not_insert", partition=1), error_row(15, "not_insert", partition=1)], lines)
        self.assertEqual((out, sorted(stops)), ({}, [(1, 10), (1, 15)]))
        self.assertEqual([(line["incident"], n) for line, n in mismatches], [("INC-8", 3)])

    def test_unmatched_range_lines_are_not_counted(self):  # 파티션 2 줄(행 수 99 — 틀림)은 이번 배치에서 맞지 않아 세지 않는다
        _, _, mismatches = self.judge_errors([error_row(10, "not_insert", partition=1)])
        self.assertEqual(mismatches, [])

    def test_output_shapes(self):
        out, stops, _ = framework.acknowledge(errs(error_row(13, "decode")), [], self.table)
        self.assertEqual(columns(out), columns(contracts.JUDGED_ERROR_SCHEMA))
        self.assertEqual(columns(stops), columns(contracts.JUDGED_ERROR_SCHEMA))
        self.assertEqual(stops.count(), 0)


class Fold(unittest.TestCase):
    def test_copies_and_first_copy_by_ingestion(self):  # D2
        frame = oks(ok_row(9, uid(1), ingested=T0 + timedelta(minutes=2), op="r"), ok_row(5, uid(1), ingested=T0, op="c"),
                    ok_row(1, uid(1), topic=TOPIC + ".g2", ingested=T0 + timedelta(minutes=1), op="r"), ok_row(7, uid(2)))
        folded, conflicts = framework.fold(frame)
        rows = {r["event_id"]: r for r in folded.collect()}
        self.assertEqual((rows[uid(1)]["src_topic"], rows[uid(1)]["src_offset"], rows[uid(1)]["first_op"], rows[uid(1)]["copies"]), (TOPIC, 5, "c", 3))
        self.assertEqual(rows[uid(2)]["copies"], 1)
        self.assertEqual(conflicts.count(), 0)
        self.assertEqual(columns(folded), columns(contracts.MERGE_SOURCE_SCHEMA))

    def test_same_ingestion_falls_back_to_topic_partition_offset(self):
        folded, _ = framework.fold(oks(ok_row(9, uid(1), op="r"), ok_row(3, uid(1), partition=1, op="r"), ok_row(5, uid(1), op="c"),
                                       ok_row(1, uid(1), topic=TOPIC + ".g2", op="r")))
        row = folded.first()
        self.assertEqual((row["src_topic"], row["src_partition"], row["src_offset"], row["first_op"], row["copies"]), (TOPIC, 0, 5, "c", 4))

    def test_event_date_is_the_utc_date_of_produced_at(self):
        late = datetime(2026, 11, 4, 23, 30, tzinfo=timezone.utc)  # 한국 시간으로는 11월 5일
        folded, _ = framework.fold(oks(ok_row(1, uid(1), produced=late)))
        self.assertEqual(folded.first()["event_date"], D0)

    def test_two_hashes_make_every_copy_a_conflict(self):
        folded, conflicts = framework.fold(oks(ok_row(1, uid(1)), ok_row(2, uid(1), SHA_B), ok_row(3, uid(1)), ok_row(4, uid(2))))
        self.assertEqual([r["event_id"] for r in folded.collect()], [uid(2)])
        self.assertEqual(sorted(r["offset"] for r in conflicts.collect()), [1, 2, 3])

    def test_session_time_zone_must_be_utc(self):
        spark.conf.set("spark.sql.session.timeZone", "Asia/Seoul")
        try:
            with self.assertRaisesRegex(ValueError, "UTC"):
                framework.fold(oks(ok_row(1, uid(1))))
        finally:
            spark.conf.set("spark.sql.session.timeZone", "UTC")


class Judge(unittest.TestCase):
    """새 ID(N), 같은 날짜·같은 해시(S, 사본 둘), 같은 날짜·다른 해시(H), 다른 날짜(D), 오류 표 충돌 ID(A), Silver 안 중복(M), 배치 안 충돌(C)."""
    N, S, H, D, A, M, C = (uid(n) for n in range(1, 8))

    @classmethod
    def setUpClass(cls):
        cls.ok = oks(ok_row(0, cls.N), ok_row(1, cls.S), ok_row(2, cls.S, op="r", ingested=T0 + timedelta(minutes=1)), ok_row(3, cls.H, SHA_B),
                     ok_row(4, cls.D), ok_row(5, cls.A), ok_row(6, cls.M), ok_row(7, cls.C), ok_row(8, cls.C, SHA_B))
        cls.errors = errs(error_row(9, "decode"), error_row(10, "contract", event_id=cls.N))
        cls.silver = silver((cls.S, D0, SHA_A), (cls.H, D0, SHA_A), (cls.D, date(2026, 11, 3), SHA_A), (cls.M, D0, SHA_A), (cls.M, date(2026, 11, 5), SHA_A),
                            (uid(99), D0, SHA_A))
        cls.conflicts = ids(cls.A, uid(98))
        cls.table = bronze(bronze_fields(0))
        cls.result = framework.judge(cls.ok, cls.errors, [], cls.table, cls.silver, cls.conflicts)

    def test_merge_rows(self):
        self.assertFalse(self.result.stop)
        merged = {r["event_id"]: r for r in self.result.merge.collect()}
        self.assertEqual(set(merged), {self.N, self.S})  # (d) 새 ID, (c) 같은 날짜·같은 해시
        self.assertEqual((merged[self.S]["copies"], merged[self.S]["first_op"], merged[self.S]["src_offset"]), (2, "c", 1))
        self.assertEqual(columns(self.result.merge), columns(contracts.MERGE_SOURCE_SCHEMA))

    def test_conflicts_with_reasons_and_existing_rows(self):
        found = {}
        for r in self.result.errors.where(F.col("error_code") == "conflict").collect():
            found.setdefault(r["event_id"], []).append((r["offset"], r["reason"], r["existing_event_id"], r["existing_event_date"]))
        self.assertEqual(sorted(found), sorted([self.H, self.D, self.A, self.M, self.C]))
        self.assertEqual(found[self.H], [(3, "같은 event_date 에 내용이 다른 같은 event_id 가 이미 있다", self.H, D0)])  # (c) 다름
        self.assertEqual(found[self.D], [(4, "같은 event_id 가 다른 event_date(2026-11-03)에 이미 있다", self.D, date(2026, 11, 3))])  # (b)
        self.assertEqual(found[self.A], [(5, "이미 conflict 로 오류 표에 있는 event_id 의 새 사본", None, None)])  # (a)
        self.assertEqual(found[self.M][0][:2], (6, "Silver 안에 같은 event_id 가 둘 이상 있다(두 번째 그물 대상) — 사람이 정할 때까지 더 넣지 않는다"))
        self.assertEqual(sorted(found[self.C]), [(7, "배치 안에서 같은 event_id 의 내용이 다르다", None, None), (8, "배치 안에서 같은 event_id 의 내용이 다르다", None, None)])

    def test_every_ok_row_is_merged_or_an_error_and_adapter_errors_pass(self):
        conflict_rows = self.result.errors.where(F.col("error_code") == "conflict").count()
        copies = self.result.merge.agg(F.sum("copies")).first()[0]
        self.assertEqual(copies + conflict_rows, self.ok.count())
        self.assertEqual(sorted(r["offset"] for r in self.result.errors.where(F.col("error_code") != "conflict").collect()), [9, 10])
        self.assertEqual(columns(self.result.errors), columns(contracts.JUDGED_ERROR_SCHEMA))
        rows = contracts.error_rows(self.result.errors, silver_release="silver@x", detected_at=T0, batch_id=1)
        self.assertEqual(columns(rows), columns(contracts.ERROR_SCHEMA))
        self.assertEqual(self.result.stops.count(), 0)

    def test_rerun_after_merge_is_not_a_conflict(self):  # MERGE 커밋 뒤 같은 배치를 다시 돌리면 자기 행을 본다 — (c) 같음
        merged = self.result.merge.select("event_id", "event_date", "content_sha256")  # MERGE 흉내: 짝이 없는 것만 넣는다(짝이 있으면 copies 만 더함)
        written = self.silver.unionByName(merged.join(self.silver, ["event_id", "event_date"], "left_anti"))
        self.assertEqual(written.count(), self.silver.count() + 1)  # N 만 새로 들어감
        again = framework.judge(self.ok, self.errors, [], self.table, written, self.conflicts)
        self.assertEqual({r["event_id"] for r in again.merge.collect()}, {self.N, self.S})
        self.assertEqual(again.errors.where(F.col("error_code") == "conflict").count(), self.result.errors.where(F.col("error_code") == "conflict").count())

    def test_broadcast_and_shuffle_paths_agree(self):  # SL-3c 결정 S3 — 배치 고유 ID 가 상한을 넘으면 broadcast 없이, 판정은 같다
        shuffled = framework.judge(self.ok, self.errors, [], self.table, self.silver, self.conflicts, broadcast_limit=0)
        self.assertEqual((self.result.broadcast, shuffled.broadcast, self.result.ids, shuffled.ids), (True, False, 2 + 4, 2 + 4))  # N·S + H·D·A·M
        merged = lambda j: sorted((r["event_id"], r["copies"], r["src_offset"]) for r in j.merge.collect())
        judged = lambda j: sorted((r["offset"], r["error_code"], r["reason"], r["existing_event_id"], r["existing_event_date"]) for r in j.errors.collect())
        self.assertEqual((merged(shuffled), judged(shuffled)), (merged(self.result), judged(self.result)))
        self.assertGreaterEqual(self.result.check_seconds, 0)
        self.assertEqual(len(self.result.cached), 2)

    def test_shuffle_path_has_no_broadcast(self):  # 작은 표는 힌트가 없어도 Spark 가 스스로 broadcast 하므로 자동 broadcast 를 끄고 계획을 본다
        spark.catalog.clearCache()  # 앞 시험이 persist 한 판정 DF 가 같은 계획으로 잡혀(힌트는 cache 비교에서 빠짐) 그 계획이 보이지 않게
        spark.conf.set("spark.sql.autoBroadcastJoinThreshold", "-1")
        try:
            folded, _ = framework.fold(self.ok)
            plans = {flag: framework.full_check(folded, self.silver, self.conflicts, broadcast=flag)[1]._jdf.queryExecution().executedPlan().toString()
                     for flag in (True, False)}
        finally:
            spark.conf.unset("spark.sql.autoBroadcastJoinThreshold")
        self.assertIn("BroadcastExchange", plans[True])
        self.assertNotIn("Broadcast", plans[False])

    def test_stop_then_acknowledged_rerun(self):  # 멈춤이면 일찍 끝나고 진단 행만, 넘김 줄을 더해 다시 돌면 오류 행으로(D4 — 쓰기는 SL-3c)
        errors = errs(error_row(12, "foreign_source"), error_row(9, "decode"))
        stopped = framework.judge(self.ok, errors, [], self.table, self.silver, self.conflicts)
        self.assertEqual((stopped.stop, stopped.merge, stopped.errors), (True, None, None))
        self.assertEqual([(r["offset"], r["acknowledged"]) for r in stopped.stops.collect()], [(12, False)])
        rerun = framework.judge(self.ok, errors, ack_lines(POINT), self.table, self.silver, self.conflicts)
        self.assertFalse(rerun.stop)
        self.assertEqual(sorted((r["offset"], r["acknowledged"]) for r in rerun.errors.where(F.col("error_code") != "conflict").collect()), [(9, False), (12, True)])


if __name__ == "__main__":
    unittest.main()
