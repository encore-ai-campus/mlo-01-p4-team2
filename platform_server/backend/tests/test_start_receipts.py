from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import connection, connections, close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from event_story import main
from event_story.access import SALT
from event_story.models import EventProgress, GameDefinition, GamePlay
from eventing.events import SESSION_KEY
from eventing.models import PlatformSession
from learning.services import LearningError


def ticket(user, target='roof-guide'):
    return signing.dumps({'user': str(user.pk), 'target': target}, salt=SALT)


class ReceiptFixtures:
    def setUp(self):
        main.catalog()
        self.user = get_user_model().objects.create_user(username='start-receipt')
        self.session = self.prepare_user(self.user)
        self.data = {'action': 'game_start', 'game': 'kkuwang_touch',
                     'request_uuid': str(uuid4()), 'ticket': ticket(self.user)}

    def prepare_user(self, user):
        session = PlatformSession.objects.create(user=user, started_at=timezone.now(), last_seen_at=timezone.now())
        for code in ('1', '3'):
            main.complete(user, code, session.pk)
        return session

    def start(self, **changes):
        return main.action(self.user, {**self.data, **changes}, self.session.pk)

    def assert_rejected(self, code, status, data=None, session=None):
        with self.assertRaises(LearningError) as caught:
            main.action(self.user, self.data if data is None else data,
                        self.session.pk if session is None else session)
        self.assertEqual((caught.exception.code, caught.exception.status), (code, status))


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class StartReceiptTests(ReceiptFixtures, TestCase):
    def test_start_response_does_not_read_status_and_receipt_is_saved(self):
        with patch.object(main, 'status', side_effect=RuntimeError('snapshot unavailable')) as status:
            result = self.start()
        status.assert_not_called()
        self.assertEqual(set(result), {'play', 'token', 'challenge', 'practice', 'status'})
        self.assertEqual(result['status'], 'RUNNING')
        play = GamePlay.objects.get(pk=result['play'])
        self.assertEqual(play.evidence['request_uuid'], self.data['request_uuid'])
        self.assertEqual(play.evidence['start_receipt'], {key: value for key, value in result.items() if key != 'status'})
        self.assertEqual(play.evidence['challenge'], result['challenge'])

    def test_same_request_returns_original_receipt_without_signing_again(self):
        first = self.start()
        with patch.object(main.signing, 'dumps', side_effect=AssertionError('must not issue a new token')):
            replay = self.start()
        self.assertEqual(replay, first)
        self.assertEqual(GamePlay.objects.filter(user=self.user).count(), 1)

    def test_new_request_interrupts_the_previous_running_play(self):
        first = self.start()
        second = self.start(request_uuid=str(uuid4()))
        self.assertNotEqual(first['play'], second['play'])
        previous = GamePlay.objects.get(pk=first['play'])
        self.assertEqual(previous.status, 'INTERRUPTED')
        self.assertIsNotNone(previous.ended_at)
        self.assertEqual(GamePlay.objects.get(pk=second['play']).status, 'RUNNING')

    def test_interrupted_receipt_replay_does_not_interrupt_current_play(self):
        first = self.start()
        current = self.start(request_uuid=str(uuid4()))
        replay = self.start()
        self.assertEqual(replay, {**first, 'status': 'INTERRUPTED'})
        self.assertEqual(GamePlay.objects.get(pk=current['play']).status, 'RUNNING')
        self.assertEqual(GamePlay.objects.filter(user=self.user).count(), 2)

    def test_finished_receipt_replay_does_not_interrupt_current_play(self):
        first = self.start()
        GamePlay.objects.filter(pk=first['play']).update(started_at=timezone.now()-timedelta(seconds=11))
        result = main.action(self.user, {'action': 'game_finish', 'play': first['play'],
            'token': first['token'], 'proof': {'taps': [100, 500]}}, self.session.pk)
        self.assertEqual(result['score'], 2)
        current = self.start(request_uuid=str(uuid4()))
        self.assertEqual(self.start(), {**first, 'status': 'FINISHED'})
        self.assertEqual(GamePlay.objects.get(pk=current['play']).status, 'RUNNING')
        saved = GamePlay.objects.get(pk=first['play'])
        self.assertEqual(saved.evidence['result'], {'taps': [100, 500]})
        self.assertEqual(saved.evidence['start_receipt']['token'], first['token'])

    def test_missing_or_malformed_request_uuid_does_not_create_a_play(self):
        for value in (None, '', 'invalid', 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA', '00000000-0000-0000-0000-000000000000'):
            with self.subTest(value=value):
                self.assert_rejected('invalid_request_uuid', 400, {**self.data, 'request_uuid': value})
        missing = dict(self.data);missing.pop('request_uuid')
        self.assert_rejected('invalid_request_uuid', 400, missing)
        self.assertFalse(GamePlay.objects.filter(user=self.user).exists())

    def test_same_request_rejects_another_game_or_practice_mode(self):
        self.user.is_staff = True;self.user.save(update_fields=['is_staff'])
        self.start()
        main.complete(self.user, '4', self.session.pk)
        self.assert_rejected('request_conflict', 409, {**self.data, 'game': 'card_match', 'ticket': ticket(self.user, 'room2')})
        self.assert_rejected('request_conflict', 409, {**self.data, 'practice': True})
        self.assertEqual(GamePlay.objects.filter(user=self.user).count(), 1)

    def test_practice_receipt_preserves_mode_and_does_not_register_main_progress(self):
        self.user.is_staff = True;self.user.save(update_fields=['is_staff'])
        EventProgress.objects.filter(user=self.user).delete()
        first = self.start(practice=True)
        self.assertTrue(first['practice'])
        self.assertEqual(self.start(practice=True), first)
        self.assertIsNone(GamePlay.objects.get(pk=first['play']).event_id)
        self.assertFalse(EventProgress.objects.filter(user=self.user).exists())

    def test_replay_revalidates_practice_permission(self):
        self.user.is_staff = True;self.user.save(update_fields=['is_staff'])
        self.start(practice=True)
        self.user.is_staff = False
        self.assert_rejected('forbidden', 403, {**self.data, 'practice': True})

    def test_replay_rejects_another_active_session_or_an_ended_session(self):
        first = self.start()
        later = PlatformSession.objects.create(user=self.user, started_at=timezone.now(), last_seen_at=timezone.now())
        self.assert_rejected('session_conflict', 409, session=later.pk)
        self.session.ended_at = timezone.now();self.session.save(update_fields=['ended_at'])
        self.assert_rejected('session_expired', 409)
        self.assertEqual(GamePlay.objects.get(pk=first['play']).status, 'RUNNING')

    def test_foreign_and_invalid_sessions_cannot_start_or_retrieve_a_play(self):
        other = get_user_model().objects.create_user(username='foreign-session')
        foreign = self.prepare_user(other)
        self.start()
        self.assert_rejected('session_expired', 409, session=foreign.pk)
        self.assert_rejected('invalid_platform_session', 400, session='invalid')
        self.assertEqual(GamePlay.objects.filter(user=self.user).count(), 1)

    def test_another_account_with_the_same_request_uuid_gets_its_own_play(self):
        first = self.start()
        other = get_user_model().objects.create_user(username='independent-receipt')
        session = self.prepare_user(other)
        response = main.action(other, {**self.data, 'ticket': ticket(other)}, session.pk)
        self.assertNotEqual(response['play'], first['play'])
        self.assertEqual(GamePlay.objects.get(pk=response['play']).user_id, other.pk)
        with self.assertRaises(LearningError):
            main.action(other, self.data, session.pk)
        self.assertEqual(GamePlay.objects.count(), 2)

    def test_replay_revalidates_closed_hours_pause_and_prerequisites(self):
        first = self.start()
        with patch.object(main, 'window', return_value={'phase': 'closed'}):
            self.assert_rejected('event_blocked', 423)
        with patch('campus_control.service.read_state', return_value={'paused': True}):
            self.assert_rejected('campus_frozen', 423)
        EventProgress.objects.filter(user=self.user, event_id=main.uid('3')).delete()
        self.assert_rejected('prerequisite', 409)
        self.assertEqual(GamePlay.objects.get(pk=first['play']).status, 'RUNNING')
        self.assertEqual(GamePlay.objects.filter(user=self.user).count(), 1)

    def test_replay_revalidates_expired_or_wrong_proximity_proof(self):
        self.start()
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp()-901):
            expired = ticket(self.user)
        for proof in (expired, ticket(self.user, 'room2'), None):
            with self.subTest(proof=proof):
                self.assert_rejected('interaction_expired', 409, {**self.data, 'ticket': proof})
        self.assertEqual(GamePlay.objects.filter(user=self.user).count(), 1)

    def test_replay_revalidates_game_enabled(self):
        self.start()
        GameDefinition.objects.filter(game_code='kkuwang_touch').update(is_enabled=False)
        with self.assertRaises(LearningError):
            self.start()
        self.assertEqual(GamePlay.objects.filter(user=self.user).count(), 1)

    def test_investigation_receipt_succeeds_even_when_status_reads_fail(self):
        EventProgress.objects.filter(user=self.user).delete()
        data = {'action': 'investigate', 'ticket': ticket(self.user, 'investigate')}
        with patch.object(main, 'status', side_effect=RuntimeError('snapshot unavailable')) as status:
            first = main.action(self.user, data, self.session.pk)
            replay = main.action(self.user, data, self.session.pk)
        status.assert_not_called()
        self.assertEqual(first, {'first': True, 'correct': True})
        self.assertEqual(replay, {'first': False, 'correct': True})
        self.assertEqual(EventProgress.objects.filter(user=self.user).count(), 1)
        self.assertEqual(EventProgress.objects.get(user=self.user).session_id, self.session.pk)

    def test_http_start_uses_server_session_and_returns_receipt_only(self):
        self.client.force_login(self.user)
        with patch.object(main, 'status', side_effect=RuntimeError('snapshot unavailable')) as status:
            first = self.client.post('/api/event-main/action/', self.data, content_type='application/json')
            replay = self.client.post('/api/event-main/action/', self.data, content_type='application/json')
        status.assert_not_called()
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(replay.json(), first.json())
        self.assertNotIn('state', first.json())
        self.assertEqual(str(GamePlay.objects.get(pk=first.json()['play']).session_id), self.client.session[SESSION_KEY])


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL account row locking')
class ConcurrentStartReceiptTests(ReceiptFixtures, TransactionTestCase):
    def test_simultaneous_identical_requests_create_one_play_and_one_receipt(self):
        barrier = Barrier(2, timeout=10)
        def start():
            close_old_connections()
            try:
                barrier.wait()
                return main.action(self.user, dict(self.data), self.session.pk)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(start) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(GamePlay.objects.filter(user=self.user).count(), 1)
        self.assertEqual(GamePlay.objects.get(user=self.user).evidence['request_uuid'], self.data['request_uuid'])
