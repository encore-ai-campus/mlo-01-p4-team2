from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch, AsyncMock
from django.contrib.auth import get_user_model
from django.db import transaction, close_old_connections, connections
from django.test import TestCase, TransactionTestCase, SimpleTestCase
from django.utils import timezone
from .test_achievements import seed_definitions
from .achievement_services import complete_achievement, achievement_snapshot, discovery_snapshot, publish_discoveries, GREETING_ID, DISCOVERY_GROUP
from .models import EventProgress

class SharedNotebookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_definitions()
        cls.a=get_user_model().objects.create_user(username='discoverer-a')
        cls.b=get_user_model().objects.create_user(username='discoverer-b')

    def test_other_discovery_reveals_art_without_granting_personal_completion(self):
        with self.captureOnCommitCallbacks(execute=True), patch('event_story.achievement_services.publish_discoveries') as notify:
            self.assertTrue(complete_achievement(self.a,GREETING_ID,timezone.now()))
        notify.assert_called_once()
        row=achievement_snapshot(self.b)[0]
        self.assertTrue(row['discovered']);self.assertFalse(row['achieved'])
        self.assertIsNone(row['completed_at'])
        self.assertEqual(row['first_discoverer']['nickname'],'discoverer-a')
        self.assertNotIn('user_id',row['first_discoverer'])
        with self.captureOnCommitCallbacks(execute=True), patch('event_story.achievement_services.publish_discoveries') as notify:
            complete_achievement(self.b,GREETING_ID,timezone.now())
            complete_achievement(self.a,GREETING_ID,timezone.now())
        notify.assert_not_called()
        self.assertEqual(achievement_snapshot(self.b)[0]['first_discoverer']['nickname'],'discoverer-a')

    def test_existing_legacy_record_reveals_without_new_completion(self):
        EventProgress.objects.create(user=self.a,event_id=GREETING_ID,status='COMPLETED',completed_at=timezone.now())
        self.assertTrue(discovery_snapshot()[0]['discovered'])
        self.assertEqual(discovery_snapshot()[0]['first_discoverer']['nickname'],'discoverer-a')

    def test_rollback_never_broadcasts_or_exposes_discovery(self):
        with self.captureOnCommitCallbacks(execute=True), patch('event_story.achievement_services.publish_discoveries') as notify:
            try:
                with transaction.atomic():
                    complete_achievement(self.a,GREETING_ID,timezone.now())
                    raise ValueError('rollback')
            except ValueError: pass
        notify.assert_not_called()
        self.assertFalse(discovery_snapshot()[0]['discovered'])

    def test_public_broadcast_has_no_personal_achievement_fields(self):
        layer=type('Layer',(),{'group_send':AsyncMock()})()
        with patch('channels.layers.get_channel_layer',return_value=layer): publish_discoveries()
        group,payload=layer.group_send.call_args.args
        self.assertEqual(group,DISCOVERY_GROUP)
        self.assertEqual(len(payload['discoveries']),11)
        self.assertNotIn('achieved',payload['discoveries'][0])

class SharedNotebookRaceTests(TransactionTestCase):
    def test_two_users_have_only_one_first_discoverer_and_one_broadcast(self):
        seed_definitions();users=[get_user_model().objects.create_user(username='race-'+str(i)) for i in range(2)]
        start=Barrier(2)
        def run(pk):
            close_old_connections()
            try:
                user=get_user_model().objects.get(pk=pk);start.wait(timeout=5)
                return complete_achievement(user,GREETING_ID,timezone.now())
            finally: connections.close_all()
        with patch('event_story.achievement_services.publish_discoveries') as notify, ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(run,[u.pk for u in users])),1)
        notify.assert_called_once()
        self.assertEqual(list(EventProgress.objects.filter(event_id=GREETING_ID).order_by('completion_order').values_list('completion_order',flat=True)),[1])
        self.assertTrue(discovery_snapshot()[0]['discovered'])

class DiscoverySocketTests(SimpleTestCase):
    async def test_global_event_sends_public_state_to_active_session_only(self):
        from campus.consumers import CampusConsumer
        c=CampusConsumer();c.current_identity=AsyncMock(return_value={'id':'viewer'});c.send_packet=AsyncMock()
        data={'type':'achievement_discoveries','discoveries':[{'event_uuid':'example','discovered':True}]}
        await c.achievement_discoveries(data);c.send_packet.assert_awaited_once_with(data)
        c.send_packet.reset_mock();c.current_identity.return_value=None
        await c.achievement_discoveries(data);c.send_packet.assert_not_awaited()
