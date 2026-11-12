import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.test import Client, RequestFactory, TestCase, SimpleTestCase, TransactionTestCase
from campus.motion import MotionController
from campus.consumers import CampusConsumer
from . import service
from .models import FreezeState, FreezeChange
from .middleware import CampusFreezeMiddleware


class FreezeApiTests(TestCase):
    def setUp(self):
        service.invalidate()
        self.staff = get_user_model().objects.create_user(username='freeze-staff', is_staff=True)
        self.user = get_user_model().objects.create_user(username='freeze-user')
        self.login(self.staff)

    def login(self, user):
        self.client.force_login(user)
        from accounts.session_policy import start_login
        session = self.client.session
        start_login(session, user)
        session.save()

    def tearDown(self):
        service.invalidate()

    def toggle(self, paused, revision=0):
        return self.client.post('/api/campus/freeze/', json.dumps({'paused':paused,'revision':revision}), content_type='application/json')

    def test_permissions_validation_and_persistent_revision(self):
        self.assertEqual(Client().get('/api/campus/freeze/').status_code,401)
        self.login(self.user)
        self.assertEqual(self.toggle(True).status_code,403)
        self.login(self.staff)
        self.assertEqual(self.client.post('/api/campus/freeze/',json.dumps({'paused':'true','revision':0}),content_type='application/json').status_code,400)
        self.assertEqual(self.toggle(True).status_code,200)
        self.assertTrue(FreezeState.objects.get(pk=1).paused)
        service.invalidate()  # Another process observes the same durable state.
        self.assertTrue(service.read_state()['paused'])
        self.assertEqual(self.toggle(False,0).status_code,409)
        self.assertTrue(FreezeState.objects.get(pk=1).paused)
        self.assertEqual(self.toggle(True,1).status_code,200)
        self.assertEqual(FreezeChange.objects.count(),1)  # Idempotent repeated setting.
        self.assertEqual(self.toggle(False,1).status_code,200)
        self.assertEqual(FreezeChange.objects.count(),2)
        self.assertEqual(service.read_state()['revision'],2)

    def test_csrf_still_required(self):
        guarded=Client(enforce_csrf_checks=True);guarded.force_login(self.staff)
        response=guarded.post('/api/campus/freeze/',json.dumps({'paused':True,'revision':0}),content_type='application/json')
        self.assertEqual(response.status_code,403)

    def test_all_content_requests_blocked_but_logout_and_status_available(self):
        self.toggle(True)
        self.login(self.user)
        for path in ['/api/learning/sessions/','/api/community/posts/','/api/messages/status/','/content/youtube/open/','/api/ads/placements/rooftop/','/api/event-story/status/']:
            self.assertEqual(self.client.get(path).status_code,423,path)
        self.assertEqual(self.client.get('/api/campus/freeze/').status_code,200)
        self.login(self.staff);self.assertEqual(self.toggle(False,1).status_code,200)
        self.assertNotEqual(self.client.get('/api/learning/sessions/').status_code,423)

    def test_fresh_reads_are_cached_once_per_process(self):
        service.read_state(force=True)
        with self.assertNumQueries(0):
            for _ in range(100):self.assertFalse(service.read_state()['paused'])


