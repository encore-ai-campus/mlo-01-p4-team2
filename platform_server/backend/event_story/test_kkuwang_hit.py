from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from eventing.models import PlatformSession
from learning.services import LearningError

from accounts.models import ActiveLogin
from campus.session_registry import session_group
from campus_control.models import EventAccessState
from .achievement_services import KKUWANG_HIT_ID, TIMER_ID, complete_achievement, record_kkuwang_hit
from .models import EventDefinition, EventProgress
from .test_achievements import seed_definitions


class KkuwangHitTests(TestCase):
    def setUp(self):
        seed_definitions()
        self.user = get_user_model().objects.create_user(username='kkuwang-hit')
        self.other = get_user_model().objects.create_user(username='kkuwang-other')

    def test_first_hit_awards_once_and_uses_pepero_art(self):
        first = record_kkuwang_hit(self.user)
        row = EventProgress.objects.get(user=self.user, event_id=KKUWANG_HIT_ID)
        stamp = row.completed_at
        self.assertEqual(first['newly_completed'], [str(KKUWANG_HIT_ID)])
        badge = next(b for b in first['achievements'] if b['event_uuid'] == str(KKUWANG_HIT_ID))
        self.assertEqual(badge['name'], '한 입 달랬지, 한 대 달랬니?')
        self.assertTrue(badge['image_url'].endswith('fragile-pepero-v1.webp'))
        for user in (self.user, self.other):
            result = record_kkuwang_hit(user)
            self.assertEqual(result['newly_completed'], [])
            self.assertTrue(result['resolved'])
        row.refresh_from_db()
        self.assertEqual(row.completed_at, stamp)
        self.assertEqual(EventProgress.objects.filter(event_id=KKUWANG_HIT_ID).count(), 1)

    def test_other_egg_and_closed_event_keep_existing_limits(self):
        self.assertTrue(complete_achievement(self.user, TIMER_ID, timezone.now()))
        self.assertTrue(record_kkuwang_hit(self.user)['resolved'])
        EventAccessState.objects.filter(pk=1).update(mode='CLOSED')
        closed = record_kkuwang_hit(self.other)
        self.assertEqual(closed['newly_completed'], [])
        self.assertFalse(closed['resolved'])
        EventAccessState.objects.filter(pk=1).update(mode='OPEN')
        self.assertEqual(record_kkuwang_hit(self.other)['newly_completed'], [str(KKUWANG_HIT_ID)])

    def test_database_failure_rolls_back_award(self):
        with patch('event_story.achievement_services.achievement_snapshot', side_effect=DatabaseError('unavailable')):
            with self.assertRaises(DatabaseError):
                record_kkuwang_hit(self.user)
        self.assertFalse(EventProgress.objects.filter(event_id=KKUWANG_HIT_ID).exists())
        self.assertTrue(record_kkuwang_hit(self.user)['resolved'])

    def test_inactive_account_and_ended_session_do_not_award(self):
        now = timezone.now()
        session = PlatformSession.objects.create(user=self.user, started_at=now, last_seen_at=now, ended_at=now)
        with self.assertRaises(LearningError) as expired:
            record_kkuwang_hit(self.user, session.pk)
        self.assertEqual(expired.exception.status, 401)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        with self.assertRaises(LearningError) as inactive:
            record_kkuwang_hit(self.user)
        self.assertEqual(inactive.exception.status, 401)
        self.assertFalse(EventProgress.objects.filter(event_id=KKUWANG_HIT_ID).exists())

    def test_private_notice_is_sent_after_commit_only_once(self):
        ActiveLogin.objects.create(user=self.user, session_key='hit-session')
        layer = Mock(group_send=AsyncMock())
        with patch('channels.layers.get_channel_layer', return_value=layer), self.captureOnCommitCallbacks(execute=True):
            result = record_kkuwang_hit(self.user)
            record_kkuwang_hit(self.user)
            layer.group_send.assert_not_awaited()
        layer.group_send.assert_awaited_once_with(session_group('hit-session'), {'type': 'achievement_update', 'result': result})

    def test_definition_change_preserves_records_and_rejects_unknown_definition(self):
        migration = import_module('event_story.migrations.0015_kkuwang_pepero_hit')
        schema = SimpleNamespace(connection=connection)
        EventDefinition.objects.filter(pk=KKUWANG_HIT_ID).update(event_name='시선 강탈 사건')
        progress = EventProgress.objects.create(user=self.user, event_id=KKUWANG_HIT_ID)
        with self.assertRaisesRegex(RuntimeError, '기존 광고 업적 기록'):
            migration.seed(apps, schema)
        self.assertTrue(EventProgress.objects.filter(pk=progress.pk).exists())
        self.assertEqual(EventDefinition.objects.get(pk=KKUWANG_HIT_ID).event_name, '시선 강탈 사건')
        EventDefinition.objects.filter(pk=KKUWANG_HIT_ID).update(event_name='다른 업적')
        with self.assertRaisesRegex(RuntimeError, '정의'):
            migration.seed(apps, schema)


class KkuwangHitConcurrencyTests(TransactionTestCase):
    def test_two_simultaneous_users_have_one_winner(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locking is required.')
        seed_definitions()
        users = [get_user_model().objects.create_user(username=f'hit-race-{i}') for i in range(2)]
        barrier = Barrier(2)

        def hit(pk):
            try:
                user = get_user_model().objects.get(pk=pk)
                barrier.wait(timeout=10)
                return record_kkuwang_hit(user)['newly_completed']
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(hit, [u.pk for u in users]))
        self.assertEqual(sum(bool(result) for result in results), 1)
        self.assertEqual(EventProgress.objects.filter(event_id=KKUWANG_HIT_ID, status='COMPLETED').count(), 1)
