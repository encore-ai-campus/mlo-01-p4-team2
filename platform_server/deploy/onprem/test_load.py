import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location('onprem_load', Path(__file__).with_name('load.py'))
load = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load)


def arguments(**changes):
    return SimpleNamespace(service_http_base='http://127.0.0.1:18004',
        movement_ws_url='ws://127.0.0.1:18005/ws/movement/', chat_ws_url='ws://127.0.0.1:18004/ws/chat/',
        origin='https://app.example.com', users=1, duration=.02, ramp=0, chat_interval=0, **changes)


class LoadTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_requires_both_roles_and_preserves_public_host_without_logging_cookie(self):
        calls, sent = [], []

        class Socket:
            def __init__(self, url, closed=False):
                self.message = {'type': 'welcome', 'chatToken': 'test-token'} if '/movement/' in url else {'type': 'chat_ready'}
                self.closed = closed
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def recv(self):
                if self.message:
                    message, self.message = self.message, None
                    return json.dumps(message)
                if self.closed:
                    raise load.ConnectionClosed(None, None)
                await asyncio.Event().wait()
            async def send(self, message): sent.append(json.loads(message))

        def connector(url, **kwargs):
            calls.append((url, kwargs))
            return Socket(url)

        cookie = 'campus_sessionid=private-test-session'
        with patch.object(load, 'connect', connector), patch.object(load, 'http_status', return_value=200):
            result = await load.run(arguments(), [cookie])
        self.assertTrue(result['success'])
        self.assertEqual(result['ready'], 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], 'ws://app.example.com/ws/movement/')
        self.assertEqual(calls[0][1]['host'], '127.0.0.1')
        self.assertEqual(calls[0][1]['port'], 18005)
        self.assertEqual(calls[0][1]['origin'], 'https://app.example.com')
        self.assertEqual(calls[0][1]['additional_headers']['Cookie'], cookie)
        self.assertTrue(any(message.get('type') == 'input' for message in sent))
        self.assertNotIn(cookie, json.dumps(result))

        connection = MagicMock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b'{"authenticated":true}'
        with patch.object(load.http.client, 'HTTPConnection', return_value=connection):
            self.assertEqual(load.http_status(arguments(), cookie), 200)
            connection.getresponse.return_value.read.return_value = b'{"authenticated":false}'
            self.assertEqual(load.http_status(arguments(), cookie), 401)
        headers = connection.request.call_args.kwargs['headers']
        self.assertEqual(headers, {'Host': 'app.example.com', 'Cookie': cookie,
                                  'Origin': 'https://app.example.com', 'X-Forwarded-Proto': 'https'})

        def failing_connector(url, **kwargs):
            if '/chat/' in url:
                raise OSError(cookie)
            return Socket(url)

        with patch.object(load, 'connect', failing_connector):
            failed = await load.run(arguments(), [cookie])
        self.assertFalse(failed['success'])
        self.assertEqual((failed['ready'], failed['failed']), (0, 1))
        self.assertNotIn(cookie, json.dumps(failed))

        with patch.object(load, 'connect', lambda url, **kwargs: Socket(url, closed='/chat/' in url)), \
             patch.object(load, 'http_status', return_value=200):
            disconnected = await load.run(arguments(), [cookie])
        self.assertFalse(disconnected['success'])
        self.assertEqual((disconnected['ready'], disconnected['failed'], disconnected['closed_connections']), (1, 1, 1))

    async def test_duplicate_sessions_header_injection_and_endpoint_credentials_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sessions.json'
            args = arguments(sessions_json=str(path))
            for values in (['abc\r\nInjected: true'], ['same', 'same'], [{'cookie': 'bad'}]):
                with self.subTest(values=values):
                    path.write_text(json.dumps(values))
                    args.users = len(values)
                    with self.assertRaises(ValueError): load.validate(args)
            path.write_text('["validsession"]')
            args.users = 1
            self.assertEqual(load.validate(args), ['campus_sessionid=validsession'])
            args.origin = 'https://user:secret@app.example.com'
            with self.assertRaises(ValueError): load.validate(args)
            args.origin = 'https://app.example.com'
            for name in ('duration', 'ramp', 'chat_interval'):
                for value in (float('inf'), float('nan')):
                    with self.subTest(name=name, value=value):
                        previous = getattr(args, name)
                        setattr(args, name, value)
                        with self.assertRaises(ValueError): load.validate(args)
                        setattr(args, name, previous)


if __name__ == '__main__':
    unittest.main()
