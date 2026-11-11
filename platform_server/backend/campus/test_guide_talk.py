import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import Client, SimpleTestCase, TransactionTestCase, override_settings

from eventing.catalog import class_group_uuid
from event_story.achievement_services import FRAGILE_ID
from event_story.models import EventProgress
from event_story.test_achievements import seed_definitions
from tests.factories import create_profile
from . import consumers
from .motion import ControlError, MotionController


class GuideTalkControlTests(SimpleTestCase):
    def setUp(self):
        self.c = consumers.CampusConsumer()
        self.c.controller = MotionController({'floor': 'campus', 'x': 1250, 'y': 200, 'dir': 'up'})
        self.c.pepero_remaining = 1
        self.c.send_packet = AsyncMock()
        self.c.touch_activity = AsyncMock()
        self.c.control_lock = asyncio.Lock()
        self.c.logout_prepared = False
        self.c.current_identity = AsyncMock(return_value={'id': 'authenticated-player'})
        self.result = {'achievements': [], 'newly_completed': []}
        self.db = AsyncMock(return_value=self.result)
        replace = patch.object(consumers.socket_db, 'run', self.db)
        replace.start();self.addCleanup(replace.stop)

    async def test_control_dispatch_uses_server_inventory_and_keeps_the_biscuit(self):
        await self.c.receive_control({'requestId': 'guide-check', 'action': 'guide_talk',
                                     'data': {'objectId': 'campus-guide-kkuwang', 'remaining': 10, 'user_id': 'forged'}})
        self.db.assert_awaited_once_with(self.c.guide_talk_now, 1)
        self.assertEqual(self.c.pepero_remaining, 1)
        packet = self.c.send_packet.await_args.args[0]
        self.assertEqual(packet['result'], {'type': 'guide_talk', **self.result})

    async def test_forged_one_remaining_cannot_replace_server_inventory(self):
        self.c.pepero_remaining = 2
        await self.c.talk_to_guide({'objectId': 'campus-guide-kkuwang', 'remaining': 1})
        self.db.assert_awaited_once_with(self.c.guide_talk_now, 2)

    async def test_distant_wrong_target_floor_interior_and_transfer_are_rejected(self):
        for target in (None, [], 'manager-suhyeon', 'hidden-recipe-note'):
            with self.subTest(target=target), self.assertRaises(ControlError):
                await self.c.talk_to_guide({'objectId': target})
        for change in ('distance', 'floor', 'interior', 'transfer'):
            self.c.controller = MotionController({'floor': 'campus', 'x': 1250, 'y': 200, 'dir': 'up'})
            if change == 'distance':self.c.controller.pose.update(x=2500, y=1000)
            elif change == 'floor':self.c.controller.pose['floor'] = 'rooftop'
            elif change == 'interior':self.c.controller.interior = {'subject': 'python'}
            else:self.c.controller.pending_transfer = {'toFloor': 'rooftop'}
            with self.subTest(change=change), self.assertRaises(ControlError):
                await self.c.talk_to_guide({'objectId': 'campus-guide-kkuwang'})
        self.db.assert_not_awaited()

    async def test_database_failure_returns_retryable_error_instead_of_closing_the_socket(self):
        self.db.side_effect = DatabaseError('save failed')
        with self.assertLogs('campus.consumers', level='ERROR'):
            await self.c.receive_control({'requestId': 'guide-check', 'action': 'guide_talk',
                                         'data': {'objectId': 'campus-guide-kkuwang'}})
        packet = self.c.send_packet.await_args.args[0]
        self.assertEqual(packet['requestId'], 'guide-check')
        self.assertIn('다시 시도', packet['error'])
        self.assertNotIn('result', packet)

    def test_revoked_session_does_not_award_and_client_identity_is_never_used(self):
        self.c.platform_session_uuid = 'server-session'
        with patch.object(self.c, 'session_user', return_value=None), patch.object(consumers, 'record_guide_talk') as record:
            self.assertIsNone(self.c.guide_talk_now(1));record.assert_not_called()
        owner = SimpleNamespace(pk='server-user')
        with patch.object(self.c, 'session_user', return_value=owner), patch.object(consumers, 'record_guide_talk', return_value=self.result) as record:
            self.assertEqual(self.c.guide_talk_now(1), self.result)
            record.assert_called_once_with(owner, 1, 'server-session')


