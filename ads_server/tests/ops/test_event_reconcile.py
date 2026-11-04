"""창 대사 한 번(`reconcile_hops`, 파이프라인 계획 P2-2): 받기 → 받은 since 로 outbox manifest → 대조·확정 기록 → 상태 파일·종료 코드.
운영 모양(MongoDB 없음 — `EVENTS_HOP_WITH_MONGO=False`, 결정 A)으로 돈다. 파이프라인 쪽 manifest 는 가짜 S3 에 게시 규칙(`ndjson.write(chain=True)`)으로 둔다."""
import fcntl
import hashlib
import io
import json
import shutil
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from random import Random
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.test import TransactionTestCase, override_settings, tag

from events import hop_rules, hopcheck, ndjson
from events.management.commands import fetch_hop_manifest, verify_hops
from foundation.api import latest_audit_detail
from records import api as records
from tests.ops.test_event_hop_fetch import CONFIG, PREFIX, FakeS3
from tests.support.cases import Cases
from tests.support.factories import NOW, SUBJECT, World

HOUR_AS_OF = NOW + timedelta(hours=3)  # 12시 창이 닫혀 있고, 기대 since(지금 창 − 48시간)가 그 창을 덮는다
DAY_AS_OF = NOW + timedelta(days=1, hours=1)


