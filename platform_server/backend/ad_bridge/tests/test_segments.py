"""과정 코드(segment) 목록 해석·사용자 과정 선택·요청 몸을 확인한다(AB-1 §2)."""

from dataclasses import replace
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import RequestFactory, TestCase, override_settings

from ad_bridge import placements as core
from ad_bridge.api.client import AdsClient
from ad_bridge.api.config import AdsConfig
from ad_bridge.game.placements import placement
from ad_bridge.game.views import report_impression, reaction_result
from ad_bridge.models import IssuedDecision
from ad_bridge.segments import registered_codes, segment_for
from ad_bridge.tests.support import shared_decision

SLOTS = dict(ADS_ROOFTOP_SLOT_ID="10000000-0000-0000-0000-000000000001",
             ADS_PORTAL_LEFT_SLOT_ID="10000000-0000-0000-0000-000000000002",
             ADS_PORTAL_RIGHT_SLOT_ID="10000000-0000-0000-0000-000000000003")


def user(course):
    return SimpleNamespace(pk=7, is_authenticated=True,
                           campus_profile=SimpleNamespace(user_uuid=uuid4(), course=course))


class RegisteredCodeTests(TestCase):
    def setUp(self):
        registered_codes.cache_clear()

    def test_valid_codes_are_kept_and_blank_items_skipped(self):
        self.assertEqual(registered_codes(" aio, mle ,mlo,skn,, "), frozenset({"aio", "mle", "mlo", "skn"}))
        self.assertEqual(registered_codes(""), frozenset())

    def test_invalid_items_are_dropped_with_one_warning_per_raw_value(self):
        raw = "aio,MLO,ml o,운영팀,skn"
        with self.assertLogs("ad_bridge.segments", "WARNING") as logs:
            self.assertEqual(registered_codes(raw), frozenset({"aio", "skn"}))
            self.assertEqual(registered_codes(raw), frozenset({"aio", "skn"}))
        self.assertEqual(len(logs.output), 1)
        self.assertIn("3개", logs.output[0])
        for item in ("MLO", "ml o", "운영팀"):
            self.assertIn(item, logs.output[0])

    def test_segment_only_for_registered_course(self):
        with override_settings(ADS_SEGMENT_CODES="aio,mle,mlo,skn"):
            self.assertEqual(segment_for(user("mlo")), "mlo")
            for course in ("", "운영팀", "xyz", None):
                with self.subTest(course=course):
                    self.assertIsNone(segment_for(user(course)))
            self.assertIsNone(segment_for(SimpleNamespace(pk=1)))
        with override_settings(ADS_SEGMENT_CODES=""):
            self.assertIsNone(segment_for(user("mlo")))
        with override_settings(ADS_SEGMENT_CODES="MLO,skn"):
            # 형식이 틀린 항목(MLO)은 버려지고, 소문자 과정도 그것과 맞지 않는다.
            with self.assertLogs("ad_bridge.segments", "WARNING"):
                self.assertIsNone(segment_for(user("mlo")))
            self.assertEqual(segment_for(user("skn")), "skn")


class ClientSegmentBodyTests(TestCase):
    def setUp(self):
        self.client = AdsClient(AdsConfig("https://ads.test/api/media/v1", "media-key", 1, subject_secret="s"))
        self.sent = []

        def fake_open(request, timeout):
            self.sent.append(json.loads(request.data))
            raise OSError("stop after capture")
        self.client._opener = Mock(open=fake_open)

    def body(self, **kwargs):
        try:
            self.client.create_decision(slot_id=str(uuid4()), **kwargs)
        except Exception:
            pass
        return self.sent[-1]

    def test_segment_is_in_body_only_when_given(self):
        subject = str(uuid4())
        self.assertEqual(self.body(subject_id=subject, segment="mlo")["segment"], "mlo")
        self.assertNotIn("segment", self.body(subject_id=subject))
        self.assertNotIn("segment", self.body())

    def test_invalid_segment_is_refused_before_sending(self):
        for kwargs in ({"segment": "mlo"}, {"subject_id": str(uuid4()), "segment": "MLO"},
                       {"subject_id": str(uuid4()), "segment": ""}, {"subject_id": str(uuid4()), "segment": 3}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.client.create_decision(slot_id=str(uuid4()), **kwargs)
        self.assertEqual(self.sent, [])


@override_settings(ADS_ENABLED=True, **SLOTS)
class PlacementViewSegmentTests(TestCase):
    def setUp(self):
        registered_codes.cache_clear()
        self.config = AdsConfig("https://ads.test/api/media/v1", "media-key", 1, subject_secret="secret")
        self.ads = Mock()
        self.ads.create_decision.side_effect = self._decision
        patcher = patch("ad_bridge.game.placements.AdsClient", return_value=self.ads)
        patcher.start()
        self.addCleanup(patcher.stop)
        config = patch("ad_bridge.game.placements.AdsConfig.from_env", return_value=self.config)
        config.start()
        self.addCleanup(config.stop)

    def _decision(self, **kw):
        return replace(shared_decision(), slot_id=kw["slot_id"], audience="individual")

    def request(self, course):
        request = RequestFactory().post("/api/ads/placements/rooftop/", data="{}", content_type="application/json")
        request.user = user(course)
        return request

    def test_view_sends_registered_course_and_omits_others(self):
        with override_settings(ADS_SEGMENT_CODES="aio,mle,mlo,skn"):
            self.assertEqual(placement(self.request("skn"), "rooftop").status_code, 200)
            self.assertEqual(self.ads.create_decision.call_args.kwargs["segment"], "skn")
            self.assertEqual(placement(self.request("운영팀"), "rooftop").status_code, 200)
            self.assertNotIn("segment", self.ads.create_decision.call_args.kwargs)

    def test_broken_code_list_still_issues_and_other_paths_are_unaffected(self):
        with override_settings(ADS_SEGMENT_CODES="aio,MLO,,skn"), self.assertLogs("ad_bridge.segments", "WARNING"):
            response = placement(self.request("aio"), "rooftop")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ads.create_decision.call_args.kwargs["segment"], "aio")
        decision_id = json.loads(response.content)["ads"][0]["decision_id"]
        # 노출 보고·판정 조회는 목록을 읽지 않는다(같은 설정에서 503이 아님).
        with override_settings(ADS_SEGMENT_CODES="aio,MLO,,skn"):
            issued = IssuedDecision.objects.get(pk=decision_id)
            viewer = SimpleNamespace(pk=7, is_authenticated=True,
                                     campus_profile=SimpleNamespace(user_uuid=issued.owner_id, course="aio"))
            impression = RequestFactory().post("/x/", data="{}", content_type="application/json")
            impression.user = viewer
            self.assertEqual(report_impression(impression, decision_id).status_code, 202)
            result = RequestFactory().post("/x/", data="{}", content_type="application/json")
            result.user = viewer
            self.assertEqual(reaction_result(result, decision_id).status_code, 202)

    def test_config_error_503_is_logged_once_per_minute_without_values(self):
        core._last_logged.clear()
        broken = patch("ad_bridge.game.placements.AdsConfig.from_env",
                       side_effect=ValueError("ADS_MEDIA_KEY가 필요합니다."))
        with broken, patch.dict(os.environ, {"ADS_MEDIA_KEY": "secret-value"}), \
                self.assertLogs("ad_bridge.placements", "WARNING") as logs:
            for _ in range(3):
                self.assertEqual(placement(self.request("mlo"), "rooftop").status_code, 503)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("ValueError ADS_MEDIA_KEY가 필요합니다.", logs.output[0])
        self.assertNotIn("secret-value", logs.output[0])
