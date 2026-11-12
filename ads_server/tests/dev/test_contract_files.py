"""계약 **파일** 검증(서버와 무관): 올바른 OpenAPI 3.1 인지, 예시가 스키마를 지키는지, 계약 규칙이 서로 모순되지 않는지."""
from datetime import datetime, timezone

from django.test import SimpleTestCase, tag
from openapi_spec_validator import validate

from campaigns.services import BATCH_MAX, SOURCE_PAGE_MAX
from events.api import ad_group_profile_changed_payload
from foundation.api import UUID_TEXT
from internal_api.views import CHALLENGE as INTERNAL_CHALLENGE
from serving_api.requests import UUID_PATTERN
from tests.support.contract_support import Contract, load_spec
from tests.support.cases import Cases

NAMES = ("serving", "console", "internal")


class ContractFileTests(Cases, SimpleTestCase):
    def case_files_are_valid_openapi_31(self):
        for name in NAMES:
            spec = load_spec(name)
            self.assertTrue(spec["openapi"].startswith("3.1"), name)
            validate(spec)  # 구조·내부 $ref 가 틀리면 예외

    def case_operation_ids_are_unique_and_present(self):
        ids = []
        for name in NAMES:
            for path, method, op in Contract(name).operations():
                self.assertIn("operationId", op, f"{name} {method} {path}")
                ids.append(op["operationId"])
        self.assertEqual(len(ids), len(set(ids)))

    def case_all_examples_match_their_schemas_including_formats(self):
        checked = 0
        for name in NAMES:
            contract = Contract(name)
            for path, method, op in contract.operations():
                body = (op.get("requestBody") or {}).get("content", {})
                for media, c in body.items():
                    for ex_name, ex in (c.get("examples") or {}).items():
                        checked += 1
                        self.assertEqual(contract.schema_errors(c["schema"], ex["value"]), [], f"{name} {method} {path} request {ex_name}")
                for status, response in op["responses"].items():
                    for media, c in (contract.resolve(response).get("content") or {}).items():
                        for ex_name, ex in (c.get("examples") or {}).items():
                            checked += 1
                            self.assertEqual(contract.schema_errors(c["schema"], ex["value"]), [], f"{name} {method} {path} {status} {ex_name}")
            for rn, r in contract.spec["components"].get("responses", {}).items():
                for media, c in (r.get("content") or {}).items():
                    for ex_name, ex in (c.get("examples") or {}).items():
                        checked += 1
                        self.assertEqual(contract.schema_errors(c["schema"], ex["value"]), [], f"{name} response {rn} {ex_name}")
        self.assertGreater(checked, 10)  # 검사가 공회전이 아님

    def test_contract_file(self):
        self.run_cases()


