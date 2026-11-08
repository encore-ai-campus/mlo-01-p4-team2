"""클릭 조회 HTTP 계약 초안과 실제 작업자 연결을 모의 응답으로 검증한다."""
from datetime import timedelta
from email.message import Message
from io import BytesIO, StringIO
import json
import os
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from ad_bridge.api.client import AdsClient, _NoRedirect
from ad_bridge.api.config import AdsConfig
from ad_bridge.api.errors import AdsHTTPError, AdsResponseError, AdsTransportError
from ad_bridge.click_lookup import lookup
from ad_bridge.click_records import record_click
from ad_bridge.click_worker import configured_lookup, check_pending_clicks
from ad_bridge.models import AdClick, IssuedDecision


class Response:
    def __init__(self, body, status=200, content_type='application/json'):
        self.body, self.status = body, status
        self.headers = Message()
        self.headers['Content-Type'] = content_type
    def read(self, limit): return self.body[:limit]
    def __enter__(self): return self
    def __exit__(self, *args): pass


def receipt(decision, subject):
    return dict(decision_id=str(decision), subject_id=str(subject), reaction_id=str(uuid4()),
                source='server', type='CLICK', status='RECEIVED',
                occurred_at='2026-11-09T00:00:00Z', received_at='2026-11-09T00:00:01Z')


class ClickLookupTests(SimpleTestCase):
    def setUp(self):
        self.decision, self.subject = uuid4(), uuid4()
        self.client = AdsClient(AdsConfig('https://ads.test/api/media/v1', 'test-media-key', 1,
                                          subject_secret='test-subject-secret'))
        self.client._opener = Mock()
        self.body = receipt(self.decision, self.subject)

    def fetch(self, **kwargs):
        args = dict(path='test-click-status', decision_id=self.decision, subject_id=self.subject)
        args.update(kwargs)
        return self.client.get_server_click(**args)

    def test_get_auth_query_and_verified_result(self):
        self.client._opener.open.return_value = Response(json.dumps(self.body).encode())
        result = self.fetch()
        request = self.client._opener.open.call_args.args[0]
        self.assertEqual(request.method, 'GET')
        self.assertEqual(request.get_header('X-media-key'), 'test-media-key')
        self.assertEqual(parse_qs(urlsplit(request.full_url).query),
                         {'decision_id': [str(self.decision)], 'subject_id': [str(self.subject)]})
        self.assertEqual(result.subject_id, self.subject)
        self.assertEqual(result.source, 'server')
        self.assertEqual(self.client._opener.open.call_args.kwargs['timeout'], 1)

    def test_404_is_not_observed(self):
        self.client._opener.open.side_effect = HTTPError('https://ads.test/', 404, 'missing', Message(), BytesIO())
        self.assertIsNone(self.fetch())

    def test_http_errors_keep_retry_information_without_key(self):
        headers = Message(); headers['Retry-After'] = '120'
        self.client._opener.open.side_effect = HTTPError('https://ads.test/', 429, 'limited', headers,
                                                        BytesIO(b'{"code":"test-media-key"}'))
        with self.assertRaises(AdsHTTPError) as error: self.fetch()
        self.assertEqual((error.exception.status, error.exception.retry_after), (429, 120))
        self.assertNotIn('test-media-key', error.exception.code)

    def test_connection_error_is_retryable(self):
        self.client._opener.open.side_effect = URLError('network')
        with self.assertRaises(AdsTransportError): self.fetch()

    def test_foreign_subject_source_type_or_malformed_fields_cannot_confirm(self):
        for change in ({'subject_id': str(uuid4())}, {'decision_id': str(uuid4())}, {'source':'media'},
                       {'source':None}, {'type':'IMPRESSION'}, {'reaction_id':'bad'}, {'status':'bad'},
                       {'occurred_at':'not-a-time'}, {'received_at':'2026-11-08T00:00:00Z'}):
            with self.subTest(change=change):
                self.client._opener.open.return_value = Response(json.dumps({**self.body, **change}).encode())
                with self.assertRaises(AdsResponseError): self.fetch()
        del self.body['source']
        self.client._opener.open.return_value = Response(json.dumps(self.body).encode())
        with self.assertRaises(AdsResponseError): self.fetch()

    def test_bad_transport_bodies_cannot_confirm(self):
        for response in (Response(b'null'), Response(b'[]'), Response(b'bad'), Response(b'x' * (1024*1024+1)),
                         Response(b'{}', content_type='text/html'), Response(b'{}', status=204)):
            self.client._opener.open.return_value = response
            with self.assertRaises(AdsResponseError): self.fetch()

    def test_missing_or_external_path_fails_before_network(self):
        for path in ('', '/clicks', '//other.test', 'https://other.test', '../clicks', 'a/../b', 'a?x=1', 'a%2fb'):
            with self.subTest(path=path), self.assertRaises(ValueError): self.fetch(path=path)
        self.client._opener.open.assert_not_called()

    def test_redirect_handler_never_forwards_auth(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))

    @override_settings(ADS_CLICK_LOOKUP_PATH='test-click-status')
    def test_slot_routes_to_its_original_media_key(self):
        portal = uuid4()
        env = dict(ADS_API_BASE_URL='https://ads.test/api/media/v1', ADS_MEDIA_KEY='roof-test-key',
                   ADS_API_TIMEOUT='1', ADS_SUBJECT_SECRET='test-secret', ADS_PORTAL_MEDIA_KEY='portal-test-key',
                   ADS_PORTAL_MEDIA_SLOT_IDS=str(portal))
        keys = []
        def fetch(client, **kwargs):
            keys.append(client._config.media_key)
            self.assertEqual(kwargs['subject_id'], self.subject)
            return None
        with patch.dict(os.environ, env, clear=True), patch.object(AdsClient, 'get_server_click', autospec=True, side_effect=fetch):
            lookup(slot_id=portal, decision_id=self.decision, subject_id=self.subject)
            lookup(slot_id=uuid4(), decision_id=self.decision, subject_id=self.subject)
        self.assertEqual(keys, ['portal-test-key', 'roof-test-key'])


