"""The transactional outbox: schema, contract and the 15 event sites (same transaction as the business change)."""
import json
import time
import uuid
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import AsyncMock, Mock, patch

import xxhash
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from accounts.models import Position, Profile
from campus.positions import flush_movement_event, movement_event_due_at, save_position
from community.models import Comment, Post, PostReadLog
from learning import services as learning_services
from learning.models import Question, QuestionChoice
from . import events
from .catalog import floor_uuid
from .models import ChatMessage, ClassGroup, ContentItem, ContentOpenRequestLog, EventOutbox, PlatformSession

COLUMNS = {
    "outbox_uuid": "uuid", "event_type": "character varying", "topic": "character varying", "message_key": "uuid",
    "raw_data": "jsonb", "occurred_at": "timestamp with time zone", "payload_json": "jsonb", "retry_count": "integer",
    "created_at": "timestamp with time zone", "published_at": "timestamp with time zone",
}


def make_user(username, cohort_code="mlo-1"):
    user = get_user_model().objects.create_user(username=username, password="Outbox!River4820")
    Profile.objects.create(user=user, nickname="원문에 넣지 않을 닉네임", avatar="male-1",
                           cohort=ClassGroup.objects.get(code=cohort_code))
    return user


class OutboxSchemaTests(TestCase):
    def test_table_columns_types_key_and_index(self):
        with connection.cursor() as cursor:
            cursor.execute("""SELECT column_name, data_type, is_nullable FROM information_schema.columns
                              WHERE table_schema='public' AND table_name='eventing_eventoutbox'""")
            rows = {name: (kind, nullable) for name, kind, nullable in cursor.fetchall()}
            cursor.execute("""SELECT a.attname FROM pg_index i JOIN pg_attribute a
                              ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
                              WHERE i.indrelid='eventing_eventoutbox'::regclass AND i.indisprimary""")
            primary = [row[0] for row in cursor.fetchall()]
            cursor.execute("SELECT indexdef FROM pg_indexes WHERE indexname='eventing_outbox_pending'")
            index = cursor.fetchone()[0]
        self.assertEqual({name: kind for name, (kind, _) in rows.items()}, COLUMNS)
        self.assertEqual({name for name, (_, nullable) in rows.items() if nullable == "YES"}, {"payload_json", "published_at"})
        self.assertEqual(primary, ["outbox_uuid"])
        self.assertIn("(published_at, created_at)", index)
        self.assertNotIn("event_uuid", rows)

    def test_position_keeps_movement_sampling_state(self):
        names = {field.name for field in Position._meta.get_fields()}
        self.assertTrue({"last_movement_event_at", "movement_event_pending"} <= names)


class ContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user("contract-user")

    def setUp(self):
        self.session_uuid = uuid.uuid4()
        self.now = timezone.now()

    def test_no_event_field_reuses_identity_names(self):
        # The app uses event_uuid for event_definition and event_id for FKs and story records.
        for event_type, fields in events.EXTRA_FIELDS.items():
            with self.subTest(event_type=event_type):
                self.assertFalse({"event_uuid", "event_id", "outbox_uuid"} & fields)
        self.assertEqual(set(events.TOPIC_BY_TYPE), set(events.EXTRA_FIELDS))
        self.assertEqual(len(events.EXTRA_FIELDS), 22)  # 23 contract lines: quiz_answered has 1.0 and 1.1

    def test_identity_uses_the_profile_class_group_itself(self):
        user = make_user("aio-user", "aio-4")
        raw = events.raw_identity(user, self.session_uuid)
        aio = ClassGroup.objects.get(code="aio-4")
        self.assertEqual(raw["class_group_uuid"], str(aio.class_group_uuid))
        self.assertNotEqual(raw["class_group_uuid"], str(ClassGroup.objects.get(code="unassigned").class_group_uuid))
        self.assertEqual(raw["user_uuid"], str(user.pk))
        events.record_chat(user, self.session_uuid, "본문", "campus")
        self.assertEqual(EventOutbox.objects.get().raw_data["class_group_uuid"], str(aio.class_group_uuid))

    def test_envelope_is_keyed_by_outbox_uuid_with_a_stable_fingerprint(self):
        raw = events.raw_identity(self.user, self.session_uuid)
        raw["session_uuid"] = str(self.session_uuid)
        outbox_uuid = uuid.uuid4()
        observed = datetime(2026, 11, 9, 8, 30, tzinfo=datetime_timezone(timedelta(hours=9)))
        envelope = events.build_envelope(outbox_uuid, "session_started", observed, raw)
        self.assertEqual(set(envelope), {"outbox_uuid", "raw_manifest", "raw_data"})
        self.assertEqual(envelope["outbox_uuid"], str(outbox_uuid))
        manifest = envelope["raw_manifest"]
        self.assertEqual(manifest["occurred_at"], "2026-11-08T23:30:00.000Z")
        self.assertEqual(manifest["fingerprint"], xxhash.xxh3_64(events.canonical_json(raw).encode()).hexdigest())
        reordered = dict(reversed(list(raw.items())))
        self.assertEqual(events.build_envelope(outbox_uuid, "session_started", observed, reordered)["raw_manifest"], manifest)

    def test_catalog_fields_accept_uuid5_but_row_identities_stay_uuid4(self):
        identity = events.raw_identity(self.user, self.session_uuid)
        v5 = str(uuid.uuid5(uuid.NAMESPACE_URL, "playdata:learning:cs:multiple-choice"))
        opened = {**identity, "content_uuid": v5, "category_uuid": v5, "zone_uuid": v5,
                  "domain": "LEARNING", "content_kind": "QUIZ", "action": "OPEN_REQUESTED"}
        events.validate_raw_data("content_open_requested", opened)
        moved = {**identity, "from_floor_uuid": v5, "to_floor_uuid": v5, "zone_uuid": None}
        events.validate_raw_data("floor_transferred", moved)
        for event_type, raw in (
            ("post_read", {**identity, "post_uuid": v5, "revision": 1, "zone_uuid": None}),
            ("session_started", {**identity, "user_uuid": v5}),
            ("chat_sent", {**identity, "message_uuid": v5, "channel_code": "GLOBAL", "floor_uuid": None}),
            # 받는 쪽(metaverse_rules)과 같이 소문자 표기만 받는다 — 행 ID(v4)도 카탈로그(v5)도
            ("chat_sent", {**identity, "message_uuid": str(uuid.uuid4()).upper(), "channel_code": "GLOBAL", "floor_uuid": None}),
            ("floor_transferred", {**moved, "from_floor_uuid": v5.upper()}),
        ):
            with self.subTest(event_type=event_type):
                with self.assertLogs("eventing.events", level="ERROR"), self.assertRaises(ValueError):
                    events.validate_raw_data(event_type, raw)

    def test_unknown_extra_and_nested_values_are_rejected_without_logging_them(self):
        identity = events.raw_identity(self.user, self.session_uuid)
        cases = [
            ("chat_sent", {**identity, "message_uuid": str(uuid.uuid4()), "channel_code": "GLOBAL",
                           "floor_uuid": None, "body": "private-secret"}),
            ("movement_observed", {**identity, "floor_uuid": str(floor_uuid("campus")), "zone_uuid": None,
                                   "x": {"body": "private-secret"}, "y": 100, "input_kind": "KEYBOARD"}),
            ("session_started", {**identity, "session_uuid": None}),
            ("session_started", {**identity, "user_uuid": "private-secret"}),
            ("unknown_type", identity),
        ]
        for event_type, raw in cases:
            with self.subTest(event_type=event_type, keys=sorted(raw)):
                with self.assertLogs("eventing.events", level="ERROR") as captured:
                    with self.assertRaises(ValueError):
                        events.build_envelope(uuid.uuid4(), event_type, self.now, raw)
                self.assertNotIn("private-secret", " ".join(captured.output))
        with self.assertLogs("eventing.events", level="ERROR"), self.assertRaises(ValueError):
            events.enqueue_event("chat_sent", self.user, self.session_uuid, {"message_uuid": str(uuid.uuid4())})
        self.assertFalse(EventOutbox.objects.exists())


class EventSiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user("site-user")

    def setUp(self):
        self.session_uuid = uuid.uuid4()
        self.now = timezone.now()

    def chat(self):
        return events.record_chat(self.user, self.session_uuid, "운영 DB에만 보관할 메시지", "campus")

    def test_chat_row_and_event_commit_together_without_the_body(self):
        message_uuid = self.chat()
        row = EventOutbox.objects.get(event_type="chat_sent")
        self.assertEqual((row.topic, row.message_key), ("raw.chat", self.user.pk))
        self.assertEqual(row.raw_data["message_uuid"], message_uuid)
        self.assertEqual(row.raw_data["floor_uuid"], str(floor_uuid("campus")))
        self.assertEqual(row.outbox_uuid.version, 4)
        self.assertEqual(row.payload_json["outbox_uuid"], str(row.outbox_uuid))
        self.assertIsNone(row.published_at)
        text = json.dumps(row.payload_json, ensure_ascii=False)
        self.assertNotIn("운영 DB에만 보관할 메시지", text)
        self.assertNotIn("원문에 넣지 않을 닉네임", text)

    def test_chat_is_not_stored_when_the_event_cannot_be_written(self):
        with patch.object(EventOutbox.objects, "create", side_effect=DatabaseError("outbox unavailable")):
            with self.assertRaises(DatabaseError):
                self.chat()
        self.assertFalse(ChatMessage.objects.exists())

    def test_session_start_once_end_once_and_failed_event_keeps_the_session_open(self):
        session = SessionStore()
        session.save()
        first = events.ensure_platform_session(self.user, session, now=self.now)
        self.assertEqual(events.ensure_platform_session(self.user, SessionStore(session_key=session.session_key), now=self.now), first)
        self.assertEqual(EventOutbox.objects.filter(event_type="session_started").count(), 1)
        with patch.object(EventOutbox.objects, "create", side_effect=DatabaseError("outbox unavailable")):
            with self.assertRaises(DatabaseError):
                events.end_platform_session(self.user, session, "LOGOUT")
        self.assertIsNone(PlatformSession.objects.get(pk=first).ended_at)
        self.assertTrue(events.end_platform_session(self.user, session, "LOGOUT"))
        self.assertFalse(events.end_platform_session(self.user, session, "LOGOUT"))
        ended = EventOutbox.objects.get(event_type="session_ended")
        self.assertEqual((ended.raw_data["session_uuid"], ended.raw_data["end_reason"]), (first, "LOGOUT"))

    def test_session_start_rolls_back_without_its_event(self):
        session = SessionStore()
        with patch.object(EventOutbox.objects, "create", side_effect=DatabaseError("outbox unavailable")):
            with self.assertRaises(DatabaseError):
                events.start_platform_session(self.user, session)
        self.assertFalse(PlatformSession.objects.exists())

    def test_floor_event_only_when_the_snapshot_is_accepted_and_rolls_back_together(self):
        initial = {"floor": "campus", "x": 1900, "y": 800, "dir": "up"}
        rooftop = {"floor": "rooftop", "x": 100, "y": 1200, "dir": "left"}
        save_position(self.user, initial, self.now)
        with patch.object(EventOutbox.objects, "create", side_effect=DatabaseError("outbox unavailable")):
            with self.assertRaises(DatabaseError):
                events.record_floor_transfer(self.user, self.session_uuid, "campus", "rooftop", rooftop, self.now + timedelta(seconds=1))
        self.assertEqual(Position.objects.get(profile__user=self.user).last_position["floor"], "campus")
        observed = self.now + timedelta(seconds=2)
        self.assertTrue(events.record_floor_transfer(self.user, self.session_uuid, "campus", "rooftop", rooftop, observed))
        self.assertFalse(events.record_floor_transfer(self.user, self.session_uuid, "campus", "rooftop", rooftop, observed))
        row = EventOutbox.objects.get(event_type="floor_transferred")
        self.assertEqual(row.raw_data["from_floor_uuid"], str(floor_uuid("campus")))
        self.assertEqual(row.raw_data["to_floor_uuid"], str(floor_uuid("rooftop")))

    def test_movement_is_sampled_once_per_interval_from_the_newest_position(self):
        def at(seconds, x):
            return save_position(self.user, {"floor": "campus", "x": x, "y": 800, "dir": "up"},
                                 self.now + timedelta(seconds=seconds), session_uuid=self.session_uuid)

        with patch("campus.positions.timezone.now", return_value=self.now):
            self.assertTrue(at(0, 10))
        self.assertEqual(EventOutbox.objects.filter(event_type="movement_observed").count(), 1)
        with patch("campus.positions.timezone.now", return_value=self.now + timedelta(seconds=5)):
            self.assertTrue(at(5, 11))
            self.assertTrue(at(6, 12))
        record = Position.objects.get(profile__user=self.user)
        self.assertEqual(record.movement_event_pending["position"]["x"], 12)
        self.assertEqual(movement_event_due_at(self.user), self.now + timedelta(seconds=30))
        self.assertEqual(EventOutbox.objects.filter(event_type="movement_observed").count(), 1)
        with patch("campus.positions.timezone.now", return_value=self.now + timedelta(seconds=31)):
            self.assertTrue(flush_movement_event(self.user))
            self.assertFalse(flush_movement_event(self.user))
        rows = list(EventOutbox.objects.filter(event_type="movement_observed").order_by("occurred_at"))
        self.assertEqual([row.raw_data["x"] for row in rows], [10.0, 12.0])
        self.assertEqual(rows[1].occurred_at, self.now + timedelta(seconds=6))
        self.assertEqual(rows[1].topic, "raw.movement")
        self.assertIsNone(movement_event_due_at(self.user))

    def save_at(self, seconds, x, *, observed_at=None, user=None, session_uuid=None):
        now = self.now + timedelta(seconds=seconds)
        with patch("campus.positions.timezone.now", return_value=now):
            return save_position(user or self.user, {"floor": "campus", "x": x, "y": 800, "dir": "up"},
                                 observed_at or now, session_uuid=session_uuid or self.session_uuid)

    def test_reconnect_keeps_original_session_and_gate(self):
        self.save_at(0, 11)
        self.save_at(5, 12)
        reconnected = get_user_model().objects.get(pk=self.user.pk)
        self.assertEqual(movement_event_due_at(reconnected), self.now + timedelta(seconds=30))
        self.save_at(10, 12, user=reconnected, session_uuid=uuid.uuid4())
        self.assertEqual(EventOutbox.objects.filter(event_type="movement_observed").count(), 1)
        self.save_at(30, 12, observed_at=self.now + timedelta(seconds=10), user=reconnected, session_uuid=uuid.uuid4())
        latest = EventOutbox.objects.filter(event_type="movement_observed").latest("occurred_at")
        self.assertEqual(latest.raw_data["session_uuid"], str(self.session_uuid))

    def test_reconnected_idle_flush_cannot_overwrite_another_workers_newer_position(self):
        from campus.consumers import CampusConsumer

        self.save_at(0, 11)
        self.save_at(5, 12)
        consumer = CampusConsumer()
        consumer.state = {"floor": "campus", "x": 12, "y": 800, "dir": "up"}
        consumer.position_changed_at = None
        consumer.policy_status = None  # connect()가 정하는 값
        consumer.session_user = Mock(return_value=self.user)
        self.save_at(10, 13, user=get_user_model().objects.get(pk=self.user.pk))
        with patch("campus.positions.timezone.now", return_value=self.now + timedelta(seconds=30)):
            self.assertIsNone(consumer.store_position_now(None, consumer.position_changed_at))
        record = Position.objects.get(profile__user=self.user)
        self.assertEqual(record.last_position["x"], 13)
        self.assertEqual(record.last_position_at, self.now + timedelta(seconds=10))
        sample = EventOutbox.objects.get(event_type="movement_observed", occurred_at=self.now + timedelta(seconds=10))
        self.assertEqual(sample.raw_data["x"], 13)
        self.assertIsNone(record.movement_event_pending)

    def test_positions_without_a_platform_session_are_not_sampled(self):
        save_position(self.user, {"floor": "campus", "x": 10, "y": 800, "dir": "up"}, self.now)
        self.assertFalse(EventOutbox.objects.exists())

    def test_cs_learning_start_succeeds_with_its_uuid5_catalog(self):
        session = SessionStore()
        session_uuid = events.start_platform_session(self.user, session)
        content, _ = learning_services.open_learning_content(self.user, session_uuid, "cs", "multiple_choice")
        self.assertEqual(content.code, "cs-multiple-choice")
        self.assertEqual(content.pk.version, 5)
        row = EventOutbox.objects.get(event_type="content_open_requested")
        self.assertEqual(row.raw_data["content_uuid"], str(content.pk))
        self.assertEqual(row.raw_data["zone_uuid"], str(content.zone_id))
        self.assertEqual((row.raw_data["domain"], row.raw_data["content_kind"]), ("LEARNING", "QUIZ"))
        self.assertEqual(ContentOpenRequestLog.objects.get().occurred_at, row.occurred_at)


