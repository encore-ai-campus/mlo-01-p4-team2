import asyncio
from unittest.mock import AsyncMock, patch

from channels.testing import WebsocketCommunicator
from django.test import SimpleTestCase, override_settings
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from config.asgi import application


class UpstreamSocket:
    def __init__(self):
        self.received = []
        self.received_message = asyncio.Event()
        self.outgoing = asyncio.Queue()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.outgoing.get()
        if isinstance(message, Exception):
            raise message
        return message

    async def send(self, message):
        self.received.append(message)
        self.received_message.set()

    async def close(self):
        self.closed = True


@override_settings(BACKEND_URL='https://http-chat.internal:9003',
                   MOVEMENT_BACKEND_URL='http://movement.internal:9004')
class SocketRoutingTests(SimpleTestCase):
    async def test_routes_preserve_query_authentication_and_payload_without_rewriting(self):
        for route, upstream_url in (
            ('/ws/movement/', 'ws://movement.internal:9004'),
            ('/ws/campus/', 'ws://movement.internal:9004'),
            ('/ws/chat/', 'wss://http-chat.internal:9003'),
        ):
            upstream = UpstreamSocket()
            connector = AsyncMock(return_value=upstream)
            headers = [(b'cookie', b'campus_sessionid=test-session'),
                       (b'user-agent', b'test-browser'), (b'origin', b'https://campus.example')]
            query = '?control=server&moves=compact'
            client = WebsocketCommunicator(application, route + query, headers=headers)
            with patch('pages.sockets.connect', connector):
                connected, _ = await client.connect()
                self.assertTrue(connected)
                connector.assert_awaited_once_with(upstream_url + route + query,
                    additional_headers={'cookie': 'campus_sessionid=test-session', 'user-agent': 'test-browser'},
                    origin='https://campus.example', proxy=None,
                    open_timeout=5, close_timeout=3, max_size=2 ** 20)
                # The bridge must preserve signed actor tokens and compact/full packets byte-for-byte.
                text = '{"type":"chat","text":"hello","actorToken":"signed-test-token"}'
                await client.send_to(text_data=text)
                await asyncio.wait_for(upstream.received_message.wait(), 1)
                await upstream.outgoing.put('{"type":"chat_ready"}')
                self.assertEqual(await client.receive_from(), '{"type":"chat_ready"}')
                self.assertEqual(upstream.received, [text])
                await upstream.outgoing.put(b'binary-data')
                self.assertEqual(await client.receive_from(), b'binary-data')
                await client.disconnect()
                self.assertTrue(upstream.closed)

    @override_settings(MOVEMENT_BACKEND_URL='https://http-chat.internal:9003')
    async def test_same_server_configuration_supports_local_composite_application(self):
        upstream = UpstreamSocket()
        client = WebsocketCommunicator(application, '/ws/movement/')
        with patch('pages.sockets.connect', AsyncMock(return_value=upstream)) as connector:
            self.assertTrue((await client.connect())[0])
            self.assertEqual(connector.call_args.args[0], 'wss://http-chat.internal:9003/ws/movement/')
            await client.disconnect()

    async def test_upstream_connection_failure_rejects_without_starting_reader(self):
        client = WebsocketCommunicator(application, '/ws/chat/')
        with patch('pages.sockets.connect', AsyncMock(side_effect=OSError('unavailable'))):
            self.assertEqual(await client.connect(), (False, 4401))
            await client.disconnect()

    async def test_backend_close_codes_reach_the_browser_as_sendable_codes(self):
        # daphne(autobahn)는 1000·3000–4999 만 보낸다. 그 밖을 그대로 넘기면 예외가 되어 브라우저 소켓이 열린 채 남았다
        for received, expected in ((Close(1000, ''), 1000), (Close(4401, ''), 4401), (Close(4013, ''), 4013),
                                   (Close(1011, ''), 4011), (Close(1001, ''), 4001), (Close(2500, ''), 4011),
                                   (None, 4011)):
            with self.subTest(received=received and received.code):
                upstream = UpstreamSocket()
                client = WebsocketCommunicator(application, '/ws/campus/')
                with patch('pages.sockets.connect', AsyncMock(return_value=upstream)):
                    self.assertTrue((await client.connect())[0])
                    await upstream.outgoing.put(ConnectionClosed(received, None))
                    self.assertEqual(await client.receive_output(timeout=1), {'type': 'websocket.close', 'code': expected})
                    await client.disconnect()

    async def test_failed_send_to_backend_closes_the_browser_socket_with_4011(self):
        upstream = UpstreamSocket()
        upstream.send = AsyncMock(side_effect=ConnectionClosed(None, None))
        client = WebsocketCommunicator(application, '/ws/chat/')
        with patch('pages.sockets.connect', AsyncMock(return_value=upstream)):
            self.assertTrue((await client.connect())[0])
            await client.send_to(text_data='{"type":"ping"}')
            self.assertEqual(await client.receive_output(timeout=1), {'type': 'websocket.close', 'code': 4011})
            await client.disconnect()
