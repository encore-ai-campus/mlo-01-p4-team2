import json
from datetime import datetime,timedelta
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from accounts.session_policy import start_login
from campus_control import service,schedule
from campus_control.models import FreezeState,FreezeChange

_scheduled = schedule.snapshot

def moment(hour,minute=0,second=0):return datetime(2026,11,11,hour,minute,second,tzinfo=schedule.TZ)

class FreezeScheduleTests(TestCase):
 def setUp(self):
  service.invalidate();self.admin=get_user_model().objects.create_user(username='schedule-admin',is_staff=True)
 def tearDown(self):service.invalidate()
 def test_each_lesson_boundary_and_every_minute(self):
  for minute in range(24*60):
   now=moment(minute//60,minute%60)
   expected=minute<510 or minute>=1320 or any(h*60+m<=minute<eh*60+em for h,m,eh,em in schedule.LESSONS)
   self.assertEqual(schedule.snapshot(now)['paused'],expected,now.isoformat())
 def test_open_and_close_second_boundaries(self):
  for now,paused in [(moment(8,29,59),True),(moment(8,30),False),(moment(9),True),(moment(9,49,59),True),(moment(9,50),False),(moment(21,59,59),False),(moment(22),True)]:
   self.assertEqual(schedule.snapshot(now)['paused'],paused,now.isoformat())
 def test_lunch_and_evening_are_open(self):
  for h,m in [(12,50),(13,0),(13,50),(17,50),(18,0),(21,59)]:self.assertFalse(schedule.snapshot(moment(h,m))['paused'])
 def test_korean_time_used_for_utc(self):
  self.assertTrue(schedule.snapshot(datetime.fromisoformat('2026-11-11T00:00:00+00:00'))['paused'])
  self.assertFalse(schedule.snapshot(datetime.fromisoformat('2026-11-10T23:30:00+00:00'))['paused'])
 def test_class_message_contains_resume_time(self):
  s=schedule.snapshot(moment(10,20));self.assertEqual(s['phase'],'class');self.assertIn('강의 시간에는 이용할 수 없습니다',s['message']);self.assertIn('10:50',s['message'])
 def test_after_hours_next_open_is_tomorrow(self):
  s=schedule.snapshot(moment(22));self.assertEqual(s['nextResumeAt'],'2026-11-12T08:30:00+09:00')
 def test_auto_transition_persists_revision_and_invalidates_stale_inputs(self):
  FreezeState.objects.create(pk=1,mode='AUTO',paused=False,message='캠퍼스를 이용할 수 있어요.')
  with patch('campus_control.schedule.snapshot',side_effect=lambda now=None:_scheduled(moment(9))):
   a=service.read_state(force=True);b=service.read_state(force=True)
  self.assertTrue(a['paused']);self.assertEqual(a['revision'],1);self.assertEqual(b['revision'],1);self.assertEqual(FreezeChange.objects.count(),1)
  with patch('campus_control.schedule.snapshot',side_effect=lambda now=None:_scheduled(moment(9,50))):
   c=service.read_state(force=True)
  self.assertFalse(c['paused']);self.assertEqual(c['revision'],2);self.assertEqual(FreezeChange.objects.count(),2)
 def test_manual_open_ignores_class_and_auto_restores_it(self):
  with patch('campus_control.schedule.snapshot',side_effect=lambda now=None:_scheduled(moment(9,20))):
   a=service.change(self.admin,False,'',0,mode='AUTO');self.assertTrue(a['paused'])
   b=service.change(self.admin,False,'',a['revision']);self.assertFalse(b['paused']);self.assertEqual(b['mode'],'MANUAL')
   c=service.change(self.admin,False,'',b['revision'],mode='AUTO');self.assertTrue(c['paused'])
 def test_manual_pause_ignores_break(self):
  with patch('campus_control.schedule.snapshot',side_effect=lambda now=None:_scheduled(moment(9,50))):
   a=service.change(self.admin,True,'추가 강의 중',0);self.assertTrue(a['paused']);self.assertEqual(a['message'],'추가 강의 중')
 def test_paused_state_shared_by_new_process_reads(self):
  FreezeState.objects.create(pk=1,mode='AUTO')
  with patch('campus_control.schedule.snapshot',side_effect=lambda now=None:_scheduled(moment(11,20))):
   a=service.read_state(force=True);service.invalidate();b=service.read_state(force=True)
  self.assertEqual(a,b);self.assertEqual(FreezeState.objects.get(pk=1).mode,'AUTO')
 def test_status_reads_cached_no_database_per_user_frame(self):
  FreezeState.objects.create(pk=1,mode='AUTO')
  with patch('campus_control.schedule.snapshot',side_effect=lambda now=None:_scheduled(moment(9,20))):
   service.read_state(force=True)
   with self.assertNumQueries(0):
    for _ in range(100):self.assertTrue(service.read_state()['paused'])
 def test_api_accepts_auto_and_force_overrides(self):
  self.client.force_login(self.admin);session=self.client.session;start_login(session,self.admin);session.save()
  revision=0
  with patch('campus_control.schedule.snapshot',side_effect=lambda now=None:_scheduled(moment(9,20))):
   for mode,paused in [('AUTO',True),('OPEN',False),('CLOSED',True),('AUTO',True)]:
    r=self.client.post('/api/campus/freeze/',json.dumps({'freezeMode':mode,'paused':False,'revision':revision}),content_type='application/json')
    self.assertEqual(r.status_code,200,r.content);state=r.json();self.assertEqual(state['paused'],paused);revision=state['revision']
 def test_invalid_schedule_mode_rejected(self):
  self.client.force_login(self.admin);session=self.client.session;start_login(session,self.admin);session.save()
  r=self.client.post('/api/campus/freeze/',json.dumps({'freezeMode':'INVALID','paused':False,'revision':0}),content_type='application/json');self.assertEqual(r.status_code,400)

 def test_scheduled_pause_settles_games_after_shared_row_commit(self):
  FreezeState.objects.create(pk=1,mode='AUTO',paused=False,message='캠퍼스를 이용할 수 있어요.')
  with patch('campus_control.schedule.snapshot',side_effect=lambda now=None:_scheduled(moment(9))),patch('event_story.interrupt.interrupt_running') as settle:
   with self.captureOnCommitCallbacks(execute=True):
    service.read_state(force=True);settle.assert_not_called()
   settle.assert_called_once()
