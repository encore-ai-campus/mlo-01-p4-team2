from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from eventing.models import PlatformSession
from .achievement_services import (
    FRAGILE_ID, CS_ID, achievement_snapshot, complete_achievement,
    complete_global_achievement, record_guide_talk,
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

    def test_guide_talk_does_not_award_before_hidden_story_is_accepted(self):
        result = record_guide_talk(self.user, 1, self.session.pk)
        self.assertEqual(result['newly_completed'], [])
        self.assertFalse(EventProgress.objects.filter(event_id=FRAGILE_ID).exists())
        badge = next(b for b in result['achievements'] if b['event_uuid'] == str(FRAGILE_ID))
        self.assertEqual(badge['name'], '부서지기 직전')
        self.assertEqual(badge['image_url'], '/static/campus/assets/achievements/kkuwang-last-bite-gift-v1.webp')
        self.assertEqual(record_guide_talk(self.user, 1)['newly_completed'], [])
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
        self.assertEqual(record_guide_talk(self.user, 1)['newly_completed'], [])


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class ConcurrentFragileAchievementTests(TransactionTestCase):
    def test_concurrent_server_claims_award_only_one_account(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks are required')
        seed_definitions()
        winner = get_user_model().objects.create_user(username='fragile-concurrent-winner')
        next_user = get_user_model().objects.create_user(username='fragile-concurrent-next')
        user_ids = [winner.pk, next_user.pk]
        start = Barrier(2)

        def claim(user_id):
            close_old_connections()
            try:
                owner = get_user_model().objects.get(pk=user_id)
                start.wait(timeout=8)
                return complete_global_achievement(owner, FRAGILE_ID, timezone.now())
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, user_ids))
        self.assertEqual(sum(results), 1)
        self.assertEqual(EventProgress.objects.filter(event_id=FRAGILE_ID, status='COMPLETED').count(), 1)
