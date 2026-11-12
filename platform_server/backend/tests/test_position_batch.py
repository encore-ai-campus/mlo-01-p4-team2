import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
import threading
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Position, Profile
from campus import consumers, position_batch
from campus.position_batch import PositionBatch
from campus.positions import DEFAULT_POSITION, movement_event_due_at, save_position, save_positions, flush_movement_event
from eventing.models import EventOutbox, PlatformSession
from tests.factories import create_profile


def make_users(count):
    users = []
    for i in range(count):
        user = get_user_model().objects.create_user(username=f'batch-user-{i}')
        create_profile(user=user, nickname=f'Batch {i}')
        users.append(user)
    return users


def position(x=300):
    return {'floor': 'campus', 'x': x, 'y': 400.25, 'dir': 'right'}


class PositionBatchStorageTests(TestCase):
    def setUp(self):
        self.users = make_users(10)

    def rows(self, users=None, observed_at=None, sessions=False):
        observed_at = observed_at or timezone.now()
        rows = []
        for i, user in enumerate(users or self.users):
            session = PlatformSession.objects.create(user=user, started_at=observed_at, last_seen_at=observed_at) if sessions else None
            rows.append((user, position(300+i), observed_at, session.pk if session else None))
        return rows

    def test_ten_users_use_one_position_update_and_fewer_queries(self):
        rows = self.rows()
        with CaptureQueriesContext(connection) as batch:
            save_positions(rows)
        updates = [q['sql'] for q in batch if q['sql'].startswith('UPDATE "accounts_position"')]
        self.assertEqual(len(updates), 1)
        self.assertIn('CASE', updates[0])
        newer = timezone.now()
        with CaptureQueriesContext(connection) as individual:
            for user, state, _, session in rows:
                save_position(user, state, newer, session_uuid=session)
                movement_event_due_at(user)
        self.assertLess(len(batch), len(individual))
        for user, state, observed_at, _ in rows:
            record = Position.objects.get(pk=user.pk)
            self.assertEqual(record.last_position, state)
            self.assertEqual(record.last_position_at, newer)

    def test_limit_is_enforced_before_any_write(self):
        rows = self.rows()
        with self.assertRaises(ValueError):
            save_positions(rows + [rows[0]])
        self.assertFalse(Position.objects.filter(last_position__isnull=False).exists())

    def test_latest_snapshot_wins_including_duplicate_users(self):
        user = self.users[0]
        now = timezone.now()
        save_positions([(user, position(301), now, None),
                        (user, position(302), now+timedelta(seconds=1), None)])
        save_positions([(user, position(100), now-timedelta(seconds=1), None)])
        self.assertEqual(Position.objects.get(pk=user.pk).last_position, position(302))

    def test_invalid_and_inactive_users_do_not_write(self):
        user = self.users[0]
        user.is_active = False
        save_positions([(user, position(), timezone.now(), None),
                        (self.users[1], {**position(), 'x': True}, timezone.now(), None)])
        self.assertFalse(Position.objects.filter(last_position__isnull=False).exists())

    def test_existing_repair_path_handles_missing_rows(self):
        Position.objects.filter(pk=self.users[0].pk).delete()
        Profile.objects.filter(pk=self.users[1].pk).delete()
        rows = self.rows(self.users[:2])
        save_positions(rows)
        for user, state, _, _ in rows:
            self.assertEqual(Position.objects.get(pk=user.pk).last_position, state)

    def test_exit_coordinates_are_normalized(self):
        save_positions([(self.users[0], {**DEFAULT_POSITION, 'y': 980}, timezone.now(), None)])
        self.assertEqual(Position.objects.get(pk=self.users[0].pk).last_position, DEFAULT_POSITION)

    def test_movement_events_sample_the_latest_pending_position(self):
        user = self.users[0]
        now = timezone.now()
        session = PlatformSession.objects.create(user=user, started_at=now, last_seen_at=now)
        with patch('campus.positions.timezone.now', return_value=now):
            save_positions([(user, position(301), now, session.pk)])
        with patch('campus.positions.timezone.now', return_value=now+timedelta(seconds=5)):
            due = save_positions([(user, position(302), now+timedelta(seconds=5), session.pk)])
        with patch('campus.positions.timezone.now', return_value=now+timedelta(seconds=6)):
            save_positions([(user, position(303), now+timedelta(seconds=6), session.pk)])
        self.assertEqual(EventOutbox.objects.filter(event_type='movement_observed').count(), 1)
        self.assertEqual(due[user.pk], now+timedelta(seconds=30))
        with patch('campus.positions.timezone.now', return_value=now+timedelta(seconds=31)):
            self.assertTrue(flush_movement_event(user))
        events = list(EventOutbox.objects.filter(event_type='movement_observed').order_by('occurred_at'))
        self.assertEqual([event.raw_data['x'] for event in events], [301.0, 303.0])
        self.assertEqual(events[1].occurred_at, now+timedelta(seconds=6))

    def test_failed_bulk_write_rolls_back_positions_and_outbox(self):
        rows = self.rows(self.users[:2], sessions=True)
        with patch.object(Position.objects, 'bulk_update', side_effect=DatabaseError('write failed')):
            with self.assertRaises(DatabaseError):
                save_positions(rows)
        self.assertFalse(Position.objects.filter(last_position__isnull=False).exists())
        self.assertFalse(EventOutbox.objects.filter(event_type='movement_observed').exists())
        save_positions(rows)
        self.assertEqual(EventOutbox.objects.filter(event_type='movement_observed').count(), 2)
        save_positions(rows)
        self.assertEqual(EventOutbox.objects.filter(event_type='movement_observed').count(), 2)

    def test_revoked_session_is_filtered_before_batch_write(self):
        from accounts.session_policy import start_login
        user = self.users[0]
        self.client.force_login(user)
        session = self.client.session
        start_login(session, user)
        session.save()
        consumer = consumers.CampusConsumer()
        consumer.user = user
        consumer.session_key = session.session_key
        consumer.platform_session_uuid = session['platform_session_uuid']
        consumer.policy_status = None
        from django.contrib.sessions.models import Session
        Session.objects.filter(session_key=session.session_key).delete()
        result = position_batch.store_positions_now([(consumer, position(), timezone.now())])
        self.assertEqual(result, [None])
        self.assertIsNone(Position.objects.get(pk=user.pk).last_position)


