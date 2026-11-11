from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings

from campus.motion import ControlError, MotionController, world
from event_story import access, main, recipe
from event_story.models import EventProgress, StoryProgress, StoryRecord
from eventing.events import SESSION_KEY
from learning.services import LearningError


def ticket(user, target='recipe-note'):
    return signing.dumps({'user': str(user.pk), 'target': target}, salt=access.SALT)


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class RecipeNoteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='recipe-owner')
        self.other = get_user_model().objects.create_user(username='recipe-other')
        main.catalog()

    def acquire(self, user=None, **data):
        user = user or self.user
        return recipe.action(user, {'action': 'acquire', 'ticket': ticket(user), **data}, 'test-session')

    def assert_no_note(self):
        self.assertFalse(StoryProgress.objects.filter(event_key=recipe.EVENT_KEY).exists())
        self.assertFalse(StoryRecord.objects.filter(kind='recipe_note_acquired').exists())

    def test_inspection_does_not_create_records(self):
        self.assertEqual(recipe.status(self.user), {'hasNote': False, 'mainCompleted': False, 'acquiredAt': None, 'noteDelivered': False, 'deferred': False, 'secretStarted': False, 'secretCompleted': False, 'topping': None})
        self.client.force_login(self.user)
        response = self.client.get('/api/event-story/recipe/status/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assert_no_note()

    def test_only_final_delivery_counts_and_client_flags_cannot_bypass_it(self):
        for code in ('1', '2', '3', '4', '5'):
            main.complete(self.user, code)
        with self.assertRaisesMessage(LearningError, recipe.PREREQUISITE_MESSAGE):
            self.acquire(mainCompleted=True, hasNote=True)
        self.assertFalse(recipe.status(self.user)['mainCompleted'])
        self.assert_no_note()
        main.complete(self.user, '6')
        self.assertTrue(self.acquire()['state']['hasNote'])

    def test_legacy_story_completion_is_not_final_delivery(self):
        from django.utils import timezone
        StoryProgress.objects.create(user=self.user, event_key='pepero-main-2026-11-11', completed_at=timezone.now())
        with self.assertRaises(LearningError):
            self.acquire()
        self.assert_no_note()

    def test_repeated_acquisition_preserves_first_record_without_awarding_achievement(self):
        main.complete(self.user, '6')
        initial = list(EventProgress.objects.values())
        first = self.acquire()
        second = recipe.action(self.user, {'action': 'acquire', 'ticket': ticket(self.user)}, 'later-session')
        self.assertEqual(first, second)
        progress = StoryProgress.objects.get(user=self.user, event_key=recipe.EVENT_KEY)
        self.assertEqual(progress.flags, {'hasNote': True})
        self.assertIsNone(progress.completed_at)
        record = progress.records.get()
        self.assertEqual(record.payload, {'objectId': recipe.NOTE_ID})
        self.assertEqual(record.session_uuid, 'test-session')
        self.assertEqual(first['state']['acquiredAt'], record.created_at.isoformat())
        self.assertEqual(list(EventProgress.objects.values()), initial)

    def test_each_account_can_acquire_the_same_note(self):
        for user in (self.user, self.other):
            main.complete(user, '6')
            self.assertTrue(self.acquire(user)['state']['hasNote'])
        self.assertEqual(StoryRecord.objects.filter(kind='recipe_note_acquired').count(), 2)

    def test_completed_account_does_not_unlock_another_account(self):
        main.complete(self.user, '6')
        self.acquire()
        with self.assertRaisesMessage(LearningError, recipe.PREREQUISITE_MESSAGE):
            self.acquire(self.other)
        self.assertFalse(recipe.status(self.other)['hasNote'])

    def test_missing_wrong_expired_and_foreign_tickets_cannot_acquire(self):
        main.complete(self.user, '6')
        with patch('django.core.signing.time.time', return_value=0):
            expired = ticket(self.user)
        for token in (None, 'forged', ticket(self.other), ticket(self.user, 'final-guide'), expired):
            with self.subTest(token=token), self.assertRaises(LearningError):
                self.acquire(ticket=token)
        self.assert_no_note()

    def test_closed_event_and_class_time_cannot_acquire(self):
        main.complete(self.user, '6')
        for phase in ('before', 'closed', 'class'):
            with self.subTest(phase=phase), patch('event_story.main.window', return_value={'phase': phase}):
                with self.assertRaises(LearningError):
                    self.acquire()
                self.assert_no_note()
        with patch('campus_control.service.read_state', return_value={'paused': True}):
            with self.assertRaises(LearningError):
                self.acquire()
        self.assert_no_note()

    def test_real_operating_window_is_used(self):
        main.complete(self.user, '6')
        for hour in (9, 22):
            now = datetime(2026, 11, 10, hour, 10, tzinfo=ZoneInfo('Asia/Seoul'))
            with self.settings(EVENT_STORY_PREVIEW=False), patch('event_story.main.timezone.now', return_value=now):
                with self.assertRaises(LearningError):
                    self.acquire()
        self.assert_no_note()

    def test_log_and_progress_roll_back_together(self):
        main.complete(self.user, '6')
        original = recipe.log

        def fail_after_insert(*args):
            original(*args)
            raise RuntimeError('write failure')

        with patch('event_story.recipe.log', side_effect=fail_after_insert):
            with self.assertRaises(RuntimeError):
                self.acquire()
        self.assert_no_note()

    def test_api_requires_login_method_valid_action_and_json(self):
        url = '/api/event-story/recipe/action/'
        self.assertEqual(self.client.get('/api/event-story/recipe/status/').status_code, 401)
        self.assertEqual(self.client.post(url, {}, content_type='application/json').status_code, 401)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, '[]', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(url, {'action': 'leave'}, content_type='application/json').status_code, 400)
        response = self.client.post(url, {'action': 'acquire', 'ticket': ticket(self.user)}, content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'error': 'main_quest_required', 'message': recipe.PREREQUISITE_MESSAGE})
        self.assert_no_note()

    def test_reconnect_recovers_ownership_and_records_connection_uuid(self):
        main.complete(self.user, '6')
        self.user.set_password('recipe-local-test')
        self.user.save(update_fields=['password'])
        credentials = {'username': self.user.username, 'password': 'recipe-local-test'}
        self.assertEqual(self.client.post('/accounts/login/', credentials).status_code, 302)
        first = self.client.post('/api/event-story/recipe/action/', {'action': 'acquire', 'ticket': ticket(self.user)}, content_type='application/json')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(StoryRecord.objects.get(kind='recipe_note_acquired').session_uuid, str(self.client.session[SESSION_KEY]))
        self.assertEqual(self.client.post('/accounts/logout/').status_code, 302)
        self.assertEqual(self.client.post('/accounts/login/', credentials).status_code, 302)
        self.assertEqual(self.client.get('/api/event-story/recipe/status/').json(), first.json()['state'])

    def test_note_is_walkable_reachable_and_only_nearby_players_get_tickets(self):
        active = world('campus')
        note = active.objects[recipe.NOTE_ID]
        x, y = note['interactionPoint']
        self.assertTrue(active.walkable(x, y))
        footprint = note['footprint']
        self.assertTrue(active.walkable(footprint['x'] + footprint['w'] / 2, footprint['y'] + footprint['h'] / 2))
        self.assertFalse(any(row.get('objectId') == recipe.NOTE_ID for row in active.data['colliders']))
        self.assertTrue(active.route({'x': 1555.75, 'y': 589.5}, {'x': x, 'y': y}))
        controller = MotionController({'floor': 'campus', 'x': x, 'y': y, 'dir': 'up'})
        self.assertEqual(controller.hints()['nearbyId'], recipe.NOTE_ID)
        proof = access.issue(self.user.pk, controller, {'target': 'recipe-note'})
        access.verify(self.user, proof['ticket'], 'recipe-note')
        for floor, xx, yy in (('campus', x + 100, y), ('rooftop', x, y)):
            far = MotionController({'floor': floor, 'x': xx, 'y': yy, 'dir': 'up'})
            with self.assertRaises(ControlError):
                access.issue(self.user.pk, far, {'target': 'recipe-note'})
        controller.interior = {'subject': 'python', 'actor': {'x': 320, 'y': 455}}
        with self.assertRaises(ControlError):
            access.issue(self.user.pk, controller, {'target': 'recipe-note'})


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class ConcurrentRecipeNoteTests(TransactionTestCase):
    def test_simultaneous_requests_return_one_acquisition(self):
        if connection.vendor == 'sqlite' and 'memory' in connection.settings_dict['NAME']:
            self.skipTest('Use file-backed SQLite or PostgreSQL for concurrent connections.')
        main.catalog()
        user = get_user_model().objects.create_user(username='concurrent-recipe')
        main.complete(user, '6')
        token = ticket(user)
        start = Barrier(2)

        def acquire():
            close_old_connections()
            try:
                owner = get_user_model().objects.get(pk=user.pk)
                start.wait(timeout=8)
                return recipe.action(owner, {'action': 'acquire', 'ticket': token}, 'concurrent-session')
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(acquire) for _ in range(2)]
            results = [task.result(timeout=15) for task in tasks]
        self.assertEqual(results[0], results[1])
        self.assertEqual(StoryProgress.objects.filter(user=user, event_key=recipe.EVENT_KEY).count(), 1)
        self.assertEqual(StoryRecord.objects.filter(kind='recipe_note_acquired').count(), 1)
