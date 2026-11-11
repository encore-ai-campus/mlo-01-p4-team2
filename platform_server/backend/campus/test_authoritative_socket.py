import asyncio
from unittest.mock import patch

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import DatabaseError
from django.test import Client, TransactionTestCase, override_settings

from accounts.models import Profile
from eventing.models import EventOutbox
from . import consumers
from .positions import DEFAULT_POSITION


@override_settings(CAMPUS_TEST_LEGACY_MOVEMENT=False, KAFKA_BOOTSTRAP_SERVERS='')
class AuthoritativeSocketTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='server-input')
        Profile.objects.create(user=self.user, nickname='입력 검증', cohort='mlo-1', setup_complete=True)
        client = Client()
        client.force_login(self.user)
        self.client = client
        self.cookie = f'{settings.SESSION_COOKIE_NAME}={client.cookies[settings.SESSION_COOKIE_NAME].value}'
        self.room = consumers.CampusRoom()
        replacement = patch.object(consumers, 'room', self.room)
        replacement.start()
        self.addCleanup(replacement.stop)

    def socket(self):
        from config.asgi import application
        return WebsocketCommunicator(application, '/ws/campus/', headers=[
            (b'origin', b'http://127.0.0.1'), (b'host', b'127.0.0.1'), (b'cookie', self.cookie.encode())])

    async def packet(self, socket, kind):
        for _ in range(30):
            packet = await socket.receive_json_from(timeout=2)
            if packet['type'] == kind:
                return packet
        self.fail(f'{kind} was not received')

    @database_sync_to_async
    def stored(self):
        return (Profile.objects.get(user_id=self.user.pk).last_position,
                list(EventOutbox.objects.filter(event_type='movement_observed').values_list('raw_data', flat=True)))

    async def test_production_socket_rejects_coordinates_and_uses_server_default_spawn(self):
        socket = self.socket()
        try:
            self.assertTrue((await socket.connect())[0])
            welcome = await self.packet(socket, 'welcome')
            self.assertTrue(welcome['serverControl'])
            await self.packet(socket, 'control_state')
            await socket.send_json_to({'type': 'move', 'floor': 'rooftop', 'x': 100, 'y': 100, 'dir': 'left', 'moving': True})
            await self.packet(socket, 'control_error')
            self.assertEqual(self.room.players[welcome['selfId']]['floor'], 'campus')
            self.assertEqual(self.room.players[welcome['selfId']]['x'], DEFAULT_POSITION['x'])
        finally:
            await socket.disconnect()
        self.assertEqual((await self.stored())[1], [])

    async def test_keyboard_moves_on_the_server_and_disconnect_flushes_one_latest_outbox(self):
        socket = self.socket()
        try:
            self.assertTrue((await socket.connect())[0])
            await self.packet(socket, 'welcome')
            await self.packet(socket, 'control_state')
            await socket.send_json_to({'type': 'input', 'keys': ['w']})
            await asyncio.sleep(.15)
            await socket.send_json_to({'type': 'input', 'keys': []})
            await socket.send_json_to({'type': 'ping'})
            await self.packet(socket, 'pong')
        finally:
            await socket.disconnect()
        position, events = await self.stored()
        self.assertNotEqual((position['x'], position['y']), (DEFAULT_POSITION['x'], DEFAULT_POSITION['y']))
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]['x'], events[0]['y']), (position['x'], position['y']))

    async def test_invalid_keys_are_rejected_without_disconnect_or_movement(self):
        socket = self.socket()
        try:
            self.assertTrue((await socket.connect())[0])
            await self.packet(socket, 'welcome')
            await socket.send_json_to({'type': 'input', 'keys': ['teleport']})
            await self.packet(socket, 'control_error')
            await socket.send_json_to({'type': 'ping'})
            await self.packet(socket, 'pong')
        finally:
            await socket.disconnect()
        self.assertEqual((await self.stored())[1], [])

    async def test_logout_ack_follows_db_save_and_http_does_not_read_redis_position(self):
        socket = self.socket()
        try:
            self.assertTrue((await socket.connect())[0])
            await self.packet(socket, 'welcome')
            await socket.send_json_to({'type': 'input', 'keys': ['w']})
            await asyncio.sleep(.15)
            await socket.send_json_to({'type': 'control', 'requestId': 'logout',
                                       'action': 'prepare_logout', 'data': {'x': -999}})
            response = await self.packet(socket, 'control_result')
            self.assertEqual(response, {'type': 'control_result', 'requestId': 'logout',
                                        'result': {'saved': True}})
            position, _ = await self.stored()
            self.assertNotEqual(position['y'], DEFAULT_POSITION['y'])
            key = self.cookie.split('=', 1)[1]
            self.assertTrue(await database_sync_to_async(Session.objects.filter(session_key=key).exists)())
            with patch('campus.session_registry.latest', side_effect=AssertionError('HTTP must not read positions')):
                response = await database_sync_to_async(self.client.post)('/accounts/logout/')
            self.assertEqual(response.status_code, 302)
            self.assertFalse(await database_sync_to_async(Session.objects.filter(session_key=key).exists)())
            self.assertEqual((await self.stored())[0], position)
        finally:
            await socket.disconnect()

    async def test_failed_logout_save_returns_error_and_can_retry_without_losing_session(self):
        socket = self.socket()
        try:
            self.assertTrue((await socket.connect())[0])
            await self.packet(socket, 'welcome')
            with patch('campus.consumers.save_position', side_effect=DatabaseError('DB unavailable')):
                with self.assertLogs('campus.consumers', level='ERROR'):
                    await socket.send_json_to({'type': 'control', 'requestId': 'failed', 'action': 'prepare_logout'})
                    response = await self.packet(socket, 'control_result')
            self.assertIn('error', response)
            self.assertNotIn('result', response)
            self.assertIsNone((await self.stored())[0])
            await socket.send_json_to({'type': 'control', 'requestId': 'retry', 'action': 'prepare_logout'})
            self.assertEqual((await self.packet(socket, 'control_result'))['result'], {'saved': True})
        finally:
            await socket.disconnect()

    async def test_logout_retry_is_idempotent_and_saved_position_cannot_move_after_ack(self):
        socket = self.socket()
        try:
            self.assertTrue((await socket.connect())[0])
            await self.packet(socket, 'welcome')
            await socket.send_json_to({'type': 'control', 'requestId': 'first', 'action': 'prepare_logout'})
            self.assertEqual((await self.packet(socket, 'control_result'))['result'], {'saved': True})
            position, events = await self.stored()
            await socket.send_json_to({'type': 'input', 'keys': ['w']})
            await socket.send_json_to({'type': 'control', 'requestId': 'move', 'action': 'navigate',
                                       'data': {'x': 1500, 'y': 900}})
            self.assertIn('error', await self.packet(socket, 'control_result'))
            with patch('campus.consumers.save_position', side_effect=AssertionError('Already saved')):
                await socket.send_json_to({'type': 'control', 'requestId': 'repeat', 'action': 'prepare_logout'})
                response = await self.packet(socket, 'control_result')
            self.assertEqual(response['result'], {'saved': True})
            await asyncio.sleep(.1)
            self.assertEqual(await self.stored(), (position, events))
        finally:
            await socket.disconnect()

    async def test_logout_authentication_db_error_returns_error_and_allows_retry(self):
        socket = self.socket()
        try:
            self.assertTrue((await socket.connect())[0])
            welcome = await self.packet(socket, 'welcome')
            for expires_at in (self.room.sockets[welcome['selfId']].policy_status['expiresAt'], 0):
                self.room.sockets[welcome['selfId']].policy_status['expiresAt'] = expires_at
                with patch('campus.consumers.CampusConsumer.current_identity', side_effect=DatabaseError('DB unavailable')):
                    with self.assertLogs('campus.consumers', level='ERROR'):
                        await socket.send_json_to({'type': 'control', 'requestId': 'failed-auth', 'action': 'prepare_logout'})
                        self.assertIn('error', await self.packet(socket, 'control_result'))
            await socket.send_json_to({'type': 'control', 'requestId': 'retry-auth', 'action': 'prepare_logout'})
            self.assertEqual((await self.packet(socket, 'control_result'))['result'], {'saved': True})
        finally:
            await socket.disconnect()

    async def test_revoked_session_cannot_receive_a_successful_logout_ack(self):
        socket = self.socket()
        try:
            self.assertTrue((await socket.connect())[0])
            await self.packet(socket, 'welcome')
            await self.packet(socket, 'control_state')
            key = self.cookie.split('=', 1)[1]
            await database_sync_to_async(Session.objects.filter(session_key=key).delete)()
            await socket.send_json_to({'type': 'control', 'requestId': 'revoked', 'action': 'prepare_logout'})
            output = await socket.receive_output(timeout=2)
            self.assertEqual(output['type'], 'websocket.close')
            self.assertEqual(output['code'], 4401)
            self.assertIsNone((await self.stored())[0])
        finally:
            await socket.disconnect()

    async def test_transfer_approval_returns_a_user_bound_ad_token_before_moving(self):
        from ad_bridge.placements import transfer_identity
        from .motion import world

        x, y = world('campus').objects['campus-roof-stairs']['interactionPoint']
        await database_sync_to_async(Profile.objects.filter(user_id=self.user.pk).update)(
            last_position={"floor": "campus", "x": x, "y": y, "dir": "down"})
        socket = self.socket()
        try:
            self.assertTrue((await socket.connect(timeout=5))[0])
            await self.packet(socket, "welcome")
            await socket.send_json_to({"type": "control", "requestId": "offer", "action": "prepare_transfer",
                                       "data": {"objectId": "campus-roof-stairs"}})
            offer = await self.packet(socket, "control_result")
            self.assertEqual(offer['requestId'], 'offer')
            self.assertNotIn('error', offer)
            self.assertEqual(offer['result']['type'], 'transfer_offer')
            await socket.send_json_to({"type": "control", "requestId": "approve", "action": "begin_transfer",
                                       "data": {"floor": "rooftop"}})
            approval = await self.packet(socket, "control_result")
            self.assertEqual(approval['requestId'], 'approve')
            self.assertNotIn('error', approval)
            result = approval['result']
            self.assertEqual(result['type'], 'transfer_ready')
            self.assertEqual(transfer_identity(result["ads_token"], self.user.pk), result["transfer_id"])
            self.assertEqual(next(iter(self.room.players.values()))["floor"], "campus")
        finally:
            await socket.disconnect()
