"""Bronze manifest 잡(`pipeline/bronze/hop_manifest_job.py`, 파이프라인 계획 P2-1)의 Spark 밖 부분: 게시 순서·사슬, 새 사슬은 포인터가 없을 때(404)만, 범위·설정.
Bronze 를 읽고 실제 S3 에 쓰는 부분은 Standalone 제출로 확인한다(끝 조건 — C4 13시 창과 같은 결과·중복 3건, Step 기록). 표준 라이브러리만 쓴다."""
import contextlib
import hashlib
import io
import json
import sys
import traceback
import unittest
from datetime import datetime, timezone
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for folder in ("pipeline/common", "pipeline/bronze"):
    if str(ROOT / folder) not in sys.path:
        sys.path.insert(0, str(ROOT / folder))

import hop_manifest_job as job  # noqa: E402
import hop_rules  # noqa: E402
import ndjson  # noqa: E402

NOW = datetime(2026, 11, 2, 15, 2, tzinfo=timezone.utc)
CONFIG = {"table": "s3a://lake/bronze/t", "uri": "s3a://lake/manifests/hop_pipeline/", "db": "ads_db", "grace": 120}


class FakeStore:
    """S3 접두사 흉내: 없는 파일은 exists() False(404). `error` 를 주면 다음 exists() 한 번이 그 예외를 올린다(일시적인 권한 없음·시간 초과 — 그 뒤 호출은 정상)."""

    def __init__(self):
        self.files, self.writes, self.error = {}, [], None

    def exists(self, name):
        if self.error:
            error, self.error = self.error, None
            raise error
        return name in self.files

    def read(self, name):
        return self.files[name]

    def write(self, name, body, overwrite):
        if not overwrite and name in self.files:
            raise FileExistsError(name)
        self.files[name] = body
        self.writes.append((name, overwrite))


def envelope(event_id, minute=31, name="ad.decision", payload="{}", db="ads_db", op="c", hour=13):
    after = {"event_id": event_id, "event_name": name, "schema_version": 1, "aggregate_id": "0197a5c0-0000-7000-8000-0000000000aa",
             "payload": payload, "created_at": f"2026-11-02T{hour:02d}:{minute:02d}:33.123456Z", "sent_at": None}
    return json.dumps({"op": op, "source": {"db": db, "schema": "public", "table": "events_outbox"}, "after": after})


def lines_and_checkpoint(rows=None):
    plan = job.plan("hour", as_of="2026-11-02T15:02:00Z", since="2026-11-02T13:00:00Z")
    rows = rows if rows is not None else [(None, envelope("0197a5c0-0000-7000-8000-000000000001"), 0, 1)]
    return job.summarize(iter(rows), plan, CONFIG)


