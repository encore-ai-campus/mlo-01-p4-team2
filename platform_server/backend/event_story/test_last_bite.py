from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from django.test import SimpleTestCase, TestCase
from django.contrib.auth import get_user_model
from pathlib import Path
from django.utils import timezone
from campus.consumers import CampusConsumer
from campus.motion import ControlError
from .achievement_services import FRAGILE_ID, complete_global_achievement, achievement_snapshot
from .models import EventProgress
from .test_achievements import seed_definitions

class LastBiteControlTests(SimpleTestCase):
    def consumer(self, remaining=1, distance=20, floor='campus', interior=None):
        world=SimpleNamespace(objects={'campus-guide-kkuwang':{}},distance=lambda o,p:distance)
        return SimpleNamespace(controller=SimpleNamespace(world=world,pose={'floor':floor},interior=interior,pending_transfer=None,input=Mock(),route=[]),pepero_remaining=remaining,user=SimpleNamespace(pk=7))
    async def test_preview_does_not_refill_and_rechecks_server_stock(self):
        for remaining in [0,1,2,10]:
            c=self.consumer(remaining);c.controller.world.objects['campus-guide-kkuwang']={'id':'guide'}
            with patch('campus.consumers.socket_db.run',new=AsyncMock(return_value={'already_claimed':False,'account_limited':False})):
                self.assertEqual((await CampusConsumer.kkuwang_last_bite(c,{}))['eligible'],remaining==1)
            self.assertEqual(c.pepero_remaining,remaining)
    async def test_preview_is_hidden_after_serverwide_winner(self):
        c=self.consumer();c.controller.world.objects['campus-guide-kkuwang']={'id':'guide'}
        with patch('campus.consumers.socket_db.run',new=AsyncMock(return_value={'already_claimed':True,'account_limited':False})) as completed:
            result=await CampusConsumer.kkuwang_last_bite(c,{})
        self.assertFalse(result['eligible']);self.assertTrue(result['already_claimed']);completed.assert_awaited_once()
    async def test_preview_is_hidden_if_account_already_earned_another_egg(self):
        c=self.consumer();c.controller.world.objects['campus-guide-kkuwang']={'id':'guide'}
        with patch('campus.consumers.socket_db.run',new=AsyncMock(return_value={'already_claimed':False,'account_limited':True})):
            result=await CampusConsumer.kkuwang_last_bite(c,{})
        self.assertFalse(result['eligible']);self.assertTrue(result['account_limited'])
    async def test_wrong_floor_distance_or_interior_cannot_claim(self):
        for kw in [{'floor':'rooftop'},{'distance':101},{'interior':{'missionRoom':1}}]:
            c=self.consumer(**kw);c.controller.world.objects['campus-guide-kkuwang']={'id':'guide'}
            with self.assertRaises(ControlError): await CampusConsumer.kkuwang_last_bite(c,{'claim':True})
    async def test_success_refills_and_next_claim_is_ineligible(self):
        c=self.consumer();c.controller.world.objects['campus-guide-kkuwang']={'id':'guide'}
        c.user=SimpleNamespace(pk=7);c.platform_session_uuid=None;c.player_id='test'
        c.publish_pepero_inventory=Mock();c.send_packet=AsyncMock();c.send_control_state=AsyncMock();c.send_poke_to=AsyncMock()
        c.current_poke_scope=lambda:{'floor':'campus'};c.poke_audience=lambda s:[]
        with patch('campus.consumers.socket_db.run',new=AsyncMock(side_effect=[{'already_claimed':False,'account_limited':False},{'eligible':True,'already_claimed':False,'account_limited':False,'achievements':[],'newly_completed':[str(FRAGILE_ID)]},{'already_claimed':True,'account_limited':False}])) as save:
            result=await CampusConsumer.kkuwang_last_bite(c,{'claim':True})
            self.assertEqual(result['remaining'],10);self.assertEqual(c.pepero_remaining,10);self.assertEqual(save.await_count,2)
            self.assertFalse((await CampusConsumer.kkuwang_last_bite(c,{'claim':True}))['eligible'])
        c.send_poke_to.assert_awaited_once()
    async def test_claim_lost_to_another_server_user_does_not_refill(self):
        c=self.consumer();c.controller.world.objects['campus-guide-kkuwang']={'id':'guide'}
        c.user=SimpleNamespace(pk=7);c.platform_session_uuid=None;c.player_id='test'
        with patch('campus.consumers.socket_db.run',new=AsyncMock(side_effect=[{'already_claimed':False,'account_limited':False},{'eligible':False,'already_claimed':True,'account_limited':False}])) as save:
            result=await CampusConsumer.kkuwang_last_bite(c,{'claim':True})
        self.assertFalse(result['eligible']);self.assertTrue(result['already_claimed']);self.assertEqual(c.pepero_remaining,1)
        self.assertEqual(save.await_count,2)

class LastBiteAchievementTests(TestCase):
    def test_only_first_account_on_server_can_earn_last_bite(self):
        seed_definitions();winner=get_user_model().objects.create_user(username='last-bite-winner')
        next_user=get_user_model().objects.create_user(username='last-bite-next')
        now=timezone.now()
        self.assertTrue(complete_global_achievement(winner,FRAGILE_ID,now))
        self.assertFalse(complete_global_achievement(next_user,FRAGILE_ID,now))
        rows=achievement_snapshot(winner)
        last_bite=next(row for row in rows if row['event_uuid']==str(FRAGILE_ID))
        self.assertEqual(last_bite['name'],'부서지기 직전')
        self.assertEqual(last_bite['image_url'],'/static/campus/assets/achievements/fragile-pepero-v1.webp')
        asset=Path(__file__).resolve().parents[2]/'frontend'/'dist'/'assets'/'achievements'/'fragile-pepero-v1.webp'
        self.assertTrue(asset.is_file())
        next_row=next(row for row in achievement_snapshot(next_user) if row['event_uuid']==str(FRAGILE_ID))
        self.assertFalse(next_row['achieved'])
        self.assertEqual(EventProgress.objects.filter(event_id=FRAGILE_ID,status='COMPLETED').count(),1)
