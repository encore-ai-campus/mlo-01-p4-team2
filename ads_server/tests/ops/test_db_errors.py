"""DB 오류 분류(`foundation/dberrors.py`): 유일 제약 위반은 다른 무결성 오류(NOT NULL·CHECK)와 구분한다. DB-1c(S6): 교착(40P01)·잠금 대기 초과(55P03)는 **실패가 아니라 일시 오류**다.

정상 동작의 일부다(서버가 트랜잭션 하나를 되돌린다). 반응 처리가 이것을 실패로 세면 정상 반응의 `attempts` 가 올라 상한(5)에서 자동 재처리에서 빠진다.
그래서 횟수를 올리지 않고 다음 주기에 다시 한다. API 는 `OperationalError` 를 503 + `Retry-After` 로 바꾼다(`serving_api/auth.py`).
읽기 전용 트랜잭션(25006, 설계 01 D21 — 장애 전환 틈에 쓰기 연결이 대기 서버에 닿음)도 같은 일시 오류다: API 503, 상시 처리·중계는 그 주기를 건너뛴다.
psycopg 는 25006 을 `InternalError` 로 올린다 — 그 밖의 `InternalError`(중단된 트랜잭션 25P02 등)는 가리지 않는다.
"""
import io
import json
import signal
import threading
import uuid
from contextlib import contextmanager
from random import Random
from unittest import mock

import psycopg
from django.core.management import call_command
from django.db import IntegrityError, InternalError, OperationalError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings, tag
from django.urls import reverse

from billing.models import Wallet
from campaigns import api as campaigns
from events.models import EventOutbox
from foundation.api import is_transient_lock_error
from foundation.dberrors import is_unique_violation
from foundation.models import User
from masters import api as masters
from records import api as records
from records.models import Decision, Reaction
from tests.support.concurrency import WAIT, Worker
from tests.support.factories import NOW, SUBJECT, World
from tests.support.helpers import db_error
from tests.support.cases import Cases

DEADLOCK = db_error(OperationalError, psycopg.errors.DeadlockDetected("deadlock detected"))
LOCK_TIMEOUT = db_error(OperationalError, psycopg.errors.LockNotAvailable("canceling statement due to lock timeout"))
LOST_CONNECTION = db_error(OperationalError, psycopg.OperationalError("server closed the connection unexpectedly"))  # SQLSTATE 없음(서버가 답하지 않았다)
READ_ONLY = db_error(InternalError, psycopg.errors.ReadOnlySqlTransaction("cannot execute INSERT in a read-only transaction"))
ABORTED = db_error(InternalError, psycopg.errors.InFailedSqlTransaction("current transaction is aborted, commands ignored until end of transaction block"))  # 25P02 — 코드 결함


@tag("security")
class ClassificationTests(Cases, TestCase):
    def case_only_deadlock_lock_wait_timeout_and_read_only_are_transient(self):
        self.assertTrue(is_transient_lock_error(DEADLOCK))
        self.assertTrue(is_transient_lock_error(LOCK_TIMEOUT))
        self.assertTrue(is_transient_lock_error(READ_ONLY))
        others = (LOST_CONNECTION, db_error(OperationalError, psycopg.errors.AdminShutdown("terminating connection due to administrator command")),
                  OperationalError("disk I/O error"), RuntimeError("x"), ValueError("40P01"), psycopg.errors.DeadlockDetected("not wrapped by Django"),
                  ABORTED, InternalError("no driver cause"), psycopg.errors.ReadOnlySqlTransaction("not wrapped by Django"))
        for other in others:
            self.assertFalse(is_transient_lock_error(other), other)

    def case_a_wrapped_driver_error_is_recognised_through_its_cause(self):
        try:
            try:
                raise psycopg.errors.DeadlockDetected("deadlock detected")
            except psycopg.Error as driver:
                raise OperationalError("wrapped") from driver
        except OperationalError as wrapped:
            self.assertTrue(is_transient_lock_error(wrapped))

    def test_db_errors_classification(self):
        self.run_cases()


