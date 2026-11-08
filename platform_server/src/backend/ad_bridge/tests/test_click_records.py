"""클릭 접수와 서버 확인의 경계. 외부 조회·업적은 명시적 테스트 대역을 사용한다."""
from datetime import timedelta
from io import StringIO
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.db import DatabaseError, connection
from django.http import HttpResponseRedirect
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.utils import timezone

from ad_bridge.api.errors import AdsHTTPError, AdsResponseError, AdsTransportError
from ad_bridge.click_records import (ClickNotFound, ServerClick, claim_click, click_status,
                                     confirm_click, record_click)
from ad_bridge.click_worker import check_pending_clicks
from ad_bridge.clicks import click_token
from ad_bridge.game.views import click_result, report_click
from ad_bridge.models import AdClick, IssuedDecision


class ClickRecordTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='click-user')
        self.other = get_user_model().objects.create_user(username='click-other')
        self.owner, self.subject = uuid4(), uuid4()
        self.user.campus_profile = SimpleNamespace(user_uuid=self.owner)
        self.other.campus_profile = SimpleNamespace(user_uuid=uuid4())
        self.now = timezone.now()
        self.decision = IssuedDecision.objects.create(decision_id=uuid4(), audience='individual',
            owner_id=self.owner, subject_id=self.subject, slot_id=uuid4(),
            decided_at=self.now - timedelta(seconds=10), has_ad=True)
        self.factory = RequestFactory()
        self.award = Mock()
        self.check_time = self.now + timedelta(seconds=3)

    def record(self, **kwargs):
        args = dict(user=self.user, owner_id=self.owner, decision_id=self.decision.pk)
        args.update(kwargs)
        return record_click(**args)[0]

    def receipt(self, **kwargs):
        args = dict(decision_id=self.decision.pk, subject_id=self.subject, reaction_id=uuid4(),
                    occurred_at=self.now, received_at=self.now, source='server', type='CLICK', status='RECEIVED')
        args.update(kwargs)
        return ServerClick(**args)

    def run_worker(self, lookup=None, **kwargs):
        return check_pending_clicks(lookup=lookup or Mock(return_value=self.receipt()),
                                   now=lambda: self.check_time, award=self.award, **kwargs)

    def request_click(self, user=None, target='https://ads.test/c/token', token=None):
        user = user or self.user
        token = click_token(self.decision.pk, target, user.campus_profile.user_uuid) if token is None else token
        request = self.factory.post('/', {'token': token, 'subject_id': str(uuid4())})
        request.user, request.session = user, {'platform_session_uuid': str(uuid4())}
        # 이 분리 테스트 설정에는 eventing 앱이 없으므로 세션 키만 제공한다.
        with patch.dict(sys.modules, {'eventing.events': SimpleNamespace(SESSION_KEY='platform_session_uuid')}), \
                patch('ad_bridge.game.views.configured_report', side_effect=ValueError('unconfigured')), \
                patch('ad_bridge.game.views.logger'):
            return report_click(request, self.decision.pk)

    def test_commit_before_redirect_and_no_award(self):
        def redirect(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            row = AdClick.objects.get()
            self.assertEqual(row.state, 'PENDING')
            self.assertEqual((row.user_id, row.owner_id, row.subject_id), (self.user.pk, self.owner, self.subject))
            self.assertIsNotNone(row.session_uuid)
            return HttpResponseRedirect(*args, **kwargs)
        with patch('ad_bridge.game.views.HttpResponseRedirect', side_effect=redirect):
            response = self.request_click()
        self.assertEqual((response.status_code, response['Location']), (303, 'https://ads.test/c/token'))
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')

    def test_duplicate_preserves_original_identity_time_and_session(self):
        session = uuid4()
        first = self.record(session_uuid=session)
        repeated = self.record(session_uuid=uuid4(), subject_id=uuid4())
        self.assertEqual((first.pk, first.clicked_at, first.subject_id, first.session_uuid),
                         (repeated.pk, repeated.clicked_at, repeated.subject_id, repeated.session_uuid))
        self.assertEqual(AdClick.objects.count(), 1)

    def test_signed_shared_subject_survives_secret_rotation(self):
        self.decision.audience, self.decision.owner_id, self.decision.subject_id = 'shared', None, None
        self.decision.save()
        self.assertEqual(self.request_click(target=f'https://ads.test/c/token?s={self.subject}').status_code, 303)
        self.assertEqual(self.request_click(target=f'https://ads.test/c/token?s={uuid4()}').status_code, 303)
        self.assertEqual(AdClick.objects.get().subject_id, self.subject)
        for suffix in ('', '?s=bad', f'?s={self.subject}&s={uuid4()}'):
            self.assertEqual(self.request_click(target='https://ads.test/c/token' + suffix).status_code, 400)

    def test_forged_and_foreign_clicks_never_create_a_record(self):
        self.assertEqual(self.request_click(token='forged').status_code, 400)
        self.assertEqual(self.request_click(user=self.other).status_code, 404)
        self.decision.has_ad = False
        self.decision.save()
        self.assertEqual(self.request_click().status_code, 404)
        self.assertFalse(AdClick.objects.exists())

    def test_storage_failure_never_redirects(self):
        with patch('ad_bridge.game.views.record_click', side_effect=DatabaseError('failed')):
            response = self.request_click()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('Location', response)

    def test_status_is_local_and_owner_only(self):
        self.record()
        request = self.factory.get('/')
        request.user = self.user
        response = click_result(request, self.decision.pk)
        self.assertEqual(json.loads(response.content)['state'], 'PENDING')
        self.assertNotIn('subject_id', json.loads(response.content))
        request.user = self.other
        self.assertEqual(click_result(request, self.decision.pk).status_code, 404)
        with self.assertRaises(ClickNotFound):
            click_status(user=self.other, owner_id=self.owner, decision_id=self.decision.pk)

    def test_confirmation_records_receipt_and_awards_once(self):
        row = self.record(session_uuid=uuid4())
        receipt = self.receipt()
        lookup = Mock(return_value=receipt)
        self.assertEqual(self.run_worker(lookup)['confirmed'], 1)
        lookup.assert_called_once_with(slot_id=self.decision.slot_id, decision_id=self.decision.pk, subject_id=self.subject)
        row.refresh_from_db()
        self.assertEqual((row.state, row.server_reaction_id, row.server_status), ('CONFIRMED', receipt.reaction_id, 'RECEIVED'))
        self.award.assert_called_once_with(self.user, session_uuid=row.session_uuid)
        self.assertEqual(self.run_worker()['checked'], 0)
        self.record()
        self.assertEqual(AdClick.objects.count(), 1)
        self.assertEqual(self.run_worker()['checked'], 0)
        self.award.assert_called_once()

    def test_billing_rejection_still_confirms_server_storage(self):
        self.record()
        self.assertEqual(self.run_worker(Mock(return_value=self.receipt(status='REJECTED')))['confirmed'], 1)
        self.award.assert_called_once()

    def test_unobserved_click_stays_pending_until_later_confirmation(self):
        row = self.record()
        lookup = Mock(return_value=None)
        self.assertEqual(self.run_worker(lookup)['pending'], 1)
        row.refresh_from_db()
        self.assertEqual(row.state, 'PENDING')
        self.assertIsNone(row.server_reaction_id)
        self.award.assert_not_called()
        self.assertEqual(self.run_worker(lookup)['checked'], 0)
        self.check_time = row.next_check_at
        self.assertEqual(self.run_worker()['confirmed'], 1)

    def test_untrusted_receipts_are_blocked_without_award(self):
        invalid = [dict(source='server'), self.receipt(source='media'), self.receipt(type='IMPRESSION'),
                   self.receipt(decision_id=uuid4()), self.receipt(subject_id=uuid4()),
                   self.receipt(reaction_id='bad'), self.receipt(status='unknown'),
                   self.receipt(occurred_at=self.now.replace(tzinfo=None)),
                   self.receipt(occurred_at=self.now - timedelta(days=1)),
                   self.receipt(received_at=self.now - timedelta(seconds=1))]
        for receipt in invalid:
            with self.subTest(receipt=receipt):
                AdClick.objects.all().delete()
                self.record()
                self.assertEqual(self.run_worker(Mock(return_value=receipt))['blocked'], 1)
                self.assertEqual(AdClick.objects.get().state, 'BLOCKED')
        self.award.assert_not_called()

    def test_transient_errors_retry_but_permission_errors_block(self):
        cases = [(AdsTransportError(), 'PENDING'), (AdsResponseError('invalid'), 'PENDING'),
                 (AdsHTTPError(404), 'PENDING'), (AdsHTTPError(429, retry_after=120), 'PENDING'),
                 (AdsHTTPError(503), 'PENDING'), (AdsHTTPError(401), 'BLOCKED')]
        for error, state in cases:
            with self.subTest(error=error):
                AdClick.objects.all().delete()
                row = self.record()
                self.run_worker(Mock(side_effect=error))
                row.refresh_from_db()
                self.assertEqual((row.state, row.attempts), (state, 1))
                if getattr(error, 'retry_after', None):
                    self.assertEqual(row.next_check_at, self.check_time + timedelta(seconds=120))
        self.award.assert_not_called()

    def test_award_failure_rolls_back_confirmation_and_retries_after_lease(self):
        row = self.record()
        self.award.side_effect = DatabaseError('failed')
        with self.assertRaises(DatabaseError):
            self.run_worker()
        row.refresh_from_db()
        self.assertEqual(row.state, 'PENDING')
        self.assertIsNone(row.server_reaction_id)
        self.check_time = row.lease_expires_at
        self.award.side_effect = None
        self.assertEqual(self.run_worker()['confirmed'], 1)

    def test_stale_or_expired_worker_cannot_award(self):
        self.record()
        old = claim_click(now=self.check_time)
        self.assertIsNone(claim_click(now=self.check_time))
        later = old.lease_expires_at
        self.assertFalse(confirm_click(old, self.receipt(), now=later, award=self.award))
        current = claim_click(now=later)
        self.assertFalse(confirm_click(old, self.receipt(), now=later, award=self.award))
        self.assertTrue(confirm_click(current, self.receipt(), now=later, award=self.award))
        self.assertFalse(confirm_click(current, self.receipt(), now=later, award=self.award))
        self.award.assert_called_once()

    @override_settings(ADS_CLICK_LOOKUP_BACKEND='')
    def test_missing_adapter_never_claims_or_awards(self):
        row = self.record()
        with self.assertRaises(CommandError):
            call_command('confirm_ad_clicks', '--lookup', stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual(row.attempts, 0)
        self.assertIsNone(row.lease_token)

    def test_receipt_and_award_rollback_together(self):
        row = self.record()
        def failing_award(user, **kwargs):
            user.last_name = 'must rollback'
            user.save(update_fields=['last_name'])
            raise DatabaseError('failed after write')
        with self.assertRaises(DatabaseError):
            check_pending_clicks(lookup=Mock(return_value=self.receipt()), now=lambda:self.check_time, award=failing_award)
        self.user.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(self.user.last_name, '')
        self.assertEqual(row.state, 'PENDING')
