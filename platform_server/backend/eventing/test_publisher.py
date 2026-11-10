"""Publisher checks without a broker or wall-clock waits."""
import uuid
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from . import publisher
from .models import EventOutbox


class Clock:
    def __init__(self):
        self.now = 0.0

    def advance(self, seconds):
        self.now += seconds


class Stop:
    def __init__(self, clock, deadline=10):
        self.clock, self.deadline, self.stopped = clock, deadline, False

    def is_set(self):
        return self.stopped or self.clock.now >= self.deadline

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.clock.advance(seconds)
        return self.is_set()


class Producer:
    def __init__(self, clock, stop, *, stop_after=None, failures=None, buffer_full=0, acknowledge=True):
        self.clock, self.stop = clock, stop
        self.stop_after, self.failures = stop_after, dict(failures or {})
        self.buffer_full, self.acknowledge = buffer_full, acknowledge
        self.accepted, self.pending = [], []
        self.max_pending = 0

    def produce(self, topic, *, key, value, on_delivery):
        if self.buffer_full:
            self.buffer_full -= 1
            raise BufferError("producer queue full")
        import json
        event_uuid = json.loads(value)["event_uuid"]
        self.accepted.append((event_uuid, key, self.clock.now))
        self.pending.append((event_uuid, on_delivery))
        self.max_pending = max(self.max_pending, len(self.pending))
        if self.stop_after and len(self.accepted) >= self.stop_after:
            self.stop.set()

    def poll(self, seconds):
        self.clock.advance(seconds)
        if self.acknowledge:
            self.deliver()

    def deliver(self):
        pending, self.pending = self.pending, []
        for event_uuid, callback in pending:
            error = self.failures.pop(event_uuid, None)
            callback(error, None)

    def flush(self, seconds):
        if self.acknowledge:
            self.deliver()
        return len(self.pending)


