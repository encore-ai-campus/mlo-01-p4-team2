"""광고 API의 인증·CSRF·이동 승인 및 사용자별 옥상 발급을 검사한다."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory, TestCase, override_settings

from ad_bridge.api.config import AdsConfig
from ad_bridge.game.placements import placement
from ad_bridge.placements import transfer_token
from ad_bridge.game.views import _subject_id
from ad_bridge.api.types import Ad, Decision
from ad_bridge.models import IssuedDecision, PlacementBatch
from ad_bridge.impressions.store import reserve_impression, DecisionNotFound
from ad_bridge.clicks import click_target
from django.core import signing
from django.utils import timezone


@override_settings(ADS_ENABLED=True)
class PlacementViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = SimpleNamespace(pk=7, is_authenticated=True,
            campus_profile=SimpleNamespace(user_uuid=uuid4()))

    def request(self, body, location="portal"):
        request = self.factory.post(f'/api/ads/placements/{location}/', data=json.dumps(body), content_type='application/json')
        request.user = self.user
        return request

    def test_invalid_or_other_user_transfer_never_calls_ad_server(self):
        for body in ([], {}, {"token": {}}, {"token": "forged"}, {"token": transfer_token(8, uuid4())}):
            with self.subTest(body=body), patch('ad_bridge.game.placements.AdsClient') as client:
                self.assertEqual(placement(self.request(body), 'portal').status_code, 400)
                client.assert_not_called()

    def test_login_and_csrf_are_required(self):
        request = self.request({})
        request.user = AnonymousUser()
        self.assertEqual(placement(request, 'portal').status_code, 302)
        request = self.request({})
        self.assertEqual(CsrfViewMiddleware(lambda r: None).process_view(request, placement, (), {"location":"portal"}).status_code, 403)

    @override_settings(ADS_ENABLED=False)
    def test_unconfigured_environment_does_not_request_ads(self):
        with patch('ad_bridge.game.placements.AdsClient') as client:
            self.assertEqual(placement(self.request({}), 'rooftop').status_code, 503)
            client.assert_not_called()

    def test_approved_transfer_is_passed_with_server_owner_only(self):
        transfer = uuid4()
        config = AdsConfig('https://ads.test/api/media/v1','test-key',1,subject_secret='secret')
        with patch('ad_bridge.game.placements.AdsConfig.from_env', return_value=config), \
             patch('ad_bridge.game.placements.get_batch') as batch, \
             patch('ad_bridge.game.placements.public_batch', return_value={"ads": []}):
            batch.return_value = Mock(state='READY')
            response = placement(self.request({"token": transfer_token(7, transfer), "owner_id": str(uuid4())}), 'portal')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(batch.call_args.kwargs['owner_id'], self.user.campus_profile.user_uuid)
            self.assertEqual(batch.call_args.kwargs['transfer_id'], str(transfer))

    @override_settings(ADS_ROOFTOP_SLOT_ID="10000000-0000-0000-0000-000000000001")
    def test_rooftop_uses_logged_in_identity_and_isolates_users(self):
        config = AdsConfig('https://ads.test/api/media/v1', 'test-key', 1, subject_secret='secret')
        client = Mock()
        def decision(*, slot_id, subject_id):
            return Decision(str(uuid4()), slot_id, "roof", "individual", "2026-10-15T01:00:00Z",
                Ad(str(uuid4()), "광고", "https://ads.test/image.png", "https://example.com",
                   f"https://ads.test/c/click?s={subject_id}"), None)
        client.create_decision.side_effect = decision
        fixed_now = timezone.now()
        with patch('ad_bridge.game.placements.AdsConfig.from_env', return_value=config), \
             patch('ad_bridge.game.placements.AdsClient', return_value=client), \
             patch('ad_bridge.placements.timezone.now', return_value=fixed_now):
            forged = {"owner_id": str(uuid4()), "subject_id": str(uuid4())}
            first = placement(self.request(forged, "rooftop"), 'rooftop')
            self.assertEqual(first.status_code, 200)
            first_data = json.loads(first.content)
            first_entry = first_data["ads"][0]
            first_owner = self.user.campus_profile.user_uuid
            first_subject = _subject_id(first_owner, subject_secret=config.subject_secret)
            client.create_decision.assert_called_once_with(
                slot_id="10000000-0000-0000-0000-000000000001", subject_id=first_subject,
            )
            self.assertNotIn(str(first_owner), first.content.decode())
            second = placement(self.request({}, "rooftop"), 'rooftop')
            self.assertEqual(json.loads(second.content)["ads"], first_data["ads"])
            self.assertEqual(client.create_decision.call_count, 1)
            self.user = SimpleNamespace(pk=8, is_authenticated=True,
                campus_profile=SimpleNamespace(user_uuid=uuid4()))
            other_owner = self.user.campus_profile.user_uuid
            other = placement(self.request({}, "rooftop"), 'rooftop')
            self.assertEqual(other.status_code, 200)
            other_data = json.loads(other.content)
            self.assertNotEqual(first_data["batch_id"], other_data["batch_id"])
            self.assertNotEqual(first_entry["decision_id"], other_data["ads"][0]["decision_id"])
            self.assertEqual(client.create_decision.call_count, 2)
            self.assertEqual(PlacementBatch.objects.count(), 2)
            saved = IssuedDecision.objects.get(pk=first_entry["decision_id"])
            self.assertEqual((saved.owner_id, saved.subject_id, saved.audience), (first_owner, first_subject, "individual"))
            with self.assertRaises(signing.BadSignature):
                click_target(first_entry["ad"]["click_token"], first_entry["decision_id"], other_owner)
            with self.assertRaises(DecisionNotFound):
                reserve_impression(first_entry["decision_id"], other_owner, "2026-10-15T01:00:01Z")

    def test_rooftop_missing_profile_does_not_issue(self):
        self.user.campus_profile.user_uuid = None
        with patch('ad_bridge.game.placements.AdsClient') as client:
            self.assertEqual(placement(self.request({}, "rooftop"), 'rooftop').status_code, 409)
            client.assert_not_called()
