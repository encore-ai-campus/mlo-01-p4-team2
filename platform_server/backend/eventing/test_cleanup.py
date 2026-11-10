from datetime import datetime, timedelta, timezone as datetime_timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import uuid
from zoneinfo import ZoneInfo

from django.db import connection
from django.db.models.query import QuerySet
from django.test import TransactionTestCase, override_settings

from . import cleanup
from .models import EventOutbox


@override_settings(CAMPUS_OUTBOX_CLEANUP_TIME="08:30", KAFKA_BOOTSTRAP_SERVERS="",
                   CAMPUS_SESSION_CLEANUP_ENABLED=False)
class OutboxCleanupTests(TransactionTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "outbox-cleanup.json"
        self.now = datetime(2026, 10, 22, 8, 30, tzinfo=ZoneInfo("Asia/Seoul"))
        EventOutbox.objects.all().delete()

    def row(self, published_at):
        return EventOutbox(event_uuid=uuid.uuid4(), event_type="movement_observed",
                           topic="raw.movement", message_key=uuid.uuid4(), raw_data={},
                           occurred_at=self.now, published_at=published_at)

    def test_first_boot_waits_until_0830_and_uses_korean_date(self):
        self.assertFalse(cleanup.run_due_cleanup(self.now - timedelta(seconds=1), self.path))
        self.assertFalse(self.path.exists())
        self.assertTrue(cleanup.run_due_cleanup(self.now.astimezone(datetime_timezone.utc), self.path))
        state = json.loads(self.path.read_text())
        self.assertEqual(state["completed_day"], "2026-10-22")
        self.assertFalse(cleanup.run_due_cleanup(self.now + timedelta(hours=2), self.path))

    def test_next_day_waits_until_schedule_and_catches_up_after_restart(self):
        cleanup.run_due_cleanup(self.now, self.path)
        tomorrow = self.now + timedelta(days=1)
        self.assertFalse(cleanup.run_due_cleanup(tomorrow - timedelta(seconds=1), self.path))
        self.assertTrue(cleanup.run_due_cleanup(tomorrow + timedelta(hours=4), self.path))
        self.assertEqual(json.loads(self.path.read_text())["completed_day"], "2026-10-23")

    def test_removes_only_acknowledged_rows_at_or_before_scheduled_cutoff(self):
        rows = [self.row(self.now - timedelta(days=1)), self.row(self.now),
                self.row(None), self.row(self.now + timedelta(seconds=1))]
        EventOutbox.objects.bulk_create(rows)
        with self.assertLogs(cleanup.logger, level="INFO") as logs:
            cleanup.run_due_cleanup(self.now + timedelta(hours=4), self.path)
        self.assertSetEqual(set(EventOutbox.objects.values_list("pk", flat=True)),
                            {rows[2].pk, rows[3].pk})
        self.assertIn("removed 2 rows", logs.output[0])

    def test_rechecks_acknowledgement_before_delete(self):
        row = self.row(self.now)
        row.save()
        original = QuerySet.delete

        def clear_acknowledgement(queryset):
            EventOutbox.objects.filter(pk=row.pk).update(published_at=None)
            return original(queryset)

        with mock.patch.object(QuerySet, "delete", autospec=True, side_effect=clear_acknowledgement):
            cleanup.run_due_cleanup(self.now, self.path)
        row.refresh_from_db()
        self.assertIsNone(row.published_at)

    def test_rows_completed_during_cleanup_remain(self):
        self.row(self.now).save()
        newly_completed = self.row(self.now + timedelta(seconds=1))
        original = QuerySet.delete

        def complete_another_row(queryset):
            newly_completed.save()
            return original(queryset)

        with mock.patch.object(QuerySet, "delete", autospec=True, side_effect=complete_another_row):
            cleanup.run_due_cleanup(self.now, self.path)
        self.assertEqual(list(EventOutbox.objects.values_list("pk", flat=True)), [newly_completed.pk])

    def test_batches_commit_and_failure_can_resume_without_completing_day(self):
        EventOutbox.objects.bulk_create([self.row(self.now) for _ in range(1001)])
        original = QuerySet.delete
        batch_sizes = []

        def fail_second_batch(queryset):
            self.assertFalse(connection.in_atomic_block)
            batch_sizes.append(queryset.count())
            if len(batch_sizes) == 2:
                raise RuntimeError("temporary database failure")
            return original(queryset)

        with mock.patch.object(QuerySet, "delete", autospec=True, side_effect=fail_second_batch):
            with self.assertRaisesMessage(RuntimeError, "temporary database failure"):
                cleanup.run_due_cleanup(self.now, self.path)
        self.assertEqual(batch_sizes, [1000, 1])
        self.assertEqual(EventOutbox.objects.count(), 1)
        self.assertFalse(self.path.exists())
        self.assertTrue(cleanup.run_due_cleanup(self.now + timedelta(minutes=1), self.path))
        self.assertEqual(EventOutbox.objects.count(), 0)
        self.assertTrue(self.path.exists())

    def test_cleanup_loop_retries_after_60_seconds(self):
        stop = mock.Mock()
        stop.is_set.side_effect = [False, True]
        with mock.patch.object(cleanup, "run_due_cleanup", side_effect=RuntimeError("database unavailable")), \
                mock.patch.object(cleanup, "close_old_connections"), \
                mock.patch.object(cleanup.connections, "close_all"), \
                self.assertLogs(cleanup.logger, level="ERROR"):
            cleanup.cleanup_loop(stop)
        stop.wait.assert_called_once_with(60)

    def test_only_one_worker_starts_without_broker_or_session_cleanup(self):
        with mock.patch.object(cleanup, "_worker", None), \
                mock.patch.object(cleanup.threading, "Thread") as thread:
            thread.return_value.is_alive.return_value = True
            first = cleanup.start_outbox_cleanup()
            self.assertIs(cleanup.start_outbox_cleanup(), first)
            thread.assert_called_once()
            first.start.assert_called_once()
