import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import HttpCommunicator, WebsocketCommunicator
from django.conf import settings
from django.core import signing
from django.db import DatabaseError
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import Profile
from direct_messages.live import user_group
from eventing.models import ChatMessage, EventOutbox, PlatformSession
from . import chat, consumers, tests as movement_tests
from .chat_tokens import issue_actor_token, verify_actor_token
from .motion import MotionController
from .positions import DEFAULT_POSITION


class ActorTokenTests(SimpleTestCase):
    def test_signature_expiry_session_and_user_bindings_are_required(self):
        token = issue_actor_token(1, 'session-a', 'a' * 32, 'campus')
        self.assertEqual(verify_actor_token(token, 1, 'session-a')['actorId'], 'a' * 32)
        for bad, user, session in [(None, 1, 'session-a'), (token + 'x', 1, 'session-a'),
                                   (token, 2, 'session-a'), (token, 1, 'session-b')]:
            with self.assertRaises(signing.BadSignature):
                verify_actor_token(bad, user, session)
        with patch('django.core.signing.time.time', return_value=1000):
            expired = issue_actor_token(1, 'session-a', 'a' * 32, 'campus')
        with override_settings(CAMPUS_LOGIN_MAX_AGE_SECONDS=10), patch('django.core.signing.time.time', return_value=1011):
            with self.assertRaises(signing.SignatureExpired):
                verify_actor_token(expired, 1, 'session-a')

    async def test_floor_changes_rotate_the_token_once_without_changing_state_shape(self):
        consumer = consumers.CampusConsumer()
        consumer.user, consumer.session_key, consumer.player_id = SimpleNamespace(pk=1), 'session-a', 'a' * 32
        consumer.controller = MotionController(DEFAULT_POSITION)
        consumer.state = {'id': consumer.player_id, 'nickname': '이름', 'avatar': 'male-1', 'color': '#dfa34d',
                          **consumer.controller.pose, 'inClassroom': False}
        consumer.chat_token_floor = 'campus'
        consumer.send_packet = AsyncMock()
        await consumer.send_control_state()
        self.assertNotIn('chatToken', consumer.send_packet.await_args.args[0])
        consumer.controller.pose['floor'] = 'rooftop'
        await consumer.send_control_state()
        packet = consumer.send_packet.await_args.args[0]
        self.assertEqual(verify_actor_token(packet['chatToken'], 1, 'session-a')['floor'], 'rooftop')
        self.assertEqual(packet['state']['main']['floor'], 'rooftop')
        await consumer.send_control_state()
        self.assertNotIn('chatToken', consumer.send_packet.await_args.args[0])


@override_settings(ALLOWED_HOSTS=['127.0.0.1', 'localhost', 'testserver'], DEBUG=False,
                   CAMPUS_TEST_LEGACY_MOVEMENT=False, CAMPUS_DM_ENABLED=True, KAFKA_BOOTSTRAP_SERVERS='')
