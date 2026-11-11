import io
import json
from pathlib import Path
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TransactionTestCase
from accounts.models import Position, Profile
from community.models import Post, Comment
from direct_messages.models import Conversation, Message
from eventing.models import PlatformSession
from learning.models import PointWallet, PointLedger, UserAnswer
from .test_postgres_platform import seed_catalog


class LegacyImportTests(TransactionTestCase):
    def test_exported_old_schema_preserves_identity_timestamps_links_and_balances(self):
        seed_catalog()
        fixture=Path(__file__).parent/'fixtures'/'legacy-platform.json'
        expected=json.loads(fixture.with_name('legacy-expected.json').read_text())
        call_command('import_platform_data',file=fixture,stdout=io.StringIO())
        self.assertEqual(get_user_model().objects.count(),0)
        report=io.StringIO()
        call_command('import_platform_data',file=fixture,apply=True,stdout=report)
        result=json.loads(report.getvalue())
        self.assertEqual(result['archived_only']['eventing_eventoutbox'],4)
        self.assertEqual(result['archived_only']['community_mealcomment'],1)
        user=get_user_model().objects.get(username='legacy-student')
        self.assertEqual(str(user.pk),expected['user_uuid'])
        self.assertEqual(user.campus_profile.pk,user.pk)
        self.assertEqual(user.campus_profile.cohort.code,'mlo-1')
        self.assertEqual(Profile.objects.get(user__username='legacy-other').cohort.code,'unassigned')
        post=Post.objects.get(pk=expected['post_uuid'])
        self.assertEqual(post.id,expected['post_id'])
        self.assertEqual(post.created_at.isoformat(),expected['post_created_at'])
        reply=Comment.objects.get(pk=expected['reply_uuid'])
        self.assertEqual(str(reply.parent_id),expected['comment_uuid'])
        self.assertEqual(reply.post_id,post.pk)
        self.assertEqual(PointWallet.objects.get(profile=user.campus_profile).balance,expected['balance'])
        answer=UserAnswer.objects.get(attempt_uuid=expected['attempt_uuid'])
        self.assertEqual(answer.user_id,user.pk)
        self.assertEqual(PointLedger.objects.get(answer=answer).profile_id,user.pk)
        self.assertEqual(Position.objects.get(profile_id=user.pk).last_position['y'],100.125)
        self.assertTrue(user.groups.filter(name='legacy-group').exists())
        self.assertTrue(user.has_perm('community.add_post'))
        conversation=Conversation.objects.get()
        self.assertLess(conversation.first_user_id,conversation.second_user_id)
        self.assertEqual(Message.objects.get().sender_id,user.pk)
        self.assertFalse(Session.objects.exists())
        self.assertFalse(PlatformSession.objects.filter(ended_at__isnull=True).exists())
        client=Client();self.assertTrue(client.login(username='legacy-student',password='LegacyPassword!4820'))
        new=Post.objects.create(author=user,title='새 글',body='신규',category='free')
        self.assertGreater(new.id,post.id)
        with self.assertRaises(CommandError):
            call_command('import_platform_data',file=fixture,apply=True,stdout=io.StringIO())
