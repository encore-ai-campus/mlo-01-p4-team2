from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4

from django.test import SimpleTestCase
from django.utils import timezone
from event_story import game_services as games


class StandaloneGameLimitTests(SimpleTestCase):
    def play(self,code,limit=40000):
        return SimpleNamespace(pk=uuid4(),game=SimpleNamespace(game_code=code),session_id=uuid4(),
            evidence=({'deck':list(range(8))*2} if code=='term_match' else {}) | ({'time_limit_ms':limit} if limit is not None else {}),
            started_at=timezone.now()-timedelta(seconds=100),status='RUNNING',duration_ms=None,ended_at=None,save=Mock())

    def picks(self,duration):
        return [[i,i+8,int(duration*(i+1)/8)] for i in range(8)]

    def finish(self,play,duration,status='SUCCESS'):
        user=SimpleNamespace(pk=uuid4());data={'status':status,'duration_ms':duration}
        if play.game.game_code=='term_match':data['picks']=self.picks(duration) if status=='SUCCESS' else []
        with patch.object(games,'require_game_open'),patch.object(games,'get_user_model'),patch.object(games,'active_session',return_value=SimpleNamespace(pk=play.session_id)),patch.object(games.GamePlay.objects,'select_for_update') as query,patch.object(games,'record_play_ended'),patch.object(games,'complete_achievement',return_value=True) as award:
            query.return_value.select_related.return_value.get.return_value=play
            result=games._finish_game.__wrapped__(user,play.pk,play.session_id,data)
            return result,award.called

    def test_default_limits_and_timer(self):
        self.assertEqual(games.CONFIGS['typing']['timeLimitMs'],40000)
        self.assertEqual(games.CONFIGS['term_match']['timeLimitMs'],40000)
        self.assertEqual(games.CONFIGS['timer']['targetMs'],11110)

    def test_typing_normal_success_does_not_relax_easter_egg(self):
        for duration in (11110,11111,20000,40000):
            with self.subTest(duration=duration):
                result,awarded=self.finish(self.play('typing'),duration)
                self.assertEqual(result['status'],'SUCCESS')
                self.assertEqual(awarded,duration<=11110)

    def test_term_normal_success_does_not_relax_easter_egg(self):
        for duration in (11110,11111,20000,40000):
            with self.subTest(duration=duration):
                result,awarded=self.finish(self.play('term_match'),duration)
                self.assertEqual(result['status'],'SUCCESS')
                self.assertEqual(awarded,duration<=11110)

    def test_both_games_reject_over_40_seconds(self):
        for code in ('typing','term_match'):
            with self.subTest(code=code),self.assertRaises(games.GameError):
                self.finish(self.play(code),40001)

    def test_term_match_new_timeout_is_40_seconds(self):
        result,awarded=self.finish(self.play('term_match'),40000,status='FAILED')
        self.assertEqual(result['status'],'FAILED');self.assertFalse(awarded)
        with self.assertRaises(games.GameError):self.finish(self.play('term_match'),11110,status='FAILED')

    def test_legacy_running_games_keep_their_original_limit(self):
        for code in ('typing','term_match'):
            with self.subTest(code=code):
                self.assertTrue(self.finish(self.play(code,None),11110)[1])
                with self.assertRaises(games.GameError):self.finish(self.play(code,None),11111)
        result,_=self.finish(self.play('term_match',None),11110,status='FAILED')
        self.assertEqual(result['status'],'FAILED')

    def test_timer_easter_egg_stays_exactly_11110(self):
        self.assertTrue(self.finish(self.play('timer',None),11110)[1])
        with self.assertRaises(games.GameError):self.finish(self.play('timer',None),40000)
