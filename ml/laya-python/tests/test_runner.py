import json
import multiprocessing
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from laya_python.client import AdsClient
from laya_python.core import CATEGORIES, Failure
from laya_python.records import JobStore
from laya_python.runner import Runner
from tests.test_client import BASE, IDS, Scripted, response, row, success
from tests.test_summary import MockSummary, URL, summary_response


class TestModel:
    def __init__(self):
        self.calls = 0
        self.active = self.peak = 0

    def predict(self, title, body):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.calls += 1
        try:
            return dict.fromkeys(CATEGORIES, 0.0)
        finally:
            self.active -= 1


def waiting_recovery(directory, events):
    store = JobStore(directory)
    events.put("waiting")
    with store.run_lock():
        store.recover_interrupted()
        events.put("acquired")


class RecordTests(unittest.TestCase):
    def test_atomic_replace_keeps_previous_json_after_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(directory)
            with store.run_lock():
                record = store.create()
                path = Path(directory) / (record["job_id"] + ".json")
                before = path.read_bytes()
                record["total"] = 99
                with patch("laya_python.records.os.replace", side_effect=OSError("secret")), self.assertRaisesRegex(Failure, "JOB_RECORD_WRITE_FAILED"):
                    store.write(record)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(list(Path(directory).glob(".job-*.tmp")), [])

    def test_recovery_preserves_last_counts_and_pending_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(directory)
            with store.run_lock():
                record = store.create()
                record.update(total=5, succeeded=2, processed=2, pending_write_ids=IDS[:2])
                store.write(record)
                store.recover_interrupted()
            saved = json.loads((Path(directory) / (record["job_id"] + ".json")).read_text())
            self.assertEqual(saved["status"], "interrupted")
            self.assertEqual(saved["reason"], "PROCESS_RESTARTED")
            self.assertFalse(saved["counts_final"])
            self.assertEqual((saved["succeeded"], saved["pending_write_ids"]), (2, IDS[:2]))
            with self.assertRaisesRegex(Failure, "JOB_LOCK_REQUIRED"):
                store.recover_interrupted()

    def test_second_process_waits_before_recovering_active_record(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(directory)
            events = context.Queue()
            process = context.Process(target=waiting_recovery, args=(directory, events))
            try:
                with store.run_lock():
                    record = store.create()
                    path = Path(directory) / (record["job_id"] + ".json")
                    process.start()
                    self.assertEqual(events.get(timeout=5), "waiting")
                    with self.assertRaises(queue.Empty):
                        events.get(timeout=0.1)
                    self.assertEqual(json.loads(path.read_text())["status"], "running")
                self.assertEqual(events.get(timeout=5), "acquired")
                process.join(timeout=5)
                self.assertEqual(process.exitcode, 0)
                self.assertEqual(json.loads(path.read_text())["status"], "interrupted")
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join()
                events.close()


class RunnerTests(unittest.TestCase):
    def run_job(self, directory, rows, outcomes=(), *, summary=None, model=None, store=None):
        transport = Scripted(response(200, {"items": rows, "next_after": None}), *outcomes)
        client = AdsClient(BASE, "test-key", transport=transport, sleep=lambda _: None)
        summary = summary or MockSummary({URL: summary_response()})
        model = model or TestModel()
        result = Runner(client, summary, model, store or JobStore(directory)).run()
        return result, transport, model

    def test_success_counts_after_ack_and_body_not_stored(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [row(IDS[0]), row(IDS[1])]
            items = [{"ad_group_id": ad_id, "category": 0} for ad_id in IDS[:2]]
            result, transport, model = self.run_job(directory, rows, [success(items)])
            self.assertEqual((result["total"], result["succeeded"], result["failed"]), (2, 2, 0))
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["counts_final"])
            self.assertEqual(result["source"], "laya")
            saved = next(Path(directory).glob("*.json")).read_text()
            self.assertNotIn("creative title", saved)
            self.assertNotIn("example.com", saved)
            self.assertNotIn("RAG", saved)
            self.assertEqual(model.calls, 2)
            self.assertEqual(result["write_results"]["unchanged"], 2)

    def test_summary_failure_is_not_stored_as_other(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [row(IDS[0]), row(IDS[1])]
            rows[0]["creatives"][0]["redirect_url"] = "https://example.com/fail"
            mock = MockSummary({URL: summary_response(), "https://example.com/fail": summary_response("", accessible=False, url="https://example.com/fail")})
            items = [{"ad_group_id": IDS[1], "category": 0}]
            result, transport, _ = self.run_job(directory, rows, [success(items)], summary=mock)
            self.assertEqual((result["succeeded"], result["failed"]), (1, 1))
            self.assertEqual(result["failures"][0]["ad_group_id"], IDS[0])
            self.assertEqual(transport.calls[-1][2], {"items": items})

    def test_clean_400_records_only_rejected_id_then_saves_rest(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [row(value) for value in IDS[:3]]
            items = [{"ad_group_id": ad_id, "category": 0} for ad_id in (IDS[0], IDS[2])]
            result, _, _ = self.run_job(directory, rows, [response(400, {"errors": [{"pointer": "/items/1/ad_group_id"}]}), success(items)])
            self.assertEqual((result["succeeded"], result["failed"], result["processed"]), (2, 1, 3))
            self.assertEqual(result["failures"][0]["ad_group_id"], IDS[1])
            self.assertEqual(result["pending_write_ids"], [])
            self.assertTrue(result["counts_final"])

    def test_unknown_write_preserves_ids_without_false_failure_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            result, _, _ = self.run_job(directory, [row(IDS[0])], [response(503, headers={"retry-after": "0"}),
                                                                               response(400, {"errors": [{"pointer": "/items/0/category"}]})])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "WRITE_OUTCOME_UNKNOWN")
            self.assertEqual(result["pending_write_ids"], [IDS[0]])
            self.assertEqual((result["succeeded"], result["failed"], result["processed"]), (0, 0, 0))
            self.assertFalse(result["counts_final"])

    def test_zero_rows_and_all_input_failures_send_one_empty_audit(self):
        for rows in ([], [{**row(IDS[0]), "creatives": []}]):
            with tempfile.TemporaryDirectory() as directory:
                result, transport, model = self.run_job(directory, rows, [response(200, {"items": []})])
                self.assertEqual(transport.calls[-1][2], {"items": []})
                self.assertEqual(model.calls, 0)
                self.assertEqual(result["succeeded"], 0)
                self.assertEqual(result["failed"], len(rows))
                self.assertTrue(result["counts_final"])

    def test_summary_parallelism_two_and_inference_serial(self):
        barrier = threading.Barrier(2)
        active = peak = 0
        lock = threading.Lock()

        def summary(url):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait(timeout=5)
            with lock:
                active -= 1
            return "試験の本文"

        with tempfile.TemporaryDirectory() as directory:
            rows = [row(value) for value in IDS[:4]]
            items = [{"ad_group_id": value, "category": 0} for value in IDS[:4]]
            result, _, model = self.run_job(directory, rows, [success(items)], summary=summary)
            self.assertEqual((peak, model.peak, model.calls), (2, 1, 4))
            self.assertEqual(result["succeeded"], 4)

    def test_record_failure_before_post_stops_new_requests_and_waits_for_summary(self):
        done = threading.Event()

        class FailingStore(JobStore):
            def write(self, record):
                if record["pending_write_ids"]:
                    raise Failure("JOB_RECORD_WRITE_FAILED")
                super().write(record)

        def summary(url):
            done.set()
            return "본문"

        with tempfile.TemporaryDirectory() as directory:
            transport = Scripted(response(200, {"items": [row(IDS[0])], "next_after": None}))
            runner = Runner(AdsClient(BASE, "key", transport=transport), summary, TestModel(), FailingStore(directory))
            with self.assertRaisesRegex(Failure, "JOB_RECORD_WRITE_FAILED"):
                runner.run()
            self.assertTrue(done.is_set())
            self.assertEqual(len(transport.calls), 1)

    def test_record_failure_after_ack_keeps_disk_pending_and_does_not_send_more(self):
        class FailingStore(JobStore):
            def write(self, record):
                if record["succeeded"]:
                    raise Failure("JOB_RECORD_WRITE_FAILED")
                super().write(record)

        with tempfile.TemporaryDirectory() as directory:
            transport = Scripted(response(200, {"items": [row(IDS[0])], "next_after": IDS[0]}),
                                 success([{"ad_group_id": IDS[0], "category": 0}]))
            runner = Runner(AdsClient(BASE, "key", transport=transport), lambda _: "본문", TestModel(), FailingStore(directory))
            with self.assertRaisesRegex(Failure, "JOB_RECORD_WRITE_FAILED"):
                runner.run()
            saved = json.loads(next(Path(directory).glob("*.json")).read_text())
            self.assertEqual(saved["pending_write_ids"], [IDS[0]])
            self.assertEqual(saved["succeeded"], 0)
            self.assertEqual(len(transport.calls), 2)

    def test_record_failure_waits_for_inflight_summary_without_submitting_more(self):
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        calls = []

        class FailingStore(JobStore):
            def write(self, record):
                if record["failed"]:
                    threading.Timer(0.05, release.set).start()
                    raise Failure("JOB_RECORD_WRITE_FAILED")
                super().write(record)

        def summary(url):
            calls.append(url)
            if url.endswith("/fail"):
                started.wait(timeout=5)
                raise Failure("URL_READ_FAILED")
            started.set()
            release.wait(timeout=5)
            done.set()
            return "본문"

        with tempfile.TemporaryDirectory() as directory:
            rows = [row(value) for value in IDS[:4]]
            rows[0]["creatives"][0]["redirect_url"] = "https://example.com/fail"
            transport = Scripted(response(200, {"items": rows, "next_after": None}))
            model = TestModel()
            runner = Runner(AdsClient(BASE, "key", transport=transport), summary, model, FailingStore(directory))
            with self.assertRaisesRegex(Failure, "JOB_RECORD_WRITE_FAILED"):
                runner.run()
            self.assertTrue(done.is_set())
            self.assertEqual((len(calls), model.calls, len(transport.calls)), (2, 0, 1))


if __name__ == "__main__":
    unittest.main()
