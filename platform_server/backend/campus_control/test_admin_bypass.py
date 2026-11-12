import asyncio
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from django.test import TestCase,SimpleTestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from accounts.session_policy import start_login
from campus_control import service,event_access
from campus_control.models import FreezeState
from campus_control.middleware import CampusFreezeMiddleware
from campus_control.tests import FreezeSocketTests
from event_story import main
from event_story.models import GameDefinition,GamePlay
from event_story.interrupt import interrupt_running
from eventing.models import PlatformSession
from django.db import transaction
from learning.services import LearningError

class AdminBypassTests(TestCase):
 def setUp(self):
  service.invalidate();self.staff=get_user_model().objects.create_user(username='bypass-admin',is_staff=True)
  self.user=get_user_model().objects.create_user(username='bypass-user');FreezeState.objects.create(pk=1,paused=True)
 def tearDown(self):service.invalidate()
 def login(self,user):
  self.client.force_login(user);s=self.client.session;start_login(s,user);s.save()
 def test_admin_http_allowed_ordinary_user_still_frozen(self):
  self.login(self.staff);self.assertEqual(self.client.get('/api/event-main/status/').status_code,200)
  self.login(self.user);self.assertEqual(self.client.get('/api/event-main/status/').status_code,423)
 def test_personal_freeze_payload_keeps_shared_pause(self):
  self.login(self.staff);s=self.client.get('/api/campus/freeze/').json()
  self.assertTrue(s['paused']);self.assertFalse(s['blocked']);self.assertTrue(s['adminBypass'])
  self.login(self.user);s=self.client.get('/api/campus/freeze/').json();self.assertTrue(s['paused']);self.assertTrue(s['blocked']);self.assertFalse(s['adminBypass'])
 def test_flags_from_client_do_not_grant_admin(self):
  self.login(self.user);r=self.client.get('/api/event-main/status/?is_staff=true&adminBypass=true');self.assertEqual(r.status_code,423)
 def test_inactive_and_anonymous_not_exempt(self):
  self.staff.is_active=False;self.assertFalse(service.admin_exempt(self.staff));self.assertFalse(service.admin_exempt(None))
 def test_database_unavailable_is_not_bypassed(self):
  self.assertTrue(service.for_user({'paused':True,'unavailable':True},self.staff)['blocked'])
 def test_admin_game_checks_bypass_freeze_but_not_event_date(self):
  event_access.change(self.staff,'OPEN',0);main.ensure_open(self.staff)
  with self.assertRaises(LearningError):main.ensure_open(self.user)
  event_access.change(self.staff,'CLOSED',1)
  with self.assertRaises(LearningError) as caught:main.ensure_open(self.staff)
  self.assertEqual(caught.exception.code,'event_blocked')
 def test_admin_active_game_kept_ordinary_game_interrupted(self):
  game=GameDefinition.objects.create(game_code='admin-bypass-test',game_name='test')
  for user in [self.staff,self.user]:
   session=PlatformSession.objects.create(user=user,started_at=timezone.now(),last_seen_at=timezone.now())
   GamePlay.objects.create(user=user,game=game,session=session)
  with transaction.atomic():interrupt_running()
  self.assertEqual(GamePlay.objects.get(user=self.staff).status,'RUNNING');self.assertEqual(GamePlay.objects.get(user=self.user).status,'INTERRUPTED')

class AdminSocketBypassTests(SimpleTestCase):
 def consumer(self,staff):
  c=FreezeSocketTests().consumer();c.user=SimpleNamespace(is_authenticated=True,is_active=True,is_staff=staff)
  c.control_lock=asyncio.Lock();c.last_seen=0;c.poke_input_scope=None;c.tick_poke=AsyncMock();return c
 async def test_admin_input_allowed_while_user_input_blocked(self):
  import json
  for staff in [True,False]:
   c=self.consumer(staff)
   with patch('campus.consumers.freeze_state',AsyncMock(return_value={'paused':True,'revision':1,'message':'강의 중'})):
    await c.handle_message(json.dumps({'type':'input','keys':['d'],'freezeRevision':1,'adminBypass':True}))
   self.assertEqual(c.controller.keys,{'d'} if staff else set())
 async def test_admin_transition_keeps_held_controls(self):
  c=self.consumer(True)
  with patch('campus.consumers.freeze_state',AsyncMock(return_value={'paused':True,'revision':1,'message':'강의 중'})):
   state=await c.sync_freeze()
  self.assertFalse(state['blocked']);self.assertTrue(c.controller.keys);c.stop_poke.assert_not_awaited()
 async def test_admin_chat_reaches_normal_validation(self):
  from campus.chat import ChatConsumer
  for staff,expected in [(True,'메시지를 입력해 주세요.'),(False,'강의 중')]:
   c=ChatConsumer();c.user=SimpleNamespace(is_authenticated=True,is_active=True,is_staff=staff);c.chat_error=AsyncMock()
   with patch('campus_control.service.async_state',AsyncMock(return_value={'paused':True,'revision':1,'message':'강의 중'})):
    await c.receive_chat({'adminBypass':True})
   c.chat_error.assert_awaited_once_with(expected)
