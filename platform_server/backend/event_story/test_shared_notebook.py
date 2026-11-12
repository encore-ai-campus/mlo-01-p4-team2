"""Only the earning account receives personal achievement records."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
from unittest.mock import patch, AsyncMock

from django.contrib.auth import get_user_model
from django.db import transaction, close_old_connections, connections
from django.test import TestCase, TransactionTestCase, SimpleTestCase, RequestFactory
from django.utils import timezone

from .test_achievements import seed_definitions
from .achievement_services import complete_achievement, achievement_snapshot, GREETING_ID
from .achievement_views import achievements
from .models import EventProgress


class PrivateNotebookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_definitions()
        cls.a = get_user_model().objects.create_user(username='discoverer-a')
        cls.b = get_user_model().objects.create_user(username='discoverer-b')

    def test_only_the_earning_account_has_completion_fields(self):
        self.assertTrue(complete_achievement(self.a, GREETING_ID, timezone.now()))
        owner = achievement_snapshot(self.a)[0]
        other = achievement_snapshot(self.b)[0]
        self.assertTrue(owner['achieved'])
        self.assertIsNotNone(owner['completed_at'])
        self.assertFalse(other['achieved'])
        self.assertIsNone(other['completed_at'])
        self.assertIsNone(other['completion_order'])
        for user in (self.a, self.b):
            for row in achievement_snapshot(user):
                self.assertEqual(set(row), {'event_uuid', 'name', 'achieved', 'completed_at',
                                             'completion_order', 'image_url'})
        self.assertFalse(complete_achievement(self.b, GREETING_ID, timezone.now()))

    def test_existing_completions_stay_private_without_rewriting_records(self):
        progress = EventProgress.objects.create(
            user=self.a, event_id=GREETING_ID, status='COMPLETED', completed_at=timezone.now())
        before = EventProgress.objects.filter(pk=progress.pk).values().get()
        self.assertTrue(achievement_snapshot(self.a)[0]['achieved'])
        self.assertFalse(achievement_snapshot(self.b)[0]['achieved'])
        self.assertEqual(EventProgress.objects.filter(pk=progress.pk).values().get(), before)

    def test_rollback_does_not_grant_either_account_a_completion(self):
        with self.assertRaises(ValueError), transaction.atomic():
            complete_achievement(self.a, GREETING_ID, timezone.now())
            raise ValueError('rollback')
        self.assertFalse(achievement_snapshot(self.a)[0]['achieved'])
        self.assertFalse(achievement_snapshot(self.b)[0]['achieved'])

    def test_api_never_returns_another_account_name_or_completion(self):
        complete_achievement(self.a, GREETING_ID, timezone.now())
        request = RequestFactory().get('/api/event-story/achievements/', {'user_id': str(self.a.pk)})
        request.user = self.b
        response = achievements(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertFalse(json.loads(response.content)['achievements'][0]['achieved'])
        self.assertNotIn(self.a.username, response.content.decode())
        self.assertNotIn('first_discoverer', response.content.decode())
        self.assertNotIn('discovered', response.content.decode())

    def test_completion_does_not_broadcast_to_other_players(self):
        layer = type('Layer', (), {'group_send': AsyncMock()})()
        with self.captureOnCommitCallbacks(execute=True), patch('channels.layers.get_channel_layer', return_value=layer):
            self.assertTrue(complete_achievement(self.a, GREETING_ID, timezone.now()))
        layer.group_send.assert_not_called()


class PrivateNotebookRaceTests(TransactionTestCase):
    def test_racing_accounts_keep_one_winner_and_only_the_winner_sees_completion(self):
        seed_definitions()
        users = [get_user_model().objects.create_user(username='race-' + str(i)) for i in range(2)]
        start = Barrier(2)

        def run(pk):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=pk)
                start.wait(timeout=5)
                return complete_achievement(user, GREETING_ID, timezone.now())
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(run, [user.pk for user in users])), 1)
        progress = EventProgress.objects.get(event_id=GREETING_ID)
        self.assertEqual(progress.completion_order, 1)
        for user in users:
            self.assertEqual(achievement_snapshot(user)[0]['achieved'], user.pk == progress.user_id)


class PrivateAchievementSocketTests(SimpleTestCase):
    async def test_personal_update_is_sent_only_while_the_login_session_is_current(self):
        from campus.consumers import CampusConsumer
        consumer = CampusConsumer()
        consumer.current_identity = AsyncMock(return_value={'id': 'owner'})
        consumer.send_packet = AsyncMock()
        result = {'achievements': [{'event_uuid': 'example', 'achieved': True}],
                  'newly_completed': ['example']}
        await consumer.achievement_update({'result': result})
        consumer.send_packet.assert_awaited_once_with({'type': 'achievement_update', **result})
        consumer.send_packet.reset_mock()
        consumer.current_identity.return_value = None
        await consumer.achievement_update({'result': result})
        consumer.send_packet.assert_not_awaited()
