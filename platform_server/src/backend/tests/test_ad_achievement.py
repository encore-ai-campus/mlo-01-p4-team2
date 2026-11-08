"""광고 클릭의 저장·이동·재시도 경계. 외부 광고 서버는 호출하지 않는다."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from dataclasses import replace
from datetime import timedelta
from email.message import Message
import json
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import URLError
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, connections
from django.http import HttpResponseRedirect
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature

from accounts.models import ActiveLogin, Profile
from ad_bridge.api.client import AdsClient
from ad_bridge.api.config import AdsConfig
from ad_bridge.clicks import click_token
from ad_bridge.decisions.store import remember_decision
from ad_bridge.delivery.worker import dispatch_due_impressions
from ad_bridge.impressions import store
from ad_bridge.impressions.service import report_impression_once
from ad_bridge.models import AdClick, Impression
from ad_bridge.click_records import ServerClick, record_click
from ad_bridge.click_worker import check_pending_clicks
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
        guard = patch('ad_bridge.game.views.configured_report', side_effect=ValueError('test-unconfigured'))
        self.configured_report = guard.start()
        self.addCleanup(guard.stop)
        quiet = patch('ad_bridge.game.views.logger')
        quiet.start(); self.addCleanup(quiet.stop)

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
        if token is None:
            target = decision.ad.click_url.replace('{subject_id}', str(owner))
            token = click_token(decision.decision_id, target, owner)
        return (client or self.client).post(f'/api/ads/decisions/{decision.decision_id}/click/', {'token': token, **fields})

    def confirm(self):
        def report(*, decision_id, subject_id, reaction_id, occurred_at, **kwargs):
            return ServerClick(decision_id=decision_id, subject_id=subject_id, reaction_id=reaction_id,
                               occurred_at=occurred_at, received_at=timezone.now(), source='media', type='CLICK', status='RECEIVED')
        return check_pending_clicks(report=report, now=lambda:timezone.now() + timedelta(seconds=3))

    def report(self, decision, status=201):
        self.api._opener.open.side_effect = lambda request, **kwargs: Response(request, status)
        return report_impression_once(client=self.api, store=store, decision_id=decision.decision_id,
            owner_id=self.user.campus_profile.user_uuid, occurred_at='2026-11-04T01:00:00Z')

    def test_click_saves_before_redirect_and_uses_only_the_signed_target(self):
        decision = self.decision()
        response = self.click(decision, click_url='https://forged.test/', owner_id=str(self.other.pk))
        self.assertEqual((response.status_code, response['Location']), (303, decision.ad.click_url))
        self.assertFalse(EventProgress.objects.exists())
        self.assertEqual(AdClick.objects.get().state, 'PENDING')
        self.confirm()
        row = EventProgress.objects.get(user=self.user, event_id=AD_ID)
        self.assertEqual(row.status, 'COMPLETED')
        self.assertEqual(str(row.session_id), self.client.session[SESSION_KEY])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertFalse(Impression.objects.exists())

    def test_put_success_awards_before_redirect_and_repeated_click_does_not_send(self):
        from ad_bridge.click_reporting import configured_report
        with patch('ad_bridge.click_reporting.AdsConfig.from_env', return_value=self.api._config), \
                patch('ad_bridge.click_reporting.RoutedAdsClient') as routed:
            routed.return_value.report_click.side_effect = lambda **kw: self.api.report_click(
                **{name: value for name, value in kw.items() if name != 'slot_id'})
            report = configured_report()
        self.configured_report.side_effect = None
        self.configured_report.return_value = report
        self.api._opener.open.side_effect = lambda request, **kwargs: Response(request, 201)
        decision = self.decision()
        self.assertEqual(self.click(decision).status_code, 303)
        self.assertTrue(EventProgress.objects.filter(user=self.user, event_id=AD_ID).exists())
        row = AdClick.objects.get()
        self.assertEqual((row.state, row.confirmation_source, row.server_reaction_id), ('CONFIRMED', 'media', row.pk))
        self.assertEqual(self.click(decision).status_code, 303)
        self.api._opener.open.assert_called_once()

    def test_put_timeout_redirects_without_award_then_same_id_retry_confirms(self):
        from ad_bridge.click_reporting import configured_report
        with patch('ad_bridge.click_reporting.AdsConfig.from_env', return_value=self.api._config), \
                patch('ad_bridge.click_reporting.RoutedAdsClient') as routed:
            routed.return_value.report_click.side_effect = lambda **kw: self.api.report_click(
                **{name: value for name, value in kw.items() if name != 'slot_id'})
            report = configured_report()
        self.configured_report.side_effect = None
        self.configured_report.return_value = report
        self.api._opener.open.side_effect = URLError('lost response')
        self.assertEqual(self.click(self.decision()).status_code, 303)
        row = AdClick.objects.get()
        self.assertEqual(row.state, 'PENDING')
        self.assertFalse(EventProgress.objects.exists())
        original = self.api._opener.open.call_args.args[0]
        self.api._opener.open.side_effect = lambda request, **kwargs: Response(request, 200)
        check_pending_clicks(report=report, now=lambda:row.next_check_at + timedelta(seconds=1))
        repeated = self.api._opener.open.call_args.args[0]
        self.assertEqual((original.full_url, original.data), (repeated.full_url, repeated.data))
        self.assertTrue(EventProgress.objects.filter(user=self.user, event_id=AD_ID).exists())

    def test_repeated_clicks_keep_the_first_completion(self):
        decision = self.decision()
        self.click(decision)
        self.confirm()
        first = EventProgress.objects.get(user=self.user, event_id=AD_ID)
        for next_decision in (decision, self.decision()):
            self.assertEqual(self.click(next_decision).status_code, 303)
        self.confirm()
        self.assertEqual(AdClick.objects.count(), 2)
        row = EventProgress.objects.get(user=self.user, event_id=AD_ID)
        self.assertEqual((row.completed_at, row.session_id), (first.completed_at, first.session_id))
        self.assertEqual(EventProgress.objects.filter(user=self.user).count(), 1)

    def test_shared_click_keeps_one_winner_but_both_users_can_navigate(self):
        decision = self.decision(shared=True)
        self.assertEqual(self.click(decision).status_code, 303)
        self.client.force_login(self.other)
        self.assertEqual(self.click(decision, user=self.other).status_code, 303)
        self.assertFalse(EventProgress.objects.exists())
        self.confirm()
        self.assertEqual(AdClick.objects.filter(state='CONFIRMED').count(), 2)
        self.assertEqual(list(EventProgress.objects.values_list('user_id', flat=True)), [self.user.pk])

    def test_another_badge_prevents_a_second_award_without_blocking_navigation(self):
        complete_achievement(self.user, TIMER_ID, timezone.now())
        self.assertEqual(self.click(self.decision()).status_code, 303)
        self.confirm()
        self.assertEqual(AdClick.objects.get().state, 'CONFIRMED')
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
        with patch('ad_bridge.game.views.record_click', side_effect=DatabaseError('failed')):
            response = self.click(decision)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('Location', response)
        self.assertIn('다시 눌러 주세요', response.content.decode())
        self.assertFalse(EventProgress.objects.exists())
        self.assertEqual(self.click(decision).status_code, 303)
        self.assertFalse(EventProgress.objects.exists())
        self.confirm()
        self.assertTrue(EventProgress.objects.filter(user=self.user, event_id=AD_ID).exists())

    def test_confirmation_failure_rolls_back_progress_then_retry_awards(self):
        self.click(self.decision())
        with patch('event_story.achievement_services.achievement_snapshot', side_effect=DatabaseError('failed')):
            with self.assertRaises(DatabaseError):
                self.confirm()
        self.assertFalse(EventProgress.objects.exists())
        row = AdClick.objects.get()
        self.assertEqual(row.state, 'PENDING')
        self.assertIsNone(row.server_reaction_id)
        AdClick.objects.filter(pk=row.pk).update(lease_expires_at=timezone.now())
        self.confirm()
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
            self.confirm()
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
    def test_redirect_is_built_only_after_pending_click_commits(self):
        seed_definitions()
        user = get_user_model().objects.create_user(username='ad-click-commit')
        Profile.objects.create(user=user, nickname='클릭 검증')
        self.client.force_login(user)
        decision = shared_decision()
        remember_decision(decision)
        token = click_token(decision.decision_id, f'https://ads.test/c/token?s={uuid4()}', user.campus_profile.user_uuid)

        def redirect(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertFalse(EventProgress.objects.filter(user=user, event_id=AD_ID).exists())
            self.assertTrue(AdClick.objects.filter(user=user, state='PENDING').exists())
            return HttpResponseRedirect(*args, **kwargs)

        with patch('ad_bridge.game.views.HttpResponseRedirect', side_effect=redirect) as response, \
                patch('ad_bridge.game.views.configured_report', side_effect=ValueError('test-unconfigured')), \
                patch('ad_bridge.game.views.logger'):
            result = self.client.post(f'/api/ads/decisions/{decision.decision_id}/click/', {'token': token})
        self.assertEqual(result.status_code, 303)
        response.assert_called_once()


@skipUnlessDBFeature('has_select_for_update')
class AdClickConcurrencyTests(TransactionTestCase):
    def setUp(self):
        seed_definitions()
        self.user = get_user_model().objects.create_user(username='ad-click-parallel')
        Profile.objects.create(user=self.user, nickname='동시 클릭')
        self.owner = self.user.campus_profile.user_uuid
        self.subject = uuid4()
        decision = replace(shared_decision(), audience='individual')
        remember_decision(decision, subject_id=self.subject, owner_id=self.owner)
        self.decision_id = decision.decision_id

    def parallel(self, operation):
        barrier = Barrier(2)
        def run():
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            tasks = [executor.submit(run) for _ in range(2)]
            return [task.result(timeout=20) for task in tasks]

    def test_simultaneous_receipts_reuse_one_record(self):
        def record():
            return record_click(user=self.user, owner_id=self.owner, decision_id=self.decision_id)[0].pk
        ids = self.parallel(record)
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(AdClick.objects.count(), 1)
        self.assertFalse(EventProgress.objects.exists())

    def test_simultaneous_workers_confirm_and_award_once(self):
        row, _ = record_click(user=self.user, owner_id=self.owner, decision_id=self.decision_id)
        AdClick.objects.filter(pk=row.pk).update(next_check_at=timezone.now())
        stamp = timezone.now()
        receipt = ServerClick(decision_id=self.decision_id, subject_id=self.subject, reaction_id=row.pk,
                              occurred_at=row.clicked_at, received_at=stamp, source='media', type='CLICK', status='RECEIVED')
        lookup = Mock(return_value=receipt)
        counts = self.parallel(lambda: check_pending_clicks(report=lookup, limit=1))
        self.assertEqual(sum(result['confirmed'] for result in counts), 1)
        lookup.assert_called_once()
        row.refresh_from_db()
        self.assertEqual(row.state, 'CONFIRMED')
        self.assertEqual(EventProgress.objects.filter(user=self.user, event_id=AD_ID, status='COMPLETED').count(), 1)