@override_settings(REACTION_PROCESS_ON_RECEIPT=False)
@tag("security")
class ProcessingRetriesTests(Cases, TestCase):
    def setUp(self):
        self.w = World()
        self.media = self.w.slot.media_id
        self.decision = records.decide(self.media, self.w.slot.slot_id, subject_id=SUBJECT, now=NOW, rng=Random(1))
        view, _ = records.receive_reaction(self.media, self.decision.decision_id, uuid.uuid4(), "IMPRESSION", NOW, {}, now=NOW)
        self.inbox_id = view.inbox_id

    def row(self):
        return Reaction.objects.get(pk=self.inbox_id)

    def case_a_deadlock_does_not_count_as_a_failure_and_leaves_no_trace(self):
        with mock.patch("records.processing.billing.charge_for_reaction", side_effect=DEADLOCK), self.assertRaises(OperationalError):
            records.process_reaction(self.inbox_id, now=NOW)
        row = self.row()
        self.assertEqual((row.status, row.attempts, row.basis), ("RECEIVED", 0, None))  # 되돌려졌고 횟수는 그대로다
        self.assertEqual(records.process_reaction(self.inbox_id, now=NOW), "ACCEPTED")  # 다시 하면 된다

    def case_a_real_failure_still_counts(self):
        with mock.patch("records.processing.billing.charge_for_reaction", side_effect=LOST_CONNECTION), self.assertRaises(OperationalError):
            records.process_reaction(self.inbox_id, now=NOW)
        self.assertEqual(self.row().attempts, 1)

    def case_a_reaction_that_keeps_hitting_deadlocks_is_never_dropped_from_automatic_retries(self):
        """상한(`REACTION_MAX_ATTEMPTS`=5)보다 많이 교착을 만나도 계속 대상이다 — 실패로 세면 6번째부터 자동 처리에서 빠진다."""
        with mock.patch("records.processing.billing.charge_for_reaction", side_effect=DEADLOCK):
            for _ in range(7):
                counts = records.process_pending(now=NOW)
                self.assertEqual((counts["transient"], counts["failed"]), (1, 0))
        self.assertEqual(self.row().attempts, 0)
        self.assertEqual(records.stuck_count(), 0)
        counts = records.process_pending(now=NOW)
        self.assertEqual((counts["accepted"], counts["transient"]), (1, 0))

    def case_the_command_summary_reports_transient_separately(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with mock.patch("records.processing.billing.charge_for_reaction", side_effect=LOCK_TIMEOUT):
            call_command("process_reactions", "--once", stdout=out)
        self.assertIn("'transient': 1", out.getvalue())

    def test_processing_retries(self):
        self.run_cases()


@override_settings(REACTION_PROCESS_ON_RECEIPT=False)
@tag("security")
class RealLockWaitTimeoutTests(TransactionTestCase):
    """실제 PostgreSQL 의 잠금 대기 초과(55P03, lock_timeout): 판정 도중 다른 연결이 쥔 행(지갑)을 기다리다 시간이 넘으면 모두 되돌리고 횟수를 올리지 않는다.
    반응 행 자체는 `SKIP LOCKED` 라 기다리지 않는다(다른 처리기가 쥐고 있으면 건너뛴다 — test_records_processing)."""

    def test_waiting_for_a_held_row_until_the_timeout_is_not_a_failure(self):
        w = World()
        decision = records.decide(w.slot.media_id, w.slot.slot_id, subject_id=SUBJECT, now=NOW, rng=Random(1))
        view, _ = records.receive_reaction(w.slot.media_id, decision.decision_id, uuid.uuid4(), "IMPRESSION", NOW, {}, now=NOW)
        holding, release = threading.Event(), threading.Event()

        def hold():
            with transaction.atomic():
                list(Wallet.objects.select_for_update().filter(owner_id=w.adv))  # 과금이 잠그는 행
                holding.set()
                release.wait(WAIT)

        holder = Worker(hold)
        holder.start()
        self.assertTrue(holding.wait(WAIT))
        try:
            with connection.cursor() as cur:
                cur.execute("SET lock_timeout = '1s'")
            with self.assertRaises(OperationalError) as caught:
                records.process_reaction(view.inbox_id, now=NOW)
            self.assertTrue(is_transient_lock_error(caught.exception), caught.exception)
        finally:
            release.set()
            holder.join(WAIT)
        self.assertEqual(Reaction.objects.get(pk=view.inbox_id).attempts, 0)
        self.assertEqual(records.process_reaction(view.inbox_id, now=NOW), "ACCEPTED")


@tag("security")
class TransientErrorResponseTests(TestCase):
    """뷰 밖으로 나온 교착·잠금 대기 초과·읽기 전용만 503 + Retry-After 다. 다른 DB 오류(연결 끊김 등 — 커밋 결과가 불명확할 수 있다, 중단된 트랜잭션)는 그대로 500."""

    def test_only_transient_lock_errors_become_503(self):
        from foundation.middleware import ApiErrorMiddleware
        from django.test import RequestFactory

        middleware = ApiErrorMiddleware(lambda request: None)
        for path in ("/advertiser/", "/api/media/v1/decisions"):
            request = RequestFactory().post(path)
            for error in (DEADLOCK, LOCK_TIMEOUT, READ_ONLY):
                response = middleware.process_exception(request, error)
                self.assertEqual((response.status_code, response["Retry-After"]), (503, "1"), (path, error))
            self.assertIsNone(middleware.process_exception(request, LOST_CONNECTION))
            self.assertIsNone(middleware.process_exception(request, ABORTED))
        self.assertEqual(middleware.process_exception(RequestFactory().post("/api/media/v1/decisions"), DEADLOCK)["Content-Type"], "application/problem+json")



@contextmanager
def read_only_server():
    """쓰기 연결이 읽기 전용(대기) 서버에 닿은 것처럼(설계 01 D21): 이 연결과 **다시 연 연결**의 모든 트랜잭션이 읽기 전용이다 —
    실제 PostgreSQL 이 25006 을 낸다. 상시 작업은 오류 뒤 연결을 버리고 다시 열므로 데이터베이스 기본값으로도 건다."""
    name = connection.settings_dict["NAME"]
    with connection.cursor() as cur:
        cur.execute(f'ALTER DATABASE "{name}" SET default_transaction_read_only = on')
        cur.execute("SET SESSION default_transaction_read_only = on")
    try:
        yield
    finally:
        with connection.cursor() as cur:  # 다시 연 연결이면 기본값이 읽기 전용이다 — 이 세션부터 풀고 데이터베이스 기본값을 지운다
            cur.execute("SET SESSION default_transaction_read_only = off")
            cur.execute(f'ALTER DATABASE "{name}" RESET default_transaction_read_only')


@override_settings(REACTION_PROCESS_ON_RECEIPT=False)
@tag("security")
class ReadOnlyServerTests(TransactionTestCase):
    """실제 읽기 전용 트랜잭션(25006, 계획 P1-10): 매체 API·콘솔은 503 + Retry-After 이고 아무것도 남지 않는다. 상시 처리·중계는 그 주기를 건너뛰고 계속한다.
    다시 쓸 수 있게 되면 같은 요청·다음 주기가 이어서 한다(그 요청은 아무것도 쓰지 못했으므로 다시 보내도 안전하다)."""

    def setUp(self):
        self.w = World()
        self.key = masters.issue_media_key(self.w.admin, self.w.slot.media_id).key
        self.enterContext(mock.patch("records.services._now", lambda now=None: now or NOW))

    def decide(self):
        return self.client.post("/api/media/v1/decisions", data=json.dumps({"slot_id": str(self.w.slot.slot_id), "subject_id": SUBJECT}),
                                content_type="application/json", HTTP_X_MEDIA_KEY=self.key)

    def put(self, decision_id, reaction_id):
        return self.client.put(f"/api/media/v1/decisions/{decision_id}/reactions/{reaction_id}", data=json.dumps({"type": "IMPRESSION", "occurred_at": "2026-10-07T12:00:00Z"}),
                               content_type="application/json", HTTP_X_MEDIA_KEY=self.key)

    def test_the_media_api_answers_503_and_writes_nothing(self):
        decision_id, reaction_id = self.decide().json()["decision_id"], uuid.uuid4()
        with read_only_server(), self.assertLogs("serving_api.auth", "ERROR") as logs:
            responses = [self.decide(), self.put(decision_id, reaction_id)]
        for response in responses:
            self.assertEqual((response.status_code, response["Retry-After"], response["Content-Type"]), (503, "1", "application/problem+json"), response.content)
        self.assertEqual(len(logs.records), 2)
        self.assertIn("read-only transaction", logs.output[0])
        self.assertEqual((Decision.objects.count(), Reaction.objects.count()), (1, 0))
        self.assertEqual(self.decide().status_code, 201)  # 다시 쓸 수 있으면 같은 요청이 된다
        self.assertEqual(self.put(decision_id, reaction_id).status_code, 201)

    def test_a_console_write_answers_503_and_writes_nothing(self):
        self.client.force_login(self.w.adv)
        group = self.w.group.ad_group_id
        with read_only_server(), self.assertLogs("foundation.middleware", "WARNING") as logs:
            response = self.client.post(reverse("ad_group_bid", args=[group]), {"amount": 600, "expected_version": 1})
        self.assertEqual((response.status_code, response["Retry-After"]), (503, "1"))
        self.assertIn("InternalError 25006", logs.output[0])
        self.assertEqual([b.amount for b in campaigns.list_bids(self.w.adv, group)], [300])

    def test_the_reaction_processor_skips_the_cycle_and_carries_on(self):
        decision = records.decide(self.w.slot.media_id, self.w.slot.slot_id, subject_id=SUBJECT, now=NOW, rng=Random(1))
        view, _ = records.receive_reaction(self.w.slot.media_id, decision.decision_id, uuid.uuid4(), "IMPRESSION", NOW, {}, now=NOW)
        sleeps, err = [], io.StringIO()

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise KeyboardInterrupt

        with read_only_server(), mock.patch("records.management.commands.process_reactions.time.sleep", fake_sleep):
            call_command("process_reactions", "--interval", "0.5", stdout=io.StringIO(), stderr=err)
        self.assertEqual(err.getvalue().count("이번 주기를 건너뛴다"), 2, err.getvalue())  # 두 주기 모두 건너뛰고 죽지 않았다
        self.assertIn("25006", err.getvalue())
        row = Reaction.objects.get(pk=view.inbox_id)
        self.assertEqual((row.status, row.attempts), ("RECEIVED", 0))  # 실패로 세지 않았다
        call_command("process_reactions", "--once", stdout=io.StringIO())
        self.assertEqual(Reaction.objects.get(pk=view.inbox_id).status, "ACCEPTED")  # 다음 주기가 이어서 한다

    def test_the_relay_skips_the_cycle_and_carries_on(self):
        records.decide(self.w.slot.media_id, self.w.slot.slot_id, subject_id=SUBJECT, now=NOW, rng=Random(1))  # 보낼 이벤트 하나
        handlers, sleeps, err = {}, [], io.StringIO()

        def capture(signum, handler):
            handlers[signum] = handler
            return signal.SIG_DFL

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                handlers[signal.SIGTERM](signal.SIGTERM, None)

        with read_only_server(), mock.patch("events.management.commands.relay_events.signal.signal", capture), \
                mock.patch("events.management.commands.relay_events.time.sleep", fake_sleep):
            call_command("relay_events", "--interval", "1", stdout=io.StringIO(), stderr=err)
        self.assertEqual(sleeps, [2, 4], err.getvalue())  # 실패한 주기의 쉬는 시간(다른 일시 장애와 같다)
        self.assertEqual(err.getvalue().count("이번 주기를 건너뛴다"), 2)
        self.assertIn("read-only transaction", err.getvalue())
        self.assertEqual(EventOutbox.objects.filter(sent_at__isnull=True).count(), EventOutbox.objects.count())  # 미전송 그대로


@tag("security")
class OtherInternalErrorsAreNotHiddenTests(Cases, TestCase):
    """읽기 전용(25006) 밖의 `InternalError` 는 일시 장애가 아니다: API 는 가리지 않고(500), 상시 처리·중계는 멈춘다(운영 systemd 가 다시 띄운다 — 설계 01 D21 4)."""

    def case_the_media_api_does_not_turn_it_into_503(self):
        w = World()
        key = masters.issue_media_key(w.admin, w.slot.media_id).key
        with mock.patch("serving_api.views.records.decide", side_effect=ABORTED), self.assertRaises(InternalError):
            self.client.post("/api/media/v1/decisions", data=json.dumps({"slot_id": str(w.slot.slot_id), "subject_id": SUBJECT}),
                             content_type="application/json", HTTP_X_MEDIA_KEY=key)

    def case_the_reaction_processor_stops(self):
        with mock.patch("records.management.commands.process_reactions.process_pending", side_effect=ABORTED), \
                mock.patch("records.management.commands.process_reactions.time.sleep", side_effect=AssertionError("쉬지 않고 멈춘다")), \
                self.assertRaises(InternalError):
            call_command("process_reactions", "--interval", "0.5", stdout=io.StringIO(), stderr=io.StringIO())

    def case_the_relay_stops(self):
        with mock.patch("events.management.commands.relay_events.relay_batch", side_effect=ABORTED), \
                mock.patch("events.management.commands.relay_events.time.sleep", side_effect=AssertionError("쉬지 않고 멈춘다")), \
                self.assertRaises(InternalError):
            call_command("relay_events", "--interval", "1", stdout=io.StringIO(), stderr=io.StringIO())

    def test_other_internal_errors(self):
        self.run_cases()


class UniqueViolationTests(Cases, TestCase):
    def raised(self, **kw):
        values = dict(username="dup_user", role="ADVERTISER", nickname="닉", class_label="1반")
        values.update(kw)
        with self.assertRaises(IntegrityError) as caught, transaction.atomic():
            User.objects.create(**values)
        return caught.exception

    def case_a_unique_violation_is_told_apart_from_other_integrity_errors(self):
        User.objects.create(username="dup_user", role="ADVERTISER", nickname="닉", class_label="1반")
        self.assertTrue(is_unique_violation(self.raised()))
        self.assertFalse(is_unique_violation(self.raised(username=None)))  # NOT NULL 위반
        with connection.cursor() as cur, self.assertRaises(IntegrityError) as caught, transaction.atomic():
            cur.execute("INSERT INTO billing_wallet (wallet_id, owner_id, balance, created_at) VALUES (gen_random_uuid(), gen_random_uuid(), 9007199254740993, '2026-01-01')")
        self.assertFalse(is_unique_violation(caught.exception))  # CHECK 위반

    def case_an_unknown_backend_error_is_never_called_a_duplicate(self):
        self.assertFalse(is_unique_violation(IntegrityError("duplicate-looking message")))

    def test_unique_violation(self):
        self.run_cases()
