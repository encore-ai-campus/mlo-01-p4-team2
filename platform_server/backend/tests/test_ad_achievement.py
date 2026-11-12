"""광고 클릭의 저장·이동·재시도 경계. 외부 광고 서버는 호출하지 않는다."""
from dataclasses import replace
from datetime import timedelta
from email.message import Message
import json
from unittest.mock import Mock, patch
from urllib.error import URLError
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import Client, TestCase

from accounts.models import Profile
from ad_bridge.api.client import AdsClient
from ad_bridge.api.config import AdsConfig
from ad_bridge.clicks import click_token
from ad_bridge.decisions.store import remember_decision
from ad_bridge.delivery.worker import dispatch_due_impressions
from ad_bridge.impressions import store
from ad_bridge.impressions.service import report_impression_once
from ad_bridge.models import Impression
from ad_bridge.tests.support import shared_decision
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

    def read(self, limit):return self.body[:limit]
    def __enter__(self):return self
    def __exit__(self, *args):pass


class AdAchievementTests(TestCase):
    def setUp(self):
        seed_definitions()
        self.user = get_user_model().objects.create_user(username='ad-click')
        self.other = get_user_model().objects.create_user(username='ad-click-other')
        for user in (self.user, self.other):
            Profile.objects.create(user=user, nickname=user.username)
        self.client.force_login(self.user)
        self.api = AdsClient(AdsConfig('https://ads.test/api/media/v1', 'test-key', 1, subject_secret='test-secret'))
        self.api._opener = Mock()


    def decision(self, user=None, *, ad=True, shared=False):
        decision = shared_decision(ad=ad)
        if shared:
            remember_decision(decision)
        else:
            user = user or self.user
            ad_value = replace(decision.ad, click_url=decision.ad.click_url.replace('{subject_id}', str(uuid4()))) if decision.ad else None
            decision = replace(decision, audience='individual', ad=ad_value)
            remember_decision(decision, subject_id=uuid4(), owner_id=user.campus_profile.user_uuid)
        return decision


    def click(self, decision, *, user=None, token=None, client=None, **fields):
        owner = (user or self.user).campus_profile.user_uuid
        token = click_token(decision.decision_id, decision.ad.click_url, owner) if token is None else token
        return (client or self.client).post(f'/api/ads/decisions/{decision.decision_id}/click/', {'token': token, **fields})


    def report(self, decision, status=201):
        self.api._opener.open.side_effect = lambda request, **kwargs: Response(request, status)
        return report_impression_once(client=self.api, store=store, decision_id=decision.decision_id,
            owner_id=self.user.campus_profile.user_uuid, occurred_at='2026-11-04T01:00:00Z')


    def test_missing_forged_other_account_and_other_decision_tokens_are_rejected(self):
        decision = self.decision()
        tokens = ['', 'forged', click_token(decision.decision_id, decision.ad.click_url, self.other.campus_profile.user_uuid),
                  click_token(uuid4(), decision.ad.click_url, self.user.campus_profile.user_uuid)]
        for token in tokens:
            with self.subTest(token=token):
                response = self.click(decision, token=token)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('Location', response)
        self.assertFalse(EventProgress.objects.exists())


    def test_non_http_targets_are_rejected_even_with_a_valid_signature(self):
        decision = self.decision()
        for url in ('javascript:alert(1)', '//evil.test/', 'https:///missing-host', 'https://['):
            token = click_token(decision.decision_id, url, self.user.campus_profile.user_uuid)
            self.assertEqual(self.click(decision, token=token).status_code, 400)
        self.assertFalse(EventProgress.objects.exists())


    def test_unknown_empty_and_other_accounts_decisions_are_rejected(self):
        unknown = shared_decision()
        empty = self.decision(ad=False)
        foreign = self.decision(self.other)
        for decision in (unknown, empty, foreign):
            token = click_token(decision.decision_id, 'https://ads.test/c/token', self.user.campus_profile.user_uuid)
            self.assertEqual(self.click(decision, token=token).status_code, 404)
        self.assertFalse(EventProgress.objects.exists())


    def test_get_login_and_csrf_guards_prevent_unintended_awards(self):
        decision = self.decision()
        url = f'/api/ads/decisions/{decision.decision_id}/click/'
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client().post(url).status_code, 302)
        guarded = Client(enforce_csrf_checks=True);guarded.cookies.update(self.client.cookies)
        self.assertEqual(self.click(decision, client=guarded).status_code, 403)
        self.assertFalse(EventProgress.objects.exists())
        guarded.get('/api/learning/csrf/')
        csrf = guarded.cookies['campus_csrftoken'].value
        self.assertEqual(self.click(decision, client=guarded, csrfmiddlewaretoken=csrf).status_code, 303)


    def test_confirmed_impressions_do_not_award(self):
        for status in (200, 201):
            self.assertEqual(self.report(self.decision(), status).record.delivery_state, 'ACKNOWLEDGED')
        self.assertEqual(Impression.objects.count(), 2)
        self.assertFalse(EventProgress.objects.exists())


    def test_impression_worker_retries_do_not_award(self):
        decision = self.decision()
        self.api._opener.open.side_effect = URLError('unreachable')
        result = report_impression_once(client=self.api, store=store, decision_id=decision.decision_id,
            owner_id=self.user.campus_profile.user_uuid, occurred_at='2026-11-04T01:00:00Z')
        self.api._opener.open.side_effect = lambda request, **kwargs: Response(request, 200)
        dispatch_due_impressions(client=self.api, store=store, now=lambda: result.record.next_attempt_at+timedelta(seconds=1))
        self.assertEqual(Impression.objects.get().delivery_state, 'ACKNOWLEDGED')
        self.assertFalse(EventProgress.objects.exists())


    def test_click_redirects_without_awarding_or_notifying(self):
        decision = self.decision()
        with patch('channels.layers.get_channel_layer') as channel:
            for _ in range(2):
                response = self.click(decision)
                self.assertEqual((response.status_code, response['Location']), (303, decision.ad.click_url))
                self.assertEqual(response['Referrer-Policy'], 'no-referrer')
            channel.assert_not_called()
        self.assertFalse(EventProgress.objects.exists())

    def test_shared_clicks_only_navigate_for_both_accounts(self):
        decision = self.decision(shared=True)
        self.assertEqual(self.click(decision).status_code, 303)
        self.client.force_login(self.other)
        self.assertEqual(self.click(decision, user=self.other).status_code, 303)
        self.assertFalse(EventProgress.objects.exists())

    def test_database_error_does_not_redirect(self):
        decision = self.decision()
        with patch('ad_bridge.models.IssuedDecision.objects.filter', side_effect=DatabaseError('unavailable')):
            response = self.click(decision)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('Location', response)
        self.assertNotIn('업적', response.content.decode())
