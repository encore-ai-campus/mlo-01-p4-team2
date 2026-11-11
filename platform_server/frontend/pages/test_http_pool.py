"""Reviewed real TCP tests: framing, isolation, bounded leases and no POST replay."""
import concurrent.futures
import http.client
import json
import select
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, override_settings

from .http_pool import backend_pool, close_backend_pool
from .rendering import render_document
from .transport import call_backend, passthrough
from urllib.parse import urlsplit


class Upstream(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self):
        self.accepts = 0
        self.rows = []
        self.guard = threading.Lock()
        self.two_arrived = threading.Event()
        self.release = threading.Event()
        self.fin_sent = threading.Event()
        super().__init__(('127.0.0.1', 0), Handler)

    def get_request(self):
        result = super().get_request()
        with self.guard:
            self.accepts += 1
        return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *_): pass
    def do_HEAD(self): self.do_GET()
    def do_POST(self): self.do_GET()

    def do_GET(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        row = {'method': self.command, 'path': self.path,
               'headers': dict(self.headers), 'body': raw.decode()}
        with self.server.guard:
            self.server.rows.append(row)
            if len(self.server.rows) >= 2:
                self.server.two_arrived.set()
        path = self.path.split('?')[0].removeprefix('/api')
        if path == '/hold':
            if not self.server.release.wait(5):
                return
        if path == '/drop-header':
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        if path == '/drop-body':
            self.send_response(200)
            self.send_header('Content-Length', '100')
            self.end_headers()
            self.wfile.write(b'partial')
            self.wfile.flush()
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        if path == '/empty':
            self.send_response(204)
            self.end_headers()
            return
        body = json.dumps(row).encode()
        self.send_response(303 if path == '/redirect' else 200)
        if path == '/redirect':
            self.send_header('Location', '/must-not-follow')
            self.send_header('Set-Cookie', 'one=first; Path=/; HttpOnly')
            self.send_header('Set-Cookie', 'two=second; Path=/')
        if path == '/chunk':
            self.send_header('Transfer-Encoding', 'chunked')
        else:
            self.send_header('Content-Length', str(len(body)))
        if path == '/close':
            self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            if path == '/chunk':
                self.wfile.write(('%x\r\n' % len(body)).encode() + body + b'\r\n0\r\n\r\n')
            else:
                self.wfile.write(body)
        self.wfile.flush()
        if path == '/fin':
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_WR)
            self.server.fin_sent.set()


@contextmanager
def upstream():
    server = Upstream()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(3)


@override_settings(BACKEND_HTTP_KEEPALIVE=True, BACKEND_HTTP_POOL_MAXSIZE=1,
                   BACKEND_HTTP_IDLE_TIMEOUT=20, BACKEND_TIMEOUT=2,
                   ALLOWED_HOSTS=['public.test', 'testserver'])
