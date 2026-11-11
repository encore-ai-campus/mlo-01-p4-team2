from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from eventing.models import PlatformSession
from .achievement_services import (
    FRAGILE_ID, CS_ID, achievement_snapshot, complete_achievement, record_guide_talk,
)
from .game_services import GameError
from .models import EventDefinition, EventProgress
from .test_achievements import seed_definitions


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class FragileAchievementTests(TestCase):
    def setUp(self):
        seed_definitions()
        self.user = get_user_model().objects.create_user(username='fragile-owner')
        self.other = get_user_model().objects.create_user(username='fragile-other')
        self.session = PlatformSession.objects.create(
            user=self.user, started_at=timezone.now(), last_seen_at=timezone.now())

    def test_one_remaining_awards_once_and_keeps_original_asset_session_and_rank(self):
        result = record_guide_talk(self.user, 1, self.session.pk)
        self.assertEqual(result['newly_completed'], [str(FRAGILE_ID)])
        row = EventProgress.objects.get(user=self.user, event_id=FRAGILE_ID)
        first = (row.completed_at, row.completion_order, row.session_id)
        self.assertEqual((row.status, row.completion_order, row.session_id), ('COMPLETED', 1, self.session.pk))
        badge = next(b for b in result['achievements'] if b['event_uuid'] == str(FRAGILE_ID))
        self.assertEqual(badge['name'], '부서지기 직전')
        self.assertEqual(badge['image_url'], '/static/campus/assets/achievements/all-subjects.png')
        self.assertEqual(record_guide_talk(self.user, 1)['newly_completed'], [])
        row.refresh_from_db()
        self.assertEqual((row.completed_at, row.completion_order, row.session_id), first)
        self.assertEqual(EventProgress.objects.filter(user=self.user, event_id=FRAGILE_ID).count(), 1)
        self.assertFalse(next(b for b in achievement_snapshot(self.other)
                              if b['event_uuid'] == str(FRAGILE_ID))['achieved'])

    def test_only_exact_integer_one_qualifies(self):
        for remaining in (0, 2, 10, -1, 11, True, '1', 1.0, None):
            with self.subTest(remaining=remaining):
                self.assertEqual(record_guide_talk(self.user, remaining)['newly_completed'], [])
                self.assertFalse(EventProgress.objects.filter(event_id=FRAGILE_ID).exists())

    def test_closed_event_and_paused_campus_cannot_award(self):
        with patch('event_story.main.window', return_value={'phase': 'closed'}):
            with self.assertRaises(GameError):
                record_guide_talk(self.user, 1)
        with patch('campus_control.service.read_state', return_value={'paused': True}):
            with self.assertRaises(GameError):
                record_guide_talk(self.user, 1)
        self.assertFalse(EventProgress.objects.filter(event_id=FRAGILE_ID).exists())

    def test_failed_save_does_not_leave_an_award(self):
        with patch('event_story.achievement_services.EventProgress.objects.get_or_create', side_effect=DatabaseError('save failed')):
            with self.assertRaises(DatabaseError):
                record_guide_talk(self.user, 1)
        self.assertFalse(EventProgress.objects.filter(event_id=FRAGILE_ID).exists())

    def test_data_migration_replaces_only_the_old_award_and_preserves_other_progress(self):
        EventDefinition.objects.filter(pk=FRAGILE_ID).update(event_name='삼각지대 탈출')
        complete_achievement(self.user, FRAGILE_ID, timezone.now())
        complete_achievement(self.other, CS_ID, timezone.now())
        other = list(EventProgress.objects.exclude(event_id=FRAGILE_ID).values())
        migration = import_module('event_story.migrations.0013_fragile_pepero_achievement')
        migration.seed(apps, SimpleNamespace(connection=connection))
        self.assertEqual(EventDefinition.objects.get(pk=FRAGILE_ID).event_name, '부서지기 직전')
        self.assertFalse(EventProgress.objects.filter(event_id=FRAGILE_ID).exists())
        self.assertEqual(list(EventProgress.objects.exclude(event_id=FRAGILE_ID).values()), other)
        self.assertEqual(record_guide_talk(self.user, 1)['newly_completed'], [str(FRAGILE_ID)])


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class ConcurrentFragileAchievementTests(TransactionTestCase):
    def test_repeated_concurrent_talks_award_one_record(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks are required')
        seed_definitions()
        user = get_user_model().objects.create_user(username='fragile-concurrent')
        start = Barrier(2)

        def talk(_):
            close_old_connections()
            try:
                owner = get_user_model().objects.get(pk=user.pk)
                start.wait(timeout=8)
                return record_guide_talk(owner, 1)['newly_completed']
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(talk, range(2)))
        self.assertEqual(sum(str(FRAGILE_ID) in result for result in results), 1)
        self.assertEqual(list(EventProgress.objects.filter(user=user, event_id=FRAGILE_ID)
                              .values_list('status', 'completion_order')), [('COMPLETED', 1)])