@tag("security")
class ServingContractRuleTests(Cases, SimpleTestCase):
    """C1·C3 의 규칙(설계 §3, 계획 2-2·2-3)이 계약 파일 안에서 서로 맞물리는지."""

    contract = Contract("serving")

    def case_decision_post_documents_every_response_the_rules_require(self):
        """키 없음 401 · 매체 비활성 403 · 없는/타 매체 슬롯 404 · 정상(비활성 슬롯·광고 없음 포함) 201 · 크기·형식·제한."""
        responses = self.contract.operation("/decisions", "post")["responses"]
        self.assertLessEqual({"201", "400", "401", "403", "404", "413", "415", "429", "503"}, set(responses))

    def case_forbidden_means_inactive_media_only_and_other_media_slot_is_not_found(self):
        spec = self.contract.spec
        self.assertIn("매체 비활성", spec["components"]["responses"]["Forbidden"]["description"])
        self.assertNotIn("이 매체의 슬롯이 아님", spec["components"]["responses"]["Forbidden"]["description"])
        self.assertIn("다른 매체의 슬롯", spec["components"]["responses"]["NotFound"]["description"])
        desc = self.contract.operation("/decisions", "post")["description"]
        for needle in ("비활성 슬롯", "403", "404", "ad: null"):
            self.assertIn(needle, desc)

    def case_every_authenticated_operation_declares_the_common_auth_responses(self):
        """인증이 필요한 모든 엔드포인트가 401·403·429·503 을 선언한다(한 곳이 빠지면 규칙대로 만든 응답이 계약 위반이 된다)."""
        checked = 0
        for path, method, op in self.contract.operations():
            if op.get("security") == []:
                continue
            checked += 1
            self.assertLessEqual({"401", "403", "429", "503"}, set(op["responses"]), f"{method} {path}")
        self.assertGreaterEqual(checked, 4)  # createDecision, getDecision, putReaction, getReaction

    def case_401_requires_a_media_key_challenge_header(self):
        header = self.contract.resolve(self.contract.spec["components"]["responses"]["Unauthorized"]["headers"]["WWW-Authenticate"])
        self.assertTrue(header.get("required"))
        self.assertEqual(self.contract.schema_errors(header["schema"], 'MediaKey realm="ads-media-api"'), [])
        for bad in ("", "Basic realm=\"x\"", "MediaKey"):
            self.assertTrue(self.contract.schema_errors(header["schema"], bad), bad)

    def case_throttled_and_unavailable_always_carry_retry_after(self):
        for name in ("TooManyRequests", "Unavailable"):
            headers = self.contract.spec["components"]["responses"][name]["headers"]
            self.assertTrue(self.contract.resolve(headers["Retry-After"]).get("required"), name)

    def case_every_response_documents_the_request_id_header(self):
        for path, method, op in self.contract.operations():
            for status, response in op["responses"].items():
                if path.startswith("/assets") or status == "204":
                    continue  # C3 는 최종 사용자 브라우저가 받는 이미지라 헤더 계약이 따로 있다
                headers = self.contract.resolve(response).get("headers") or {}
                self.assertIn("X-Request-ID", headers, f"{method} {path} {status}")

    def case_error_responses_are_problem_json_with_a_shared_schema(self):
        for path, method, op in self.contract.operations():
            for status, response in op["responses"].items():
                if status.startswith(("4", "5")) and not path.startswith("/assets"):
                    content = self.contract.resolve(response).get("content", {})
                    self.assertEqual(list(content), ["application/problem+json"], f"{method} {path} {status}")
                    self.assertEqual(content["application/problem+json"]["schema"], {"$ref": "#/components/schemas/Problem"})

    def case_media_key_security_everywhere_except_health_assets_and_clicks(self):
        for path, method, op in self.contract.operations():
            if path in ("/health",) or path.startswith(("/assets", "/c/")):  # 최종 사용자 브라우저가 여는 주소(C3·클릭)
                self.assertEqual(op.get("security"), [], f"{method} {path}")
            else:
                self.assertNotIn("security", op, f"{method} {path}")  # 전역 MediaKey 를 따른다
        self.assertEqual(self.contract.spec["security"], [{"MediaKey": []}])

    def case_rate_limits_and_confirmed_decisions_are_written_in_the_contract(self):
        text = self.contract.spec["info"]["description"]
        for needle in ("150", "300", "450", "600", "Retry-After", "사전에 정의한 목표 완료", "1~200자"):
            self.assertIn(needle, text)
        self.assertNotIn("열린 항목", text)  # 전환 정의·title 은 확정됐다

    def case_asset_404_text_covers_shared_files(self):
        desc = self.contract.operation("/assets/{asset_name}", "get")["description"]
        self.assertIn("같은 내용의 파일을 쓰는 다른 활성 캠페인", desc)
        self.assertIn("교체된 이전 이미지", desc)

    def test_serving_contract_rule(self):
        self.run_cases()


