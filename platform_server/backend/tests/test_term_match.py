"""Prepared for later execution; no external services are required."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from eventing.models import PlatformSession
from event_story.achievement_services import MEMORY_ID
from event_story.game_services import GameError, finish_game, start_game
from event_story.models import EventProgress, GamePlay
from event_story.test_achievements import seed_definitions


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class TermMatchTests(TestCase):
    def setUp(self):
        seed_definitions()
        self.user = get_user_model().objects.create_user(username='term-player')
        self.other = get_user_model().objects.create_user(username='term-other')

    def play(self, user):
        now = timezone.now()
        session = PlatformSession.objects.create(user=user, started_at=now, last_seen_at=now)
        play = start_game(user, 'term_match', session.pk)
        GamePlay.objects.filter(pk=play['play_uuid']).update(started_at=now-timedelta(seconds=20))
        pairs = {}
        for index, term in enumerate(play['config']['deck']):
            pairs.setdefault(term, []).append(index)
        picks = [[*pair, (i+1)*1000] for i, pair in enumerate(pairs.values())]
        return session.pk, play, {'status':'SUCCESS', 'duration_ms':8000, 'picks':picks}

    def test_success_persists_proof_badge_order_and_retries_once(self):
        session, play, data = self.play(self.user)
        self.assertEqual(play['config']['timeLimitMs'], 11110)
        result = finish_game(self.user, play['play_uuid'], session, data)
        self.assertEqual(result['newly_completed'], [str(MEMORY_ID)])
        first = EventProgress.objects.get(user=self.user, event_id=MEMORY_ID)
        stamp = first.completed_at
        self.assertEqual(first.completion_order, 1)
        self.assertEqual(GamePlay.objects.get(pk=play['play_uuid']).evidence['picks'], data['picks'])
        self.assertEqual(finish_game(self.user, play['play_uuid'], session, data)['newly_completed'], [])
        for user in (self.other, self.user):
            session, play, data = self.play(user)
            replay = finish_game(user, play['play_uuid'], session, data)
            self.assertEqual((replay['status'], replay['newly_completed']), ('SUCCESS', []))
            self.assertEqual(GamePlay.objects.get(pk=play['play_uuid']).evidence['picks'], data['picks'])
        self.assertFalse(EventProgress.objects.filter(user=self.other, event_id=MEMORY_ID).exists())
        first.refresh_from_db();self.assertEqual(first.completed_at, stamp)
        self.assertEqual(first.completion_order, 1)

    def test_late_incomplete_and_reused_cards_cannot_unlock(self):
        session, play, data = self.play(self.user)
        for patch in ({'duration_ms':11111}, {'picks':[]}, {'picks':[[0,0,8000]]},
                      {'picks':data['picks']+[data['picks'][-1]]}):
            with self.assertRaises(GameError):
                finish_game(self.user, play['play_uuid'], session, {**data, **patch})
        self.assertFalse(EventProgress.objects.filter(event_id=MEMORY_ID).exists())
        self.assertEqual(GamePlay.objects.get(pk=play['play_uuid']).status, 'RUNNING')

    def test_timeout_and_interruption_do_not_unlock(self):
        for data in ({'status':'FAILED','duration_ms':11110,'picks':[]},
                     {'status':'INTERRUPTED','duration_ms':None}):
            session, play, _ = self.play(self.user)
            finish_game(self.user, play['play_uuid'], session, data)
        self.assertFalse(EventProgress.objects.filter(event_id=MEMORY_ID).exists())
