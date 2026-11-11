from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from event_story.achievement_services import TYPING_ID
from event_story.game_services import GameError, finish_game, start_game
from event_story.models import EventProgress, GamePlay
from event_story.test_achievements import seed_definitions
from eventing.events import SESSION_KEY
from eventing.models import PlatformSession


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class TypingDeadlineTests(TestCase):
    def setUp(self):
        seed_definitions()
        self.user = get_user_model().objects.create_user(username='typing-deadline')
        self.session = PlatformSession.objects.create(user=self.user, started_at=timezone.now(), last_seen_at=timezone.now())

    def start(self):
        play = start_game(self.user, 'typing', self.session.pk)
        GamePlay.objects.filter(pk=play['play_uuid']).update(started_at=timezone.now()-timedelta(seconds=70))
        return play

    def finish(self, play, status, duration):
        return finish_game(self.user, play['play_uuid'], self.session.pk, {'status': status, 'duration_ms': duration})

    def test_typing_config_includes_deadline_and_existing_accuracy(self):
        self.assertEqual(self.start()['config'], {'minimumAccuracy': 95, 'timeLimitMs': 11110})

    def test_success_before_and_at_deadline_preserves_first_award_on_replay(self):
        for duration in (11109, 11110):
            with self.subTest(duration=duration):
                play = self.start()
                first = self.finish(play, 'SUCCESS', duration)
                self.assertEqual(first['status'], 'SUCCESS')
                row = EventProgress.objects.get(user=self.user, event_id=TYPING_ID)
                before = (row.completed_at, row.completion_order, row.session_id)
                retry = self.finish(play, 'SUCCESS', duration)
                self.assertEqual(retry['newly_completed'], [])
                self.assertEqual(first['ended_at'], retry['ended_at'])
                row.refresh_from_db()
                self.assertEqual((row.completed_at, row.completion_order, row.session_id), before)
        self.assertEqual(EventProgress.objects.filter(user=self.user, event_id=TYPING_ID).count(), 1)

    def test_overtime_success_is_rejected_without_awarding_or_settling_the_play(self):
        for duration in (11111, 20000, 60000):
            with self.subTest(duration=duration):
                play = self.start()
                with self.assertRaisesMessage(GameError, '타이핑은 11.11초 안에 완료해야 해요.'):
                    self.finish(play, 'SUCCESS', duration)
                row = GamePlay.objects.get(pk=play['play_uuid'])
                self.assertEqual((row.status, row.duration_ms, row.ended_at), ('RUNNING', None, None))
                self.assertFalse(EventProgress.objects.filter(user=self.user, event_id=TYPING_ID).exists())
                failure = self.finish(play, 'FAILED', duration)
                self.assertEqual(failure['newly_completed'], [])
                self.assertEqual(failure['status'], 'FAILED')
                self.assertEqual(self.finish(play, 'FAILED', duration)['ended_at'], failure['ended_at'])

    def test_http_cannot_award_for_an_overtime_success_claim(self):
        self.client.force_login(self.user)
        session = self.client.session
        session[SESSION_KEY] = str(self.session.pk);session.save()
        play = self.start()
        response = self.client.post(f"/api/event-story/plays/{play['play_uuid']}/result/", {
            'status': 'SUCCESS', 'duration_ms': 11111,
        }, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('11.11초', response.json()['error'])
        self.assertFalse(EventProgress.objects.filter(user=self.user, event_id=TYPING_ID).exists())

    def test_existing_overtime_award_is_not_revoked_or_reawarded(self):
        play = self.start()
        ended = timezone.now()
        GamePlay.objects.filter(pk=play['play_uuid']).update(status='SUCCESS', duration_ms=20000, ended_at=ended)
        award = EventProgress.objects.create(user=self.user, event_id=TYPING_ID, status='COMPLETED',
            completed_at=ended, completion_order=1, session=self.session)
        result = self.finish(play, 'SUCCESS', 20000)
        self.assertEqual(result['newly_completed'], [])
        award.refresh_from_db()
        self.assertEqual((award.completed_at, award.completion_order, award.session_id), (ended, 1, self.session.pk))
