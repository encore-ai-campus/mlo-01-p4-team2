"""C1 인증·요청 제한(2-2). 실제 데코레이터를 붙인 임시 뷰(tests/support/urls_media_api.py)에 계약 경로로 요청하고, 응답을 계약에 대조한다."""
import json
import re
import uuid
from datetime import datetime, timedelta
from unittest import mock

from django.db import OperationalError
from django.test import Client, TestCase, override_settings, tag
from django.utils import timezone

from assets.api import asset_url
from campaigns.models import AdGroup
from events.api import event_id_for
from events.models import EventOutbox
from foundation.models import AuditLog
from masters import api as masters
from serving_api.auth import CHALLENGE
from masters.models import MediaRateBucket
from tests.support.contract_support import Contract, ProviderContractMixin, cookie_problems
from tests.support.factories import NOW, World, make_admin
from tests.support.cases import Cases

BASE = "/api/media/v1"
DID, RID = uuid.uuid4(), uuid.uuid4()
# (계약 경로, 메서드, 실제 URL, 요청 제한 경로군)
OPERATIONS = [
    ("/decisions", "post", f"{BASE}/decisions", "decisions"),
    ("/decisions/{decision_id}", "get", f"{BASE}/decisions/{DID}", "reads"),
    ("/decisions/{decision_id}/reactions/{reaction_id}", "put", f"{BASE}/decisions/{DID}/reactions/{RID}", "reactions"),
    ("/decisions/{decision_id}/reactions/{reaction_id}", "get", f"{BASE}/decisions/{DID}/reactions/{RID}", "reads"),
]

IBASE = "/api/internal/v1"  # C8 내부 입력 API(ACW-3)
INTERNAL = Contract("internal")
C8_OPERATIONS = [("/ad-groups", "get", f"{IBASE}/ad-groups"), ("/profiles/categories", "post", f"{IBASE}/profiles/categories"),
                 ("/profiles/weights", "post", f"{IBASE}/profiles/weights")]
TIME_6 = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$"


class FakeClock:
    """요청 제한 버킷의 시각(`masters.services._now`)을 시험이 움직인다."""

    def __init__(self):
        self.now = timezone.now()

    def __call__(self, now=None):
        return now if now is not None else self.now


@override_settings(ROOT_URLCONF="tests.support.urls_media_api")
class MediaApiBase(ProviderContractMixin, TestCase):
    contract = Contract("serving")

    def setUp(self):
        self.admin = make_admin()
        self.media = masters.create_media(self.admin, "메타버스")
        self.key = masters.issue_media_key(self.admin, self.media.media_id).key
        self.clock = FakeClock()
        self.enterContext(mock.patch("masters.services._now", self.clock))

    def call(self, method, url, key="USE", body=None, content_type="application/json", **extra):
        headers = dict(extra)
        if key == "USE":
            key = self.key
        if key is not None:
            headers["HTTP_X_MEDIA_KEY"] = key
        send = getattr(self.client, method)
        if method in ("post", "put"):
            data = '{"slot_id": "x"}' if body is None else body
            return send(url, data=data, content_type=content_type, **headers)
        return send(url, **headers)

    def c8_setup(self):
        """C8 시험 세계: 끝나지 않는 캠페인의 광고 하나(소재 하나)와 그 세계의 관리자가 발급한 내부 키."""
        w = World(ends_in=None)
        return w, masters.issue_internal_key(w.admin, "분류 모듈").key

    def c8(self, method, url, key, body=None, content_type="application/json"):
        headers = {} if key is None else {"HTTP_X_INTERNAL_KEY": key}
        if method == "post":
            return self.client.post(url, data='{"items": []}' if body is None else body, content_type=content_type, **headers)
        return getattr(self.client, method)(url, **headers)


