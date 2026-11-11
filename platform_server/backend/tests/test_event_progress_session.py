from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from event_story import main, recipe
from event_story.achievement_services import (
    AD_ID, GREETING_ID, RECIPE_ID, TIMER_ID, complete_achievement,
    manager_ids, record_ad_impression, record_manager_action,
)
from event_story.game_services import finish_game, start_game
from event_story.models import EventProgress, GamePlay, StoryProgress
from event_story.test_achievements import seed_definitions
from event_story.test_learning_achievements import lesson
from eventing.events import SESSION_KEY
from eventing.models import PlatformSession
from learning import services as learning
from tests.test_recipe_note import ticket


def login_session(user):
    return PlatformSession.objects.create(user=user, started_at=timezone.now(), last_seen_at=timezone.now())


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class EventProgressSessionTests(TestCase):
    def setUp(self):
        seed_definitions();main.catalog()
        self.user = get_user_model().objects.create_user(username='progress-session')
        self.other = get_user_model().objects.create_user(username='progress-session-other')
        self.session = login_session(self.user)
        self.later = login_session(self.user)
        self.foreign = login_session(self.other)

    def test_nullable_uuid_foreign_key_has_the_requested_physical_column(self):
        field = EventProgress._meta.get_field('session')
        self.assertEqual(field.column, 'session_uuid')
        self.assertTrue(field.null)
        self.assertIs(field.remote_field.model, PlatformSession)
        with connection.cursor() as cursor:
            columns = {column.name: column for column in connection.introspection.get_table_description(cursor, 'event_progress')}
        self.assertIn('session_uuid', columns)
        self.assertTrue(columns['session_uuid'].null_ok)

    def test_missing_session_stays_null_even_when_an_account_has_active_sessions(self):
        complete_achievement(self.user, AD_ID, timezone.now())
        self.assertIsNone(EventProgress.objects.get(user=self.user, event_id=AD_ID).session_id)
        self.assertFalse(complete_achievement(self.user, AD_ID, timezone.now(), session_uuid=self.later.pk))
        self.assertIsNone(EventProgress.objects.get(user=self.user, event_id=AD_ID).session_id)

    def test_completed_achievement_keeps_first_session_timestamp_and_rank(self):
        complete_achievement(self.user, AD_ID, timezone.now(), session_uuid=self.session.pk)
        first = EventProgress.objects.get(user=self.user, event_id=AD_ID)
        self.assertEqual(first.session_id, self.session.pk)
        self.assertFalse(complete_achievement(self.user, AD_ID, timezone.now(), session_uuid=self.later.pk))
        again = EventProgress.objects.get(pk=first.pk)
        self.assertEqual((again.session_id, again.completed_at, again.completion_order),
                         (first.session_id, first.completed_at, first.completion_order))

    def test_foreign_invalid_and_ended_sessions_cannot_complete_an_achievement(self):
        ended = login_session(self.user)
        ended.ended_at = timezone.now();ended.save(update_fields=['ended_at'])
        for session in (self.foreign.pk, 'invalid', ended.pk):
            with self.subTest(session=session), self.assertRaises(learning.LearningError):
                complete_achievement(self.user, AD_ID, timezone.now(), session_uuid=session)
            self.assertFalse(EventProgress.objects.filter(user=self.user, event_id=AD_ID).exists())

    def test_main_action_records_first_completion_session(self):
        data = {'action': 'investigate', 'ticket': ticket(self.user, 'investigate')}
        main.action(self.user, data, self.session.pk)
        first = EventProgress.objects.get(user=self.user, event_id=main.uid('1'))
        main.action(self.user, data, self.later.pk)
        again = EventProgress.objects.get(pk=first.pk)
        self.assertEqual((again.session_id, again.completed_at), (self.session.pk, first.completed_at))

    def test_http_uses_server_session_and_ignores_submitted_session_uuid(self):
        self.client.force_login(self.user)
        response = self.client.post('/api/event-main/action/', {
            'action': 'investigate', 'ticket': ticket(self.user, 'investigate'),
            'session_uuid': str(self.foreign.pk),
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        row = EventProgress.objects.get(user=self.user, event_id=main.uid('1'))
        self.assertEqual(str(row.session_id), self.client.session[SESSION_KEY])

    def test_main_bad_session_rolls_back_the_new_progress(self):
        with self.assertRaises(learning.LearningError):
            main.action(self.user, {'action': 'investigate', 'ticket': ticket(self.user, 'investigate')}, self.foreign.pk)
        self.assertFalse(main.done(self.user, '1'))
        self.assertFalse(EventProgress.objects.filter(user=self.user).exists())

    def test_game_success_records_its_validated_session(self):
        play = start_game(self.user, 'timer', self.session.pk)
        GamePlay.objects.filter(pk=play['play_uuid']).update(started_at=timezone.now()-timedelta(seconds=12))
        finish_game(self.user, play['play_uuid'], self.session.pk, {'status': 'SUCCESS', 'duration_ms': 11110})
        self.assertEqual(EventProgress.objects.get(user=self.user, event_id=TIMER_ID).session_id, self.session.pk)

    def test_manager_completion_records_the_triggering_session(self):
        for manager in manager_ids():
            record_manager_action(self.user, manager, 'greet', self.session.pk)
        self.assertEqual(EventProgress.objects.get(user=self.user, event_id=GREETING_ID).session_id, self.session.pk)

    def test_learning_http_completion_records_request_session(self):
        session = lesson(self.user)
        self.client.force_login(self.user)
        response = self.client.post(f'/api/learning/sessions/{session.pk}/complete/', {}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        award = EventProgress.objects.get(user=self.user)
        self.assertEqual(str(award.session_id), self.client.session[SESSION_KEY])

    def test_recipe_keeps_story_tables_and_records_only_final_award_session(self):
        main.complete(self.user, '6')
        for action, extra in (('acquire', {}), ('recognize', {}), ('start', {}), ('topping', {'topping': 'thanks'})):
            recipe.action(self.user, {'action': action, 'ticket': ticket(self.user, 'recipe-note' if action == 'acquire' else 'recipe-guide'), **extra}, self.session.pk)
        self.assertTrue(StoryProgress.objects.filter(user=self.user, event_key=recipe.EVENT_KEY).exists())
        self.assertFalse(EventProgress.objects.filter(user=self.user, event_id=RECIPE_ID).exists())
        recipe.action(self.user, {'action': 'finish', 'ticket': ticket(self.user, 'recipe-guide')}, self.later.pk)
        self.assertEqual(EventProgress.objects.get(user=self.user, event_id=RECIPE_ID).session_id, self.later.pk)

    def test_async_ad_ack_does_not_guess_a_session(self):
        result = record_ad_impression(SimpleNamespace(owner_id=self.user.pk, delivery_state='ACKNOWLEDGED', status='ACCEPTED'))
        self.assertEqual(result['newly_completed'], [str(AD_ID)])
        self.assertIsNone(EventProgress.objects.get(user=self.user, event_id=AD_ID).session_id)

    def test_session_can_end_but_cannot_be_deleted_after_completion(self):
        complete_achievement(self.user, AD_ID, timezone.now(), session_uuid=self.session.pk)
        self.session.ended_at = timezone.now();self.session.save(update_fields=['ended_at'])
        with self.assertRaises(ProtectedError):
            self.session.delete()
        self.assertEqual(EventProgress.objects.get(user=self.user, event_id=AD_ID).session_id, self.session.pk)


class ConcurrentEventProgressSessionTests(TransactionTestCase):
    def test_simultaneous_accounts_keep_only_the_winners_session_and_rank(self):
        seed_definitions()
        users = [get_user_model().objects.create_user(username='session-race-' + str(i)) for i in range(2)]
        sessions = [login_session(user) for user in users]
        barrier = Barrier(2)
        def complete(user, session):
            close_old_connections()
            try:
                barrier.wait(timeout=8)
                return complete_achievement(user, AD_ID, timezone.now(), session_uuid=session.pk)
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(complete, user, session) for user, session in zip(users, sessions)]
            outcomes = [result.result(timeout=15) for result in pending]
        self.assertEqual(sum(outcomes), 1)
        row = EventProgress.objects.get(event_id=AD_ID)
        winner = outcomes.index(True)
        self.assertEqual((row.user_id, row.session_id, row.completion_order), (users[winner].pk, sessions[winner].pk, 1))
