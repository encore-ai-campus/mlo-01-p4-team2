"""PostgreSQL·연결 풀에서 광고 어댑터가 메타버스 요청을 늦추지 않는지 확인한다(AB-1 §4.4·§4.5·C2).

SQLite 메모리 설정(ad_bridge.tests.settings)에서는 건너뛴다. config.postgres_test_settings로 돌린다.
"""

from dataclasses import replace
from io import StringIO
import os
import threading
import time
from unittest import skipUnless
from unittest.mock import Mock, patch
from uuid import uuid4

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase, override_settings

from ad_bridge import placements
from ad_bridge.decisions.store import remember_decision
from ad_bridge.delivery.worker import dispatch_due_impressions
from ad_bridge.impressions import store
from ad_bridge.impressions.store import reserve_impression
from ad_bridge.models import Impression, PlacementBatch
from ad_bridge.tests.support import received_reaction, shared_decision

POSTGRES = connection.vendor == "postgresql"
SLOTS = dict(ADS_ROOFTOP_SLOT_ID="10000000-0000-0000-0000-000000000001",
             ADS_PORTAL_LEFT_SLOT_ID="10000000-0000-0000-0000-000000000002",
             ADS_PORTAL_RIGHT_SLOT_ID="10000000-0000-0000-0000-000000000003")


def individual(**kwargs):
    return replace(shared_decision(), slot_id=kwargs["slot_id"], audience="individual")


@skipUnless(POSTGRES, "PostgreSQL 전용")
@override_settings(**SLOTS)
class DecisionConnectionTests(TransactionTestCase):
    def setUp(self):
        placements._slots = None
        self.addCleanup(setattr, placements, "_slots", None)

    def test_pool_is_on_for_this_run(self):
        self.assertIn("pool", settings.DATABASES["default"].get("OPTIONS", {}))

    def test_decision_does_not_hold_a_db_connection_while_waiting(self):
        held = []

        def create_decision(**kwargs):
            held.append(connection.connection is not None)
            return individual(**kwargs)
        client = Mock(create_decision=Mock(side_effect=create_decision))
        batch = placements.get_batch(client=client, owner_id=uuid4(), subject_id=uuid4(), transfer_id=str(uuid4()))
        self.assertEqual(batch.state, "READY")
        self.assertEqual(held, [False, False])

    def test_slow_ads_server_does_not_make_other_db_requests_wait(self):
        # 풀(기본 10)보다 많은 발급이 광고 서버를 1초씩 기다려도, 다른 요청은 풀 연결을 바로 얻는다.
        pool_size = settings.DATABASES["default"]["OPTIONS"]["pool"]["max_size"]
        issuers = pool_size + 2
        started = threading.Barrier(issuers + 1)

        def create_decision(**kwargs):
            started.wait(5)
            time.sleep(1.0)
            return individual(**kwargs)
        client = Mock(create_decision=Mock(side_effect=create_decision))
        errors = []

        def issue():
            try:
                placements.get_batch(client=client, owner_id=uuid4(), subject_id=uuid4())
            except Exception as error:  # 시험 실패를 본 스레드로 넘긴다.
                errors.append(error)
            finally:
                connection.close()
        with override_settings(ADS_DECISION_CONCURRENCY=str(issuers)):
            placements._slots = None
            threads = [threading.Thread(target=issue) for _ in range(issuers)]
            for thread in threads:
                thread.start()
            started.wait(5)
            elapsed = []

            def game_request():
                begin = time.monotonic()
                with connection.cursor() as cursor:
                    cursor.execute("select 1")
                elapsed.append(time.monotonic() - begin)
                connection.close()
            other = threading.Thread(target=game_request)
            other.start()
            other.join(10)
            for thread in threads:
                thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(PlacementBatch.objects.filter(state="READY").count(), issuers)
        print(f"\n[AB-1] 광고 서버 1초 지연 중 다른 DB 요청 대기 {elapsed[0]:.3f}초 (발급 {issuers}개, 풀 {pool_size})")
        self.assertLess(elapsed[0], 0.5)