@tag("security")
class AuthenticationTests(Cases, MediaApiBase):
    def case_missing_or_invalid_key_is_401_with_challenge_and_identical_bodies(self):
        key_hex, secret = self.key.split(".")
        bad = {
            "none": None, "empty": "", "blank": "   ", "garbage": "garbage", "unknown key_id": uuid.uuid4().hex + "." + secret,
            "wrong secret": key_hex + "." + ("A" if secret[0] != "A" else "B") + secret[1:], "truncated": self.key[:-3],
        }
        bodies = set()
        for label, key in bad.items():
            for contract_path, method, url, _group in OPERATIONS:
                response = self.call(method, url, key=key)
                self.assertEqual(response.status_code, 401, (label, method, url))
                self.assertEqual(response["WWW-Authenticate"], CHALLENGE)
                self.assertEqual(response["Content-Type"], "application/problem+json")
                self.assertContract(self.contract, contract_path, method, response)  # 상태·헤더·본문이 계약대로
            bodies.add(response.json()["detail"])
        self.assertEqual(len(bodies), 1)  # 어떤 이유로 거절됐는지 알리지 않는다

    def case_revoked_and_expired_keys_are_401(self):
        revoked = masters.issue_media_key(self.admin, self.media.media_id)
        masters.revoke_media_key(self.admin, revoked.key_id)
        self.assertEqual(self.call("get", f"{BASE}/decisions/{DID}", key=revoked.key).status_code, 401)
        old = self.key
        masters.rotate_media_key(self.admin, self.media.media_id, grace_seconds=0)  # 옛 키는 바로 만료
        self.assertEqual(self.call("get", f"{BASE}/decisions/{DID}", key=old).status_code, 401)

    def case_inactive_media_with_a_valid_key_is_403_and_with_a_bad_key_is_401(self):
        masters.set_media_active(self.admin, self.media.media_id, False)
        for contract_path, method, url, _group in OPERATIONS:
            blocked = self.call(method, url)
            self.assertEqual(blocked.status_code, 403, (method, url))
            self.assertNotIn("WWW-Authenticate", blocked.headers)
            self.assertContract(self.contract, contract_path, method, blocked)
            self.assertEqual(self.call(method, url, key="nope").status_code, 401)  # 키가 틀리면 비활성 여부를 알리지 않는다
        masters.set_media_active(self.admin, self.media.media_id, True)
        self.assertEqual(self.call("get", f"{BASE}/decisions/{DID}").status_code, 200)

    def case_authentication_precedes_input_validation(self):
        """인증은 입력 검증보다 먼저다: 키가 없으면 본문이 깨졌어도 잘못된 형식이어도 401 이다."""
        for key, expected in ((None, 401), ("nope", 401), ("USE", None)):
            for label, kwargs in {
                "invalid json": dict(body="{not json"), "wrong content type": dict(body="x", content_type="text/plain"), "empty": dict(body=""),
            }.items():
                response = self.call("post", f"{BASE}/decisions", key=key, **kwargs)
                if expected:
                    self.assertEqual(response.status_code, expected, label)
                else:
                    self.assertIn(response.status_code, (400, 415), label)  # 키가 맞아야 비로소 입력을 검증한다

    def case_valid_key_reaches_the_view_with_the_media_identity(self):
        response = self.call("post", f"{BASE}/decisions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["media_id"], str(self.media.media_id))

    def case_both_keys_work_during_rotation_and_use_the_same_bucket(self):
        new = masters.rotate_media_key(self.admin, self.media.media_id, grace_seconds=3600).key
        for key in (self.key, new):
            self.assertEqual(self.call("get", f"{BASE}/decisions/{DID}", key=key).status_code, 200)

    def case_no_csrf_token_needed_and_no_cookies_ever(self):
        client = Client(enforce_csrf_checks=True)  # /api/media/ 는 CSRF 예외다(키 인증 서버 간 API)
        responses = [
            client.post(f"{BASE}/decisions", data='{"a": 1}', content_type="application/json", HTTP_X_MEDIA_KEY=self.key),
            client.post(f"{BASE}/decisions", data="{}", content_type="application/json"),  # 401
        ]
        masters.set_media_active(self.admin, self.media.media_id, False)
        responses.append(client.get(f"{BASE}/decisions/{DID}", HTTP_X_MEDIA_KEY=self.key))  # 403
        self.assertEqual([r.status_code for r in responses], [200, 401, 403])
        for response in responses:
            self.assertEqual(cookie_problems(response), [])

    def case_rejected_key_log_has_the_public_id_but_never_the_secret(self):
        key_hex, secret = self.key.split(".")
        with self.assertLogs("serving_api.auth", "WARNING") as logs:
            wrong = key_hex + "." + ("B" if secret[0] == "A" else "A") + secret[1:]  # 첫 글자가 반드시 달라진다
            self.assertNotEqual(wrong, self.key)
            self.assertEqual(self.call("get", f"{BASE}/decisions/{DID}", key=wrong).status_code, 401)
            self.assertEqual(self.call("get", f"{BASE}/decisions/{DID}", key="SECRET-LOOKING-VALUE-123").status_code, 401)
        text = "\n".join(logs.output)
        self.assertIn(key_hex, text)  # 어느 키가 거절됐는지 공개 식별자로 추적한다
        self.assertNotIn(secret[1:], text)
        self.assertNotIn("SECRET-LOOKING", text)  # 형식이 틀린 값은 아예 기록하지 않는다
        self.assertIn("key_id=-", text)

    # ---------------------------------------------------------------- C8 내부 입력 API(ACW-3)

    def case_c8_reads_return_the_contract_shape(self):
        """①·③ 200 본문이 계약 모양이다: 시각은 UTC `…Z` 6자리, 이미지 주소는 C3 공개 주소, `profile` 은 일곱 필드. 모르는 질의 인자는 무시한다."""
        w, key = self.c8_setup()
        for url in (f"{IBASE}/ad-groups", f"{IBASE}/ad-groups?state=unclassified&limit=5", f"{IBASE}/ad-groups?after={uuid.UUID(int=0)}&other=x"):
            response = self.c8("get", url, key)
            self.assertEqual(response.status_code, 200, url)
            self.assertContract(INTERNAL, "/ad-groups", "get", response)
        body = self.c8("get", f"{IBASE}/ad-groups", key).json()
        item = body["items"][0]
        self.assertEqual((len(body["items"]), body["next_after"], item["ad_group_id"], item["name"]), (1, None, str(w.group.ad_group_id), "그룹"))
        self.assertEqual([(c["creative_id"], c["title"], c["image_url"]) for c in item["creatives"]],
                         [(str(w.creative.creative_id), "제목", asset_url(w.creative.image_name))])
        for value in (item["updated_at"], item["creatives"][0]["updated_at"], item["profile"]["changed_at"]):
            self.assertRegex(value, TIME_6)
        self.assertEqual({k: item["profile"][k] for k in ("version", "category", "weight_mlo")}, {"version": 1, "category": None, "weight_mlo": 0})
        self.assertEqual(self.c8("get", f"{IBASE}/ad-groups?limit=1", key).json()["next_after"], str(w.group.ad_group_id))

    def case_c8_writes_return_the_contract_shape_and_the_event_values(self):
        """②·④ 200 본문이 계약 모양이고, 항목의 `profile` 일곱 값 = 그 판 이벤트 페이로드의 값(시각은 값으로 비교 — 응답은 6자리 `Z`). 광고그룹 행은 그대로다."""
        w, key = self.c8_setup()
        g = str(w.group.ad_group_id)
        before = AdGroup.objects.get(pk=g).updated_at
        for path, item, version in (("/profiles/categories", {"category": 12}, 2), ("/profiles/weights", {"weight_mlo": 50, "weight_aio": 30, "weight_skn": 20}, 3)):
            response = self.c8("post", IBASE + path, key, json.dumps({"items": [{"ad_group_id": g, **item}]}))
            self.assertEqual(response.status_code, 200, path)
            self.assertContract(INTERNAL, path, "post", response)
            out = response.json()["items"]
            self.assertEqual([(x["ad_group_id"], x["result"]) for x in out], [(g, "changed")])
            payload = EventOutbox.objects.get(event_id=event_id_for("ad.ad_group_profile_changed", f"{g}/{version}")).payload
            profile = out[0]["profile"]
            self.assertEqual({k: v for k, v in profile.items() if k != "changed_at"}, {k: v for k, v in payload.items() if k != "changed_at"})
            self.assertEqual(datetime.fromisoformat(profile["changed_at"]), datetime.fromisoformat(payload["changed_at"]))
            self.assertRegex(profile["changed_at"], TIME_6)
        self.assertEqual((profile["category"], profile["weight_mlo"]), (12, 50))
        self.assertEqual(AdGroup.objects.get(pk=g).updated_at, before)

    def case_c8_bad_query_or_body_is_400_413_415_or_405(self):
        """읽기 질의(`state`·`after`·`limit`)와 묶음 본문(객체·`items` 하나뿐·64 KiB·JSON)이 틀리면 4xx 이고 계약대로다. 405 는 인증보다 먼저다."""
        w, key = self.c8_setup()
        for query, pointer in (("state=all", "/state"), ("state=", "/state"), ("after=nope", "/after"), ("limit=abc", "/limit"), ("limit=-1", "/limit"),
                               ("limit=0", "/limit"), ("limit=201", "/limit"), ("limit=9999999999", "/limit")):
            response = self.c8("get", f"{IBASE}/ad-groups?{query}", key)
            self.assertEqual((response.status_code, [e["pointer"] for e in response.json()["errors"]]), (400, [pointer]), query)
            self.assertContract(INTERNAL, "/ad-groups", "get", response)
        g, ghost = str(w.group.ad_group_id), str(uuid.uuid4())
        with self.assertLogs("internal_api.views", "WARNING") as logs:  # 거절된 묶음은 감사가 없어 경고 한 줄(계획 W28) — 입구·항목 수·오류 수만, 값·광고 ID·키는 없다
            for body, line in (('{"other": 1}', "entry=categories items=- errors=2"),
                               (json.dumps({"items": [{"ad_group_id": g, "category": 777}, {"ad_group_id": ghost, "category": 4242}]}), "entry=categories items=2 errors=2"),
                               (json.dumps({"items": [{"ad_group_id": ghost, "category": 5}]}), "entry=categories items=1 errors=1")):  # 둘째 단계(없는 광고)도
                self.assertEqual(self.c8("post", f"{IBASE}/profiles/categories", key, body).status_code, 400, body)
                self.assertIn(line, logs.output[-1])
        text = "\n".join(logs.output)
        self.assertEqual(len(logs.output), 3)
        for leaked in ("777", "4242", g, ghost, key, key.split(".")[0], key.split(".")[1]):
            self.assertNotIn(leaked, text)
        for path in ("/profiles/categories", "/profiles/weights"):
            for body, pointers in (('{"other": 1}', ["/items", "/other"]), ('{"items": [], "x": 1}', ["/x"]), ('{"items": {}}', ["/items"]),
                                   ('[]', None), ('{"items": [], "items": []}', None), ("{not json", None)):
                response = self.c8("post", IBASE + path, key, body)
                self.assertEqual(response.status_code, 400, (path, body))
                self.assertContract(INTERNAL, path, "post", response)
                if pointers:
                    self.assertEqual([e["pointer"] for e in response.json()["errors"]], pointers, body)
            too_big = self.c8("post", IBASE + path, key, json.dumps({"items": [], "pad": "x" * (64 * 1024)}))
            self.assertEqual(too_big.status_code, 413)
            self.assertContract(INTERNAL, path, "post", too_big)
            wrong_type = self.c8("post", IBASE + path, key, "{}", content_type="text/plain")
            self.assertEqual(wrong_type.status_code, 415)
            self.assertContract(INTERNAL, path, "post", wrong_type)
            self.assertEqual((self.c8("get", IBASE + path, key).status_code, self.c8("get", IBASE + path, None).status_code), (405, 405))
        self.assertEqual(self.c8("post", f"{IBASE}/ad-groups", None).status_code, 405)
        self.assertFalse(AuditLog.objects.filter(action__endswith="_written").exists())

    def case_c8_requests_without_a_valid_internal_key_are_401(self):
        """키 없음·형식 오류·틀린 비밀·폐기 키·매체 키는 모두 401 + `InternalKey` challenge, 본문은 같은 문구(이유를 알리지 않는다). 내부 키로 C1 도 안 된다."""
        w, key = self.c8_setup()
        revoked = masters.issue_internal_key(w.admin, "옛 키")
        masters.revoke_internal_key(w.admin, revoked.key_id)
        key_hex, secret = key.split(".")
        bad = {"none": None, "empty": "", "garbage": "garbage", "media key": self.key, "revoked": revoked.key,
               "wrong secret": key_hex + "." + ("A" if secret[0] != "A" else "B") + secret[1:]}
        details = set()
        for label, value in bad.items():
            for contract_path, method, url in C8_OPERATIONS:
                response = self.c8(method, url, value)
                self.assertEqual(response.status_code, 401, (label, url))
                self.assertEqual(response["WWW-Authenticate"], 'InternalKey realm="ads-internal-api"')
                self.assertContract(INTERNAL, contract_path, method, response)
                details.add(response.json()["detail"])
        self.assertEqual(len(details), 1)
        self.assertEqual(self.call("post", f"{BASE}/decisions", key=key).status_code, 401)

    def case_c8_authentication_precedes_reading_the_body(self):
        """인증은 본문 해석보다 먼저다: 키가 없거나 틀리면 본문이 깨졌어도·형식이 달라도·커도 401. 키가 맞아야 비로소 400·415."""
        self.c8_setup()
        for value in (None, "nope"):
            for body, content_type in (("{not json", "application/json"), ("x", "text/plain"), ("x" * (70 * 1024), "application/json")):
                self.assertEqual(self.c8("post", f"{IBASE}/profiles/categories", value, body, content_type).status_code, 401, (value, content_type))
        key = masters.issue_internal_key(make_admin(), "다른 모듈").key
        self.assertEqual(self.c8("post", f"{IBASE}/profiles/categories", key, "{not json").status_code, 400)
        self.assertEqual(self.c8("post", f"{IBASE}/profiles/weights", key, "x", "text/plain").status_code, 415)

    def case_c8_no_csrf_token_needed_and_no_cookies(self):
        """`/api/internal/` 은 CSRF 예외(키 인증 서버 간 API)이고 세션·쿠키를 만들지 않는다."""
        w, key = self.c8_setup()
        client = Client(enforce_csrf_checks=True)
        responses = [
            client.post(f"{IBASE}/profiles/categories", data='{"items": []}', content_type="application/json", HTTP_X_INTERNAL_KEY=key),
            client.post(f"{IBASE}/profiles/weights", data='{"items": []}', content_type="application/json"),  # 401
            client.get(f"{IBASE}/ad-groups", HTTP_X_INTERNAL_KEY=key),
        ]
        self.assertEqual([r.status_code for r in responses], [200, 401, 200])
        for response in responses:
            self.assertEqual(cookie_problems(response), [])

    def case_c8_an_owner_who_is_no_longer_an_admin_gets_403(self):
        """키의 주인 운영자가 비활성화되면 그 키는 바로 못 쓴다 — 읽기·두 쓰기 모두 403(계약대로), 아무것도 쓰지 않는다."""
        w, key = self.c8_setup()
        w.admin.is_active = False
        w.admin.save()
        for contract_path, method, url in C8_OPERATIONS:
            response = self.c8(method, url, key)
            self.assertEqual(response.status_code, 403, url)
            self.assertContract(INTERNAL, contract_path, method, response)
        self.assertFalse(AuditLog.objects.filter(action__endswith="_written").exists())

    def test_authentication(self):
        self.run_cases()


@tag("security")
class StorageFailureTests(Cases, MediaApiBase):
    """키 저장소(DB)가 잠기거나 끊기면 500 이 아니라 **503 + Retry-After** 다(계약 C1 공통 응답)."""

    def assert_unavailable(self, method, contract_path, url):
        response = self.call(method, url)
        self.assertEqual(response.status_code, 503, (method, url))
        self.assertEqual(response["Content-Type"], "application/problem+json")
        self.assertEqual(response.json()["code"], "unavailable")
        self.assertEqual(response["Retry-After"], "1")
        self.assertTrue(response["X-Request-ID"])
        self.assertContract(self.contract, contract_path, method, response)

    def case_key_lookup_failure_is_503_on_every_operation_and_the_view_never_runs(self):
        with mock.patch("serving_api.auth.masters.authenticate_media_key", side_effect=OperationalError("database is locked")), \
                mock.patch("tests.support.urls_media_api.parse_json") as view_ran:
            for contract_path, method, url, _group in OPERATIONS:
                self.assert_unavailable(method, contract_path, url)
            view_ran.assert_not_called()
        self.assertEqual(self.call("get", f"{BASE}/decisions/{DID}").status_code, 200)  # 복구되면 바로 정상

    def case_storage_failure_inside_the_view_is_503_too(self):
        with mock.patch("tests.support.urls_media_api.parse_json", side_effect=OperationalError("disk I/O error")):
            self.assert_unavailable("post", "/decisions", f"{BASE}/decisions")

    def case_a_failed_lookup_consumes_no_token_and_other_errors_are_not_turned_into_503(self):
        with mock.patch("serving_api.auth.masters.authenticate_media_key", side_effect=OperationalError("locked")):
            for _ in range(10):
                self.call("get", f"{BASE}/decisions/{DID}")
        self.assertFalse(MediaRateBucket.objects.filter(media_id=self.media.media_id).exists())  # 토큰을 한 번도 쓰지 않았다(버킷이 생기지도 않았다)
        with mock.patch("tests.support.urls_media_api.parse_json", side_effect=ValueError("bug")), \
                self.assertRaises(ValueError):
            Client(raise_request_exception=True).post(
                f"{BASE}/decisions", data="{}", content_type="application/json", HTTP_X_MEDIA_KEY=self.key
            )  # 프로그래밍 오류는 가리지 않는다(일괄 503 금지)

    def case_c8_storage_failure_is_503(self):
        """C8 도 키 조회의 저장소 일시 장애는 503 + `Retry-After`(뷰는 돌지 않는다), 뷰 안의 저장 장애도 503. 다른 예외는 가리지 않는다."""
        w, key = self.c8_setup()
        with mock.patch("internal_api.views.masters.authenticate_internal_key", side_effect=OperationalError("database is locked")), \
                mock.patch("internal_api.views.campaigns.write_categories") as view_ran:
            for contract_path, method, url in C8_OPERATIONS:
                response = self.c8(method, url, key)
                self.assertEqual((response.status_code, response["Retry-After"], response.json()["code"]), (503, "1", "unavailable"), url)
                self.assertContract(INTERNAL, contract_path, method, response)
            view_ran.assert_not_called()
        with mock.patch("internal_api.views.campaigns.write_categories", side_effect=OperationalError("disk I/O error")):
            response = self.c8("post", f"{IBASE}/profiles/categories", key)
            self.assertEqual(response.status_code, 503)
            self.assertContract(INTERNAL, "/profiles/categories", "post", response)
        with mock.patch("internal_api.views.campaigns.write_categories", side_effect=ValueError("bug")), self.assertRaises(ValueError):
            Client(raise_request_exception=True).post(f"{IBASE}/profiles/categories", data='{"items": []}', content_type="application/json", HTTP_X_INTERNAL_KEY=key)
        self.assertEqual(self.c8("post", f"{IBASE}/profiles/categories", key).status_code, 200)  # 복구되면 바로 정상

    def test_serving_auth_storage_failure(self):
        self.run_cases()


@tag("security")
@override_settings(MEDIA_RATE_LIMITS={"decisions": (2, 3), "reactions": (4, 5), "reads": (1, 2)})
class RateLimitTests(Cases, MediaApiBase):
    def hit(self, method, url, **kw):
        return self.call(method, url, **kw)

    def case_burst_then_429_with_retry_after_and_refill(self):
        url = f"{BASE}/decisions"
        self.assertEqual([self.hit("post", url).status_code for _ in range(3)], [200, 200, 200])
        blocked = self.hit("post", url)
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked["Retry-After"], "1")
        self.assertContract(self.contract, "/decisions", "post", blocked)  # Problem(status=429) + Retry-After 정수
        self.assertEqual(blocked.json()["code"], "rate_limited")
        self.clock.now += timedelta(seconds=1)  # 초당 2개 충전
        self.assertEqual([self.hit("post", url).status_code for _ in range(3)], [200, 200, 429])

    def case_every_operation_returns_a_contract_conforming_429(self):
        for contract_path, method, url, group in OPERATIONS:
            MediaRateBucket.objects.all().delete()  # 경로군마다 새 버킷
            burst = {"decisions": 3, "reactions": 5, "reads": 2}[group]
            for _ in range(burst):
                self.assertEqual(self.hit(method, url).status_code, 200)
            blocked = self.hit(method, url)
            self.assertEqual(blocked.status_code, 429, (method, url))
            self.assertContract(self.contract, contract_path, method, blocked)

    def case_buckets_are_per_media_and_per_group(self):
        other = masters.create_media(self.admin, "다른 매체")
        other_key = masters.issue_media_key(self.admin, other.media_id).key
        [self.hit("post", f"{BASE}/decisions") for _ in range(3)]
        self.assertEqual(self.hit("post", f"{BASE}/decisions").status_code, 429)
        self.assertEqual(self.hit("post", f"{BASE}/decisions", key=other_key).status_code, 200)  # 다른 매체는 영향 없음
        self.assertEqual(self.hit("get", f"{BASE}/decisions/{DID}").status_code, 200)  # 같은 매체의 다른 경로군도 영향 없음
        self.assertEqual(self.hit("put", f"{BASE}/decisions/{DID}/reactions/{RID}").status_code, 200)
        self.assertEqual(self.hit("get", f"{BASE}/decisions/{DID}").status_code, 200)  # 조회 버킷(버스트 2)의 두 번째
        self.assertEqual(self.hit("get", f"{BASE}/decisions/{DID}").status_code, 429)  # 조회 버킷만 따로 소진
        self.assertEqual(self.hit("put", f"{BASE}/decisions/{DID}/reactions/{RID}").status_code, 200)  # 반응 버킷은 그대로

    def case_rotating_the_key_keeps_the_same_bucket(self):
        [self.hit("post", f"{BASE}/decisions") for _ in range(3)]
        new = masters.rotate_media_key(self.admin, self.media.media_id, grace_seconds=3600).key
        self.assertEqual(self.hit("post", f"{BASE}/decisions", key=new).status_code, 429)  # 새 키로 한도를 우회할 수 없다

    def case_unauthenticated_and_forbidden_requests_consume_no_tokens(self):
        for _ in range(20):
            self.assertEqual(self.hit("post", f"{BASE}/decisions", key="nope").status_code, 401)
        masters.set_media_active(self.admin, self.media.media_id, False)
        for _ in range(20):
            self.assertEqual(self.hit("post", f"{BASE}/decisions").status_code, 403)
        masters.set_media_active(self.admin, self.media.media_id, True)
        self.assertEqual([self.hit("post", f"{BASE}/decisions").status_code for _ in range(3)], [200, 200, 200])  # 버스트가 그대로 남아 있다

    def case_throttle_runs_after_authentication_but_before_input_validation(self):
        [self.hit("post", f"{BASE}/decisions") for _ in range(3)]
        broken = self.hit("post", f"{BASE}/decisions", body="{not json")
        self.assertEqual(broken.status_code, 429)  # 한도를 넘으면 본문을 읽기 전에 거절
        self.assertEqual(self.hit("post", f"{BASE}/decisions", key="nope", body="{not json").status_code, 401)

    def case_default_limits_match_the_contract_numbers(self):
        text = Contract("serving").spec["info"]["description"]
        from config.settings import base

        rows = re.findall(r"^\s*\|\s*(결정 생성|반응 접수|조회)\b[^|]*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", text, re.M)  # 계약 표의 경로군 행(숫자만 찾으면 다른 행의 같은 숫자로 통과했다, 버그 탐색 B14)
        groups = {"결정 생성": "decisions", "반응 접수": "reactions", "조회": "reads"}
        self.assertEqual({groups[label]: (int(rate), int(burst)) for label, rate, burst in rows}, base.MEDIA_RATE_LIMITS)
        self.assertEqual(len(rows), 3)
        self.assertEqual(base.MEDIA_RATE_LIMITS, {"decisions": (150, 300), "reactions": (450, 600), "reads": (150, 300)})

    def test_rate_limit(self):
        self.run_cases()