class PublishTests(unittest.TestCase):
    def check(self, store, name="hop_pipeline_hour"):
        """광고 서버 받기(fetch_hop_manifest)와 같은 규칙(ndjson.check·check_chain — 같은 바이트 사본)으로 확인한다."""
        pointer = store.files[f"{name}.manifest.json"]
        problems = ndjson.check(json.loads(pointer), name, store.files.get)
        return problems + ndjson.check_chain(pointer, name, store.files.get)

    def test_the_first_publish_starts_a_chain_and_writes_the_pointer_last(self):
        store = FakeStore()
        lines, checkpoint, _ = lines_and_checkpoint()
        manifest, digest = job.publish(store, "hop_pipeline_hour", lines, checkpoint, now=NOW)
        self.assertIsNone(manifest["previous_sha256"])
        names = [name for name, _ in store.writes]
        self.assertEqual([n.endswith(".ndjson") for n in names], [True, False, False])  # 데이터 → 세대 사본 → 포인터
        self.assertEqual(store.writes[-1], ("hop_pipeline_hour.manifest.json", True))
        self.assertTrue(all(not overwrite for _, overwrite in store.writes[:-1]))  # 불변 파일은 덮어쓰지 않는다
        self.assertEqual(digest, hashlib.sha256(store.files["hop_pipeline_hour.manifest.json"]).hexdigest())
        self.assertEqual(names[1], ndjson.generation_copy_name("hop_pipeline_hour", digest))
        self.assertEqual(self.check(store), [])

    def test_the_next_publish_links_to_the_previous_pointer_and_adds_only_new_files(self):
        store = FakeStore()
        lines, checkpoint, _ = lines_and_checkpoint()
        _, first = job.publish(store, "hop_pipeline_hour", lines, checkpoint, now=NOW)
        before = dict(store.files)
        store.writes.clear()
        more, checkpoint, _ = lines_and_checkpoint([(None, envelope("0197a5c0-0000-7000-8000-000000000001"), 0, 1), (None, envelope("0197a5c0-0000-7000-8000-000000000002"), 0, 2)])
        manifest, second = job.publish(store, "hop_pipeline_hour", more, checkpoint, now=NOW)
        self.assertEqual(manifest["previous_sha256"], first)
        self.assertEqual([n.endswith(".ndjson") for n, _ in store.writes], [True, False, False])  # 새 데이터·새 사본·포인터만
        self.assertTrue(all(store.files[name] == body for name, body in before.items() if name != "hop_pipeline_hour.manifest.json"))  # 앞 세대 파일은 그대로
        self.assertEqual(self.check(store), [])
        self.assertNotEqual(first, second)

    def test_only_a_missing_pointer_starts_a_new_chain(self):
        """포인터가 없을 때(404 → exists False)만 새 사슬이다. 권한 없음·시간 초과는 실패이고 아무것도 올리지 않는다(새 사슬로 착각하면 받는 쪽 고정점이 막는다).
        이미 사슬이 있는 접두사에서, 포인터 확인 한 번만 실패하고 나머지는 정상인 경우다."""
        lines, checkpoint, _ = lines_and_checkpoint()
        for error in (PermissionError("403 AccessDenied"), TimeoutError("read timed out")):
            with self.subTest(error=type(error).__name__):
                store = FakeStore()
                job.publish(store, "hop_pipeline_hour", lines, checkpoint, now=NOW)
                before, store.writes = dict(store.files), []
                store.error = error
                with self.assertRaises(type(error)):
                    job.publish(store, "hop_pipeline_hour", lines, checkpoint, now=NOW)
                self.assertEqual((store.writes, store.files), ([], before))


class PlanAndSettingsTests(unittest.TestCase):
    def test_the_default_range_is_48_hours_or_8_days_back_from_the_current_window(self):
        hour = job.plan("hour", as_of="2026-11-02T15:02:00Z")
        self.assertEqual((hop_rules.format_time(hour["since"]), str(hour["read_from"]), hour["name"], hour["label"]), ("2026-10-31T15:00:00Z", "2026-10-30", "hop_pipeline_hour", "utc-hour"))
        day = job.plan("day", as_of="2026-11-04T00:35:00Z")
        self.assertEqual((hop_rules.format_time(day["since"]), str(day["read_from"]), day["name"], day["label"]), ("2026-10-27T00:00:00Z", "2026-10-26", "hop_pipeline_day", "utc-day"))

    def test_an_explicit_since_must_sit_on_a_window_boundary(self):
        self.assertEqual(hop_rules.format_time(job.plan("hour", as_of="2026-11-02T15:02:00Z", since="2026-11-02T13:00:00Z")["since"]), "2026-11-02T13:00:00Z")
        for window, since in (("day", "2026-11-02T13:00:00Z"), ("hour", "2026-11-02T13:30:00Z"), ("hour", "2026-11-2T13:00:00Z")):
            with self.subTest(window=window, since=since), self.assertRaises((job.JobError, ValueError)):
                job.plan(window, as_of="2026-11-02T15:02:00Z", since=since)

    def test_settings_have_no_defaults_and_the_prefix_must_be_a_folder(self):
        full = {"HOP_BRONZE_TABLE": "s3a://lake/bronze/t", "HOP_MANIFEST_URI": "s3a://lake/tmp/x/", "HOP_SOURCE_DB": "ads_db", "HOP_GRACE_SECONDS": "120"}
        self.assertEqual(job.settings(full)["grace"], 120)
        for name in job.ENV:
            with self.subTest(missing=name), self.assertRaisesRegex(job.JobError, name):
                job.settings({k: v for k, v in full.items() if k != name})
        for uri in ("s3a://lake/tmp/x", "s3://lake/tmp/x/", "s3a://lake/tmp/../x/", "s3a://lake/tmp/./x/", "/tmp/x/"):
            with self.subTest(uri=uri), self.assertRaises(job.JobError):
                job.settings(full | {"HOP_MANIFEST_URI": uri})
        with self.assertRaises(job.JobError):
            job.settings(full | {"HOP_GRACE_SECONDS": "two"})


