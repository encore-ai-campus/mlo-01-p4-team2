"""PostgreSQL 전환(T0-4): 실제 연결 여럿으로 잠금 대기·교착·outbox 원자성을 확인한다.

다른 연결(스레드)이 잠금을 쥔 동안 실제 요청·함수를 돌린다. 잠금 대기 한도는 운영과 같은 5초(settings 의 lock_timeout)다.
"""
import json
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import DatabaseError, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import Position, Profile
from community.models import Comment, Post
from config.dberrors import is_transient, sqlstate
from direct_messages.models import Message
from eventing.events import SESSION_KEY, end_platform_session, record_chat, record_floor_transfer, start_platform_session
from eventing.models import ChatMessage, EventOutbox, PlatformSession
from learning import services
from learning.models import PointLedger, Question, UserAnswer
from learning.tests.legacy_fixtures import prepare_event_context
from tests.factories import create_profile

POSTGRESQL = connection.vendor == "postgresql"
OUTBOX = EventOutbox._meta.db_table


@contextmanager
def held(sql, params=()):
    """다른 연결이 `sql` 로 잡은 잠금을 트랜잭션을 열어 둔 채 쥐고 있는 동안."""
    locked, release, errors = threading.Event(), threading.Event(), []

    def hold():
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql, params)
                locked.set()
                release.wait(30)
        except Exception as error:  # noqa: BLE001 — 잠금을 못 잡았으면 시험이 바로 알게 한다
            errors.append(error)
            locked.set()
        finally:
            connection.close()

    thread = threading.Thread(target=hold)
    thread.start()
    locked.wait(10)
    if errors:
        raise errors[0]
    try:
        yield
    finally:
        release.set()
        thread.join(30)


def own_connection(fn):
    """스레드가 자기 연결로 돌리고 끝에 닫는다. DB 오류는 돌려준다."""
    def run(*args):
        try:
            return fn(*args)
        except DatabaseError as error:
            return error
        finally:
            connection.close()
    return run


@unittest.skipIf(connection.vendor == "mysql", "MySQL 기본 콜레이션은 대소문자를 구분하지 않는다")
class CaseSensitiveLoginTests(TestCase):
    def test_login_with_a_different_letter_case_fails(self):
        # 06 결정 D1: PostgreSQL 은 아이디의 대소문자를 구분한다(MySQL 콜레이션과 다름)
        user = get_user_model().objects.create_user(username="CaseLogin", password="Case!4820Login")
        Profile.objects.create(user=user, nickname="대소문자")
        response = self.client.post("/accounts/login/", {"username": "caselogin", "password": "Case!4820Login"})
        self.assertNotEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        response = self.client.post("/accounts/login/", {"username": "CaseLogin", "password": "Case!4820Login"})
        self.assertEqual(response.status_code, 302)