@tag("security")
class InternalContractRuleTests(Cases, SimpleTestCase):
    """C8(ACW-3, 설계 §3 "C8 Internal API")의 규칙이 계약 파일 안에서 맞물리고, 서버 쪽 값(이벤트 페이로드·상한·ID 형식·challenge)과 같은지."""

    contract = Contract("internal")

    def response(self, name):
        return self.contract.spec["components"]["responses"][name]

    def case_every_operation_needs_the_internal_key_and_declares_401_403_503(self):
        self.assertEqual(self.contract.spec["security"], [{"InternalKey": []}])
        scheme = self.contract.spec["components"]["securitySchemes"]["InternalKey"]
        self.assertEqual((scheme["type"], scheme["in"], scheme["name"]), ("apiKey", "header", "X-Internal-Key"))
        operations = list(self.contract.operations())
        self.assertEqual(sorted((path, method) for path, method, _ in operations),
                         [("/ad-groups", "get"), ("/profiles/categories", "post"), ("/profiles/weights", "post")])
        for path, method, op in operations:
            self.assertNotIn("security", op, path)  # 전역 InternalKey 를 따른다
            self.assertLessEqual({"200", "400", "401", "403", "503"}, set(op["responses"]), path)
            if method == "post":
                self.assertLessEqual({"413", "415"}, set(op["responses"]), path)

    def case_401_challenge_and_503_retry_after(self):
        header = self.contract.resolve(self.response("Unauthorized")["headers"]["WWW-Authenticate"])
        self.assertTrue(header.get("required"))
        self.assertEqual(self.contract.schema_errors(header["schema"], INTERNAL_CHALLENGE), [])  # 서버가 보내는 값 그대로
        for bad in ("", 'MediaKey realm="ads-media-api"', "InternalKey"):
            self.assertTrue(self.contract.schema_errors(header["schema"], bad), bad)
        self.assertTrue(self.contract.resolve(self.response("Unavailable")["headers"]["Retry-After"]).get("required"))

    def case_every_response_has_the_request_id_and_errors_are_problem_json(self):
        for path, method, op in self.contract.operations():
            for status, response in op["responses"].items():
                response = self.contract.resolve(response)
                self.assertIn("X-Request-ID", response.get("headers") or {}, f"{method} {path} {status}")
                if status.startswith(("4", "5")):
                    self.assertEqual(list(response["content"]), ["application/problem+json"], f"{method} {path} {status}")
                    self.assertEqual(response["content"]["application/problem+json"]["schema"], {"$ref": "#/components/schemas/Problem"})

    def case_the_profile_schema_has_the_event_payload_keys(self):
        """응답의 `profile` = 이벤트 `ad.ad_group_profile_changed` 페이로드와 같은 일곱 키(빌더에서 만든다)."""
        payload = ad_group_profile_changed_payload(ad_group_id="0", version=1, category=None, weight_mlo=0, weight_aio=0, weight_skn=0,
                                                   changed_at=datetime(2026, 11, 12, tzinfo=timezone.utc))
        schema = self.contract.spec["components"]["schemas"]["AdGroupProfile"]
        self.assertEqual(set(schema["required"]), set(payload))
        self.assertEqual(set(schema["properties"]), set(payload))

    def case_limits_and_the_uuid_pattern_match_the_server(self):
        limit = next(p for p in self.contract.operation("/ad-groups", "get")["parameters"] if p["name"] == "limit")["schema"]
        self.assertEqual((limit["minimum"], limit["maximum"]), (1, SOURCE_PAGE_MAX))
        schemas = self.contract.spec["components"]["schemas"]
        self.assertEqual({schemas[name]["properties"]["items"]["maxItems"] for name in ("CategoryBatch", "WeightBatch")}, {BATCH_MAX})
        self.assertEqual(schemas["AdGroupSourcePage"]["properties"]["items"]["maxItems"], SOURCE_PAGE_MAX)
        self.assertEqual(schemas["Uuid"]["pattern"], f"^{UUID_TEXT.pattern}$")
        self.assertEqual(UUID_TEXT.pattern, UUID_PATTERN.pattern)  # C1 과 C8 이 같은 ID 형식(N8)

    def test_internal_contract_rule(self):
        self.run_cases()