class SummarizeTests(unittest.TestCase):
    def test_the_shared_rule_counts_unique_events_and_reports_duplicates_with_a_contract_checkpoint(self):
        rows = [(None, envelope("0197a5c0-0000-7000-8000-000000000001"), 0, 1), (None, envelope("0197a5c0-0000-7000-8000-000000000001"), 0, 9),
                (None, envelope("0197a5c0-0000-7000-8000-000000000002", minute=45), 1, 3), (None, envelope("x", db="other_db"), 2, 1),
                (None, envelope("0197a5c0-0000-7000-8000-000000000003", hour=12), 0, 4)]  # since(13시) 앞 창 — 읽는 범위(적재일 하루 여유) 안이지만 줄에 넣지 않는다
        lines, checkpoint, scanned = lines_and_checkpoint(rows)
        self.assertEqual(scanned, 5)
        self.assertEqual([(l["window_start"], l["event_name"], l["count"], l["duplicates"]) for l in lines], [("2026-11-02T13:00:00Z", "ad.decision", 2, 1)])
        self.assertEqual((checkpoint["foreign_source_rows"], checkpoint["since"], checkpoint["window"], checkpoint["rule_sha256"]),
                         (1, "2026-11-02T13:00:00Z", "utc-hour", hop_rules.rule_sha256()))
        self.assertEqual(checkpoint["duplicate_examples"][0]["offset"], 9)
        # 닫힘은 창 끝 + 여유(HOP_GRACE_SECONDS) ≤ 기준 시각(--as-of)이다: 14시 창은 15:02 에 닫혀 있고 15:01:59 에는 열려 있다(열린 창은 줄에 넣지 않고 checkpoint 에만)
        plan = job.plan("hour", as_of="2026-11-02T15:01:59Z", since="2026-11-02T13:00:00Z")
        lines, checkpoint, _ = job.summarize(iter([(None, envelope("0197a5c0-0000-7000-8000-000000000004", hour=14), 0, 5)]), plan, CONFIG)
        self.assertEqual((lines, checkpoint["open_windows"]), ([], ["2026-11-02T14:00:00Z"]))


# ---------------------------------------------------------------- 원천 설정·실패 로그(MVR-2 — 계획 temp/review/MVR-2-plan.md 3판 §4.1·§6 잡 시험)
# 아래 ads_envelope·ads_fixed_rows·ADS_FIXED_PLANS·ads_fixed_digest 는 바꾸기 전 코드(39cb0b2)로 고정값을 잰 스크립트와 글자까지 같다(잰 명령·값은 Step 기록).

def ads_envelope(event_id, hour, minute, *, payload="{}", db="ads_db", op="c", name="ad.decision"):
    after = {"event_id": event_id, "event_name": name, "schema_version": 1, "aggregate_id": "0197a5c0-0000-7000-8000-0000000000aa",
             "payload": payload, "created_at": f"2026-11-02T{hour:02d}:{minute:02d}:33.123456Z", "sent_at": None}
    return json.dumps({"op": op, "source": {"db": db, "schema": "public", "table": "events_outbox"}, "after": after})


