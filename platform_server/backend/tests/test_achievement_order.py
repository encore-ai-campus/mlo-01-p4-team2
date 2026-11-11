"""Prepared for later execution; uses a separate Django test database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib import import_module
from threading import Barrier
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from event_story.achievement_services import AD_ID, RECIPE_ID, achievement_snapshot, complete_achievement
from event_story.models import EventProgress
from event_story.test_achievements import seed_definitions


class AchievementOrderTests(TestCase):
    def setUp(self):
        seed_definitions()
        self.alice = get_user_model().objects.create_user(username='order-alice')
        self.bob = get_user_model().objects.create_user(username='order-bob')
        self.now = timezone.now()

    def test_per_achievement_order_and_duplicate_keep_first_completion(self):
        self.assertTrue(complete_achievement(self.alice, AD_ID, self.now))
        self.assertTrue(complete_achievement(self.bob, AD_ID, self.now))
        self.assertTrue(complete_achievement(self.bob, RECIPE_ID, self.now))
        self.assertFalse(complete_achievement(self.alice, AD_ID, self.now + timedelta(seconds=5)))
        row = EventProgress.objects.get(user=self.alice, event_id=AD_ID)
        self.assertEqual((row.completion_order, row.completed_at), (1, self.now))
        self.assertEqual(EventProgress.objects.get(user=self.bob, event_id=AD_ID).completion_order, 2)
        self.assertEqual(EventProgress.objects.get(user=self.bob, event_id=RECIPE_ID).completion_order, 1)
        badge = next(r for r in achievement_snapshot(self.alice) if r['event_uuid'] == str(AD_ID))
        self.assertEqual(badge['completion_order'], 1)

    def test_rollback_does_not_consume_rank(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            complete_achievement(self.alice, AD_ID, self.now)
            raise RuntimeError('rollback')
        complete_achievement(self.bob, AD_ID, self.now)
        self.assertEqual(EventProgress.objects.get(user=self.bob, event_id=AD_ID).completion_order, 1)

    def test_historical_backfill_uses_time_and_is_idempotent(self):
        later = EventProgress.objects.create(user=self.alice, event_id=AD_ID, status='COMPLETED',
                                             completed_at=self.now + timedelta(seconds=1))
        earlier = EventProgress.objects.create(user=self.bob, event_id=AD_ID, status='COMPLETED',
                                               completed_at=self.now)
        pending = EventProgress.objects.create(user=self.alice, event_id=RECIPE_ID)
        backfill = import_module('event_story.migrations.0008_easter_egg_completion_order').backfill
        for _ in range(2):
            backfill(apps, SimpleNamespace(connection=connection))
        for row in (earlier, later, pending):
            row.refresh_from_db()
        self.assertEqual((earlier.completion_order, later.completion_order), (1, 2))
        self.assertIsNone(pending.completion_order)
        self.assertEqual(earlier.completed_at, self.now)


class ConcurrentAchievementOrderTests(TransactionTestCase):
    def test_two_accounts_get_distinct_orders_and_both_receive_achievement(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks are required.')
        seed_definitions()
        users = [get_user_model().objects.create_user(username='rank-'+str(i)).pk for i in range(2)]
        start = Barrier(2)

        def finish(user_id):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=user_id)
                start.wait(timeout=5)
                return complete_achievement(user, AD_ID, timezone.now())
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(finish, user_id) for user_id in users]
            self.assertEqual([future.result(timeout=10) for future in futures], [True, True])
        self.assertEqual(list(EventProgress.objects.filter(event_id=AD_ID).order_by(
            'completion_order').values_list('completion_order', flat=True)), [1, 2])
