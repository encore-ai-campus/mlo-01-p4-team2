from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from campus_control import service
from campus_control.models import EventAccessState, FreezeState
from eventing.models import PlatformSession
from learning.services import LearningError
from . import main
from .game_services import start_game, finish_game, GameError, require_open
from .models import GamePlay, EventProgress
from .test_achievements import seed_definitions


class StandaloneAccessTests(TestCase):
    def setUp(self):
        seed_definitions()
        self.user = get_user_model().objects.create_user(username='standalone-access')
        now = timezone.now()
        self.session = PlatformSession.objects.create(user=self.user, started_at=now, last_seen_at=now)
        FreezeState.objects.update_or_create(pk=1, defaults={'paused': False, 'mode': 'MANUAL'})
        service.invalidate()
        self.addCleanup(service.invalidate)

    def test_all_three_games_start_and_finish_under_closed_event(self):
        for mode in ('CLOSED', 'AUTO'):
            EventAccessState.objects.update_or_create(pk=1, defaults={'mode': mode})
            for code in ('timer', 'typing', 'term_match'):
                with self.subTest(mode=mode, code=code):
                    play = start_game(self.user, code, self.session.pk)
                    GamePlay.objects.filter(pk=play['play_uuid']).update(started_at=timezone.now()-timedelta(seconds=16))
                    payload = {'status': 'FAILED', 'duration_ms': 11110 if code=='term_match' else 0}
                    if code == 'term_match':
                        payload['picks'] = []
                    result = finish_game(self.user, play['play_uuid'], self.session.pk, payload)
                    self.assertEqual(result['status'], 'FAILED')
                    self.assertEqual(result['newly_completed'], [])

    def test_closing_event_during_game_keeps_result_submission_available(self):
        EventAccessState.objects.update_or_create(pk=1, defaults={'mode': 'OPEN'})
        play = start_game(self.user, 'timer', self.session.pk)
        EventAccessState.objects.filter(pk=1).update(mode='CLOSED')
        GamePlay.objects.filter(pk=play['play_uuid']).update(started_at=timezone.now()-timedelta(seconds=12))
        result = finish_game(self.user, play['play_uuid'], self.session.pk,
                             {'status': 'SUCCESS', 'duration_ms': 11110})
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(result['newly_completed'], [])
        self.assertFalse(EventProgress.objects.filter(user=self.user).exists())

    def test_main_and_hidden_event_gate_is_still_enforced(self):
        EventAccessState.objects.update_or_create(pk=1, defaults={'mode': 'CLOSED'})
        with self.assertRaises(LearningError):
            main.ensure_open(self.user)
        with self.assertRaises(GameError):
            require_open(self.user)

    def test_lecture_pause_still_blocks_standalone_games(self):
        FreezeState.objects.filter(pk=1).update(paused=True)
        service.invalidate()
        for code in ('timer', 'typing', 'term_match'):
            with self.subTest(code=code), self.assertRaises(GameError) as caught:
                start_game(self.user, code, self.session.pk)
            self.assertEqual(caught.exception.status, 423)