@override_settings(ADS_CLICK_LOOKUP_BACKEND='ad_bridge.click_lookup.lookup')
class ClickLookupWorkerTests(TransactionTestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username='lookup-worker')
        owner, subject = uuid4(), uuid4()
        decision = IssuedDecision.objects.create(decision_id=uuid4(), audience='individual', owner_id=owner,
            subject_id=subject, slot_id=uuid4(), decided_at='2026-11-08T00:00:00Z', has_ad=True)
        self.row, _ = record_click(user=user, owner_id=owner, decision_id=decision.pk)
        self.env = dict(ADS_API_BASE_URL='https://ads.test/api/media/v1', ADS_MEDIA_KEY='test-key',
                        ADS_API_TIMEOUT='1', ADS_SUBJECT_SECRET='test-secret')

    @override_settings(ADS_CLICK_LOOKUP_PATH='')
    def test_unconfigured_endpoint_leaves_pending_unclaimed(self):
        with self.assertRaises(CommandError): call_command('confirm_ad_clicks', '--lookup', stdout=StringIO())
        self.row.refresh_from_db()
        self.assertEqual((self.row.state, self.row.attempts), ('PENDING', 0))

    @override_settings(ADS_CLICK_LOOKUP_PATH='test-click-status')
    def test_http_receipt_reaches_worker_and_award(self):
        body = receipt(self.row.decision_id, self.row.subject_id)
        opener = Mock(); opener.open.return_value = Response(json.dumps(body).encode())
        award = Mock()
        with patch.dict(os.environ, self.env, clear=True), patch('ad_bridge.api.client.build_opener', return_value=opener):
            callback = configured_lookup()
            counts = check_pending_clicks(lookup=callback, now=lambda:timezone.now()+timedelta(seconds=3), award=award)
        self.assertEqual(counts['confirmed'], 1)
        self.row.refresh_from_db()
        self.assertEqual(self.row.state, 'CONFIRMED')
        award.assert_called_once()
