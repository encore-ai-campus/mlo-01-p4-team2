# test/api/test_impression.py

- 경로: [ad-bridge/test/api/test_impression.py](../../../../test/api/test_impression.py)
- 기능: 실제 광고 서버 없이 노출 PUT 한 번의 계약·멱등 값·오류 처리를 확인한다.
- 호출·연결: ad_bridge: AdsClient, AdsConfig, AdsHTTPError, AdsResponseError, AdsTransportError, Reaction
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `_headers(content_type="application/json")` | 테스트 보조:  headers |
| `_response(body=None, *, status=201, raw=None, content_type="application/json")` | 테스트 보조:  response |
| `ImpressionClientTests(unittest.TestCase)` | 테스트 사례·도우미 묶음 |
| `ImpressionClientTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `ImpressionClientTests._report(self, **changes)` | 테스트 보조:  report |
| `ImpressionClientTests.test_first_report_puts_only_impression_and_fixed_time(self)` | 검증: first report puts only impression and fixed time |
| `ImpressionClientTests.test_explicit_same_report_accepts_200_and_preserves_request_bytes(self)` | 검증: explicit same report accepts 200 and preserves request bytes |
| `ImpressionClientTests.test_subject_is_normalized_and_sent_when_supplied(self)` | 검증: subject is normalized and sent when supplied |
| `ImpressionClientTests.test_invalid_uuids_are_rejected_before_network(self)` | 검증: invalid uuids are rejected before network |
| `ImpressionClientTests.test_invalid_utc_times_are_rejected_before_network(self)` | 검증: invalid utc times are rejected before network |
| `ImpressionClientTests.test_same_time_with_different_fraction_precision_is_accepted(self)` | 검증: same time with different fraction precision is accepted |
| `ImpressionClientTests.test_known_states_new_fields_and_unknown_reason_are_accepted(self)` | 검증: known states new fields and unknown reason are accepted |
| `ImpressionClientTests.test_every_required_response_field_is_checked(self)` | 검증: every required response field is checked |
| `ImpressionClientTests.test_wrong_response_types_and_dates_are_rejected(self)` | 검증: wrong response types and dates are rejected |
| `ImpressionClientTests.test_response_ids_subject_and_time_must_match_request(self)` | 검증: response ids subject and time must match request |
| `ImpressionClientTests.test_http_failures_preserve_status_without_retry(self)` | 검증: http failures preserve status without retry |
| `ImpressionClientTests.test_transport_failure_before_acknowledgement_does_not_retry(self)` | 검증: transport failure before acknowledgement does not retry |
| `ImpressionClientTests.test_malformed_json_after_2xx_retains_acknowledgement(self)` | 검증: malformed json after 2xx retains acknowledgement |
| `ImpressionClientTests.test_body_read_failure_after_2xx_retains_acknowledgement(self)` | 검증: body read failure after 2xx retains acknowledgement |
| `ImpressionClientTests.test_bad_content_type_or_oversized_body_retains_acknowledgement(self)` | 검증: bad content type or oversized body retains acknowledgement |
| `ImpressionClientTests.test_unexpected_2xx_is_acknowledged_but_not_a_valid_reaction(self)` | 검증: unexpected 2xx is acknowledged but not a valid reaction |
| `ImpressionClientTests.test_reflected_media_key_is_removed_from_result_and_http_metadata(self)` | 검증: reflected media key is removed from result and http metadata |
| `ImpressionClientTests.test_redirect_is_blocked_without_following_or_retrying(self)` | 검증: redirect is blocked without following or retrying |