def ads_fixed_rows():
    """중복·충돌·not_insert·tombstone·다른 출처·since 앞 창·열린 창·두 종류·두 창."""
    e = "0197a5c0-0000-7000-8000-0000000000{:02d}".format
    return [
        (None, ads_envelope(e(1), 13, 31), 0, 1), (None, ads_envelope(e(1), 13, 31), 0, 9),  # 중복
        (None, ads_envelope(e(2), 13, 45, name="ad.reaction"), 1, 3),
        (None, ads_envelope(e(3), 13, 50, db="other_db"), 2, 1),  # 다른 출처
        (None, ads_envelope(e(4), 12, 10), 0, 4),  # since(13시) 앞 창
        (None, ads_envelope(e(5), 14, 59), 0, 5),  # 14시 창(15:02 에 닫힘)
        (None, ads_envelope(e(6), 15, 1), 0, 6),  # 열린 창(15시)
        (None, ads_envelope(e(7), 14, 20, payload='{"a": 1}'), 1, 7), (None, ads_envelope(e(7), 14, 20, payload='{"a": 2}'), 1, 8),  # 충돌
        (None, ads_envelope(e(8), 14, 30, op="u"), 2, 2),  # not_insert
        ('{"payload": {"event_id": "' + e(9) + '"}}', None, 2, 3),  # tombstone
        (None, ads_envelope(e(10), 14, 40, payload='{"b": 1.0, "a": [1, 2]}'), 0, 10), (None, ads_envelope(e(10), 14, 40, payload='{"a":[1,2],"b":1}'), 2, 4),  # 같은 내용(공백·키 순서·1.0) — 중복
    ]


ADS_FIXED_PLANS = (("hour", "2026-11-02T15:02:00Z", "2026-11-02T13:00:00Z"), ("hour", "2026-11-02T15:01:59Z", "2026-11-02T13:00:00Z"),
                   ("day", "2026-11-03T00:02:00Z", "2026-11-02T00:00:00Z"), ("day", "2026-11-03T00:02:00Z", None))