class SplitSocketTests(TransactionTestCase):
    def setUp(self):
        movement_tests.CampusSocketTests.setUp(self)

    def socket(self, client, path, application=None):
        from config.service_asgi import application as service
        key = client.cookies[settings.SESSION_COOKIE_NAME].value if client else ''
        return WebsocketCommunicator(application or service, path, headers=[(b'origin', b'http://127.0.0.1'),
            (b'host', b'127.0.0.1'), (b'cookie', f'{settings.SESSION_COOKIE_NAME}={key}'.encode())])

    async def packet(self, socket, kind):
        for _ in range(30):
            packet = await socket.receive_json_from(timeout=2)
            if packet['type'] == kind:
                return packet
        self.fail(f'{kind} was not received')

    @asynccontextmanager
    async def connections(self, *clients, movement=True):
        from config.movement_asgi import application
        sockets, pairs = [], []
        try:
            for client in clients:
                move, welcome = None, None
                if movement:
                    move = self.socket(client, '/ws/movement/?control=server&moves=compact', application)
                    sockets.append(move)
                    self.assertTrue((await move.connect())[0])
                    welcome = await self.packet(move, 'welcome')
                    await self.packet(move, 'control_state')
                channel = self.socket(client, '/ws/chat/')
                sockets.append(channel)
                self.assertTrue((await channel.connect())[0])
                self.assertEqual(await channel.receive_json_from(), {'type': 'chat_ready'})
                pairs.append(SimpleNamespace(move=move, chat=channel, welcome=welcome))
            yield pairs
        finally:
            for socket in reversed(sockets):
                if not socket.future.done():
                    await socket.disconnect()

    async def send(self, pair, text, **extra):
        await pair.chat.send_json_to({'type': 'chat', 'text': text, 'actorToken': pair.welcome['chatToken'], **extra})

    async def close_code(self, socket):
        for _ in range(30):
            output = await socket.receive_output(timeout=2)
            if output['type'] == 'websocket.close':
                return output['code']
        self.fail('socket was not closed')

    async def test_standalone_chat_never_starts_movement_or_reads_a_position(self):
        with patch.object(consumers.room.cluster, 'start', AsyncMock(side_effect=AssertionError('movement cluster'))), \
                patch.object(consumers, 'MotionController', side_effect=AssertionError('physics')), \
                patch('campus.positions.get_position', side_effect=AssertionError('position lookup')):
            async with self.connections(self.a, movement=False) as (a,):
                self.assertFalse(consumers.room.players)
                self.assertIsNone(consumers.room.flusher)
                await a.chat.send_json_to({'type': 'ping'})
                self.assertEqual(await a.chat.receive_json_from(), {'type': 'pong'})
                await a.chat.send_json_to({'type': 'input', 'keys': ['w']})
                self.assertTrue(await a.chat.receive_nothing(timeout=.02))

    async def test_chat_first_reads_the_platform_session_created_by_later_movement(self):
        from config.movement_asgi import application
        before = await database_sync_to_async(PlatformSession.objects.count)()
        async with self.connections(self.a, movement=False) as (a,):
            self.assertEqual(await database_sync_to_async(PlatformSession.objects.count)(), before)
            move = self.socket(self.a, '/ws/movement/?control=server', application)
            try:
                self.assertTrue((await move.connect())[0])
                a.welcome = await self.packet(move, 'welcome')
                await self.send(a, '채팅 연결 뒤 이동 연결')
                self.assertEqual((await a.chat.receive_json_from())['type'], 'chat')
                self.assertEqual(await database_sync_to_async(PlatformSession.objects.count)(), before + 1)
            finally:
                await move.disconnect()

    async def test_movement_reconnect_issues_a_new_actor_token_without_reconnecting_chat(self):
        from config.movement_asgi import application
        async with self.connections(self.a) as (a,):
            move = self.socket(self.a, '/ws/movement/?control=server', application)
            try:
                self.assertTrue((await move.connect())[0])
                welcome = await self.packet(move, 'welcome')
                self.assertNotEqual(welcome['selfId'], a.welcome['selfId'])
                self.assertNotEqual(welcome['chatToken'], a.welcome['chatToken'])
                self.assertEqual(await self.close_code(a.move), 4409)
                a.welcome = welcome
                await self.send(a, '재접속 후 채팅')
                self.assertEqual((await a.chat.receive_json_from())['message']['playerId'], welcome['selfId'])
            finally:
                await move.disconnect()

    async def test_chat_uses_signed_actor_floor_current_identity_and_persists_one_message(self):
        async with self.connections(self.a, self.b) as (a, b):
            await database_sync_to_async(Profile.objects.filter(user=self.alice).update)(nickname='새 닉네임')
            await self.send(a, '  안녕하세요!  ', playerId=b.welcome['selfId'], nickname='사칭', floor='rooftop')
            own, other = await a.chat.receive_json_from(), await b.chat.receive_json_from()
            self.assertEqual(own, other)
            message = own['message']
            self.assertEqual((message['playerId'], message['nickname'], message['floor'], message['text']),
                             (a.welcome['selfId'], '새 닉네임', 'campus', '안녕하세요!'))
            self.assertIsNotNone(datetime.fromisoformat(message['sentAt']).tzinfo)

            @database_sync_to_async
            def stored():
                saved = ChatMessage.objects.get(pk=message['id'])
                event = EventOutbox.objects.get(event_type='chat_sent')
                return saved.body, event.raw_data
            body, event = await stored()
            self.assertEqual(body, message['text'])
            self.assertEqual(event['message_uuid'], message['id'])
            self.assertNotIn('text', event)
            self.assertNotIn('nickname', event)

    async def test_missing_tampered_expired_wrong_user_and_wrong_session_tokens_are_rejected(self):
        async with self.connections(self.a, self.b) as (a, b):
            with patch('django.core.signing.time.time', return_value=0):
                expired = issue_actor_token(self.alice.pk, self.a.cookies[settings.SESSION_COOKIE_NAME].value,
                                            a.welcome['selfId'], 'campus')
            wrong_session = issue_actor_token(self.alice.pk, 'other-session', a.welcome['selfId'], 'campus')
            for token in [None, 'invalid', a.welcome['chatToken'] + 'x', b.welcome['chatToken'], wrong_session, expired]:
                await self.send(a, '위조 시도', actorToken=token)
                self.assertEqual((await a.chat.receive_json_from())['type'], 'chat_error')
            self.assertTrue(await b.chat.receive_nothing(timeout=.02))
            self.assertEqual(await database_sync_to_async(ChatMessage.objects.count)(), 0)
            await self.send(a, '정상 메시지')
            self.assertEqual((await a.chat.receive_json_from())['type'], 'chat')
            self.assertEqual((await b.chat.receive_json_from())['type'], 'chat')

    async def test_validation_preserves_unicode_limit_and_html_as_text(self):
        async with self.connections(self.a, self.b) as (a, b):
            for text in [None, [], True, '', '   ', '가' * 201, 'bad\x00text', '\ud800']:
                await self.send(a, text)
                self.assertEqual((await a.chat.receive_json_from())['type'], 'chat_error')
            for text in ['🎮' * 200, '<img src=x onerror="alert(1)">']:
                with patch.object(chat.ChatConsumer, 'chat_interval', 0):
                    await self.send(a, text)
                    self.assertEqual((await a.chat.receive_json_from())['message']['text'], text)
                    self.assertEqual((await b.chat.receive_json_from())['message']['text'], text)

    async def test_chat_cooldown_does_not_block_movement(self):
        async with self.connections(self.a, self.b) as (a, b):
            clock = {'now': 1000.0}
            started = timezone.now()
            with patch.object(chat, 'time', SimpleNamespace(monotonic=lambda: clock['now'])), \
                    patch.object(chat.timezone, 'now', side_effect=lambda: started + timedelta(seconds=clock['now'] - 1000)):
                await self.send(a, '첫 메시지')
                await a.chat.receive_json_from()
                await b.chat.receive_json_from()
                await self.send(a, '너무 빠른 메시지')
                self.assertEqual((await a.chat.receive_json_from())['type'], 'chat_error')
                await a.move.send_json_to({'type': 'input', 'keys': ['w']})
                self.assertTrue((await self.packet(a.move, 'control_state'))['state']['main']['moving'])
                clock['now'] += .81
                await self.send(a, '다음 메시지')
                self.assertEqual((await a.chat.receive_json_from())['type'], 'chat')
                self.assertEqual((await b.chat.receive_json_from())['type'], 'chat')

    async def test_two_chat_connections_cannot_bypass_the_user_cooldown(self):
        async with self.connections(self.a) as (a,):
            async with self.connections(self.a, movement=False) as (second,):
                await self.send(a, '첫 메시지')
                await a.chat.receive_json_from()
                await second.chat.receive_json_from()
                await second.chat.send_json_to({'type': 'chat', 'text': '다른 연결에서 우회',
                                               'actorToken': a.welcome['chatToken']})
                self.assertEqual((await second.chat.receive_json_from())['type'], 'chat_error')
                self.assertTrue(await a.chat.receive_nothing(timeout=.02))
                self.assertEqual(await database_sync_to_async(ChatMessage.objects.count)(), 1)

    async def test_private_notifications_arrive_only_on_recipient_chat_socket(self):
        async with self.connections(self.a, self.b) as (a, b):
            await get_channel_layer().group_send(user_group(self.bob.pk),
                {'type': 'direct.message', 'conversationId': 'conversation-test'})
            self.assertEqual(await b.chat.receive_json_from(), {'type': 'private_message', 'conversationId': 'conversation-test'})
            self.assertTrue(await a.chat.receive_nothing(timeout=.02))
            for pair in (a, b):
                await pair.move.send_json_to({'type': 'ping'})
                while (packet := await pair.move.receive_json_from())['type'] != 'pong':
                    self.assertNotEqual(packet['type'], 'private_message')

    async def test_http_logout_closes_both_roles_and_reconnect_is_rejected(self):
        async with self.connections(self.a) as (a,):
            self.assertEqual((await database_sync_to_async(self.a.post)('/accounts/logout/')).status_code, 302)
            self.assertEqual(await self.close_code(a.chat), 4401)
            self.assertEqual(await self.close_code(a.move), 4401)
            rejected = self.socket(self.a, '/ws/chat/')
            self.assertEqual(await rejected.connect(), (False, 4401))
            await rejected.disconnect()

    async def test_periodic_guard_detects_cookie_revocation_without_ping(self):
        with patch.object(chat.ChatConsumer, 'session_check_interval', .02):
            async with self.connections(self.a, movement=False) as (a,):
                await database_sync_to_async(self.a.logout)()
                self.assertEqual(await self.close_code(a.chat), 4401)

    async def test_oversized_or_invalid_frames_close_only_chat(self):
        for data, code in [('x' * 4097, 1009), ('{broken', 1007)]:
            async with self.connections(self.a) as (a,):
                await a.chat.send_to(text_data=data)
                self.assertEqual(await self.close_code(a.chat), code)
                await a.move.send_json_to({'type': 'ping'})
                self.assertEqual((await self.packet(a.move, 'pong'))['type'], 'pong')

    async def test_database_failure_does_not_publish_unstored_chat(self):
        async with self.connections(self.a, self.b) as (a, b):
            with patch.object(chat, 'record_chat', side_effect=DatabaseError('offline')), self.assertLogs('campus.chat', 'ERROR'):
                await self.send(a, '저장 실패')
                self.assertEqual((await a.chat.receive_json_from())['type'], 'chat_error')
            self.assertTrue(await b.chat.receive_nothing(timeout=.02))

    async def test_roles_reject_cross_role_websockets_and_movement_rejects_http(self):
        from config.movement_asgi import application as movement
        from config.service_asgi import application as service
        for app, paths in [(movement, ['/ws/chat/', '/ws/unknown/']), (service, ['/ws/movement/', '/ws/campus/'])]:
            for path in paths:
                socket = self.socket(self.a, path, app)
                self.assertEqual(await socket.connect(), (False, 4404))
                await socket.disconnect()
        response = await HttpCommunicator(movement, 'GET', '/accounts/session/status/').get_response()
        self.assertEqual(response['status'], 404)
        key = self.a.cookies[settings.SESSION_COOKIE_NAME].value
        response = await HttpCommunicator(service, 'GET', '/accounts/session/status/', headers=[
            (b'host', b'127.0.0.1'), (b'cookie', f'{settings.SESSION_COOKIE_NAME}={key}'.encode())]).get_response()
        self.assertEqual(response['status'], 200)
        self.assertIn('authenticated', json.loads(response['body']))

    async def test_anonymous_chat_is_rejected(self):
        socket = self.socket(None, '/ws/chat/')
        self.assertEqual(await socket.connect(), (False, 4401))
        await socket.disconnect()
