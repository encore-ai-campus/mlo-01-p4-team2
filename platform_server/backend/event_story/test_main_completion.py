from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core import signing
from eventing.models import PlatformSession
from event_story import main
from event_story.main_achievement import snapshot
from event_story.achievement_services import complete_achievement,GREETING_ID,PYTHON_ID,achievement_snapshot
from event_story.models import EventProgress
from event_story.access import SALT

@override_settings(DEBUG=True,EVENT_STORY_PREVIEW=True)
class MainBadgeTests(TestCase):
 def setUp(self):
  main.catalog();self.users=[get_user_model().objects.create_user(username='badge-person-'+str(i)) for i in range(2)]
 def deliver(self,user):
  main.complete(user,'5');session=PlatformSession.objects.create(user=user,started_at=timezone.now(),last_seen_at=timezone.now())
  ticket=signing.dumps({'user':str(user.pk),'target':'final-guide'},salt=SALT)
  return main.action(user,dict(action='deliver',ticket=ticket),session.pk)
 def test_every_account_can_receive_main_badge_even_with_existing_egg(self):
  for user,egg in zip(self.users,[GREETING_ID,PYTHON_ID]):
   self.assertTrue(complete_achievement(user,egg,timezone.now()))
   result=self.deliver(user);self.assertTrue(main.status(user)['mainAchievement']['achieved']);self.assertEqual(snapshot(user)['kind'],'MAIN_MISSION')
   self.assertEqual(EventProgress.objects.filter(user=user,event__event_type='EASTER_EGG',status='COMPLETED').count(),1)
  self.assertEqual(sum(snapshot(user)['achieved'] for user in self.users),2)
 def test_partial_progress_and_other_person_completion_do_not_award(self):
  self.deliver(self.users[0]);main.complete(self.users[1],'5')
  self.assertFalse(snapshot(self.users[1])['achieved']);self.assertIsNone(snapshot(self.users[1])['completed_at'])
 def test_delivery_replay_preserves_receipt_and_egg_quota(self):
  user=self.users[0];self.deliver(user);first=snapshot(user);self.deliver(user);self.assertEqual(first,snapshot(user))
  self.assertTrue(complete_achievement(user,GREETING_ID,timezone.now()))
  self.assertEqual(len(achievement_snapshot(user)),11)
 def test_existing_completed_receipt_is_visible_without_new_record(self):
  user=self.users[0];main.complete(user,'6');before=EventProgress.objects.count();self.assertTrue(snapshot(user)['achieved']);self.assertEqual(EventProgress.objects.count(),before)
 def test_admin_preview_does_not_award_or_change_completion(self):
  user=self.users[0];user.is_staff=True;user.save(update_fields=['is_staff']);before=EventProgress.objects.count()
  badge=snapshot(user);self.assertTrue(badge['can_preview']);self.assertFalse(badge['achieved']);self.assertEqual(EventProgress.objects.count(),before)
  self.assertFalse(snapshot(self.users[1])['can_preview'])
