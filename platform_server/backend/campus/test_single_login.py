import asyncio
import json
from channels.db import database_sync_to_async
from django.contrib.sessions.models import Session
from django.test import Client, TransactionTestCase, override_settings

from . import tests as socket_tests


async def receive_type(socket, expected):
    async with asyncio.timeout(3):
        while True:
            message = await socket.receive_json_from()
            if message.get('type') == expected:
                return message
            # The server may publish its initial control policy before the reply.
            if message.get('type') != 'campus_freeze' or message.get('paused') is not False or not isinstance(message.get('revision'), int):
                raise AssertionError(f'Unexpected packet while waiting for {expected}: {message}')


@override_settings(ALLOWED_HOSTS=["127.0.0.1", "localhost", "testserver"], DEBUG=False, CAMPUS_TEST_LEGACY_MOVEMENT=True)
class SingleLoginSocketTests(TransactionTestCase):
    setUp = socket_tests.CampusSocketTests.setUp
    socket = socket_tests.CampusSocketTests.socket
    connections = socket_tests.CampusSocketTests.connections
    movement = socket_tests.CampusSocketTests.movement

    async def test_new_browser_closes_all_old_tabs_and_restores_latest_position(self):
        async with self.connections(self.a, self.a, self.b) as (old, old_tab, observer):
            await old.receive_json_from()
            await old_tab.receive_json_from()
            await observer.receive_json_from()
            packet = await old.receive_output()
            while packet['type'] == 'websocket.send':
                self.assertIn(json.loads(packet['text'])['type'], ('pepero_status', 'campus_freeze'))
                packet = await old.receive_output()
            self.assertEqual(packet['type'], 'websocket.close')
            self.assertEqual(packet['code'], 4409)
            await old_tab.receive_json_from()
            position = self.movement(x=1720.25, y=455.5)
            await old_tab.send_json_to(position)
            await receive_type(observer, 'move')
            await old_tab.send_json_to({"type": "ping"})
            await receive_type(old_tab, 'pong')

            new = Client()
            response = await database_sync_to_async(new.post)("/accounts/login/", {
                "username": "alice", "password": "Test!River4820",
            })
            self.assertEqual(response.status_code, 302)
            for ws in (old_tab,):
                self.assertEqual(await receive_type(ws, 'session_expired'), {"type": "session_expired", "reason": "replaced"})
                self.assertEqual((await ws.receive_output())["code"], 4401)
            for _ in range(1):
                self.assertEqual((await receive_type(observer, 'leave'))["type"], "leave")
            stale_key = self.a.cookies["campus_sessionid"].value
            self.assertFalse(await database_sync_to_async(Session.objects.filter(pk=stale_key).exists)())
            rejected = self.socket(self.a)
            connected, code = await rejected.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4401)
            await rejected.disconnect()
            current = self.socket(new)
            self.assertTrue((await current.connect())[0])
            try:
                welcome = await receive_type(current, 'welcome')
                player = next(p for p in welcome["players"] if p["id"] == welcome["selfId"])
                self.assertEqual(player["x"], 1720.25)
                self.assertEqual(player["y"], 455.5)
                await receive_type(observer, 'join')
                await current.send_json_to({"type": "ping"})
                self.assertEqual((await receive_type(current, 'pong'))["type"], "pong")
            finally:
                await current.disconnect()
            await receive_type(observer, 'leave')
