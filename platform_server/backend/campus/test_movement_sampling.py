from datetime import timedelta
import time
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import Profile
from eventing.events import record_floor_transfer
from eventing.models import EventOutbox
from .positions import movement_event_due_at, save_position


@override_settings(CAMPUS_MOVEMENT_EVENT_INTERVAL_SECONDS=30, CAMPUS_KAFKA_ENABLED=False)
class MovementSamplingTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = get_user_model().objects.create_user(username="movement-sampling")
        self.session_uuid = str(uuid4())
        self.position = {"floor": "campus", "x": 10.0, "y": 20.0, "dir": "up"}
        self.profile = Profile.objects.create(
            user=self.user, nickname="movement", last_position=self.position,
            last_position_at=self.now - timedelta(seconds=1),
        )

    def save_at(self, seconds, x, *, observed_at=None, user=None, session_uuid=None, direction="up"):
        now = self.now + timedelta(seconds=seconds)
        with patch("campus.positions.timezone.now", return_value=now):
            return save_position(
                user or self.user, {**self.position, "x": x, "dir": direction},
                observed_at or now, session_uuid=session_uuid or self.session_uuid,
            )

    def test_five_second_positions_keep_only_one_pending_sample(self):
        self.assertTrue(self.save_at(0, 11))
        self.assertTrue(self.save_at(5, 12))
        self.assertTrue(self.save_at(10, 13))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_position["x"], 13)
        self.assertEqual(self.profile.last_position_at, self.now + timedelta(seconds=10))
        self.assertEqual(self.profile.movement_event_pending["position"]["x"], 13)
        self.assertEqual(EventOutbox.objects.filter(event_type="movement_observed").count(), 1)

    def test_stopped_position_flushes_at_thirty_seconds_with_original_observation(self):
        self.save_at(0, 11)
        self.save_at(5, 12)
        observation = self.now + timedelta(seconds=5)
        self.assertFalse(self.save_at(29, 12, observed_at=observation))
        self.assertEqual(EventOutbox.objects.count(), 1)
        self.assertFalse(self.save_at(30, 12, observed_at=observation))
        sample = EventOutbox.objects.get(occurred_at=observation)
        self.assertEqual(sample.raw_data["x"], 12)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.movement_event_pending)
        self.assertEqual(self.profile.last_movement_event_at, self.now + timedelta(seconds=30))
        self.assertIsNone(movement_event_due_at(self.user))

    def test_direction_change_preserves_pending_movement_time(self):
        self.save_at(0, 11)
        self.save_at(5, 12)
        self.save_at(10, 12, direction="left")
        self.save_at(30, 12, direction="left", observed_at=self.now + timedelta(seconds=10))
        self.assertTrue(EventOutbox.objects.filter(occurred_at=self.now + timedelta(seconds=5)).exists())
        self.assertEqual(EventOutbox.objects.count(), 2)

    def test_reconnect_keeps_original_session_and_gate(self):
        self.save_at(0, 11)
        self.save_at(5, 12)
        reconnected = get_user_model().objects.get(pk=self.user.pk)
        self.assertEqual(movement_event_due_at(reconnected), self.now + timedelta(seconds=30))
        self.save_at(10, 12, user=reconnected, session_uuid=str(uuid4()))
        self.assertEqual(EventOutbox.objects.count(), 1)
        self.save_at(30, 12, observed_at=self.now + timedelta(seconds=10), user=reconnected, session_uuid=str(uuid4()))
        self.assertEqual(EventOutbox.objects.latest("occurred_at").raw_data["session_uuid"], self.session_uuid)

    def test_reconnected_idle_flush_cannot_overwrite_another_workers_newer_position(self):
        from .consumers import CampusConsumer

        self.save_at(0, 11)
        self.save_at(5, 12)
        consumer = CampusConsumer()
        consumer.state = {**self.position, "x": 12}
        consumer.position_changed_at = None
        consumer.session_user = Mock(return_value=self.user)
        self.save_at(10, 13, user=get_user_model().objects.get(pk=self.user.pk))
        with patch("campus.positions.timezone.now", return_value=self.now + timedelta(seconds=30)):
            self.assertIsNone(consumer.store_position_now(None, consumer.position_changed_at))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_position["x"], 13)
        self.assertEqual(self.profile.last_position_at, self.now + timedelta(seconds=10))
        sample = EventOutbox.objects.get(occurred_at=self.now + timedelta(seconds=10))
        self.assertEqual(sample.raw_data["x"], 13)
        self.assertIsNone(self.profile.movement_event_pending)

    def test_stale_snapshot_cannot_replace_saved_or_pending_position(self):
        self.save_at(0, 11)
        self.save_at(5, 12)
        self.assertFalse(self.save_at(10, 100, observed_at=self.now - timedelta(seconds=1)))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_position["x"], 12)
        self.assertEqual(self.profile.movement_event_pending["position"]["x"], 12)

    def test_outbox_failure_rolls_back_position_and_sampling_state(self):
        self.save_at(0, 11)
        self.save_at(5, 12)
        with patch("eventing.events.enqueue_event", side_effect=DatabaseError("unavailable")):
            with self.assertRaises(DatabaseError):
                self.save_at(30, 13)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_position["x"], 12)
        self.assertEqual(self.profile.movement_event_pending["position"]["x"], 12)
        self.assertEqual(self.profile.last_movement_event_at, self.now)
        self.assertEqual(EventOutbox.objects.count(), 1)

    def test_floor_transfer_is_immediate_within_movement_sample_interval(self):
        self.save_at(0, 11)
        now = self.now + timedelta(seconds=5)
        with patch("campus.positions.timezone.now", return_value=now):
            self.assertTrue(record_floor_transfer(
                self.user, self.session_uuid, "campus", "rooftop",
                {**self.position, "floor": "rooftop"}, now,
            ))
        self.assertEqual(EventOutbox.objects.filter(event_type="floor_transferred").count(), 1)
        self.assertEqual(EventOutbox.objects.filter(event_type="movement_observed").count(), 1)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_position["floor"], "rooftop")


class MovementSamplingCheckpointTests(SimpleTestCase):
    def consumer(self, due_at=None):
        from .consumers import CampusConsumer

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
