from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib import import_module
from threading import Barrier
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from eventing.models import PlatformSession
from learning import services as learning
from tests.test_recipe_note import ticket
from . import main, recipe
from .achievement_services import (
    AD_ID, RECIPE_ID, SQL_ID, TIMER_ID, complete_achievement,
    manager_ids, record_ad_impression, record_guide_talk, record_manager_action,
)
from .game_services import finish_game, start_game
from .models import EventProgress, GamePlay, StoryProgress
from .test_achievements import seed_definitions
from .test_learning_achievements import lesson


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class AchievementExclusivityTests(TestCase):
    def setUp(self):
        seed_definitions();main.catalog()
        self.a = get_user_model().objects.create_user(username='exclusive-a')
        self.b = get_user_model().objects.create_user(username='exclusive-b')
        self.now = timezone.now()

    def claim(self, user, event):
        return complete_achievement(user, event, self.now)

    def test_one_egg_has_one_owner_and_rejected_claim_does_not_consume_account_quota(self):
        self.assertTrue(self.claim(self.a, AD_ID))
        self.assertFalse(self.claim(self.b, AD_ID))
        self.assertFalse(EventProgress.objects.filter(user=self.b).exists())
        self.assertTrue(self.claim(self.b, RECIPE_ID))
        self.assertEqual(EventProgress.objects.filter(status='COMPLETED').count(), 2)

    def test_one_account_has_one_egg_and_failed_claim_does_not_reserve_another_egg(self):
        self.assertTrue(self.claim(self.a, AD_ID))
        self.assertFalse(self.claim(self.a, RECIPE_ID))
        self.assertFalse(EventProgress.objects.filter(event_id=RECIPE_ID).exists())
        self.assertTrue(self.claim(self.b, RECIPE_ID))
        self.assertEqual(EventProgress.objects.filter(user=self.a, status='COMPLETED').count(), 1)

    def test_pending_records_do_not_consume_quota_and_denials_preserve_them(self):
        EventProgress.objects.create(user=self.a, event_id=AD_ID)
        pending = EventProgress.objects.create(user=self.b, event_id=AD_ID)
        self.assertTrue(self.claim(self.a, AD_ID))
        self.assertFalse(self.claim(self.b, AD_ID))
        pending.refresh_from_db()
        self.assertEqual((pending.status, pending.completed_at, pending.completion_order), ('IN_PROGRESS', None, None))
        self.assertTrue(self.claim(self.b, RECIPE_ID))

    def test_repeat_preserves_the_first_timestamp_session_and_order(self):
        session = PlatformSession.objects.create(user=self.a, started_at=self.now, last_seen_at=self.now)
        complete_achievement(self.a, AD_ID, self.now, session_uuid=session.pk)
        first = EventProgress.objects.get(user=self.a, event_id=AD_ID)
        self.assertFalse(complete_achievement(self.a, AD_ID, self.now+timedelta(seconds=60)))
        again = EventProgress.objects.get(pk=first.pk)
        self.assertEqual((again.completed_at, again.session_id, again.completion_order), (self.now, session.pk, 1))

    def test_main_progress_is_independent_of_egg_quota(self):
        main.complete(self.a, '1')
        self.assertTrue(self.claim(self.a, AD_ID))
        main.complete(self.a, '3')
        self.assertTrue(main.done(self.a, '1'));self.assertTrue(main.done(self.a, '3'))
        with self.assertRaises(ValueError):
            self.claim(self.b, main.uid('1'))
        self.assertEqual(EventProgress.objects.filter(user=self.a, status='COMPLETED').count(), 3)

    def test_rollback_releases_both_the_account_and_the_egg(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.assertTrue(self.claim(self.a, AD_ID))
            raise RuntimeError('rollback')
        self.assertTrue(self.claim(self.b, AD_ID))
        self.assertTrue(self.claim(self.a, RECIPE_ID))
        self.assertEqual(set(EventProgress.objects.values_list('completion_order', flat=True)), {1})

    def test_closed_game_award_keeps_success_and_replay_receipt(self):
        self.claim(self.a, TIMER_ID)
        session = PlatformSession.objects.create(user=self.b, started_at=self.now, last_seen_at=self.now)
        play = start_game(self.b, 'timer', session.pk)
        GamePlay.objects.filter(pk=play['play_uuid']).update(started_at=self.now-timedelta(seconds=12))
        payload = {'status':'SUCCESS', 'duration_ms':11110}
        result = finish_game(self.b, play['play_uuid'], session.pk, payload)
        self.assertEqual((result['status'], result['newly_completed']), ('SUCCESS', []))
        self.assertEqual(GamePlay.objects.get(pk=play['play_uuid']).status, 'SUCCESS')
        self.assertFalse(EventProgress.objects.filter(user=self.b).exists())
        retry = finish_game(self.b, play['play_uuid'], session.pk, payload)
        self.assertEqual((retry['ended_at'], retry['newly_completed']), (result['ended_at'], []))

    def test_learning_completion_is_saved_even_when_the_badge_is_closed(self):
        self.claim(self.a, SQL_ID)
        session = learning.complete_session(self.b, lesson(self.b, 'sql').pk)
        self.assertEqual(session.achievement_update['newly_completed'], [])
        session.refresh_from_db()
        self.assertIsNotNone(session.completed_at);self.assertEqual(session.correct_count, 7)
        self.assertFalse(EventProgress.objects.filter(user=self.b).exists())

    def test_recipe_story_can_finish_after_another_egg_without_a_second_award(self):
        self.claim(self.a, AD_ID);main.complete(self.a, '6')
        for action, extra in [('acquire',{}),('recognize',{}),('start',{}),('topping',{'topping':'thanks'}),('finish',{})]:
            result = recipe.action(self.a, {'action':action, 'ticket':ticket(self.a, 'recipe-note' if action=='acquire' else 'recipe-guide'), **extra})
        self.assertTrue(result['state']['secretCompleted'])
        self.assertEqual(result['newly_completed'], [])
        self.assertIsNotNone(StoryProgress.objects.get(user=self.a, event_key=recipe.EVENT_KEY).completed_at)
        self.assertFalse(EventProgress.objects.filter(user=self.a, event_id=RECIPE_ID).exists())

    def test_ad_manager_and_guide_paths_cannot_bypass_account_quota(self):
        self.claim(self.a, TIMER_ID)
        ad = record_ad_impression(SimpleNamespace(owner_id=self.a.pk, delivery_state='ACKNOWLEDGED', status='ACCEPTED'))
        self.assertEqual(ad['newly_completed'], [])
        self.assertEqual(record_guide_talk(self.a, 1)['newly_completed'], [])
        for manager in manager_ids():result = record_manager_action(self.a, manager, 'greet')
        self.assertEqual(result['newly_completed'], [])
        self.assertEqual(list(EventProgress.objects.filter(user=self.a, status='COMPLETED')
                              .values_list('event_id', flat=True)), [TIMER_ID])

    def test_reset_migration_removes_only_completed_eggs(self):
        EventProgress.objects.create(user=self.a, event_id=AD_ID, status='COMPLETED', completed_at=self.now, completion_order=1)
        EventProgress.objects.create(user=self.b, event_id=AD_ID, status='COMPLETED', completed_at=self.now, completion_order=2)
        EventProgress.objects.create(user=self.a, event_id=RECIPE_ID, status='COMPLETED', completed_at=self.now, completion_order=1)
        pending = EventProgress.objects.create(user=self.b, event_id=RECIPE_ID)
        main.complete(self.a, '1')
        preserved = list(EventProgress.objects.filter(pk__in=[pending.pk, EventProgress.objects.get(user=self.a, event_id=main.uid('1')).pk]).order_by('pk').values())
        import_module('event_story.migrations.0014_reset_easter_egg_completions').reset_completions(apps, SimpleNamespace(connection=connection))
        self.assertEqual(list(EventProgress.objects.order_by('pk').values()), preserved)


class ConcurrentExclusiveAchievementTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':self.skipTest('PostgreSQL row locks are required')
        seed_definitions()
        self.users = [get_user_model().objects.create_user(username='exclusive-race-'+str(i)) for i in range(8)]

    def race(self, claims):
        barrier = Barrier(len(claims))
        def finish(claim):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=claim[0])
                barrier.wait(timeout=10)
                return complete_achievement(user, claim[1], timezone.now())
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=len(claims)) as pool:
            futures = [pool.submit(finish, claim) for claim in claims]
            return [future.result(timeout=15) for future in futures]

    def test_eight_accounts_racing_for_one_egg_have_one_winner(self):
        results = self.race([(user.pk, AD_ID) for user in self.users])
        self.assertEqual(sum(results), 1)
        self.assertEqual(list(EventProgress.objects.values_list('completion_order', flat=True)), [1])

    def test_one_account_racing_for_different_eggs_has_one_winner(self):
        results = self.race([(self.users[0].pk, AD_ID), (self.users[0].pk, RECIPE_ID)])
        self.assertEqual(sum(results), 1)
        self.assertEqual(EventProgress.objects.filter(user=self.users[0], status='COMPLETED').count(), 1)

    def test_crossed_claims_obey_both_limits_without_deadlock(self):
        results = self.race([(user.pk, event) for user in self.users[:2] for event in [AD_ID, RECIPE_ID]])
        self.assertEqual(sum(results), 2)
        for user in self.users[:2]:self.assertEqual(EventProgress.objects.filter(user=user, status='COMPLETED').count(), 1)
        for event in [AD_ID, RECIPE_ID]:self.assertEqual(EventProgress.objects.filter(event_id=event, status='COMPLETED').count(), 1)
