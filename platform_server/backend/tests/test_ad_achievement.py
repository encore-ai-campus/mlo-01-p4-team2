"""광고 클릭의 저장·이동·재시도 경계. 외부 광고 서버는 호출하지 않는다."""
from dataclasses import replace
from datetime import timedelta
from email.message import Message
import json
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import URLError
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.http import HttpResponseRedirect
from django.test import Client, TestCase, TransactionTestCase

from accounts.models import ActiveLogin, Profile
from ad_bridge.api.client import AdsClient
from ad_bridge.api.config import AdsConfig
from ad_bridge.clicks import click_token
from ad_bridge.decisions.store import remember_decision
from ad_bridge.delivery.worker import dispatch_due_impressions
from ad_bridge.impressions import store
from ad_bridge.impressions.service import report_impression_once
from ad_bridge.models import Impression
from ad_bridge.tests.support import shared_decision
from campus.session_registry import session_group
from event_story.achievement_services import AD_ID, TIMER_ID, complete_achievement, record_ad_click
from event_story.models import EventProgress
from event_story.test_achievements import seed_definitions
from eventing.events import SESSION_KEY
from django.utils import timezone


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

    def test_click_saves_before_redirect_and_uses_only_the_signed_target(self):
        decision = self.decision()
        response = self.click(decision, click_url='https://forged.test/', owner_id=str(self.other.pk))
        self.assertEqual((response.status_code, response['Location']), (303, decision.ad.click_url))
        row = EventProgress.objects.get(user=self.user, event_id=AD_ID)
        self.assertEqual(row.status, 'COMPLETED')
        self.assertEqual(str(row.session_id), self.client.session[SESSION_KEY])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertFalse(Impression.objects.exists())

    def test_repeated_clicks_keep_the_first_completion(self):
        decision = self.decision()
        self.click(decision)
        first = EventProgress.objects.get(user=self.user, event_id=AD_ID)
        for next_decision in (decision, self.decision()):
            self.assertEqual(self.click(next_decision).status_code, 303)
        row = EventProgress.objects.get(user=self.user, event_id=AD_ID)
        self.assertEqual((row.completed_at, row.session_id), (first.completed_at, first.session_id))
        self.assertEqual(EventProgress.objects.filter(user=self.user).count(), 1)

    def test_shared_click_keeps_one_winner_but_both_users_can_navigate(self):
        decision = self.decision(shared=True)
        self.assertEqual(self.click(decision).status_code, 303)
        self.client.force_login(self.other)
        self.assertEqual(self.click(decision, user=self.other).status_code, 303)
        self.assertEqual(list(EventProgress.objects.values_list('user_id', flat=True)), [self.user.pk])

    def test_another_badge_prevents_a_second_award_without_blocking_navigation(self):
        complete_achievement(self.user, TIMER_ID, timezone.now())
        self.assertEqual(self.click(self.decision()).status_code, 303)
        self.assertEqual(list(EventProgress.objects.filter(user=self.user).values_list('event_id', flat=True)), [TIMER_ID])

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

    def test_storage_failure_rolls_back_and_never_redirects_then_retry_can_succeed(self):
        decision = self.decision()
        with patch('event_story.achievement_services.achievement_snapshot', side_effect=DatabaseError('failed')):
            response = self.click(decision)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('Location', response)
        self.assertIn('다시 눌러 주세요', response.content.decode())
        self.assertFalse(EventProgress.objects.exists())
        self.assertEqual(self.click(decision).status_code, 303)
        self.assertTrue(EventProgress.objects.filter(user=self.user, event_id=AD_ID).exists())

    def test_notice_is_sent_only_after_commit_and_once(self):
        ActiveLogin.objects.update_or_create(user=self.user, defaults={'session_key': 'ad-session'})
        layer = Mock(group_send=AsyncMock())
        with patch('channels.layers.get_channel_layer', return_value=layer), self.captureOnCommitCallbacks(execute=True):
            result = record_ad_click(self.user)
            record_ad_click(self.user)
            self.assertEqual(layer.group_send.await_count, 0)
        layer.group_send.assert_awaited_once_with(session_group('ad-session'), {'type': 'achievement_update', 'result': result})

    def test_notification_failure_keeps_the_reward_and_redirect(self):
        layer = Mock(group_send=AsyncMock(side_effect=RuntimeError('disconnected')))
        with patch('channels.layers.get_channel_layer', return_value=layer), self.assertLogs(level='ERROR'), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.click(self.decision())
        self.assertEqual(response.status_code, 303)
        self.assertEqual(layer.group_send.await_count, 1)
        self.assertTrue(EventProgress.objects.filter(user=self.user, event_id=AD_ID).exists())
        snapshot = self.client.get('/api/event-story/achievements/').json()['achievements']
        self.assertTrue(next(row for row in snapshot if row['event_uuid'] == str(AD_ID))['achieved'])

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


class AdClickCommitTests(TransactionTestCase):
    def test_redirect_is_built_only_after_the_reward_commits(self):
        seed_definitions()
        user = get_user_model().objects.create_user(username='ad-click-commit')
        Profile.objects.create(user=user, nickname='클릭 검증')
        self.client.force_login(user)
        decision = shared_decision()
        remember_decision(decision)
        token = click_token(decision.decision_id, 'https://ads.test/c/token', user.pk)

        def redirect(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertTrue(EventProgress.objects.filter(user=user, event_id=AD_ID, status='COMPLETED').exists())
            return HttpResponseRedirect(*args, **kwargs)

        with patch('ad_bridge.game.views.HttpResponseRedirect', side_effect=redirect) as response:
            result = self.client.post(f'/api/ads/decisions/{decision.decision_id}/click/', {'token': token})
        self.assertEqual(result.status_code, 303)
        response.assert_called_once()
