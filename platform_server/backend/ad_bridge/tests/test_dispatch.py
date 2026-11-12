"""campus_jobs의 노출 전송 작업자·시작 보호·상태 점검·재전송 대기 상한(AB-1 §4)."""

from datetime import datetime, timedelta, timezone
from io import StringIO
import os
import threading
from unittest.mock import patch
from uuid import uuid4

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from ad_bridge.api.config import AdsConfig
from ad_bridge.decisions.store import remember_decision
from ad_bridge.delivery import loop
from ad_bridge.delivery.policy import RetryPolicy
from ad_bridge.impressions.store import reserve_impression
from ad_bridge.models import Impression
from ad_bridge.tests.support import shared_decision
from campus.management.commands.campus_jobs import start_ad_dispatch

CONFIG = AdsConfig("https://ads.test/api/media/v1", "media-key-value", 1, subject_secret="subject-secret-value")


class RetryCapTests(TestCase):
    def test_default_cap_is_sixty_seconds_and_retry_after_wins(self):
        policy = RetryPolicy()
        self.assertEqual(policy.max_delay, 60)
        self.assertEqual([policy.delay_for(n) for n in (1, 2, 6, 7, 30)], [1, 2, 32, 60, 60])
        self.assertEqual(policy.delay_for(30, retry_after=120), 120)
        self.assertEqual(policy.delay_for(1, retry_after=0), 1)


class DispatchLoopTests(TestCase):
    def test_round_failure_is_logged_and_the_loop_continues_until_stop(self):
        stop, calls = threading.Event(), []

        def dispatch(**kwargs):
            calls.append(kwargs["limit"])
            if len(calls) == 1:
                raise RuntimeError("boom")
            stop.set()
            return {"attempted": 0}
        with self.assertLogs("ad_bridge.delivery.loop", "ERROR"), \
                patch.object(loop, "IDLE_SECONDS", 0), patch.object(loop, "close_old_connections"), \
                patch.object(loop.connections, "close_all"):
            loop.dispatch_loop(stop, client=object(), policy=RetryPolicy(), dispatch=dispatch)
        self.assertEqual(calls, [100, 100])

    def test_busy_rounds_do_not_sleep_and_idle_rounds_do(self):
        stop, results = threading.Event(), [{"attempted": 5}, {"attempted": 0}]
        waits = []

        class Stop:
            def is_set(self):
                return stop.is_set()

            def wait(self, seconds):
                waits.append(seconds)
                if not results:
                    stop.set()
                return stop.is_set()
        with patch.object(loop, "close_old_connections"):
            loop.dispatch_loop(Stop(), client=object(), policy=RetryPolicy(),
                               dispatch=lambda **kw: results.pop(0) if results else {"attempted": 0})
        self.assertEqual(waits, [loop.IDLE_SECONDS])

    def test_worker_count_setting_falls_back_to_default(self):
        for raw, expected in (("", 4), ("1", 1), ("8", 8), ("9", 4), ("0", 4), ("x", 4)):
            with self.subTest(raw=raw), override_settings(ADS_DISPATCH_WORKERS=raw):
                self.assertEqual(loop.worker_count(), expected)