@override_settings(ALLOWED_HOSTS=['127.0.0.1','testserver'], DEBUG=True, EVENT_STORY_PREVIEW=True)
class GuideTalkSocketTests(TransactionTestCase):
    def setUp(self):
        seed_definitions()
        self.user = get_user_model().objects.create_user(username='ee15-socket')
        create_profile(user=self.user, nickname='잔량검증', cohort_id=class_group_uuid('mlo-1'),
                       setup_complete=True, last_position={'floor':'campus','x':1250,'y':200,'dir':'up'})
        client = Client();client.force_login(self.user)
        self.cookie = f'{settings.SESSION_COOKIE_NAME}={client.cookies[settings.SESSION_COOKIE_NAME].value}'
        room = patch.object(consumers, 'room', consumers.CampusRoom())
        room.start();self.addCleanup(room.stop)

    async def talk(self, stock, supplied, repeat=False):
        from config.asgi import application
        original = consumers.CampusConsumer.connect

        async def connect_with_stock(consumer):
            await original(consumer)
            consumer.pepero_remaining = stock
            consumer.publish_pepero_inventory()
            await consumer.send_packet({'type':'pepero_status','remaining':stock})

        results = []
        with patch.object(consumers.CampusConsumer, 'connect', connect_with_stock):
            socket = WebsocketCommunicator(application, '/ws/campus/?control=server', headers=[
                (b'origin', b'http://127.0.0.1'), (b'host', b'127.0.0.1'), (b'cookie', self.cookie.encode())])
            try:
                self.assertTrue((await socket.connect())[0])
                for _ in range(30):
                    message = await socket.receive_json_from(timeout=5)
                    if message.get('type') == 'pepero_status' and message.get('remaining') == stock:break
                else:self.fail('Server inventory was not initialized')
                for index in range(2 if repeat else 1):
                    request_id = 'guide-talk-' + str(index)
                    await socket.send_json_to({'type':'control','requestId':request_id,'action':'guide_talk',
                                               'data':{'objectId':'campus-guide-kkuwang','remaining':supplied}})
                    for _ in range(30):
                        message = await socket.receive_json_from(timeout=5)
                        if message.get('type') == 'control_result' and message.get('requestId') == request_id:
                            self.assertNotIn('error', message, message)
                            results.append(message['result']);break
                    else:self.fail('Guide acknowledgement was not received')
            finally:
                await socket.disconnect()
        return results

    async def test_real_socket_guide_talk_does_not_award_before_hidden_story_claim(self):
        results = await self.talk(stock=1, supplied=10, repeat=True)
        self.assertEqual(results[0]['newly_completed'], [])
        self.assertEqual(results[1]['newly_completed'], [])
        badge = next(b for b in results[0]['achievements'] if b['event_uuid'] == str(FRAGILE_ID))
        self.assertEqual((badge['name'], badge['image_url']),
                         ('부서지기 직전', '/static/campus/assets/achievements/fragile-pepero-v1.webp'))
        rows = await database_sync_to_async(list)(EventProgress.objects.filter(user=self.user, event_id=FRAGILE_ID)
                                                  .values('status', 'completion_order', 'session_id'))
        self.assertEqual(rows, [])
        revisited = await self.talk(stock=0, supplied=1)
        self.assertEqual(revisited[0]['newly_completed'], [])
        self.assertFalse(next(b for b in revisited[0]['achievements']
                              if b['event_uuid'] == str(FRAGILE_ID))['achieved'])

    async def test_real_socket_rejects_forged_client_remaining(self):
        results = await self.talk(stock=2, supplied=1)
        self.assertEqual(results[0]['newly_completed'], [])
        self.assertFalse(await database_sync_to_async(EventProgress.objects.filter(event_id=FRAGILE_ID).exists)())