@skipUnless(POSTGRES, "PostgreSQL 전용")
class ConcurrentDispatchTests(TransactionTestCase):
    def queue(self, count):
        decision = shared_decision()
        remember_decision(decision)
        for _ in range(count):
            reserve_impression(decision.decision_id, uuid4(), "2026-10-15T01:00:01Z", subject_id=uuid4())

    def run_workers(self, workers, *, latency):
        sent, lock = [], threading.Lock()

        def report(**request):
            time.sleep(latency)
            with lock:
                sent.append(request["reaction_id"])
            return received_reaction(**request)
        client = Mock(report_impression=Mock(side_effect=report))

        def work():
            try:
                while dispatch_due_impressions(client=client, store=store)["attempted"]:
                    pass
            finally:
                connection.close()
        begin = time.monotonic()
        threads = [threading.Thread(target=work) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        return sent, time.monotonic() - begin

    def test_k_workers_never_send_the_same_impression_twice(self):
        self.queue(60)
        sent, _ = self.run_workers(4, latency=0.005)
        self.assertEqual(len(sent), 60)
        self.assertEqual(len(set(sent)), 60)
        self.assertEqual(Impression.objects.filter(delivery_state="ACKNOWLEDGED").count(), 60)

    def test_four_workers_drain_faster_than_one(self):
        self.queue(50)
        _, one = self.run_workers(1, latency=0.02)
        Impression.objects.update(delivery_state="UNCONFIRMED", next_attempt_at=None, status=None)
        _, four = self.run_workers(4, latency=0.02)
        print(f"\n[AB-1] 가짜 광고 서버 20ms, 노출 50건: K=1 {one:.2f}초, K=4 {four:.2f}초")
        self.assertLess(four, one)


@skipUnless(POSTGRES, "PostgreSQL 전용")
class CampusJobsStartTests(TransactionTestCase):
    def test_bad_ad_setup_never_stops_campus_jobs(self):
        from django.core.management import call_command
        from campus.management.commands import campus_jobs

        stopped = threading.Event()
        stopped.set()
        cases = {
            "missing keys": {},
            "portal key without slot list": {"ADS_API_BASE_URL": "https://ads.test/api/media/v1",
                                             "ADS_MEDIA_KEY": "media-key-value", "ADS_API_TIMEOUT": "1",
                                             "ADS_SUBJECT_SECRET": "subject-secret-value",
                                             "ADS_PORTAL_MEDIA_KEY": "portal-key-value"},
        }
        for name, env in cases.items():
            with self.subTest(name), override_settings(ADS_ENABLED=True), patch.dict(os.environ, env, clear=True), \
                    patch("campus.menu_updater.start_menu_updater") as menu, \
                    patch("accounts.session_cleanup.start_session_cleanup") as cleanup, \
                    patch.object(campus_jobs.threading, "Event", return_value=stopped), \
                    patch.object(campus_jobs.signal, "signal"), \
                    self.assertLogs("campus.management.commands.campus_jobs", "ERROR") as logs:
                out = StringIO()
                call_command("campus_jobs", stdout=out)
            menu.assert_called_once()
            cleanup.assert_called_once()
            self.assertIn("ads impression dispatch: NOT RUNNING", out.getvalue())
            self.assertIn("campus jobs running", out.getvalue())
            for secret in ("media-key-value", "subject-secret-value", "portal-key-value"):
                self.assertNotIn(secret, "\n".join(logs.output))

    def test_ads_off_says_so(self):
        from django.core.management import call_command
        from campus.management.commands import campus_jobs

        stopped = threading.Event()
        stopped.set()
        with override_settings(ADS_ENABLED=False), patch("campus.menu_updater.start_menu_updater"), \
                patch("accounts.session_cleanup.start_session_cleanup"), \
                patch.object(campus_jobs.threading, "Event", return_value=stopped), \
                patch.object(campus_jobs.signal, "signal"):
            out = StringIO()
            call_command("campus_jobs", stdout=out)
        self.assertIn("ads impression dispatch: off (ADS_ENABLED=0)", out.getvalue())
