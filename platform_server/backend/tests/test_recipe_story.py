from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings

from campus.motion import ControlError, MotionController, world
from event_story import access, main, recipe
from event_story.achievement_services import RECIPE_ID, achievement_snapshot
from event_story.models import EventProgress, StoryProgress, StoryRecord
from eventing.models import PlatformSession
from django.utils import timezone
from event_story.test_achievements import seed_definitions
from learning.services import LearningError
from tests.test_recipe_note import ticket


def act(user, action, **extra):
    session = PlatformSession.objects.filter(user=user, ended_at__isnull=True).first()
    if session is None:
        session = PlatformSession.objects.create(user=user, started_at=timezone.now(), last_seen_at=timezone.now())
    return recipe.action(user, {'action': action, 'ticket': ticket(user, 'recipe-note' if action == 'acquire' else 'recipe-guide'), **extra}, session.pk)


def ready(user, topping=None):
    main.complete(user, '6')
    for action in ('acquire', 'recognize', 'start'):
        act(user, action)
    if topping:
        act(user, 'topping', topping=topping)


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class RecipeStoryTests(TestCase):
    def setUp(self):
        seed_definitions()
        main.catalog()
        self.user = get_user_model().objects.create_user(username='recipe-story')
        self.other = get_user_model().objects.create_user(username='recipe-story-other')
        main.complete(self.user, '6')

    def test_note_delivery_and_deferral_persist_without_consumption_or_reward(self):
        act(self.user, 'acquire')
        for action in ('recognize', 'defer'):
            first = act(self.user, action)
            self.assertEqual(act(self.user, action), first)
        state = recipe.status(get_user_model().objects.get(pk=self.user.pk))
        self.assertTrue(state['hasNote'])
        self.assertTrue(state['noteDelivered'])
        self.assertTrue(state['deferred'])
        self.assertFalse(state['secretStarted'])
        self.assertFalse(state['secretCompleted'])
        self.assertFalse(EventProgress.objects.filter(user=self.user, event_id=RECIPE_ID).exists())
        self.assertEqual(StoryRecord.objects.filter(progress__user=self.user, key='defer').count(), 1)
        state = act(self.user, 'start')['state']
        self.assertTrue(state['secretStarted'])
        self.assertFalse(state['deferred'])

    def test_note_ownership_main_completion_and_stage_order_are_required(self):
        for action in ('recognize', 'defer', 'start', 'topping', 'finish'):
            with self.subTest(action=action), self.assertRaises(LearningError):
                act(self.user, action, topping='thanks')
        self.assertFalse(StoryProgress.objects.filter(user=self.user).exists())
        act(self.user, 'acquire')
        for action in ('start', 'topping', 'finish'):
            with self.subTest(action=action), self.assertRaises(LearningError):
                act(self.user, action, topping='thanks')
        act(self.user, 'recognize')
        with self.assertRaises(LearningError):
            act(self.user, 'finish')
        act(self.user, 'start')
        with self.assertRaises(LearningError):
            act(self.user, 'finish')
        self.assertFalse(EventProgress.objects.filter(event_id=RECIPE_ID).exists())
        StoryProgress.objects.create(user=self.other, event_key=recipe.EVENT_KEY, flags={'hasNote': True})
        with self.assertRaisesMessage(LearningError, recipe.PREREQUISITE_MESSAGE):
            act(self.other, 'recognize')

    def test_rejected_choices_and_wrong_proximity_tickets_leave_progress_unchanged(self):
        ready(self.user)
        before = recipe.status(self.user)
        for value in (None, '', 'unknown', [], True):
            with self.subTest(topping=value), self.assertRaises(LearningError):
                act(self.user, 'topping', topping=value)
        for proof in (None, ticket(self.user), ticket(self.other, 'recipe-guide')):
            with self.subTest(ticket=proof), self.assertRaises(LearningError):
                act(self.user, 'topping', topping='thanks', ticket=proof)
        self.assertEqual(recipe.status(self.user), before)
        self.assertFalse(StoryRecord.objects.filter(progress__user=self.user, key='topping').exists())
        with self.assertRaises(LearningError):
            act(self.user, [])

    def test_both_toppings_finish_but_only_first_account_receives_the_badge(self):
        for index, (user, topping) in enumerate(((self.user, 'thanks'), (self.other, 'promise'))):
            with self.subTest(topping=topping):
                ready(user, topping)
                main_before = list(EventProgress.objects.filter(user=user).values())
                first = act(user, 'finish')
                self.assertEqual(first['newly_completed'], [str(RECIPE_ID)] if index == 0 else [])
                self.assertTrue(first['state']['secretCompleted']);self.assertTrue(first['state']['hasNote'])
                progress = StoryProgress.objects.get(user=user, event_key=recipe.EVENT_KEY)
                finished_at = progress.completed_at
                record = progress.records.get(key='finish')
                self.assertEqual(record.payload, {'objectId': recipe.GUIDE_ID, 'topping': topping})
                for action, data in (('recognize', {}), ('start', {}), ('topping', {'topping': topping}), ('finish', {})):
                    replay = act(user, action, **data)
                    self.assertEqual(replay['state'], first['state'])
                    if action == 'finish':self.assertEqual(replay['newly_completed'], [])
                progress.refresh_from_db()
                self.assertEqual(progress.completed_at, finished_at)
                self.assertEqual(progress.records.get(key='finish').created_at, record.created_at)
                self.assertEqual(progress.records.filter(key='finish').count(), 1)
                self.assertEqual(EventProgress.objects.filter(user=user, event_id=RECIPE_ID).count(), 1 if index == 0 else 0)
                if index == 0:self.assertEqual(EventProgress.objects.get(user=user, event_id=RECIPE_ID).completed_at, finished_at)
                self.assertEqual(list(EventProgress.objects.filter(user=user).exclude(event_id=RECIPE_ID).values()), main_before)
                self.assertEqual(next(a for a in achievement_snapshot(user) if a['event_uuid'] == str(RECIPE_ID))['achieved'], index == 0)

    def test_chosen_topping_cannot_change_and_deferral_cannot_undo_started_recipe(self):
        ready(self.user, 'thanks')
        before = recipe.status(self.user)
        with self.assertRaises(LearningError):
            act(self.user, 'topping', topping='promise')
        with self.assertRaises(LearningError):
            act(self.user, 'defer')
        self.assertEqual(recipe.status(self.user), before)

    def test_completion_survives_response_failure_and_retry_does_not_duplicate(self):
        ready(self.user, 'thanks')
        with patch('event_story.recipe.achievement_snapshot', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                act(self.user, 'finish')
        self.assertTrue(recipe.status(self.user)['secretCompleted'])
        first=EventProgress.objects.get(user=self.user,event_id=RECIPE_ID)
        self.assertEqual(StoryRecord.objects.filter(progress__user=self.user,key='finish').count(),1)
        retry=act(self.user,'finish')
        self.assertTrue(retry['state']['secretCompleted'])
        self.assertEqual(retry['newly_completed'],[])
        self.assertEqual(EventProgress.objects.get(pk=first.pk).completed_at,first.completed_at)
        self.assertEqual(StoryRecord.objects.filter(progress__user=self.user,key='finish').count(),1)

    def test_guide_proofs_only_issue_at_the_actual_guide(self):
        near = MotionController({'floor': 'campus', 'x': 1250, 'y': 185, 'dir': 'up'})
        self.assertTrue(world('campus').walkable(1250, 185))
        proof = access.issue(self.user.pk, near, {'target': 'recipe-guide'})
        access.verify(self.user, proof['ticket'], 'recipe-guide')
        for floor, x, y in (('rooftop', 950, 1660), ('rooftop', 1250, 185)):
            far = MotionController({'floor': floor, 'x': x, 'y': y, 'dir': 'up'})
            with self.assertRaises(ControlError):
                access.issue(self.user.pk, far, {'target': 'recipe-guide'})

    def test_class_pause_and_operating_hours_block_all_recipe_writes(self):
        ready(self.user, 'thanks')
        before = recipe.status(self.user)
        for action in ('recognize', 'defer', 'start', 'topping', 'finish'):
            with self.subTest(action=action), patch('event_story.main.window', return_value={'phase': 'class'}):
                with self.assertRaises(LearningError):
                    act(self.user, action, topping='thanks')
        with patch('campus_control.service.read_state', return_value={'paused': True}):
            with self.assertRaises(LearningError):
                act(self.user, 'finish')
        self.assertEqual(recipe.status(self.user), before)

    def test_http_actions_and_reloaded_status_are_account_specific(self):
        ready(self.user, 'promise')
        self.client.force_login(self.user)
        response = self.client.post('/api/event-story/recipe/action/', {'action': 'finish', 'ticket': ticket(self.user, 'recipe-guide')}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['newly_completed'], [str(RECIPE_ID)])
        self.assertEqual(self.client.get('/api/event-story/recipe/status/').json(), response.json()['state'])
        self.client.force_login(self.other)
        other = self.client.get('/api/event-story/recipe/status/').json()
        self.assertFalse(other['hasNote']);self.assertFalse(other['secretCompleted'])


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class ConcurrentRecipeStoryTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks are required for this check.')
        seed_definitions();main.catalog()
        self.user = get_user_model().objects.create_user(username='concurrent-recipe-story')
        ready(self.user)

    def simultaneous(self, actions):
        start = Barrier(len(actions))
        def submit(data):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.user.pk)
                start.wait(timeout=8)
                try:
                    return act(user, **data)
                except LearningError as error:
                    return error.code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            tasks = [pool.submit(submit, data) for data in actions]
            return [task.result(timeout=15) for task in tasks]

    def test_simultaneous_completion_awards_once_with_one_completion_record(self):
        act(self.user, 'topping', topping='thanks')
        results = self.simultaneous([{'action': 'finish'}, {'action': 'finish'}])
        self.assertEqual(results[0]['state'], results[1]['state'])
        self.assertEqual(sum(len(r['newly_completed']) for r in results), 1)
        self.assertEqual(EventProgress.objects.filter(user=self.user, event_id=RECIPE_ID).count(), 1)
        self.assertEqual(StoryRecord.objects.filter(progress__user=self.user, key='finish').count(), 1)

    def test_conflicting_toppings_commit_one_choice_and_do_not_award_early(self):
        results = self.simultaneous([{'action': 'topping', 'topping': 'thanks'}, {'action': 'topping', 'topping': 'promise'}])
        self.assertEqual(sum(r == 'recipe_choice_locked' for r in results), 1)
        winner = next(r['state']['topping'] for r in results if isinstance(r, dict))
        self.assertEqual(recipe.status(self.user)['topping'], winner)
        self.assertEqual(StoryRecord.objects.filter(progress__user=self.user, key='topping').count(), 1)
        self.assertFalse(EventProgress.objects.filter(user=self.user, event_id=RECIPE_ID).exists())
