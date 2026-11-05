"""Bronze manifest 잡(`pipeline/bronze/hop_manifest_job.py`, 파이프라인 계획 P2-1)의 Spark 밖 부분: 게시 순서·사슬, 새 사슬은 포인터가 없을 때(404)만, 범위·설정.
Bronze 를 읽고 실제 S3 에 쓰는 부분은 Standalone 제출로 확인한다(끝 조건 — C4 13시 창과 같은 결과·중복 3건, Step 기록). 표준 라이브러리만 쓴다."""
import hashlib
import json
import sys
import unittest
from datetime import datetime, timezone
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


if __name__ == "__main__":
    unittest.main()