def ads_fixed_digest(summarize_rows):
    """`summarize_rows(rows, plan)` → (lines, checkpoint, scanned). 네 경우의 출력을 정렬 JSON 으로 묶은 SHA-256.
    checkpoint 의 `rule_sha256` 은 규칙 파일 판이라, 지금 판인지 본 뒤 고정값을 잰 때의 값(`ADS_FIXED_RULE`)으로 바꿔 묶는다 —
    규칙 파일이 메타버스 설정 때문에 바뀌어도(MVR-3a) 광고 출력의 나머지 바이트는 그대로임을 고정값 하나로 본다."""
    out = {}
    for window, as_of, since in ADS_FIXED_PLANS:
        plan = job.plan(window, as_of=as_of, since=since)
        lines, checkpoint, scanned = summarize_rows(ads_fixed_rows(), plan)
        if checkpoint["rule_sha256"] != hop_rules.rule_sha256():
            raise AssertionError(f"checkpoint.rule_sha256 이 지금 규칙 판이 아니다: {checkpoint['rule_sha256']}")
        out[f"{window}/{as_of}/{since}"] = {"lines": lines, "checkpoint": checkpoint | {"rule_sha256": ADS_FIXED_RULE}, "scanned": scanned}
    return hashlib.sha256(json.dumps(out, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(), out


ADS_FIXED_DIGEST = "7229a3bf917176f5464923f4cc21fe6b24e1d4a1ffdd36a7278f533bc289a874"  # 39cb0b2 의 hop_manifest_job.summarize 로 잼(바꾸기 전 코드 — Reviewer C2)
ADS_FIXED_RULE = "4043ed001c944ed5328cc617b590eb5d937e70933e5fcad53340a03335e369c3"  # 그때(39cb0b2)의 rule_sha256
MV_CONFIG = CONFIG | {"db": "metaverse_lab", "source": job.SOURCES["METAVERSE"]}
MV_TOPIC = "metaverse.outbox.events"


def bronze_record(row, topic=MV_TOPIC):
    """(key, value, partition, offset) → Spark Row 처럼 이름으로 읽는 Bronze 행."""
    key, value, partition, offset = row
    return {"key": key, "value": value, "topic": topic, "partition": partition, "offset": offset}


def real_metaverse_rows():
    from pipeline.tests.common import test_hop_rules_sources as mv  # 모듈만 가져온다(시험 클래스를 이 모듈에 들이지 않음)

    return mv, [mv.mv_row(i, json.loads(mv.REAL[name])) for i, name in enumerate(mv.REAL)]


def failure_line(output):
    lines = [line for line in output.splitlines() if line.startswith("HOP_MANIFEST_FAILED ")]
    assert len(lines) == 1, output
    return json.loads(lines[0].removeprefix("HOP_MANIFEST_FAILED "))


class SourceSettingTests(unittest.TestCase):
    def test_source_defaults_to_ads_and_only_two_values_are_accepted(self):
        full = {"HOP_BRONZE_TABLE": "s3a://lake/bronze/t", "HOP_MANIFEST_URI": "s3a://lake/tmp/x/", "HOP_SOURCE_DB": "ads_db", "HOP_GRACE_SECONDS": "120"}
        self.assertEqual(job.settings(full)["source"]["name"], "ADS")
        self.assertEqual(job.settings(full | {"HOP_SOURCE": ""})["source"]["name"], "ADS")
        self.assertEqual(job.settings(full | {"HOP_SOURCE": "METAVERSE"})["source"]["name"], "METAVERSE")
        for value in ("metaverse", "ads", "SILVER", " ADS"):
            with self.subTest(value=value), self.assertRaisesRegex(job.JobError, "HOP_SOURCE"):
                job.settings(full | {"HOP_SOURCE": value})

    def test_ads_output_is_byte_identical_to_the_code_before_mvr2(self):
        self.assertEqual(ads_fixed_digest(lambda rows, plan: job.summarize(iter(rows), plan, CONFIG))[0], ADS_FIXED_DIGEST)
        self.assertEqual(ads_fixed_digest(lambda rows, plan: job.summarize(iter(rows), plan, CONFIG, job.SOURCES["ADS"]))[0], ADS_FIXED_DIGEST)
        config = CONFIG | {"source": job.SOURCES["ADS"]}  # main 이 실제로 부르는 길(topic 열이 있는 Row)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            digest = ads_fixed_digest(lambda rows, plan: job.summarize_bronze(iter([bronze_record(r, "ads.outbox.events") for r in rows]), plan, config))[0]
        self.assertEqual((digest, out.getvalue()), (ADS_FIXED_DIGEST, ""))

    def test_metaverse_rows_are_summarized_with_the_metaverse_rule(self):
        mv, rows = real_metaverse_rows()
        plan = job.plan("hour", as_of="2026-11-04T08:00:00Z", since="2026-11-02T04:00:00Z")
        lines, checkpoint, scanned = job.summarize_bronze(iter([bronze_record(r) for r in rows]), plan, MV_CONFIG)
        expected, opened, extra = hop_rules.summarize_changes(iter(rows), "metaverse_lab", plan["now"], 120, plan["since"], plan["size"], source=hop_rules.METAVERSE)
        self.assertEqual((lines, checkpoint["open_windows"], scanned), (expected, [hop_rules.format_time(w) for w in opened], 16))
        self.assertEqual({k: checkpoint[k] for k in extra}, extra)
        self.assertEqual((sum(line["count"] for line in lines), sum(line["duplicates"] for line in lines)), (13, 1))  # 고유 15 가운데 since(11-02 04시) 앞 둘(03:52·03:55)을 뺀 13, POINT_C·R 사본 하나

    def test_metaverse_rows_without_the_source_setting_are_all_foreign_sources(self):
        """메타버스 배포에서 HOP_SOURCE 를 빠뜨리면(ADS) 행이 모두 다른 출처가 된다 — 대조가 그 쌍을 입력 실패로 막는다(Planner 조건, 대조 쪽은 reconcile 시험)."""
        _, rows = real_metaverse_rows()
        plan = job.plan("day", as_of="2026-11-05T00:02:00Z", since="2026-11-01T00:00:00Z")
        lines, checkpoint, _ = job.summarize_bronze(iter([bronze_record(r) for r in rows]), plan, MV_CONFIG | {"source": job.SOURCES["ADS"]})
        self.assertEqual((lines, checkpoint["foreign_source_rows"]), ([], 16))


class FailureLogTests(unittest.TestCase):
    def run_failing(self, records, config=MV_CONFIG):
        plan = job.plan("hour", as_of="2026-11-04T08:00:00Z", since="2026-11-02T04:00:00Z")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(Exception) as caught:
            job.summarize_bronze(iter(records), plan, config)
        return failure_line(out.getvalue()), caught.exception, out.getvalue()

    def test_an_unreadable_row_is_named_by_table_topic_and_position_without_values(self):
        mv, rows = real_metaverse_rows()
        bad = json.loads(mv.REAL["FILL"])
        bad["after"]["occurred_at"] = "2026-11-02 04:16:20.396888+09:00"  # Silver 와 같은 엄격 UTC 모양이 아니다 → 공용 규칙이 멈춘다(E1)
        records = [bronze_record(rows[0]), bronze_record(rows[3]), bronze_record(mv.mv_row(7, bad, partition=2), "metaverse.outbox.events.v2"), bronze_record(rows[4])]
        line, error, output = self.run_failing(records)
        self.assertEqual(line, {"name": "hop_pipeline_hour", "source": "METAVERSE", "table": "s3a://lake/bronze/t", "topic": "metaverse.outbox.events.v2",
                                "partition": 2, "offset": 7, "error": "ValueError"})
        self.assertIsInstance(error, ValueError)
        self.assertIn("원천 METAVERSE", str(error))
        text = output + "".join(traceback.format_exception(error))
        for value in ("04:16:20.396888", bad["after"]["message_key"], json.loads(bad["after"]["raw_data"])["user_uuid"], "+09:00"):
            self.assertNotIn(value, text)

    def test_a_failure_while_taking_the_next_row_has_no_position(self):
        """다음 행을 꺼내다 실패하면(toLocalIterator 가 다음 파티션을 가져오다) 앞의 멀쩡한 행을 가리키지 않는다(Reviewer C1)."""
        _, rows = real_metaverse_rows()

        def records():
            yield bronze_record(rows[0])
            yield bronze_record(rows[2])
            raise RuntimeError("다음 파티션을 가져오지 못함")

        line, error, _ = self.run_failing(records())
        self.assertEqual((line["topic"], line["partition"], line["offset"], line["error"]), (None, None, None, "RuntimeError"))
        self.assertEqual(str(error), "다음 파티션을 가져오지 못함")

    def test_a_failure_after_all_rows_were_read_has_no_position(self):
        _, rows = real_metaverse_rows()

        def consume_then_fail(rows_, plan, config, source):
            for _ in rows_:
                pass
            raise ArithmeticError("줄 만들기에서 실패")

        with mock.patch.object(job, "summarize", consume_then_fail):
            line, error, _ = self.run_failing([bronze_record(r) for r in rows[:3]])
        self.assertEqual((line["topic"], line["partition"], line["offset"], line["error"]), (None, None, None, "ArithmeticError"))

    def test_ads_failures_get_the_same_line_and_the_same_exception(self):
        rows = [(None, ads_envelope("0197a5c0-0000-7000-8000-000000000001", 13, 31), 0, 1)]
        broken = json.loads(ads_envelope("0197a5c0-0000-7000-8000-000000000002", 13, 40))
        broken["after"]["created_at"] = "2026-11-02T13:40:33"  # 시간대 없음 — 광고는 지금처럼 그 예외로 멈춘다(DRD §5.6 (7))
        records = [bronze_record(rows[0], "ads.outbox.events"), bronze_record((None, json.dumps(broken), 1, 5), "ads.outbox.events")]
        line, error, _ = self.run_failing(records, CONFIG | {"source": job.SOURCES["ADS"]})
        self.assertEqual((line["source"], line["topic"], line["partition"], line["offset"], line["error"]), ("ADS", "ads.outbox.events", 1, 5, "ValueError"))
        self.assertIn("시간대가 없다", str(error))


if __name__ == "__main__":
    unittest.main()