class PositionBatchQueueTests(SimpleTestCase):
    async def test_twenty_three_requests_split_ten_ten_three(self):
        batch = PositionBatch()
        sizes = []
        async def run(fn, requests):
            sizes.append(len(requests))
            return [row[0] for row in requests]
        with patch('campus.position_batch.socket_db.run', side_effect=run):
            results = await asyncio.gather(*(batch.save(i, position(), None) for i in range(23)))
        self.assertEqual(sizes, [10, 10, 3])
        self.assertEqual(results, list(range(23)))
        self.assertIsNone(batch.task)

    async def test_single_request_does_not_wait_to_fill_batch(self):
        batch = PositionBatch()
        with patch('campus.position_batch.socket_db.run', AsyncMock(return_value=[None])):
            await asyncio.wait_for(batch.save('one', position(), None), .5)

    async def test_cancelled_waiter_does_not_cancel_other_saves(self):
        batch = PositionBatch()
        entered, release = asyncio.Event(), asyncio.Event()
        async def run(fn, requests):
            entered.set()
            await release.wait()
            return [None] * len(requests)
        with patch('campus.position_batch.socket_db.run', side_effect=run):
            first = asyncio.create_task(batch.save('first', position(), None))
            second = asyncio.create_task(batch.save('second', position(), None))
            await entered.wait()
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            release.set()
            await asyncio.wait_for(second, .5)
        self.assertIsNone(batch.task)

    async def test_failure_releases_waiters_and_next_request_can_retry(self):
        batch = PositionBatch()
        with patch('campus.position_batch.socket_db.run', AsyncMock(side_effect=DatabaseError('offline'))):
            results = await asyncio.gather(*(batch.save(i, position(), None) for i in range(2)), return_exceptions=True)
        self.assertTrue(all(isinstance(result, DatabaseError) for result in results))
        with patch('campus.position_batch.socket_db.run', AsyncMock(return_value=[None])):
            await batch.save('retry', position(), None)
        self.assertIsNone(batch.task)
        self.assertFalse(batch.pending)