class HttpPoolTests(SimpleTestCase):
    def setUp(self):
        close_backend_pool()
        self.addCleanup(close_backend_pool)

    def request(self, path='/echo', cookie=None, body=b'', method='GET'):
        extra = {'HTTP_HOST': 'public.test'}
        if cookie is not None:
            extra['HTTP_COOKIE'] = cookie
        request = RequestFactory().generic(method, path, data=body,
            content_type='application/json', **extra)
        if cookie is None:
            request.META.pop('HTTP_COOKIE', None)
        return request

    def test_K01_sequential_requests_share_one_real_tcp_connection(self):
        with upstream() as server, self.settings(BACKEND_URL='http://127.0.0.1:%d' % server.server_port):
            for index in range(8):
                result = call_backend(self.request('/echo?n=%d' % index))
                self.assertEqual(result.status, 200)
                self.assertEqual(json.loads(result.body)['path'], '/echo?n=%d' % index)
            self.assertEqual(len(server.rows), 8)
            self.assertEqual(server.accepts, 1)
            close_backend_pool()

    def test_K02_expiry_close_and_peer_fin_open_new_connections(self):
        with upstream() as server, self.settings(BACKEND_URL='http://127.0.0.1:%d' % server.server_port):
            now = [100.0]
            with patch('pages.http_pool.monotonic', side_effect=lambda: now[0]):
                call_backend(self.request())
                now[0] += 21
                call_backend(self.request())
                self.assertEqual(server.accepts, 2)
                call_backend(self.request('/close'))
                call_backend(self.request())
                self.assertEqual(server.accepts, 3)
                call_backend(self.request('/fin'))
                self.assertTrue(server.fin_sent.wait(2))
                pool = backend_pool(urlsplit(settings.BACKEND_URL), 2, 1, 20)
                self.assertTrue(select.select([pool.idle[0][0].sock], [], [], 2)[0])
                call_backend(self.request())
                self.assertEqual(server.accepts, 4)
                self.assertEqual(len(server.rows), 6)
                close_backend_pool()

    @override_settings(BACKEND_HTTP_POOL_MAXSIZE=2, BACKEND_TIMEOUT=4)
    def test_K03_concurrent_requests_are_isolated_and_capacity_is_bounded(self):
        with upstream() as server, self.settings(BACKEND_URL='http://127.0.0.1:%d' % server.server_port):
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                futures = [executor.submit(call_backend, self.request('/hold', 'user=%d' % i,
                           ('body-%d' % i).encode(), 'POST')) for i in range(6)]
                self.assertTrue(server.two_arrived.wait(2))
                self.assertEqual(server.accepts, 2)
                self.assertEqual(len(server.rows), 2)
                server.release.set()
                results = [json.loads(f.result(timeout=5).body) for f in futures]
            self.assertEqual(server.accepts, 2)
            for i, row in enumerate(results):
                self.assertEqual(row['headers']['Cookie'], 'user=%d' % i)
                self.assertEqual(row['body'], 'body-%d' % i)
            close_backend_pool()

    @override_settings(BACKEND_TIMEOUT=.15)
    def test_K03_pool_wait_is_finite_and_view_returns_unavailable(self):
        with upstream() as server, self.settings(BACKEND_URL='http://127.0.0.1:%d' % server.server_port):
            pool = backend_pool(urlsplit(settings.BACKEND_URL), .15, 1, 20)
            with pool.lease():
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    call_backend(self.request())
                self.assertLess(time.monotonic() - started, 2)
                response = self.client.get('/api/probe/')
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json()['error'], 'backend_unavailable')
            self.assertEqual(len(server.rows), 0)
            close_backend_pool()

    def test_K04_headers_accounts_framing_redirect_and_cookies_remain_exact(self):
        with upstream() as server, self.settings(BACKEND_URL='http://127.0.0.1:%d' % server.server_port):
            request = self.request('/echo?q=one', 'account=A; csrf=A', b'{"value":1}', 'POST')
            request.META.update(HTTP_X_FORWARDED_PROTO='https', HTTP_X_CSRFTOKEN='csrf-A',
                HTTP_ORIGIN='https://public.test', HTTP_REFERER='https://public.test/play/')
            row = json.loads(call_backend(request).body)
            for name, expected in [('Host','public.test'), ('X-Forwarded-Proto','https'),
                                   ('X-CSRFToken','csrf-A'), ('Origin','https://public.test'),
                                   ('Referer','https://public.test/play/')]:
                self.assertEqual(row['headers'][name], expected)
            self.assertEqual(row['body'], '{"value":1}')
            self.assertEqual(row['path'], '/echo?q=one')
            other = json.loads(call_backend(self.request(cookie='account=B')).body)
            self.assertEqual(other['headers']['Cookie'], 'account=B')
            anonymous = json.loads(call_backend(self.request()).body)
            self.assertNotIn('Cookie', anonymous['headers'])
            head = call_backend(self.request('/head', method='HEAD'))
            self.assertEqual(head.body, b'')
            empty = call_backend(self.request('/empty'))
            self.assertEqual((empty.status, empty.body), (204, b''))
            chunk = call_backend(self.request('/chunk'))
            self.assertEqual(json.loads(chunk.body)['path'], '/chunk')
            response = passthrough(call_backend(self.request('/redirect')))
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response['Location'], '/must-not-follow')
            self.assertEqual(response.cookies['one'].value, 'first')
            self.assertEqual(response.cookies['two'].value, 'second')
            self.assertEqual(server.accepts, 1)
            self.assertNotIn('/must-not-follow', [r['path'] for r in server.rows])
            close_backend_pool()

    def test_K04_origin_change_never_sends_new_account_to_old_server(self):
        with upstream() as first, upstream() as second:
            with self.settings(BACKEND_URL='http://127.0.0.1:%d' % first.server_port):
                call_backend(self.request(cookie='account=A'))
            with self.settings(BACKEND_URL='http://127.0.0.1:%d' % second.server_port):
                row = json.loads(call_backend(self.request(cookie='account=B', body=b'new', method='POST')).body)
            self.assertEqual(len(first.rows), 1)
            self.assertEqual(first.rows[0]['headers']['Cookie'], 'account=A')
            self.assertEqual(row['headers']['Cookie'], 'account=B')
            self.assertEqual(row['body'], 'new')
            close_backend_pool()

    def test_K05_committed_post_lost_response_is_never_replayed(self):
        for path, exception in [('/api/drop-header', http.client.RemoteDisconnected),
                                ('/api/drop-body', http.client.IncompleteRead)]:
            with self.subTest(path=path), upstream() as server, self.settings(
                    BACKEND_URL='http://127.0.0.1:%d' % server.server_port):
                close_backend_pool()
                with self.assertRaises(exception):
                    call_backend(self.request(path, body=b'committed-once', method='POST'))
                self.assertEqual(len(server.rows), 1)
                pool = backend_pool(urlsplit(settings.BACKEND_URL), 2, 1, 20)
                self.assertEqual(pool.idle, [])
                self.assertEqual(pool.leased, 0)
                self.assertEqual(server.rows[0]['body'], 'committed-once')
                result = self.client.post(path, data=b'second-independent-request',
                                          content_type='application/json')
                self.assertEqual(result.status_code, 502)
                self.assertEqual(result.json()['error'], 'backend_unavailable')
                self.assertEqual(len(server.rows), 2)
                self.assertEqual(call_backend(self.request()).status, 200)
                self.assertEqual(len(server.rows), 3)
                close_backend_pool()

    @override_settings(BACKEND_HTTP_KEEPALIVE=False)
    def test_K01_disabled_flag_retains_one_connection_per_request(self):
        with upstream() as server, self.settings(BACKEND_URL='http://127.0.0.1:%d' % server.server_port):
            for _ in range(3):
                self.assertEqual(call_backend(self.request()).status, 200)
            self.assertEqual(server.accepts, 3)


class RenderedMapTests(SimpleTestCase):
    def test_F07_ads_on_off_use_real_renderer_and_escape_user_values(self):
        root = Path(__file__).resolve().parents[1]
        for enabled in (False, True):
            with self.subTest(ads=enabled):
                data = {'template':'index.html', 'context':{
                    'user':{'is_authenticated':True,'is_staff':False,'username':'<unsafe-user>'},
                    'ads_enabled':enabled, 'nickname':'<unsafe-user>', 'csrf_token':'token'}}
                html, _ = render_document(data, root, debug=False)
                self.assertNotIn('{%', html)
                self.assertNotIn('<!-- learning-panels -->', html)
                self.assertEqual('campus-ads.js' in html, enabled)
                self.assertNotIn('<unsafe-user>', html)
                self.assertIn('&lt;unsafe-user&gt;', html)
