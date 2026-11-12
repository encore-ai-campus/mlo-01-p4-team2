# test/api/test_client.py

- 경로: [ad-bridge/test/api/test_client.py](../../../../test/api/test_client.py)
- 기능: 외부 서버 없이 요청 형식·오류·키 보호·계약 응답 처리를 확인한다.
- 호출·연결: ad_bridge: AdsClient, AdsConfig, AdsHTTPError, AdsResponseError, AdsTransportError
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `headers(content_type="application/json", **extra)` | 테스트 보조: headers |
| `response(body=None, status=201, content_type="application/json")` | 테스트 보조: response |
| `DecisionClientTests(unittest.TestCase)` | 테스트 사례·도우미 묶음 |
| `DecisionClientTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `DecisionClientTests.test_personal_request_uses_config_and_only_pseudonymous_ids(self)` | 검증: personal request uses config and only pseudonymous ids |
| `DecisionClientTests.test_no_ad_and_new_fields_are_supported(self)` | 검증: no ad and new fields are supported |
| `DecisionClientTests.test_shared_omits_subject_and_keeps_click_template(self)` | 검증: shared omits subject and keeps click template |
| `DecisionClientTests.test_bad_input_is_rejected_before_network_access(self)` | 검증: bad input is rejected before network access |
| `DecisionClientTests.test_http_failures_are_exposed_without_retry(self)` | 검증: http failures are exposed without retry |
| `DecisionClientTests.test_non_json_http_failure_keeps_status_and_redacts_metadata(self)` | 검증: non json http failure keeps status and redacts metadata |
| `DecisionClientTests.test_timeout_connection_and_truncated_response_do_not_retry(self)` | 검증: timeout connection and truncated response do not retry |
| `DecisionClientTests.test_invalid_success_status_or_content_type_is_not_no_ad(self)` | 검증: invalid success status or content type is not no ad |
| `DecisionClientTests.test_missing_wrong_or_mismatched_required_fields(self)` | 검증: missing wrong or mismatched required fields |
| `DecisionClientTests.test_invalid_json_oversized_response_and_unsafe_urls_are_rejected(self)` | 검증: invalid json oversized response and unsafe urls are rejected |
