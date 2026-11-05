"""Prepared for later execution; upstream HTTP replies are simulated."""
from dataclasses import replace
from datetime import timedelta
from email.message import Message
import json
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import URLError
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import ActiveLogin
from ad_bridge.api.client import AdsClient
from ad_bridge.api.config import AdsConfig
from ad_bridge.decisions.store import remember_decision
from ad_bridge.delivery.worker import dispatch_due_impressions
from ad_bridge.impressions import store
from ad_bridge.impressions.service import report_impression_once
from ad_bridge.models import Impression
from ad_bridge.tests.support import shared_decision
from campus.session_registry import session_group
from event_story.achievement_services import AD_ID
from event_story.models import EventProgress
from event_story.test_achievements import seed_definitions


class Response:
    def __init__(self, request, status):
        self.status = status
        self.headers = Message();self.headers['Content-Type'] = 'application/json'
        sent = json.loads(request.data)
        self.body = json.dumps({**sent, 'decision_id': request.full_url.split('/')[-3],
            'reaction_id': request.full_url.split('/')[-1], 'received_at': sent['occurred_at'],
            'status': 'RECEIVED', 'reason_code': None}).encode()

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class AdAchievementTests(TestCase):
    def setUp(self):
        seed_definitions()
        self.user = get_user_model().objects.create_user(username='ad-achievement')
        self.other = get_user_model().objects.create_user(username='ad-achievement-other')
        self.client_api = AdsClient(AdsConfig('https://ads.test/api/media/v1', 'test-key', 1, subject_secret='test-secret'))
        self.client_api._opener = Mock()

    def decision(self, user, ad=True):
        decision = replace(shared_decision(ad=ad), audience='individual')
        remember_decision(decision, subject_id=uuid4(), owner_id=user.pk)
        return decision

    def report(self, user, decision, status=201):
        self.client_api._opener.open.side_effect = lambda request, **kwargs: Response(request, status)
        return report_impression_once(client=self.client_api, store=store, decision_id=decision.decision_id,
                                      owner_id=user.pk, occurred_at='2026-11-04T01:00:00Z')

    def test_valid_200_can_award_the_receipt_owner_when_unclaimed(self):
        result = self.report(self.user, self.decision(self.user), 200)
        self.assertEqual(result.record.achievement_update['newly_completed'], [str(AD_ID)])
        self.assertTrue(EventProgress.objects.filter(user=self.user, event_id=AD_ID, status='COMPLETED').exists())

    def test_valid_201_can_award_the_receipt_owner_when_unclaimed(self):
        result = self.report(self.user, self.decision(self.user), 201)
        self.assertEqual(result.record.achievement_update['newly_completed'], [str(AD_ID)])
        self.assertTrue(EventProgress.objects.filter(user=self.user, event_id=AD_ID, status='COMPLETED').exists())

    def test_second_acknowledgement_is_stored_without_a_second_winner(self):
        first = self.report(self.user, self.decision(self.user), 200)
        second = self.report(self.other, self.decision(self.other), 201)
        self.assertEqual(first.record.achievement_update['newly_completed'], [str(AD_ID)])
        self.assertEqual(second.record.achievement_update['newly_completed'], [])
        self.assertEqual(Impression.objects.filter(delivery_state='ACKNOWLEDGED').count(), 2)
        self.assertFalse(EventProgress.objects.filter(user=self.other).exists())

    def test_duplicates_and_other_decisions_never_award_twice(self):
        decision = self.decision(self.user)
        self.report(self.user, decision)
        first = EventProgress.objects.get(user=self.user, event_id=AD_ID).completed_at
        self.report(self.user, decision)
        other = self.report(self.user, self.decision(self.user), 200)
        self.assertEqual(other.record.achievement_update['newly_completed'], [])
        self.assertEqual(EventProgress.objects.filter(user=self.user, event_id=AD_ID).count(), 1)
        self.assertEqual(EventProgress.objects.get(user=self.user, event_id=AD_ID).completed_at, first)
        self.assertFalse(EventProgress.objects.filter(user=self.other, event_id=AD_ID).exists())

    def test_202_and_unconfirmed_transport_failures_do_not_award(self):
        self.report(self.user, self.decision(self.user), 202)
        decision = self.decision(self.user)
        self.client_api._opener.open.side_effect = URLError('unreachable')
        report_impression_once(client=self.client_api, store=store, decision_id=decision.decision_id,
                               owner_id=self.user.pk, occurred_at='2026-11-04T01:00:00Z')
        self.assertFalse(EventProgress.objects.filter(event_id=AD_ID).exists())

    def test_malformed_200_response_does_not_award(self):
        decision = self.decision(self.user)
        def malformed_response(request, **kwargs):
            response = Response(request, 200)
            response.body = b'{}'
            return response
        self.client_api._opener.open.side_effect = malformed_response
        result = report_impression_once(client=self.client_api, store=store, decision_id=decision.decision_id,
                                       owner_id=self.user.pk, occurred_at='2026-11-04T01:00:00Z')
        self.assertEqual(result.error, 'ads_invalid_response')
        self.assertIsNone(result.record.achievement_update)
        self.assertFalse(EventProgress.objects.filter(event_id=AD_ID).exists())

    def test_one_shared_ad_records_each_viewer_but_awards_only_the_first(self):
        decision = shared_decision()
        remember_decision(decision)
        self.client_api._opener.open.side_effect = lambda request, **kwargs: Response(request, 200)
        for index, user in enumerate((self.user, self.other)):
            result = report_impression_once(client=self.client_api, store=store, decision_id=decision.decision_id,
                                           owner_id=user.pk, subject_id=uuid4(), occurred_at='2026-11-04T01:00:00Z')
            self.assertEqual(result.record.achievement_update['newly_completed'], [str(AD_ID)] if index == 0 else [])
        self.assertEqual(EventProgress.objects.filter(event_id=AD_ID).count(), 1)
        self.assertEqual(Impression.objects.filter(delivery_state='ACKNOWLEDGED').count(), 2)

    def test_empty_decisions_and_another_accounts_decisions_cannot_award(self):
        with self.assertRaises(store.NoAdForDecision):
            self.report(self.user, self.decision(self.user, ad=False))
        with self.assertRaises(store.DecisionNotFound):
            self.report(self.other, self.decision(self.user))
        self.assertFalse(EventProgress.objects.filter(event_id=AD_ID).exists())

    def test_worker_retry_grants_the_same_single_achievement(self):
        decision = self.decision(self.user)
        self.client_api._opener.open.side_effect = URLError('unreachable')
        result = report_impression_once(client=self.client_api, store=store, decision_id=decision.decision_id,
                                       owner_id=self.user.pk, occurred_at='2026-11-04T01:00:00Z')
        self.client_api._opener.open.side_effect = lambda request, **kwargs: Response(request, 200)
        due = result.record.next_attempt_at + timedelta(seconds=1)
        dispatch_due_impressions(client=self.client_api, store=store, now=lambda: due)
        self.assertEqual(EventProgress.objects.filter(user=self.user, event_id=AD_ID, status='COMPLETED').count(), 1)

    def test_receipt_and_reward_roll_back_together(self):
        with patch('event_story.achievement_services.achievement_snapshot', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                self.report(self.user, self.decision(self.user))
        self.assertFalse(EventProgress.objects.filter(event_id=AD_ID).exists())
        self.assertFalse(Impression.objects.filter(delivery_state='ACKNOWLEDGED').exists())

    def test_personal_notice_is_sent_after_commit_once(self):
        ActiveLogin.objects.create(user=self.user, session_key='ad-session')
        layer = Mock(group_send=AsyncMock())
        with patch('channels.layers.get_channel_layer', return_value=layer), self.captureOnCommitCallbacks(execute=True):
            result = self.report(self.user, self.decision(self.user))
        personal = [call for call in layer.group_send.await_args_list if call.args[0] == session_group('ad-session')]
        self.assertEqual(len(personal), 1)
        self.assertEqual(personal[0].args[1], {'type': 'achievement_update', 'result': result.record.achievement_update})
        self.assertEqual(sum(call.args[0] == 'event.achievement.discoveries' for call in layer.group_send.await_args_list), 1)
