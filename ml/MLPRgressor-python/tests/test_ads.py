import copy
from datetime import datetime, timedelta, timezone
from http.client import HTTPResponse, IncompleteRead
import io
import json
import unittest
from urllib.error import URLError
from uuid import UUID

from ads import AdsClient, MAX_RESPONSE, NoRedirect
from errors import JobError
from helpers import ID
from http_fakes import Opener, Response, acknowledgement, http_error, item, profile
from test_settings import config

ID2 = str(UUID(int=2))


class AdsTests(unittest.TestCase):
    def client(self, *responses):
        client = AdsClient(config())
        client.opener = Opener(*responses)
        return client

    def test_cursor_all_ads_and_uuid_case(self):
        client = self.client(Response({'items': [{'ad_group_id': ID.upper(), 'profile': profile(ID.upper()), 'future': 1}], 'next_after': ID.upper()}),
                             Response({'items': [{'ad_group_id': ID2, 'profile': None}], 'next_after': None}))
        ads = client.list_ads()
        self.assertEqual(set(ads), {ID, ID2})
        self.assertNotIn('state=', client.opener.requests[0].full_url)
        self.assertTrue(client.opener.requests[0].full_url.endswith('/ad-groups?limit=200'))
        self.assertIn('after=' + ID, client.opener.requests[1].full_url)
        self.assertEqual(client.request_ids, [ID, ID])
        self.assertEqual(client.opener.requests[0].get_header('X-internal-key'), 'NEVER_STORE_THIS_KEY')

    def test_page_contract_and_loops(self):
        ad = dict(ad_group_id=ID, profile=profile())
        bad_pages = [{'items': [ad, ad], 'next_after': None}, {'items': [ad] * 201, 'next_after': None},
                     {'items': [None], 'next_after': None}, {'items': [dict(ad_group_id=ID)], 'next_after': None},
                     {'items': [], 'next_after': 'bad'}, {'items': []}]
        for page in bad_pages:
            with self.subTest(page=str(page)[:80]), self.assertRaises(JobError):
                self.client(Response(page)).list_ads()
        client = self.client(Response({'items': [], 'next_after': ID}), Response({'items': [], 'next_after': ID}))
        with self.assertRaises(JobError) as caught:
            client.list_ads()
        self.assertEqual(caught.exception.code, 'api_cursor_loop')

    def test_empty_and_200_item_write(self):
        for items in ([], [item(str(UUID(int=i))) for i in range(200)]):
            client = self.client(Response({**acknowledgement(items), 'unknown': True}))
            self.assertEqual(len(client.write_weights(items)), len(items))
            request = client.opener.requests[0]
            self.assertEqual(request.method, 'POST')
            self.assertTrue(request.full_url.endswith('/profiles/weights'))
            body = json.loads(request.data)
            self.assertEqual(set(body), {'items'})
            for x in body['items']:
                self.assertEqual(set(x), {'ad_group_id', 'weight_mlo', 'weight_aio', 'weight_skn'})
                self.assertIs(type(x['weight_mlo']), int)

    def test_bad_requests_never_send(self):
        for items in ([item()] * 201, [item(), item()], [item(weight_mlo=True)], [item(weight_mlo=60.0)],
                      [item(weight_mlo=101)], [item(weight_mlo=61)], [item(source='mlpregressor')], [item(ad_group_id='bad')]):
            client = self.client()
            with self.assertRaises(JobError):
                client.write_weights(items)
            self.assertEqual(client.opener.requests, [])
        client = self.client()
        with self.assertRaises(JobError):
            client.request('POST', '/profiles/weights', {'items': ['x' * 65536]})
        self.assertEqual(client.opener.requests, [])

    def test_write_ack_contract(self):
        good = acknowledgement([item()])
        bad = []
        for key, value in [('ad_group_id', ID2), ('result', 'unknown'), ('profile', None)]:
            response = copy.deepcopy(good)
            response['items'][0][key] = value
            bad.append(response)
        response = copy.deepcopy(good)
        response['items'][0]['profile']['weight_mlo'] = 59
        bad += [response, {'items': []}, {'items': [None]}]
        for response in bad:
            with self.assertRaises(JobError):
                self.client(Response(response)).write_weights([item()])
        two = acknowledgement([item(), item(ID2)])
        two['items'].reverse()
        with self.assertRaises(JobError):
            self.client(Response(two)).write_weights([item(), item(ID2)])

    def test_profile_values_utc_and_null(self):
        for changes in ({'version': True}, {'version': 0}, {'category': True}, {'category': 256},
                        {'weight_aio': 30.0}, {'changed_at': '2026-02-30T00:00:00Z'},
                        {'changed_at': '2026-11-12T03:15:20+00:00'}, {'changed_at': '2026-11-12T03:15:20.1234567Z'}):
            page = {'items': [{'ad_group_id': ID, 'profile': profile(**changes)}], 'next_after': None}
            with self.assertRaises(JobError):
                self.client(Response(page)).list_ads()
        page = {'items': [{'ad_group_id': ID, 'profile': profile(category=None)}], 'next_after': None}
        self.assertIsNone(self.client(Response(page)).list_ads()[ID]['profile']['category'])

    def test_json_request_id_and_response_size(self):
        for response in (Response(b'{"n":NaN}'), Response(b'{"n":Infinity}'), Response(b'broken'),
                         Response({}, {}), Response({}, {'X-Request-ID': 'bad'}), Response(b'x' * (MAX_RESPONSE + 1))):
            with self.assertRaises(JobError) as caught:
                self.client(response).list_ads()
            self.assertFalse(caught.exception.retryable)

    def test_retry_classification_one_attempt(self):
        errors = [http_error(503, {'Retry-After': '900', 'X-Request-ID': ID}), URLError('SECRET'), IncompleteRead(b'secret')]
        for error in errors:
            client = self.client(error)
            with self.assertRaises(JobError) as caught:
                client.list_ads()
            self.assertTrue(caught.exception.retryable)
            self.assertEqual(len(client.opener.requests), 1)
            self.assertNotIn('SECRET', str(caught.exception))
        for status in (400, 401, 403, 413, 415, 500, 302):
            client = self.client(http_error(status))
            with self.assertRaises(JobError) as caught:
                client.list_ads()
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(len(client.opener.requests), 1)
        for value in ('', 'tomorrow', '-1', '1.5'):
            with self.assertRaises(JobError) as caught:
                self.client(http_error(503, {'Retry-After': value})).list_ads()
            self.assertEqual(caught.exception.code, 'invalid_retry_after')

    def test_real_http_response_shorter_than_declared_length(self):
        class Socket:
            def makefile(self, mode):
                return io.BytesIO((f'HTTP/1.1 200 OK\r\nContent-Length: 100\r\nX-Request-ID: {ID}\r\n\r\n'
                                   + '{"items":').encode())
        response = HTTPResponse(Socket())
        response.begin()
        client = self.client(response)
        with self.assertRaises(JobError) as caught:
            client.list_ads()
        self.assertEqual(caught.exception.code, 'api_connection_failed')
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(len(client.opener.requests), 1)

    def test_safe_problem_diagnostics(self):
        problem = {'code': 'invalid_request', 'detail': 'NEVER_STORE_THIS_KEY', 'errors': [
            {'pointer': '/items/2/weight_aio', 'message': 'NEVER_STORE_THIS_KEY'},
            {'pointer': '/NEVER_STORE_THIS_KEY', 'message': 'NEVER_STORE_THIS_KEY'}]}
        with self.assertRaises(JobError) as caught:
            self.client(http_error(400, {'X-Request-ID': ID}, problem)).write_weights([])
        self.assertEqual(caught.exception.details['pointers'], ['/items/2/weight_aio'])
        self.assertEqual(caught.exception.request_id, ID)
        self.assertNotIn('NEVER_STORE_THIS_KEY', str(caught.exception.details))

    def test_body_interruption_and_expiry_and_redirect(self):
        response = Response({})
        response.read = lambda size: (_ for _ in ()).throw(IncompleteRead(b'partial'))
        with self.assertRaises(JobError) as caught:
            self.client(response).write_weights([])
        self.assertTrue(caught.exception.retryable)
        now = datetime.now(timezone.utc)
        client = AdsClient(config(), expires_at=now, now=lambda: now + timedelta(seconds=1))
        client.opener = Opener()
        with self.assertRaises(JobError) as caught:
            client.write_weights([])
        self.assertTrue(caught.exception.deferred)
        self.assertEqual(client.opener.requests, [])
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other'))