class MovementCheckpointTests(SimpleTestCase):
    """소켓의 저장 틱: 멈춘 뒤에는 대기 표본의 기한에만 DB에 간다(t0 test_movement_sampling)."""

    def consumer(self, due_at=None):
        from campus.consumers import CampusConsumer

        consumer = CampusConsumer()
        consumer.position_dirty = False
        consumer.movement_event_due_at = due_at
        consumer.pending_floor_transfer = None
        consumer.last_checkpoint = -time.monotonic()
        consumer.position_changed_at = timezone.now() - timedelta(seconds=25)
        consumer.state = {"floor": "campus", "x": 10.0, "y": 20.0, "dir": "up"}
        consumer.store_position = AsyncMock(return_value=None)
        return consumer

    def test_idle_position_without_pending_sample_does_not_query_database(self):
        consumer = self.consumer()
        async_to_sync(consumer.checkpoint)()
        consumer.store_position.assert_not_awaited()

    def test_stopped_position_is_checked_only_when_pending_sample_is_due(self):
        consumer = self.consumer(timezone.now() + timedelta(seconds=5))
        async_to_sync(consumer.checkpoint)()
        consumer.store_position.assert_not_awaited()
        consumer.movement_event_due_at = timezone.now() - timedelta(seconds=1)
        async_to_sync(consumer.checkpoint)()
        consumer.store_position.assert_awaited_once_with(None, consumer.position_changed_at, None)
        self.assertIsNone(consumer.movement_event_due_at)


class HttpEventTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user("http-user")

    def setUp(self):
        self.client.force_login(self.user)
        self.client.get("/api/accounts/session/status/")
        self.session_uuid = self.client.session[events.SESSION_KEY]

    def send(self, method, url, data=None):
        return getattr(self.client, method)(url, data=json.dumps(data) if data is not None else "", content_type="application/json")

    def test_post_and_comment_lifecycle_events_carry_revisions_and_no_text(self):
        created = self.send("post", "/api/community/posts/", {"category": "free", "title": "원문 제목", "body": "게시글 원문"})
        self.assertEqual(created.status_code, 201, created.content)
        post = created.json()["post"]
        detail = f"/api/community/posts/{post['id']}/"
        changed = self.send("patch", detail, {"title": "수정 제목", "version": post["updatedAt"]})
        self.assertEqual(changed.status_code, 200, changed.content)
        comment = self.send("post", f"{detail}comments/", {"body": "댓글 원문"}).json()["comment"]
        reply = self.send("post", f"{detail}comments/", {"body": "답글 원문", "parentCommentUuid": comment["commentUuid"]})
        self.assertEqual(reply.status_code, 201, reply.content)
        comment_url = f"/api/community/comments/{comment['id']}/"
        self.assertEqual(self.send("patch", comment_url, {"body": "댓글 수정", "version": comment["updatedAt"]}).status_code, 200)
        self.assertEqual(self.send("delete", comment_url).status_code, 200)
        self.assertEqual(self.send("delete", detail).status_code, 200)
        for prefix, topic, subject in (("post", "raw.board", {}),
                                       ("comment", "raw.comment", {"raw_data__comment_uuid": comment["commentUuid"]})):
            for action, revision in (("created", 1), ("updated", 2), ("deleted", 3)):
                with self.subTest(event=f"{prefix}_{action}"):
                    row = EventOutbox.objects.get(event_type=f"{prefix}_{action}", **subject)
                    self.assertEqual((row.topic, row.raw_data["revision"]), (topic, revision))
                    self.assertEqual(row.raw_data["session_uuid"], self.session_uuid)
                    self.assertNotIn("원문", json.dumps(row.payload_json, ensure_ascii=False))
        # 답글만 부모를 싣는다(계약이 NULL을 허용해 틀려도 조용히 "답글 아님"이 되므로 둘 다 본다)
        created = {row.raw_data["comment_uuid"]: row.raw_data["parent_comment_uuid"]
                   for row in EventOutbox.objects.filter(event_type="comment_created")}
        self.assertEqual(created, {comment["commentUuid"]: None,
                                   reply.json()["comment"]["commentUuid"]: comment["commentUuid"]})

    def test_a_read_event_once_per_read_request_uuid(self):
        post = Post.objects.create(author=self.user, category="free", title="읽기", body="본문")
        key = uuid.uuid4()
        for _ in range(2):
            self.assertEqual(self.client.get(f"/api/community/posts/{post.id}/?read_uuid={key}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/community/posts/{post.id}/?read_uuid={uuid.uuid4()}").status_code, 200)
        rows = EventOutbox.objects.filter(event_type="post_read")
        self.assertEqual(rows.count(), 2)
        self.assertEqual(PostReadLog.objects.count(), 2)
        self.assertEqual({row.raw_data["post_uuid"] for row in rows}, {str(post.post_uuid)})

    def test_read_log_is_not_kept_when_its_event_cannot_be_written(self):
        post = Post.objects.create(author=self.user, category="free", title="읽기", body="본문")
        with patch("community.views.record_post_event", side_effect=DatabaseError("outbox unavailable")):
            with self.assertRaises(DatabaseError):
                self.client.get(f"/api/community/posts/{post.id}/?read_uuid={uuid.uuid4()}")
        self.assertFalse(PostReadLog.objects.exists())

    def test_post_and_comment_changes_roll_back_with_a_failed_event(self):
        with patch("community.views.record_post_event", side_effect=DatabaseError("outbox unavailable")):
            with self.assertRaises(DatabaseError):
                self.send("post", "/api/community/posts/", {"category": "free", "title": "실패", "body": "원문"})
        self.assertFalse(Post.objects.exists())
        post = Post.objects.create(author=self.user, category="free", title="글", body="원문")
        with patch("community.views.record_comment_event", side_effect=DatabaseError("outbox unavailable")):
            with self.assertRaises(DatabaseError):
                self.send("post", f"/api/community/posts/{post.id}/comments/", {"body": "실패 댓글"})
        self.assertFalse(Comment.objects.exists())

    def test_external_link_open_log_and_event_commit_together(self):
        content = ContentItem.objects.get(code="gomoku")
        self.assertEqual(self.client.get("/content/gomoku/open/").status_code, 302)
        row = EventOutbox.objects.get(event_type="content_open_requested")
        self.assertEqual(row.raw_data["content_uuid"], str(content.pk))
        self.assertEqual(row.raw_data["domain"], "REST")
        with patch.object(EventOutbox.objects, "create", side_effect=DatabaseError("outbox unavailable")):
            with self.assertRaises(DatabaseError):
                self.client.get("/content/gomoku/open/")
        self.assertEqual(ContentOpenRequestLog.objects.count(), 1)

    def test_answers_and_points_are_events_in_the_answer_transaction(self):
        for i in range(10):
            question = Question.objects.create(subject="python", question_type="multiple_choice", difficulty="beginner",
                                               category="syntax", question_text=f"Question {i}", correct_answer="one", explanation="first")
            for n, text in enumerate(["one", "two", "three", "four"], 1):
                QuestionChoice.objects.create(question=question, choice_no=n, choice_text=text)
        response = self.send("post", "/api/learning/sessions/", {"subject": "python", "question_type": "multiple_choice", "difficulty": "beginner"})
        self.assertEqual(response.status_code, 201, response.content)
        session = response.json()["session_id"]
        shown = self.client.get(f"/api/learning/sessions/{session}/questions/1/").json()["question"]
        path = f"/api/learning/sessions/{session}/questions/1/answers/"
        wrong = next(c for c in shown["choices"] if c["choice_text"] == "two")
        right = next(c for c in shown["choices"] if c["choice_text"] == "one")
        first = {"answer": "two", "choice_uuid": wrong["choice_uuid"], "submission_request_uuid": str(uuid.uuid4())}
        self.assertEqual(self.send("post", path, first).status_code, 201)
        self.assertEqual(self.send("post", path, first).status_code, 201)
        second = {"answer": "one", "choice_uuid": right["choice_uuid"], "submission_request_uuid": str(uuid.uuid4())}
        self.assertEqual(self.send("post", path, second).status_code, 201)
        answers = list(EventOutbox.objects.filter(event_type="quiz_answered").order_by("occurred_at"))
        self.assertEqual([row.raw_data["is_correct"] for row in answers], [False, True])
        self.assertEqual(answers[1].raw_data["question_type"], "MULTIPLE_CHOICE")
        self.assertEqual(answers[1].payload_json["raw_manifest"]["schema_version"], "1.1")
        granted = EventOutbox.objects.get(event_type="point_granted")
        self.assertEqual(granted.raw_data["attempt_uuid"], answers[1].raw_data["attempt_uuid"])
        self.assertEqual((granted.raw_data["delta"], granted.raw_data["balance_after"]), (20, 20))

    def test_http_logout_ends_the_platform_session_once(self):
        self.assertEqual(self.client.post("/accounts/logout/").status_code, 302)
        self.client.post("/accounts/logout/")
        row = EventOutbox.objects.get(event_type="session_ended")
        self.assertEqual((row.raw_data["session_uuid"], row.raw_data["end_reason"]), (self.session_uuid, "LOGOUT"))