class FreezeSocketTests(SimpleTestCase):
    def consumer(self):
        c=CampusConsumer();c.controller=MotionController({'floor':'campus','x':130,'y':100,'dir':'right'})
        c.freeze_signature=(0,False,False);c.controller.keys={'w','shift','control'};c.controller.route=[{'x':160,'y':100}]
        c.controller.pose.update(moving=True,running=True,guarding=True)
        c.poke_requested=True;c.poke_started=None;c.send_packet=AsyncMock();c.stop_poke=AsyncMock()
        c.receive_control=AsyncMock();c.receive_movement=AsyncMock();c.current_identity=AsyncMock(return_value={})
        c.policy_status={'expiresAt':time.time()*1000+60000};c.logout_prepared=False;c.server_control=True
        return c

    async def test_pause_stops_controls_and_invalidates_old_input_after_resume(self):
        c=self.consumer()
        with patch('campus.consumers.freeze_state',AsyncMock(return_value={'paused':True,'revision':1,'message':'강의 중'})):
            await c.handle_message(json.dumps({'type':'control','action':'jump','requestId':'j','data':{}}))
        c.receive_control.assert_not_awaited()
        self.assertFalse(c.controller.keys);self.assertFalse(c.controller.route)
        self.assertFalse(c.controller.pose['running']);self.assertFalse(c.controller.pose['guarding'])
        self.assertFalse(c.poke_requested)
        with patch('campus.consumers.freeze_state',AsyncMock(return_value={'paused':False,'revision':2,'message':'강의 중'})):
            await c.handle_message(json.dumps({'type':'input','keys':['w'],'freezeRevision':0}))
            self.assertFalse(c.controller.keys)
            c.control_lock=asyncio.Lock();c.last_seen=0;c.poke_input_scope=None;c.tick_poke=AsyncMock()
            await c.handle_message(json.dumps({'type':'input','keys':['w'],'freezeRevision':2}))
        self.assertEqual(c.controller.keys,{'w'})

    async def test_logout_allowed_while_paused(self):
        c=self.consumer()
        with patch('campus.consumers.freeze_state',AsyncMock(return_value={'paused':True,'revision':1,'message':'강의 중'})):
            await c.handle_message(json.dumps({'type':'control','action':'prepare_logout','requestId':'leave','data':{}}))
        c.receive_control.assert_awaited_once()

    async def test_chat_bypasses_cannot_store_during_pause(self):
        from campus.chat import ChatConsumer
        c=ChatConsumer();c.chat_error=AsyncMock()
        with patch('campus_control.service.async_state',AsyncMock(return_value={'paused':True,'revision':1,'message':'강의 중'})):
            await c.receive_chat({'text':'must not store'})
        c.chat_error.assert_awaited_once_with('강의 중')


class FreezeLiveTests(TransactionTestCase):
    def setUp(self):
        from tests.factories import create_profile
        from accounts.session_policy import start_login
        from campus import consumers
        consumers.room = consumers.CampusRoom()
        service.invalidate()
        self.user=get_user_model().objects.create_user(username='freeze-live')
        self.staff=get_user_model().objects.create_user(username='freeze-live-admin',is_staff=True)
        create_profile(user=self.user,nickname='Freeze Test',cohort='mlo-1',setup_complete=True,
                               last_position={'floor':'campus','x':2000,'y':900,'dir':'right'})
        self.client.force_login(self.user)
        session=self.client.session;start_login(session,self.user);session.save()

    def tearDown(self):
        service.invalidate()

    async def test_active_socket_stops_without_new_input_and_rejects_stale_resume(self):
        from channels.testing import WebsocketCommunicator
        from django.conf import settings
        from config.asgi import application
        from campus import consumers, socket_db
        key=self.client.cookies[settings.SESSION_COOKIE_NAME].value
        socket=WebsocketCommunicator(application,'/ws/movement/?control=server',headers=[
            (b'origin',b'http://127.0.0.1'),(b'host',b'127.0.0.1'),
            (b'cookie',f'{settings.SESSION_COOKIE_NAME}={key}'.encode())])
        async def packet(kind, predicate=lambda p:True):
            for _ in range(100):
                result=await socket.receive_json_from(timeout=3)
                if result.get('type')==kind and predicate(result):return result
            self.fail('Packet not received '+kind)
        try:
            self.assertTrue((await socket.connect())[0])
            welcome=await packet('welcome');player_id=welcome['selfId']
            await socket.send_json_to({'type':'input','keys':['w','shift'],'freezeRevision':0})
            await asyncio.sleep(.06)
            await socket_db.run(service.change,self.staff,True,'강의 중',0)
            await packet('campus_freeze',lambda p:p['paused'])
            controller=consumers.room.controllers[player_id]
            self.assertFalse(controller.keys)
            pose=controller.pose.copy();await asyncio.sleep(.12)
            self.assertEqual((pose['x'],pose['y']),(controller.pose['x'],controller.pose['y']))
            await socket.send_json_to({'type':'control','requestId':'blocked-jump','action':'jump',
                                      'freezeRevision':1,'data':{'context':'world','floor':'campus'}})
            self.assertIn('error',await packet('control_result'))
            await socket_db.run(service.change,self.staff,False,'강의 중',1)
            await packet('campus_freeze',lambda p:not p['paused'] and p['revision']==2)
            await socket.send_json_to({'type':'input','keys':['d'],'freezeRevision':0})
            await asyncio.sleep(.06);self.assertFalse(controller.keys)
            await socket.send_json_to({'type':'input','keys':['d'],'freezeRevision':2})
            await asyncio.sleep(.06);self.assertEqual(controller.keys,{'d'})
        finally:
            await socket.disconnect()