class StartDispatchTests(TestCase):
    def test_off_when_ads_disabled(self):
        with override_settings(ADS_ENABLED=False):
            self.assertEqual(loop.start_dispatch(), [])
            self.assertEqual(start_ad_dispatch(), [])

    def test_starts_k_workers_that_stop_on_signal(self):
        stop = threading.Event()
        stop.set()  # 바로 끝나도 시작 자체(스레드 K개)는 확인된다.
        with override_settings(ADS_ENABLED=True, ADS_DISPATCH_WORKERS="3"), \
                patch("ad_bridge.api.config.AdsConfig.from_env", return_value=CONFIG):
            workers = loop.start_dispatch(stop)
        self.assertEqual(len(workers), 3)
        for worker in workers:
            worker.join(2)
            self.assertFalse(worker.is_alive())

    def test_campus_jobs_start_never_raises_on_bad_ad_setup(self):
        cases = {
            "missing keys": {},
            "portal key without slot list": {"ADS_API_BASE_URL": "https://ads.test/api/media/v1",
                                             "ADS_MEDIA_KEY": "media-key-value", "ADS_API_TIMEOUT": "1",
                                             "ADS_SUBJECT_SECRET": "subject-secret-value",
                                             "ADS_PORTAL_MEDIA_KEY": "portal-key-value"},
        }
        for name, env in cases.items():
            with self.subTest(name), override_settings(ADS_ENABLED=True), \
                    patch.dict(os.environ, env, clear=True), self.assertLogs("campus.management.commands.campus_jobs", "ERROR") as logs:
                self.assertEqual(start_ad_dispatch(), [])
            for secret in ("media-key-value", "subject-secret-value", "portal-key-value"):
                self.assertNotIn(secret, "\n".join(logs.output))
        with override_settings(ADS_ENABLED=True), patch.dict("sys.modules", {"ad_bridge.delivery.loop": None}), \
                self.assertLogs("campus.management.commands.campus_jobs", "ERROR"):
            self.assertEqual(start_ad_dispatch(), [])


class StatusCommandTests(TestCase):
    def setUp(self):
        self.decision = shared_decision()
        remember_decision(self.decision)

    def status(self):
        out = StringIO()
        with patch.dict(os.environ, {}, clear=True):
            call_command("dispatch_ad_reactions", "--status", stdout=out)
        return out.getvalue()

    def test_empty_queue_is_ok_without_any_ad_settings(self):
        self.assertIn("result ok", self.status())

    def test_overdue_beyond_120_seconds_exits_1_without_ids(self):
        now = datetime.now(timezone.utc)
        record, _ = reserve_impression(self.decision.decision_id, uuid4(), "2026-10-15T01:00:01Z",
                                       subject_id=uuid4(), now=now - timedelta(seconds=30))
        output = self.status()
        self.assertIn("states UNCONFIRMED=1 SENDING=0 ACKNOWLEDGED=0 BLOCKED=0", output)
        self.assertIn("overdue count=1", output)
        self.assertIn("result ok", output)
        Impression.objects.filter(pk=record.reaction_id).update(next_attempt_at=now - timedelta(seconds=200))
        with self.assertRaises(CommandError) as error:
            self.status()
        self.assertIn("초 밀렸습니다", str(error.exception))
        out = StringIO()
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(CommandError):
            call_command("dispatch_ad_reactions", "--status", stdout=out)
        for secret in (record.reaction_id, record.subject_id, self.decision.decision_id):
            self.assertNotIn(str(secret), out.getvalue())

    def test_sending_with_expired_lease_counts_as_overdue(self):
        now = datetime.now(timezone.utc)
        record, _ = reserve_impression(self.decision.decision_id, uuid4(), "2026-10-15T01:00:01Z",
                                       subject_id=uuid4(), now=now)
        # 작업자가 꺼낸 뒤 멈춘 기록: SENDING이고 임대가 200초 전에 끝났다.
        Impression.objects.filter(pk=record.reaction_id).update(
            delivery_state="SENDING", next_attempt_at=None, lease_token=uuid4(),
            lease_expires_at=now - timedelta(seconds=200))
        with self.assertRaises(CommandError):
            self.status()
        Impression.objects.filter(pk=record.reaction_id).update(lease_expires_at=now + timedelta(seconds=30))
        output = self.status()
        self.assertIn("states UNCONFIRMED=0 SENDING=1", output)
        self.assertIn("overdue count=0 oldest_seconds=0", output)

    def test_future_retry_is_not_overdue(self):
        now = datetime.now(timezone.utc)
        record, _ = reserve_impression(self.decision.decision_id, uuid4(), "2026-10-15T01:00:01Z",
                                       subject_id=uuid4(), now=now)
        Impression.objects.filter(pk=record.reaction_id).update(next_attempt_at=now + timedelta(seconds=50))
        output = self.status()
        self.assertIn("overdue count=0 oldest_seconds=0", output)
        self.assertIn("pending count=1", output)
