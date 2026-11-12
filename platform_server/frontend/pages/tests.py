import http.client
import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from .transport import BackendResponse, call_backend, passthrough


class TransportTests(SimpleTestCase):
    def setUp(self):
        from .http_pool import close_backend_pool
        close_backend_pool()
        self.addCleanup(close_backend_pool)

    def test_click_redirect_keeps_the_signed_target_and_referrer_policy(self):
        response = passthrough(BackendResponse(303, b'', [('Location', 'https://ads.test/c/token?s=viewer'),
            ('Referrer-Policy', 'no-referrer'), ('Cache-Control', 'private, no-store')]))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['Location'], 'https://ads.test/c/token?s=viewer')
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertIn('no-store', response['Cache-Control'])

    @override_settings(BACKEND_URL='http://127.0.0.1:9003', BACKEND_TIMEOUT=30,
                       MOVEMENT_BACKEND_URL='http://movement.internal:9004')
    def test_post_body_cookie_csrf_origin_and_public_host_are_forwarded(self):
        request = RequestFactory().post('/accounts/login/?next=/', {'username': 'tester'},
            HTTP_HOST='127.0.0.1:8004', HTTP_COOKIE='campus_sessionid=test-session',
            HTTP_X_CSRFTOKEN='csrf-token', HTTP_ORIGIN='http://127.0.0.1:8004')
        upstream = MagicMock(status=302)
        upstream.read.return_value = b''
        upstream.getheaders.return_value = [('Location', '/')]
        connection = MagicMock()
        connection.getresponse.return_value = upstream
        with patch('pages.transport.http.client.HTTPConnection', return_value=connection):
            self.assertEqual(call_backend(request).status, 302)
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ('POST', '/accounts/login/?next=/'))
        self.assertEqual(kwargs['body'], request.body)
        self.assertEqual(kwargs['headers']['Host'], '127.0.0.1:8004')
        self.assertEqual(kwargs['headers']['Cookie'], 'campus_sessionid=test-session')
        self.assertEqual(kwargs['headers']['X-CSRFToken'], 'csrf-token')
        self.assertEqual(kwargs['headers']['Origin'], 'http://127.0.0.1:8004')
        connection.close.assert_not_called()

    @override_settings(BACKEND_URL='http://127.0.0.1:9003', BACKEND_TIMEOUT=30,
                       ALLOWED_HOSTS=['43.203.25.153'])
    def test_trusted_tls_proxy_keeps_public_https_origin_for_backend_csrf(self):
        request = RequestFactory().post('/accounts/login/', {'username': 'tester'},
            HTTP_HOST='43.203.25.153', HTTP_X_FORWARDED_PROTO='https',
            HTTP_ORIGIN='https://43.203.25.153',
            HTTP_REFERER='https://43.203.25.153/accounts/login/')
        upstream = MagicMock(status=302)
        upstream.read.return_value = b''
        upstream.getheaders.return_value = []
        connection = MagicMock()
        connection.getresponse.return_value = upstream
        with patch('pages.transport.http.client.HTTPConnection', return_value=connection):
            self.assertEqual(call_backend(request).status, 302)
        forwarded = connection.request.call_args.kwargs['headers']
        self.assertEqual(forwarded['Host'], '43.203.25.153')
        self.assertEqual(forwarded['X-Forwarded-Proto'], 'https')
        self.assertEqual(forwarded['Origin'], 'https://43.203.25.153')
        self.assertEqual(forwarded['Referer'], 'https://43.203.25.153/accounts/login/')

    def test_login_sets_multiple_cookies_and_logout_deletes_them(self):
        response = passthrough(BackendResponse(302, b'', [('Location', '/'),
            ('Set-Cookie', 'campus_sessionid=abc; HttpOnly; Path=/; SameSite=Lax'),
            ('Set-Cookie', 'campus_csrftoken=csrf; Path=/; SameSite=Lax')]))
        self.assertEqual(response.cookies['campus_sessionid'].value, 'abc')
        self.assertTrue(response.cookies['campus_sessionid']['httponly'])
        self.assertEqual(response.cookies['campus_csrftoken'].value, 'csrf')
        deleted = passthrough(BackendResponse(302, b'', [('Set-Cookie', 'campus_sessionid=""; Max-Age=0; Path=/')]))
        self.assertEqual(deleted.cookies['campus_sessionid']['max-age'], '0')

    def test_backend_failure_provides_a_retryable_api_error(self):
        with patch('pages.views.call_backend', side_effect=OSError('internal hostname')):
            response = self.client.get('/api/campus/menu-photo/')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['error'], 'backend_unavailable')
        self.assertNotIn('internal hostname', response.content.decode())

    def test_backend_database_busy_keeps_retry_after_and_pages_get_an_html_notice(self):
        busy = BackendResponse(503, json.dumps({'error': 'database_busy', 'message': '잠시 후 다시 시도해 주세요.'}).encode(),
            [('Content-Type', 'application/json'), ('Retry-After', '1')])
        with patch('pages.views.call_backend', return_value=busy):
            api = self.client.get('/api/campus/menu-photo/')
            page = self.client.get('/accounts/login/')
            content = self.client.get('/content/game-1/open/')
        self.assertEqual((api.status_code, api['Retry-After'], api.json()['error']), (503, '1', 'database_busy'))
        for response in (page, content):
            self.assertEqual((response.status_code, response['Retry-After']), (503, '1'))
            self.assertTrue(response['Content-Type'].startswith('text/html'))
            self.assertContains(response, '잠시 후 다시 시도해 주세요.', status_code=503)
            self.assertNotContains(response, 'database_busy', status_code=503)

    def test_other_backend_errors_on_pages_pass_through_unchanged(self):
        # 화면 안내로 바꾸는 것은 503(DB 를 잠깐 못 씀)뿐이다. 백엔드 500 은 본문·형식 그대로 넘긴다
        failed = BackendResponse(500, b'{"error": "server_error"}', [('Content-Type', 'application/json')])
        with patch('pages.views.call_backend', return_value=failed):
            for path in ('/accounts/login/', '/content/game-1/open/'):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual((response.status_code, response['Content-Type'], response.content),
                                     (500, 'application/json', b'{"error": "server_error"}'))
                    self.assertNotIn('Retry-After', response)

    def test_form_validation_errors_are_rendered_without_password_values(self):
        context = {'user': {'is_authenticated': False, 'is_staff': False}, 'csrf_token': 'csrf',
            'form': {'bound': True, 'fields': [
                {'name': 'username', 'label': '아이디', 'kind': 'text', 'required': True, 'value': '<script>', 'attrs': {}},
                {'name': 'password', 'label': '비밀번호', 'kind': 'password', 'required': True, 'value': None, 'attrs': {}}],
                'errors': {'__all__': ['아이디 또는 비밀번호를 확인해 주세요.']}}}
        backend = BackendResponse(200, json.dumps({'presentation': {'template': 'accounts/login.html', 'context': context}}).encode(),
            [('Content-Type', 'application/json'), ('Cache-Control', 'private, no-store')])
        with patch('pages.views.call_backend', return_value=backend):
            response = self.client.get('/accounts/login/')
        self.assertContains(response, '아이디 또는 비밀번호를 확인해 주세요.')
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>')
        self.assertIn('no-store', response['Cache-Control'])
