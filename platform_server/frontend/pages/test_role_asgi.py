import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from channels.testing import HttpCommunicator, WebsocketCommunicator
from django.test import SimpleTestCase, override_settings

from config.movement_asgi import application as movement_application
from config.service_asgi import application as service_application
from .test_sockets import UpstreamSocket


@override_settings(ALLOWED_HOSTS=['app.example.com', 'testserver'],
                   BACKEND_URL='http://127.0.0.1:8000',
                   MOVEMENT_BACKEND_URL='http://127.0.0.1:8001')
class FrontRoleRoutingTests(SimpleTestCase):
    def setUp(self):
        from .http_pool import close_backend_pool
        close_backend_pool()
        self.addCleanup(close_backend_pool)

    async def test_movement_http_exposes_only_health_without_calling_backend(self):
        with patch('pages.views.call_backend') as backend:
            client = HttpCommunicator(movement_application, 'GET', '/health/',
                                      headers=[(b'host', b'app.example.com')])
            response = await client.get_response()
            self.assertEqual(response['status'], 200)
            self.assertEqual(json.loads(response['body']), {'service': 'frontend', 'status': 'ok'})
            for path in ('/', '/accounts/logout/', '/api/learning/wallet/', '/static/campus/presence.js'):
                with self.subTest(path=path):
                    client = HttpCommunicator(movement_application, 'POST', path)
                    self.assertEqual((await client.get_response())['status'], 404)
            backend.assert_not_called()

    async def test_service_http_preserves_body_and_public_authentication_headers(self):
        upstream = MagicMock(status=200)
        upstream.read.return_value = b'{"ok":true}'
        upstream.getheaders.return_value = [('Content-Type', 'application/json')]
        connection = MagicMock()
        connection.getresponse.return_value = upstream
        body = b'{"message":"hello"}'
        client = HttpCommunicator(service_application, 'POST', '/api/messages/send/', body=body,
                                  headers=[(b'host', b'app.example.com'),
                                           (b'cookie', b'campus_sessionid=test-session'),
                                           (b'origin', b'https://app.example.com'),
                                           (b'x-forwarded-proto', b'https'),
                                           (b'x-csrftoken', b'csrf-token'),
                                           (b'content-type', b'application/json')])
        with patch('pages.transport.http.client.HTTPConnection', return_value=connection) as factory:
            response = await client.get_response()
        self.assertEqual(response['status'], 200)
        self.assertEqual(response['body'], b'{"ok":true}')
        factory.assert_called_once_with('127.0.0.1', 8000, timeout=30)
        self.assertEqual(connection.request.call_args.args, ('POST', '/api/messages/send/'))
        forwarded = connection.request.call_args.kwargs
        self.assertEqual(forwarded['body'], body)
        self.assertEqual(forwarded['headers']['Host'], 'app.example.com')
        self.assertEqual(forwarded['headers']['Cookie'], 'campus_sessionid=test-session')
        self.assertEqual(forwarded['headers']['Origin'], 'https://app.example.com')
        self.assertEqual(forwarded['headers']['X-Forwarded-Proto'], 'https')
        self.assertEqual(forwarded['headers']['X-CSRFToken'], 'csrf-token')
        connection.close.assert_not_called()

    async def test_each_role_bridges_its_own_socket_with_authentication_and_query(self):
        for application, route, destination in (
            (movement_application, '/ws/movement/', 'ws://127.0.0.1:8001'),
            (service_application, '/ws/chat/', 'ws://127.0.0.1:8000'),
        ):
            with self.subTest(route=route):
                upstream = UpstreamSocket()
                client = WebsocketCommunicator(application, route + '?control=server&moves=compact',
                    headers=[(b'cookie', b'campus_sessionid=test-session'),
                             (b'origin', b'https://app.example.com')])
                with patch('pages.sockets.connect', AsyncMock(return_value=upstream)) as connector:
                    self.assertTrue((await client.connect())[0])
                    connector.assert_awaited_once_with(destination + route + '?control=server&moves=compact',
                        additional_headers={'cookie': 'campus_sessionid=test-session'},
                        origin='https://app.example.com', proxy=None,
                        open_timeout=5, close_timeout=3, max_size=2 ** 20)
                    await upstream.outgoing.put('server-state')
                    self.assertEqual(await client.receive_from(), 'server-state')
                    await client.send_to(text_data='client-input')
                    await asyncio.wait_for(upstream.received_message.wait(), 1)
                    self.assertEqual(upstream.received, ['client-input'])
                    await client.disconnect()
                    self.assertTrue(upstream.closed)

    async def test_role_sockets_reject_other_roles_legacy_and_unknown_without_upstream(self):
        for application, routes in (
            (movement_application, ('/ws/chat/', '/ws/campus/', '/unknown/')),
            (service_application, ('/ws/movement/', '/ws/campus/', '/unknown/')),
        ):
            for route in routes:
                with self.subTest(application=application, route=route):
                    client = WebsocketCommunicator(application, route)
                    with patch('pages.sockets.connect', AsyncMock()) as connector:
                        self.assertEqual(await client.connect(), (False, 4404))
                        connector.assert_not_awaited()
                        await client.disconnect()