class PositionBatchConsumerTests(SimpleTestCase):
    def setUp(self):
        self.room = consumers.CampusRoom()
        self.patcher = patch.object(consumers, 'room', self.room)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.room.position_batch.save = AsyncMock(return_value=None)
        self.consumer = consumers.CampusConsumer()
        self.consumer.saving = None
        self.consumer.position_dirty = True
        self.consumer.pending_floor_transfer = None
        self.consumer.movement_event_due_at = None
        self.consumer.last_checkpoint = -10000
        self.consumer.state = position()
        self.consumer.position_changed_at = timezone.now()
        self.consumer.store_position = AsyncMock(return_value=None)

    async def test_regular_checkpoint_batches_and_clears_only_saved_snapshot(self):
        c = self.consumer
        await c.checkpoint(wait=False)
        await c.saving
        self.room.position_batch.save.assert_awaited_once()
        c.store_position.assert_not_awaited()
        self.assertFalse(c.position_dirty)

    async def test_newer_position_stays_dirty_while_old_batch_finishes(self):
        c = self.consumer
        async def update(*args):
            c.position_changed_at += timedelta(seconds=1)
            c.state = position(500)
        self.room.position_batch.save.side_effect = update
        await c.checkpoint(wait=False)
        await c.saving
        self.assertTrue(c.position_dirty)

    async def test_forced_and_floor_transfer_checkpoints_remain_immediate(self):
        c = self.consumer
        c.pending_floor_transfer = ('campus', 'rooftop')
        await c.checkpoint(force=True)
        c.store_position.assert_awaited_once()
        self.room.position_batch.save.assert_not_awaited()

    async def test_failed_batch_keeps_latest_position_for_retry(self):
        c = self.consumer
        self.room.position_batch.save.side_effect = DatabaseError('offline')
        with self.assertLogs('campus.consumers', level='ERROR'):
            await c.checkpoint(wait=False)
            await c.saving
        self.assertTrue(c.position_dirty)
        c.last_checkpoint = -10000
        self.room.position_batch.save.side_effect = None
        await c.checkpoint(wait=False)
        await c.saving
        self.assertFalse(c.position_dirty)

    async def test_event_only_flush_uses_existing_path(self):
        c = self.consumer
        c.position_dirty = False
        c.movement_event_due_at = timezone.now()-timedelta(seconds=1)
        await c.checkpoint(wait=False)
        await c.saving
        c.store_position.assert_awaited_once_with(None, c.position_changed_at, None)
        self.room.position_batch.save.assert_not_awaited()


class PositionBatchConcurrencyTests(TransactionTestCase):
    def test_overlapping_batches_keep_the_newest_position_without_deadlock(self):
        users = make_users(2)
        gate = threading.Barrier(2)
        now = timezone.now()
        def write(reverse):
            try:
                gate.wait(timeout=5)
                stamp = now+timedelta(seconds=1 if reverse else 0)
                ordered = list(reversed(users)) if reverse else users
                save_positions([(u, position(501 if reverse else 301), stamp, None) for u in ordered])
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(write, reverse) for reverse in (False, True)]
            for future in futures:
                future.result(timeout=10)
        self.assertEqual(list(Position.objects.values_list('last_position_at', flat=True)), [now+timedelta(seconds=1)]*2)
        self.assertTrue(all(row.last_position['x']==501 for row in Position.objects.all()))