@override_settings(HOP_PIPELINE_S3=CONFIG, EVENTS_HOP_WITH_MONGO=False, EVENTS_HOP_CONFIRM_WITH_MONGO=True)
@tag("security")
class ReconcileTests(Cases, TransactionTestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.work, True)
        self.s3 = FakeS3()
        self.enterContext(mock.patch.object(fetch_hop_manifest, "client", lambda config: self.s3))
        self.enterContext(mock.patch("events.api.mongo_streams", side_effect=AssertionError("MongoDB 를 읽지 않는다")))
        w = World()
        decision = records.decide(w.slot.media_id, w.slot.slot_id, subject_id=SUBJECT, now=NOW, rng=Random(1))
        view, _ = records.receive_reaction(w.slot.media_id, decision.decision_id, uuid.uuid4(), "IMPRESSION", NOW, {"type": "IMPRESSION"}, now=NOW)
        records.process_reaction(view.inbox_id, now=NOW + timedelta(seconds=7))

    def expected_since(self, window, as_of):
        size = hop_rules.WINDOWS[window]
        return hop_rules.format_time(hop_rules.window_start(as_of, size) - {"hour": timedelta(hours=48), "day": timedelta(days=8)}[window])

    def publish(self, window="hour", as_of=HOUR_AS_OF, since=None, change=None, **checkpoint):
        """outbox 와 같은 줄의 파이프라인 manifest(계약 모양)를 가짜 S3 에 게시한다. `change(lines)` 로 줄을 바꿀 수 있다."""
        since = since or self.expected_since(window, as_of)
        scratch = Path(tempfile.mkdtemp(dir=self.work))
        call_command("export_hop_manifests", "--out", str(scratch / "o"), "--window", window, "--since", since, "--as-of", as_of.isoformat(), stdout=io.StringIO())
        outbox = ndjson.read_manifest(ndjson.manifest_path(scratch / "o", "hop_outbox"))
        lines = [{k: l[k] for k in ("window_start", "window_end", "event_name", "count", "ids_sha256", "content_sha256")} | {"duplicates": 0, "conflicts": 0, "not_insert": 0}
                 for l in map(json.loads, (scratch / "o" / outbox["file"]).read_text().splitlines())]
        if change:
            change(lines)
        full = {"window": hop_rules.window_label(window), "since": since, "started_at": as_of.isoformat(), "open_windows": [], "rule_sha256": hop_rules.rule_sha256(), "not_insert_unplaced": 0,
                "foreign_source_rows": 0, **{f: [] for f in hop_rules.EXAMPLE_FIELDS}, **checkpoint}
        remote = scratch / "remote"
        ndjson.write(remote, f"hop_pipeline_{window}", lines, "hop_pipeline", 1, checkpoint=full, chain=True)
        self.s3.objects = {PREFIX + p.name: p.read_bytes() for p in remote.iterdir() if not p.name.startswith(".")}
        return lines

    def reconcile(self, window="hour", as_of=HOUR_AS_OF, work=None):
        """(종료 코드, 출력, 상태 파일 dict 또는 None)."""
        work, out, code = work or Path(tempfile.mkdtemp(dir=self.work)), io.StringIO(), 0
        try:
            call_command("reconcile_hops", "--window", window, "--dir", str(work), "--as-of", as_of.isoformat(), stdout=out)
        except SystemExit as exit_:
            code = exit_.code
        state = work / f"reconcile-{window}.json"
        return code, out.getvalue(), json.loads(state.read_text()) if state.exists() else None

    def case_a_matching_pair_is_confirmed_without_mongodb(self):
        self.publish()
        code, text, state = self.reconcile()
        self.assertEqual((code, state["status"], state["rc"], state["stage"]), (0, "ok", 0, None), text)
        self.assertEqual(state["pipeline_since"], state["expected_since"])
        self.assertEqual((state["mismatch_lines"], state["exception_lines"]), (0, 0))
        self.assertGreaterEqual(state["confirmed"], 1)
        self.assertEqual(state["pipeline_manifest_sha256"], hashlib.sha256(self.s3.objects[PREFIX + "hop_pipeline_hour.manifest.json"]).hexdigest())
        verdict = latest_audit_detail(hopcheck.CONFIRM_ACTION, window="utc-hour")
        self.assertEqual((verdict["inputs"]["pipeline"], sorted(verdict["inputs"]), verdict["with_mongo"]), (state["pipeline_manifest_sha256"], ["hop_outbox", "pipeline"], False))
        self.assertIn("2026-10-07T12:00:00Z", hopcheck.window_verdicts("utc-hour"))
        self.assertTrue(hopcheck.window_verdicts("utc-hour")["2026-10-07T12:00:00Z"])  # 결정·반응이 있는 12시 창이 확정됐다

    def case_an_older_pipeline_since_is_late_not_a_mismatch(self):
        expected = self.expected_since("hour", HOUR_AS_OF)
        older = hop_rules.format_time(hop_rules.parse_time(expected) - timedelta(hours=1))
        self.publish(since=older)
        code, text, state = self.reconcile()
        self.assertEqual((code, state["status"], state["stage"], state["late_by_seconds"], state["mismatch_lines"]), (3, "late", "since", 3600, 0), text)
        self.assertEqual(state["pipeline_since"], older)
        newer = hop_rules.format_time(hop_rules.parse_time(expected) + timedelta(hours=1))  # 기대보다 새 since(이쪽 시계가 늦음)도 "기대와 다름"이다
        self.publish(since=newer)
        code, _, state = self.reconcile()
        self.assertEqual((code, state["status"], state["late_by_seconds"]), (3, "late", -3600))

    def case_a_window_closed_after_a_late_job_stays_open_instead_of_a_false_mismatch(self):
        """잡이 늦은 회차(Planner 결정 (가)): 직전 포인터는 12:30 에 낸 것이라 12시 창이 그때 열려 있었고 Bronze 행이 없어 open_windows 에도 없다.
        대사(13:07)가 outbox 를 지금 기준으로 닫으면 12시 창이 outbox 에만 나와 line_missing_in_b 불일치가 된다 — outbox 도 잡의 started_at 으로 닫아 그 창을 열림으로 둔다."""
        job_as_of, reconcile_as_of = NOW + timedelta(minutes=30), NOW + timedelta(hours=1, minutes=7)
        lines = self.publish(as_of=job_as_of)
        self.assertEqual([l for l in lines if l["window_start"] == "2026-10-07T12:00:00Z"], [])  # 잡이 본 12시 창은 열려 있었다(줄 없음)
        code, text, state = self.reconcile(as_of=reconcile_as_of)
        self.assertEqual((code, state["status"], state["mismatch_lines"]), (3, "late", 0), text)  # 늦음일 뿐 불일치가 아니다
        self.assertEqual(state["pipeline_as_of"], job_as_of.isoformat())
        outbox = ndjson.read_manifest(next(Path(self.work).glob("*/hour/outbox/hop_outbox.manifest.json")))
        self.assertEqual(outbox["checkpoint"]["started_at"], job_as_of.isoformat())  # outbox 도 잡과 같은 시각으로 닫았다
        self.assertIn("2026-10-07T12:00:00Z", outbox["checkpoint"]["open_windows"])

    def case_a_mismatch_in_a_late_run_is_still_a_mismatch(self):
        """늦음보다 불일치가 앞선다 — 늦은 회차의 진짜 불일치를 rc 3 으로 가리지 않는다."""
        older = hop_rules.format_time(hop_rules.parse_time(self.expected_since("hour", HOUR_AS_OF)) - timedelta(hours=1))
        self.publish(since=older, change=lambda lines: lines[0].update(count=lines[0]["count"] + 1))
        code, text, state = self.reconcile()
        self.assertEqual((code, state["status"], state["stage"]), (1, "mismatch", "verify"), text)

    def case_a_changed_line_is_a_mismatch(self):
        self.publish(change=lambda lines: lines[0].update(count=lines[0]["count"] + 1))
        code, text, state = self.reconcile()
        self.assertEqual((code, state["status"], state["stage"]), (1, "mismatch", "verify"), text)
        self.assertGreaterEqual(state["mismatch_lines"], 1)

    def case_input_failures_are_told_apart_from_mismatches(self):
        code, _, state = self.reconcile()  # 게시 전(S3 에 포인터 없음)
        self.assertEqual((code, state["status"], state["stage"]), (2, "input_failed", "fetch"))
        self.assertIn("없거나 읽을 권한이 없다", state["message"])
        self.publish(rule_sha256="0" * 64)  # 다른 판의 요약 규칙 — 대조하지 않는다
        code, text, state = self.reconcile()
        self.assertEqual((code, state["status"], state["stage"]), (2, "input_failed", "verify"), text)
        for started_at in (None, "2026-10-07T15:00:00", "어제"):  # 닫힘 기준 시각이 없거나 시간대가 없으면 같은 닫힘을 맞출 수 없다
            with self.subTest(started_at=started_at):
                self.publish(started_at=started_at)
                code, _, state = self.reconcile()
                self.assertEqual((code, state["status"], state["stage"]), (2, "input_failed", "since"))

    def case_a_database_error_rewrites_the_state_as_an_input_failure(self):
        """CommandError 밖의 예외(DB 연결 끊김·읽기 전용 등)도 상태 파일을 새로 쓰고 rc 2 다 — 지난 회차의 ok 가 남거나 불일치(1)로 보이지 않는다."""
        self.publish()
        work = Path(tempfile.mkdtemp(dir=self.work))
        code, _, first = self.reconcile(work=work)
        self.assertEqual(code, 0)
        with mock.patch("events.api.outbox_streams", side_effect=OperationalError("connection failed: server closed the connection unexpectedly")):
            code, text, state = self.reconcile(work=work)
        self.assertEqual((code, state["status"], state["rc"], state["stage"]), (2, "input_failed", 2, "export"), text)
        self.assertTrue(state["message"].startswith("OperationalError: connection failed"))
        self.assertNotEqual(state["finished_at"], first["finished_at"])

    def case_a_verify_that_stops_before_its_report_is_an_input_failure(self):
        """대조가 보고를 쓰기 전에 멈추면(판정 기록이 감사 한도를 넘음 등) 지난 회차의 보고로 분류하지 않는다."""
        self.publish()
        work = Path(tempfile.mkdtemp(dir=self.work))
        self.assertEqual(self.reconcile(work=work)[0], 0)
        with mock.patch.object(verify_hops.Command, "record_verdicts", side_effect=CommandError("판정 기록이 감사 detail 크기 한도를 넘어")):
            code, text, state = self.reconcile(work=work)
        self.assertEqual((code, state["status"], state["stage"]), (2, "input_failed", "verify"), text)
        self.assertIn("한도를 넘어", state["message"])

    def case_a_running_reconcile_of_the_same_window_is_skipped(self):
        self.publish()
        work = Path(tempfile.mkdtemp(dir=self.work))
        with open(work / ".reconcile-hour.lock", "a+") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            code, text, state = self.reconcile(work=work)
        self.assertEqual((code, state), (75, None), text)  # 상태 파일은 돌고 있는 회차의 것으로 둔다
        code, _, state = self.reconcile(window="day", as_of=DAY_AS_OF, work=work)  # 다른 창 크기는 막지 않는다
        self.assertEqual(code, 2)

    def case_the_day_window_uses_its_own_name_and_eight_days(self):
        self.publish(window="day", as_of=DAY_AS_OF)
        code, text, state = self.reconcile(window="day", as_of=DAY_AS_OF)
        self.assertEqual((code, state["status"], state["expected_since"]), (0, "ok", "2026-09-30T00:00:00Z"), text)
        self.assertTrue(hopcheck.window_verdicts("utc-day")["2026-10-07T00:00:00Z"])

    def test_reconcile(self):
        self.run_cases()