@override_settings(CAMPUS_OUTBOX_PUBLISH_RATE_PER_SECOND=100, CAMPUS_OUTBOX_IDLE_POLL_SECONDS=0.25)
class OutboxPublisherTests(TestCase):
    def row(self, key=None, *, payload=True):
        event_uuid = uuid.uuid4()
        return EventOutbox.objects.create(
            event_uuid=event_uuid, event_type="session_started", topic="raw.account",
            message_key=key or uuid.uuid4(), occurred_at=timezone.now(), raw_data={},
            payload_json={"event_uuid": str(event_uuid)} if payload else None,
        )

    def run_publisher(self, producer):
        with patch.object(publisher.time, "monotonic", side_effect=lambda: producer.clock.now), \
                patch.object(publisher, "_wait_for_work", side_effect=lambda stop, seconds: stop.wait(seconds)), \
                patch.object(publisher, "close_old_connections"), \
                patch.object(publisher.connections, "close_all"):
            publisher._publish_pending(producer, producer.stop)

    def producer(self, **kwargs):
        clock = Clock()
        return Producer(clock, Stop(clock), **kwargs)

    def test_batches_different_keys_and_does_not_hide_them_behind_one_users_rows(self):
        key = uuid.uuid4()
        repeated = [self.row(key) for _ in range(101)]
        other = self.row()
        producer = self.producer(stop_after=2)
        with CaptureQueriesContext(connection) as queries:
            self.run_publisher(producer)
        updates = [query["sql"] for query in queries if query["sql"].startswith("UPDATE")]
        self.assertEqual(len(updates), 1)
        self.assertEqual([entry[0] for entry in producer.accepted],
                         [str(repeated[0].event_uuid), str(other.event_uuid)])
        self.assertEqual(producer.max_pending, 2)
        self.assertEqual(EventOutbox.objects.filter(published_at__isnull=False).count(), 2)

    def test_limits_every_rolling_second_and_inflight_to_one_hundred(self):
        for _ in range(201):
            self.row()
        producer = self.producer(stop_after=201)
        self.run_publisher(producer)
        times = [entry[2] for entry in producer.accepted]
        self.assertEqual(len(times), 201)
        self.assertLessEqual(producer.max_pending, 100)
        self.assertTrue(all(later - earlier >= 1 for earlier, later in zip(times, times[100:])))
        self.assertEqual(EventOutbox.objects.filter(published_at__isnull=False).count(), 201)

    def test_failed_key_backs_off_while_other_keys_finish_and_order_is_preserved(self):
        first = self.row()
        second = self.row(first.message_key)
        other = self.row()
        producer = self.producer(stop_after=4, failures={str(first.event_uuid): "broker rejected"})
        self.run_publisher(producer)
        accepted = [entry[0] for entry in producer.accepted]
        self.assertEqual(accepted, [str(first.event_uuid), str(other.event_uuid),
                                    str(first.event_uuid), str(second.event_uuid)])
        self.assertGreaterEqual(producer.accepted[2][2], 1)
        first.refresh_from_db()
        self.assertEqual(first.retry_count, 1)
        self.assertEqual(EventOutbox.objects.filter(published_at__isnull=False).count(), 3)

    def test_bad_payload_stays_pending_and_does_not_block_other_keys(self):
        bad, good = self.row(payload=False), self.row()
        producer = self.producer(stop_after=1)
        with patch.object(publisher, "build_envelope", side_effect=ValueError("invalid payload")):
            self.run_publisher(producer)
        bad.refresh_from_db()
        good.refresh_from_db()
        self.assertIsNone(bad.published_at)
        self.assertEqual(bad.retry_count, 1)
        self.assertIsNotNone(good.published_at)

    def test_full_producer_queue_waits_without_counting_unaccepted_record(self):
        row = self.row()
        producer = self.producer(stop_after=1, buffer_full=1)
        self.run_publisher(producer)
        self.assertEqual(len(producer.accepted), 1)
        self.assertGreaterEqual(producer.accepted[0][2], 0.1)
        row.refresh_from_db()
        self.assertEqual(row.retry_count, 0)
        self.assertIsNotNone(row.published_at)

    def test_database_ack_failure_keeps_key_occupied_and_never_resends_acknowledged_record(self):
        first, second = self.row(), self.row()
        second.message_key = first.message_key
        second.save(update_fields=["message_key"])
        producer = self.producer(stop_after=2)
        original = publisher._record_deliveries
        failed = False

        def save(inflight, delivered, retry_at):
            nonlocal failed
            if delivered and not failed:
                failed = True
                raise RuntimeError("database temporarily unavailable")
            return original(inflight, delivered, retry_at)

        with patch.object(publisher, "_record_deliveries", side_effect=save):
            self.run_publisher(producer)
        self.assertTrue(failed)
        self.assertEqual([entry[0] for entry in producer.accepted],
                         [str(first.event_uuid), str(second.event_uuid)])
        self.assertGreaterEqual(producer.accepted[1][2], 1)
        self.assertEqual(EventOutbox.objects.filter(published_at__isnull=False).count(), 2)

    def test_unknown_delivery_is_not_retried_after_an_application_timeout(self):
        row = self.row()
        producer = self.producer(acknowledge=False)
        producer.stop.deadline = 130
        self.run_publisher(producer)
        row.refresh_from_db()
        self.assertEqual(len(producer.accepted), 1)
        self.assertIsNone(row.published_at)
        self.assertEqual(row.retry_count, 0)

    def test_shutdown_records_flush_acknowledgement_and_preserves_unknown_record(self):
        first, second = self.row(), self.row()
        producer = self.producer(stop_after=2, acknowledge=False)

        def flush(seconds):
            event_uuid, callback = producer.pending.pop(0)
            callback(None, None)
            return len(producer.pending)

        producer.flush = flush
        self.run_publisher(producer)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNotNone(first.published_at)
        self.assertIsNone(second.published_at)

    def test_producer_configuration_enables_batching_and_idempotence(self):
        with override_settings(KAFKA_BOOTSTRAP_SERVERS="unused:9092", KAFKA_SECURITY_PROTOCOL="",
                               KAFKA_SASL_MECHANISM="", KAFKA_SASL_USERNAME="", KAFKA_SASL_PASSWORD=""):
            config = publisher.producer_config()
        self.assertTrue(config["enable.idempotence"])
        self.assertEqual(config["acks"], "all")
        self.assertEqual(config["batch.num.messages"], 100)
        self.assertGreater(config["linger.ms"], 0)
