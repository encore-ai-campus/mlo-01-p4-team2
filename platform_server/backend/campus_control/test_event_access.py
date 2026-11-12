import json
from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase,override_settings,Client
from django.contrib.auth import get_user_model
from django.utils import timezone
from accounts.session_policy import start_login
from campus_control import event_access as gate,service
from campus_control.models import EventAccessState,FreezeState
from event_story import main
from event_story.achievement_services import complete_achievement,GREETING_ID,record_manager_action,record_learning_completion
from event_story.models import EventProgress,ManagerInteraction
from learning.services import LearningError

class EventAccessTests(TestCase):
 def setUp(self):
  service.invalidate();self.admin=get_user_model().objects.create_user(username='event-operator',is_staff=True)
  self.user=get_user_model().objects.create_user(username='event-visitor')
 def tearDown(self):service.invalidate()
 def login(self,user,client=None):
  c=client or self.client;c.force_login(user);session=c.session;start_login(session,user);session.save();return c
 def post(self,mode,revision=0):return self.client.post('/api/campus/freeze/',json.dumps({'eventMode':mode,'eventRevision':revision}),content_type='application/json')
 def test_calendar_boundaries_in_korean_time(self):
  for when,opened in [(gate.START-timedelta(microseconds=1),False),(gate.START,True),(gate.START+timedelta(hours=10,minutes=30),True),(gate.END-timedelta(microseconds=1),True),(gate.END,False),(gate.END+timedelta(days=1),False)]:
   with self.subTest(when=when):self.assertEqual(gate.snapshot(when)['open'],opened)
 def test_utc_boundary_matches_kst(self):
  self.assertTrue(gate.snapshot(datetime.fromisoformat('2026-11-10T15:00:00+00:00'))['open'])
  self.assertFalse(gate.snapshot(datetime.fromisoformat('2026-11-11T15:00:00+00:00'))['open'])
 def test_force_open_before_and_after(self):
  gate.change(self.admin,'OPEN',0)
  self.assertTrue(gate.snapshot(gate.START-timedelta(days=2))['open']);self.assertTrue(gate.snapshot(gate.END+timedelta(days=2))['open'])
 def test_force_closed_even_on_event_day(self):
  gate.change(self.admin,'CLOSED',0);self.assertFalse(gate.snapshot(gate.START)['open'])
 def test_return_to_auto_and_persistent_revision(self):
  gate.change(self.admin,'OPEN',0);self.assertEqual(gate.snapshot()['revision'],1)
  gate.change(self.admin,'AUTO',1);row=EventAccessState.objects.get(pk=1)
  self.assertEqual(row.mode,'AUTO');self.assertEqual(row.updated_by,self.admin);self.assertEqual(row.revision,2)
 def test_stale_operator_update_rejected(self):
  gate.change(self.admin,'OPEN',0)
  with self.assertRaises(gate.RevisionConflict):gate.change(self.admin,'CLOSED',0)
  self.assertEqual(gate.snapshot()['mode'],'OPEN')
 def test_staff_api_and_state_visible(self):
  self.login(self.admin);r=self.post('OPEN');self.assertEqual(r.status_code,200);self.assertEqual(r.json()['eventAccess']['mode'],'OPEN')
  self.assertEqual(self.client.get('/api/campus/freeze/').json()['eventAccess']['mode'],'OPEN')
 def test_ordinary_user_cannot_change(self):
  self.login(self.user);self.assertEqual(self.post('OPEN').status_code,403)
  self.assertEqual(gate.snapshot()['mode'],'AUTO')
 def test_anonymous_cannot_change(self):self.assertEqual(self.post('OPEN').status_code,401)
 def test_csrf_required_for_admin_write(self):
  c=self.login(self.admin,Client(enforce_csrf_checks=True))
  self.assertEqual(c.post('/api/campus/freeze/',json.dumps({'eventMode':'OPEN','eventRevision':0}),content_type='application/json').status_code,403)
 def test_invalid_mode_and_revision(self):
  self.login(self.admin)
  self.assertEqual(self.post('INVALID').status_code,400);self.assertEqual(self.post('OPEN',True).status_code,400)
 def test_api_revision_conflict(self):
  self.login(self.admin);self.post('OPEN');r=self.post('CLOSED');self.assertEqual(r.status_code,409);self.assertEqual(r.json()['eventAccess']['mode'],'OPEN')
 @override_settings(DEBUG=True,EVENT_STORY_PREVIEW=True)
 def test_debug_preview_does_not_bypass_closed_gate(self):
  gate.change(self.admin,'CLOSED',0)
  with self.assertRaises(LearningError):main.ensure_open()
 def test_closed_main_mutation_is_rejected(self):
  gate.change(self.admin,'CLOSED',0)
  with self.assertRaises(LearningError):main.action(self.user,{'action':'investigate','ticket':''},None)
  self.assertFalse(EventProgress.objects.filter(user=self.user).exists())
 def test_closed_gate_keeps_existing_badges(self):
  gate.change(self.admin,'OPEN',0);self.assertTrue(complete_achievement(self.user,GREETING_ID,timezone.now()))
  before=list(EventProgress.objects.filter(user=self.user).values_list('pk','status','completed_at'))
  gate.change(self.admin,'CLOSED',1);self.assertFalse(complete_achievement(self.admin,GREETING_ID,timezone.now()))
  self.assertEqual(before,list(EventProgress.objects.filter(user=self.user).values_list('pk','status','completed_at')))
 def test_closed_manager_actions_do_not_accumulate(self):
  gate.change(self.admin,'CLOSED',0)
  record_manager_action(self.user,'not-needed-while-closed','poke')
  self.assertFalse(ManagerInteraction.objects.filter(user=self.user).exists())
 def test_closed_learning_does_not_award_or_raise(self):
  gate.change(self.admin,'CLOSED',0)
  self.assertIsNone(record_learning_completion(self.user,SimpleNamespace()))
 def test_closed_status_does_not_recover_progress(self):
  gate.change(self.admin,'CLOSED',0)
  with patch('event_story.main._recover_finished_challenge') as write:
   main.recover_finished_challenge(self.user);write.assert_not_called()
 def test_existing_lecture_freeze_still_blocks_forced_open(self):
  gate.change(self.admin,'OPEN',0);FreezeState.objects.update_or_create(pk=1,defaults={'paused':True});service.invalidate()
  with self.assertRaises(LearningError) as caught:main.ensure_open()
  self.assertEqual(caught.exception.code,'campus_frozen')
