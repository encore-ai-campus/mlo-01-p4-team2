"""기존 CLICK PUT의 접수 응답, 고정 ID 재시도와 지급 경계를 검증한다."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone as utc
from email.message import Message
from io import BytesIO, StringIO
import json
import os
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from ad_bridge.api.client import AdsClient
from ad_bridge.api.config import AdsConfig
from ad_bridge.api.errors import AdsResponseError
from ad_bridge.click_records import record_click, ServerClick
from ad_bridge.click_reporting import configured_report
from ad_bridge.click_worker import check_pending_clicks
from ad_bridge.models import AdClick, IssuedDecision
from ad_bridge.tests.test_click_lookup import Response


class ClickPutHttpTests(SimpleTestCase):
    def setUp(self):
        self.client = AdsClient(AdsConfig('https://ads.test/api/media/v1', 'test-media-key', 1,
                                          subject_secret='test-secret'))
        self.client._opener = Mock()
        self.args = dict(decision_id=uuid4(), reaction_id=uuid4(), subject_id=uuid4(),
                         occurred_at='2026-11-09T00:00:00.123456Z')
        self.body = {**{key:str(value) for key,value in self.args.items()}, 'type':'CLICK',
                     'received_at':'2026-11-09T00:00:01Z', 'status':'RECEIVED', 'reason_code':None}

    def test_201_and_200_accept_rejected_media_receipts_without_source_field(self):
        for status, state in ((201, 'RECEIVED'), (200, 'REJECTED')):
            with self.subTest(status=status):
                self.client._opener.open.return_value = Response(json.dumps({**self.body, 'status':state,
                    'reason_code':'click_not_reported' if state == 'REJECTED' else None}).encode(), status=status)
                result = self.client.report_click(**self.args)
                request = self.client._opener.open.call_args.args[0]
                self.assertEqual(request.method, 'PUT')
                self.assertEqual(request.get_header('X-media-key'), 'test-media-key')
                self.assertEqual(request.full_url, f'https://ads.test/api/media/v1/decisions/{self.args["decision_id"]}/reactions/{self.args["reaction_id"]}')
                self.assertEqual(json.loads(request.data), {'type':'CLICK', 'occurred_at':self.args['occurred_at'],
                                                          'subject_id':str(self.args['subject_id'])})
                self.assertEqual((result.type, result.status), ('CLICK', state))

    def test_success_status_alone_and_mismatching_payload_cannot_confirm(self):
        changes = [{'decision_id':str(uuid4())}, {'reaction_id':str(uuid4())}, {'subject_id':str(uuid4())},
                   {'type':'IMPRESSION'}, {'occurred_at':'2026-11-09T00:00:02Z'}, {'status':'unknown'}]
        for change in changes:
            with self.subTest(change=change):
                self.client._opener.open.return_value = Response(json.dumps({**self.body, **change}).encode(), status=201)
                with self.assertRaises(AdsResponseError): self.client.report_click(**self.args)
        for response in (Response(b'{}', status=204), Response(b'', status=201),
                         Response(b'{}', status=200, content_type='text/html')):
            self.client._opener.open.return_value = response
            with self.assertRaises(AdsResponseError): self.client.report_click(**self.args)

    def test_equivalent_timestamp_precision_and_server_clock_skew_are_valid_receipts(self):
        self.args['occurred_at'] = '2026-11-09T00:00:00.123Z'
        body = {**self.body, 'occurred_at':'2026-11-09T00:00:00.123000Z',
                'received_at':'2026-11-08T23:59:59Z'}
        self.client._opener.open.return_value = Response(json.dumps(body).encode(), status=200)
        self.assertEqual(self.client.report_click(**self.args).type, 'CLICK')


class ClickReportingTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='click-put')
        self.owner, self.subject, self.slot = uuid4(), uuid4(), uuid4()
        self.now = timezone.now()
        self.decision = IssuedDecision.objects.create(decision_id=uuid4(), audience='individual',
            owner_id=self.owner, subject_id=self.subject, slot_id=self.slot,
            decided_at=self.now-timedelta(seconds=10), has_ad=True)
        self.row, _ = record_click(user=self.user, owner_id=self.owner, decision_id=self.decision.pk)
        self.clock = self.now + timedelta(seconds=1)
        self.env = dict(ADS_API_BASE_URL='https://ads.test/api/media/v1', ADS_MEDIA_KEY='roof-key',
                        ADS_API_TIMEOUT='1', ADS_SUBJECT_SECRET='test-secret')
        self.opener = Mock()
        self.requests = []
        self.failures = []
        self.response_status = 'RECEIVED'
        self.award = Mock()
        self.opener.open.side_effect = self.send

    def send(self, request, **kwargs):
        self.requests.append(request)
        if self.failures:
            raise self.failures.pop(0)
        sent = json.loads(request.data)
        body = {**sent, 'decision_id':request.full_url.split('/')[-3],
                'reaction_id':request.full_url.split('/')[-1], 'received_at':sent['occurred_at'],
                'status':self.response_status, 'reason_code':'click_not_reported'}
        return Response(json.dumps(body).encode(), status=201 if len(self.requests)==1 else 200)

    def execute(self):
        with patch.dict(os.environ, self.env, clear=True), patch('ad_bridge.api.client.build_opener', return_value=self.opener):
            return check_pending_clicks(report=configured_report(), now=lambda:self.clock, award=self.award)

    def test_stored_click_report_awards_and_never_repeats_after_confirmation(self):
        self.assertEqual(self.execute()['confirmed'], 1)
        self.row.refresh_from_db()
        self.assertEqual((self.row.confirmation_source, self.row.server_reaction_id), ('media', self.row.pk))
        self.award.assert_called_once_with(self.user, session_uuid=None)
        self.assertEqual(self.execute()['checked'], 0)
        record_click(user=self.user, owner_id=self.owner, decision_id=self.decision.pk)
        self.assertEqual(self.execute()['checked'], 0)
        self.assertEqual(len(self.requests), 1)

    def test_lost_response_reuses_exact_url_subject_and_original_time(self):
        self.failures = [URLError('response lost')]
        self.assertEqual(self.execute()['pending'], 1)
        self.award.assert_not_called()
        self.row.refresh_from_db()
        self.clock = self.row.next_check_at
        self.assertEqual(self.execute()['confirmed'], 1)
        self.assertEqual((self.requests[0].full_url, self.requests[0].data),
                         (self.requests[1].full_url, self.requests[1].data))
        self.assertIn(str(self.row.pk), self.requests[0].full_url)

    def test_rejected_billing_report_still_confirms_storage(self):
        self.response_status = 'REJECTED'
        self.assertEqual(self.execute()['confirmed'], 1)
        self.row.refresh_from_db()
        self.assertEqual(self.row.server_status, 'REJECTED')
        self.award.assert_called_once()

    def test_conflict_blocks_without_changing_id_or_awarding(self):
        self.failures = [HTTPError('https://ads.test/',409,'conflict',Message(),BytesIO(b'{}'))]
        self.assertEqual(self.execute()['blocked'], 1)
        self.row.refresh_from_db()
        self.assertEqual(self.row.state, 'BLOCKED')
        self.award.assert_not_called()
        self.assertEqual(self.execute()['checked'], 0)

    def test_success_with_malformed_body_keeps_pending_and_id(self):
        self.opener.open.side_effect = None
        self.opener.open.return_value = Response(b'{}',status=201)
        self.assertEqual(self.execute()['pending'], 1)
        self.row.refresh_from_db()
        self.assertEqual(self.row.state, 'PENDING')
        self.assertIsNone(self.row.server_reaction_id)
        self.award.assert_not_called()

    def test_portal_slot_uses_portal_media_key(self):
        self.env.update(ADS_PORTAL_MEDIA_KEY='portal-key', ADS_PORTAL_MEDIA_SLOT_IDS=str(self.slot))
        self.execute()
        self.assertEqual(self.requests[0].get_header('X-media-key'), 'portal-key')

    def test_worker_cannot_accept_another_report_id_time_or_server_receipt(self):
        valid = ServerClick(decision_id=self.row.decision_id, subject_id=self.subject, reaction_id=self.row.pk,
            occurred_at=self.row.clicked_at, received_at=self.row.clicked_at, source='media', type='CLICK', status='RECEIVED')
        for value in (replace(valid, reaction_id=uuid4()), replace(valid, occurred_at=self.clock), replace(valid, source='server')):
            with self.subTest(value=value):
                AdClick.objects.filter(pk=self.row.pk).update(state='PENDING', next_check_at=self.now)
                result = check_pending_clicks(report=Mock(return_value=value), now=lambda:self.clock, award=self.award)
                self.assertEqual(result['blocked'], 1)
        self.award.assert_not_called()

    def test_default_command_uses_put_without_lookup_configuration(self):
        with patch.dict(os.environ, self.env, clear=True), patch('ad_bridge.api.client.build_opener', return_value=self.opener), \
                patch('ad_bridge.click_worker.confirm_click', return_value=True) as confirm:
            call_command('confirm_ad_clicks', stdout=StringIO())
        self.assertEqual(self.requests[0].method, 'PUT')
        self.assertEqual(confirm.call_args.kwargs['source'], 'media')

    def test_missing_config_does_not_claim_pending_record(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(CommandError):
            call_command('confirm_ad_clicks', stdout=StringIO())
        self.row.refresh_from_db()
        self.assertEqual((self.row.state,self.row.attempts),('PENDING',0))