@unittest.skipUnless(POSTGRESQL, "PostgreSQL 행·표 잠금")
@override_settings(CAMPUS_DM_ENABLED=True, KAFKA_BOOTSTRAP_SERVERS="")
class PostgresLockTests(TransactionTestCase):
    def setUp(self):
        self.users = [get_user_model().objects.create_user(username=f"pg-lock-{index}") for index in range(2)]
        self.profiles = [create_profile(user=user, nickname=f"잠금{index}", cohort="mlo-1", setup_complete=True)
                         for index, user in enumerate(self.users)]
        self.user = self.users[0]
        self.client = Client(raise_request_exception=False)
        self.client.force_login(self.user)
        self.user_table = get_user_model()._meta.db_table

    def send_message(self):
        return self.client.post("/api/messages/send/", json.dumps({
            "recipient": str(self.profiles[1].user_uuid), "body": "잠금 대기", "requestId": str(uuid.uuid4()),
        }), content_type="application/json")

    def lesson(self):
        prepare_event_context(self.user)
        for number in range(10):
            Question.objects.create(subject="sql", question_type="fill_blank", difficulty="beginner",
                                    category="concept", question_text=f"query {number}", correct_answer="SELECT")
        lesson = services.start_learning_session(self.user, "fill_blank", "beginner", "sql")
        services.get_current_question(self.user, lesson.pk, 1)
        return lesson

    def test_waiting_more_than_five_seconds_for_a_row_lock_returns_503_with_retry_after(self):
        with held(f"SELECT id FROM {self.user_table} WHERE id = %s FOR UPDATE", [self.users[1].pk]):
            started = time.monotonic()
            with self.assertLogs("django.request", level="ERROR") as logs:
                response = self.send_message()
            waited = time.monotonic() - started
        self.assertEqual((response.status_code, response["Retry-After"]), (503, "1"))
        self.assertEqual(response.json()["error"], "database_busy")
        self.assertTrue(4.9 <= waited < 7, waited)
        self.assertEqual(sqlstate(logs.records[0].exc_info[1]), "55P03")
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(self.send_message().status_code, 201)

    async def test_waiting_more_than_five_seconds_while_connecting_closes_with_4013(self):
        from config.asgi import application
        key = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        socket = WebsocketCommunicator(application, "/ws/campus/", headers=[
            (b"origin", b"http://127.0.0.1"), (b"host", b"127.0.0.1"),
            (b"cookie", f"{settings.SESSION_COOKIE_NAME}={key}".encode())])
        # 아직 정책 필드가 없는 세션이라 연결 중에 세션 행을 고쳐 쓴다. 그 행을 다른 연결이 쥐고 있다
        with held("SELECT session_key FROM django_session WHERE session_key = %s FOR UPDATE", [key]):
            started = time.monotonic()
            with self.assertLogs("campus.socket_db", level="WARNING") as logs:
                self.assertTrue((await socket.connect(timeout=10))[0])
                self.assertEqual(await socket.receive_output(timeout=10), {"type": "websocket.close", "code": 4013})
            waited = time.monotonic() - started
        await socket.disconnect()
        self.assertTrue(4.9 <= waited < 7, waited)
        self.assertIn("sqlstate=55P03", logs.output[0])

    def test_lock_timeout_rolls_back_business_rows_together_with_their_outbox_rows(self):
        lesson = self.lesson()
        post = Post.objects.create(author=self.user, category="free", title="잠금", body="본문")
        self.client.get("/api/learning/wallet/")  # 미들웨어가 붙이는 플랫폼 세션은 잠그기 전에 만든다
        session_uuid = str(PlatformSession.objects.filter(user=self.user, ended_at__isnull=True).first().session_uuid)
        rooftop = {"floor": "rooftop", "x": 300, "y": 300, "dir": "up"}
        operations = {
            "answer": lambda: services.submit_answer(self.user, lesson.pk, 1, "SELECT", uuid.uuid4()),
            "chat": lambda: record_chat(self.user, session_uuid, "잠금 대기", "campus"),
            "floor": lambda: record_floor_transfer(self.user, session_uuid, "campus", "rooftop", rooftop),
            "session_start": lambda: start_platform_session(self.user, import_module(settings.SESSION_ENGINE).SessionStore()),
            "comment": lambda: self.client.post(f"/api/community/posts/{post.id}/comments/", json.dumps({"body": "잠금 대기"}),
                                                content_type="application/json").status_code,
            "session_end": lambda: end_platform_session(self.user, {SESSION_KEY: session_uuid}, "LOGOUT"),
        }

        def state():
            return {
                "answers": UserAnswer.objects.count(), "ledger": PointLedger.objects.count(),
                "chat": ChatMessage.objects.count(), "comments": Comment.objects.count(),
                "sessions": PlatformSession.objects.count(),
                "open": PlatformSession.objects.filter(ended_at__isnull=True).count(),
                "position": Position.objects.get(profile__user=self.user).last_position,
                "outbox": sorted(EventOutbox.objects.values_list("event_type", flat=True)),
            }

        before = state()
        with held(f"LOCK TABLE {OUTBOX} IN EXCLUSIVE MODE"):
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=len(operations)) as pool, self.assertLogs("django.request", "ERROR"):
                results = dict(zip(operations, pool.map(lambda fn: own_connection(fn)(), operations.values())))
            waited = time.monotonic() - started
        self.assertTrue(4.9 <= waited < 9, waited)
        self.assertEqual(results.pop("comment"), 503)
        self.assertEqual({name: sqlstate(error) for name, error in results.items()}, dict.fromkeys(results, "55P03"))
        self.assertEqual(state(), before)  # 업무 행도 outbox 행도 하나도 남지 않았다

        for name, operation in operations.items():  # 잠금이 없으면 같은 일이 업무 행과 outbox 행을 함께 남긴다
            self.assertNotIsInstance(operation(), DatabaseError, name)
        after = state()
        self.assertEqual((after["answers"], after["ledger"], after["chat"], after["comments"], after["sessions"]),
                         (before["answers"] + 1, before["ledger"] + 1, before["chat"] + 1, before["comments"] + 1,
                          before["sessions"] + 1))
        added = list(after["outbox"])
        for event_type in before["outbox"]:
            added.remove(event_type)
        for event_type in ("quiz_answered", "point_granted", "chat_sent", "floor_transferred",
                           "session_started", "comment_created", "session_ended"):
            self.assertIn(event_type, added)

    def deadlock(self, row_sql, params, send):
        """outbox 표를 먼저 잡은 연결이, 요청이 `row_sql` 행을 쥔 채 outbox 쓰기에서 기다리면 그 행을 요청한다(반대 순서)."""
        locked, holder_errors = threading.Event(), []

        def holder():
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(f"LOCK TABLE {OUTBOX} IN EXCLUSIVE MODE")
                    locked.set()
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        # pg_locks 는 지금 상태를 보여 준다(pg_stat_activity 는 트랜잭션 안에서 처음 읽은 값을 다시 준다)
                        cursor.execute("SELECT count(*) FROM pg_locks WHERE NOT granted AND locktype = 'relation' "
                                       "AND relation = %s::regclass", [OUTBOX])
                        if cursor.fetchone()[0]:
                            break
                        time.sleep(0.05)
                    cursor.execute(row_sql, params)
            except DatabaseError as error:
                holder_errors.append(error)
            finally:
                connection.close()

        thread = threading.Thread(target=holder)
        thread.start()
        self.assertTrue(locked.wait(10))
        started = time.monotonic()
        with self.assertLogs("django.request", level="ERROR") as logs:
            response = send()
        waited = time.monotonic() - started
        thread.join(30)
        self.assertEqual((response.status_code, response.get("Retry-After")), (503, "1"))
        self.assertEqual(response.json(), {"error": "database_busy", "message": "잠시 후 다시 시도해 주세요."})
        error = logs.records[0].exc_info[1]
        self.assertEqual(sqlstate(error), "40P01")
        self.assertTrue(is_transient(error))
        self.assertLess(waited, 4)  # 잠금 대기 한도(5초)가 아니라 교착 감지(deadlock_timeout 1초)로 끝났다
        self.assertEqual(holder_errors, [])  # 먼저 기다린 요청이 교착을 알아채고 물러났다

    def test_deadlock_victim_reply_rolls_back_its_rows_and_outbox_and_returns_503(self):
        post = Post.objects.create(author=self.user, category="free", title="교착", body="본문")
        parent = Comment.objects.create(post=post, author=self.user, body="원래 댓글")
        self.client.get("/api/community/")  # 미들웨어가 붙이는 플랫폼 세션은 잠그기 전에 만든다
        self.deadlock(f"SELECT comment_uuid FROM {Comment._meta.db_table} WHERE comment_uuid = %s FOR UPDATE", [parent.pk],
                      lambda: self.client.post(f"/api/community/posts/{post.id}/comments/", json.dumps({
                          "body": "교착 답글", "parentCommentUuid": str(parent.comment_uuid)}), content_type="application/json"))
        self.assertEqual(list(Comment.objects.values_list("pk", flat=True)), [parent.pk])
        self.assertFalse(EventOutbox.objects.filter(event_type="comment_created").exists())

    def test_deadlock_victim_answer_gets_the_same_503_as_other_apis(self):
        # 학습 API 의 SQLite 'locked' 처리가 PostgreSQL 교착 문구('blocked by process')에 걸리지 않는다
        lesson = self.lesson()
        self.client.get("/api/learning/wallet/")
        self.deadlock(f"SELECT id FROM {self.user_table} WHERE id = %s FOR UPDATE", [self.user.pk],
                      lambda: self.client.post(f"/api/learning/sessions/{lesson.pk}/questions/1/answers/", json.dumps({
                          "answer": "SELECT", "submission_request_uuid": str(uuid.uuid4())}), content_type="application/json"))
        self.assertEqual((UserAnswer.objects.count(), PointLedger.objects.count()), (0, 0))
        self.assertFalse(EventOutbox.objects.filter(event_type__in=("quiz_answered", "point_granted")).exists())

    def test_replacing_a_login_reads_redis_before_taking_the_locks(self):
        password = "Redis!4820Lock"
        self.user.set_password(password)
        self.user.save()

        def log_in(client):
            return client.post("/accounts/login/", {"username": self.user.username, "password": password}).status_code

        old = Client()
        self.assertEqual(log_in(old), 302)
        old_key = old.session.session_key
        position = {"floor": "rooftop", "x": 1812.25, "y": 1478.5, "dir": "right"}
        reading, seen = threading.Event(), {}

        def slow_get(key):  # 응답이 2초 늦는 Redis
            seen["in_transaction"] = connection.in_atomic_block
            reading.set()
            time.sleep(2)
            return json.dumps({"user": str(self.user.pk), "position": position, "observedAt": timezone.now().isoformat()})

        def lock_user_row():  # 같은 사용자의 다른 쓰기가 사용자 행을 잠그기까지 기다린 시간
            reading.wait(10)
            started = time.monotonic()
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(f"SELECT id FROM {self.user_table} WHERE id = %s FOR UPDATE", [self.user.pk])
            seen["waited"] = time.monotonic() - started

        with override_settings(CAMPUS_MOVEMENT_REDIS_URL="redis://movement.invalid:6379/0"), \
                patch("campus.cluster._sync_redis", SimpleNamespace(get=slow_get)), ThreadPoolExecutor(max_workers=1) as pool:
            probe = pool.submit(own_connection(lock_user_row))
            self.assertEqual(log_in(Client()), 302)
            self.assertIsNone(probe.result(10))
        self.assertFalse(seen["in_transaction"])
        self.assertLess(seen["waited"], 0.5)  # 로그인이 Redis 를 기다리는 동안 사용자 행은 잠겨 있지 않았다
        self.assertEqual(Position.objects.get(profile__user=self.user).last_position, position)  # 미리 읽은 위치를 저장했다
        self.assertFalse(Session.objects.filter(session_key=old_key).exists())

    def test_concurrent_position_saves_keep_the_newest_observation(self):
        from campus.positions import save_position
        for round_ in range(5):
            older, newer = timezone.now(), timezone.now()
            positions = {older: {"floor": "campus", "x": 100 + round_, "y": 100, "dir": "up"},
                         newer: {"floor": "campus", "x": 200 + round_, "y": 200, "dir": "down"}}
            barrier = threading.Barrier(2)

            def save(observed_at):
                barrier.wait(5)
                return save_position(self.user, positions[observed_at], observed_at)

            order = [older, newer] if round_ % 2 else [newer, older]
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(own_connection(save), order))
            saved = Position.objects.get(profile__user=self.user)
            self.assertEqual((saved.last_position, saved.last_position_at), (positions[newer], newer))

    def test_concurrent_replies_to_one_comment_are_all_saved_with_their_outbox_rows(self):
        post = Post.objects.create(author=self.user, category="free", title="답글", body="본문")
        parent = Comment.objects.create(post=post, author=self.user, body="원래 댓글")
        clients = []
        for index in range(5):
            user = get_user_model().objects.create_user(username=f"pg-reply-{index}")
            create_profile(user=user, nickname=f"답글{index}", cohort="mlo-1", setup_complete=True)
            client = Client()
            client.force_login(user)
            client.get("/api/community/")  # 플랫폼 세션을 미리 붙인다
            clients.append(client)
        barrier = threading.Barrier(len(clients))

        def reply(client):
            barrier.wait(5)
            return client.post(f"/api/community/posts/{post.id}/comments/", json.dumps({
                "body": "동시 답글", "parentCommentUuid": str(parent.comment_uuid)}), content_type="application/json").status_code

        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            codes = list(pool.map(own_connection(reply), clients))
        self.assertEqual(codes, [201] * len(clients))
        self.assertEqual(parent.replies.count(), len(clients))
        self.assertEqual(EventOutbox.objects.filter(event_type="comment_created").count(), len(clients))
