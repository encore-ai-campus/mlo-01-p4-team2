# test/impressions/test_impression_service.py

- 경로: [ad-bridge/test/impressions/test_impression_service.py](../../../../test/impressions/test_impression_service.py)
- 기능: 노출 예약·최초 전송·접수 상태 저장을 외부 연결 없이 검사한다.
- 호출·연결: ad_bridge.api.errors: AdsHTTPError, AdsResponseError, AdsTransportError; ad_bridge.impressions.service: report_impression_once
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `ImpressionServiceTests(unittest.TestCase)` | 테스트 사례·도우미 묶음 |
| `ImpressionServiceTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `ImpressionServiceTests.report(self, *, occurred_at=OCCURRED_AT)` | 테스트 보조: report |
| `ImpressionServiceTests.test_first_report_reserves_stable_payload_before_http(self)` | 검증: first report reserves stable payload before http |
| `ImpressionServiceTests.test_first_report_reserves_stable_payload_before_http.send(**payload)` | 테스트 보조: send |
| `ImpressionServiceTests.test_acknowledged_duplicate_preserves_uuid_and_time_without_resending(self)` | 검증: acknowledged duplicate preserves uuid and time without resending |
| `ImpressionServiceTests.test_unconfirmed_duplicate_is_not_retried_in_this_step(self)` | 검증: unconfirmed duplicate is not retried in this step |
| `ImpressionServiceTests.test_existing_sending_reservation_is_not_sent_again(self)` | 검증: existing sending reservation is not sent again |
| `ImpressionServiceTests.test_terminal_http_errors_are_blocked_without_new_reaction(self)` | 검증: terminal http errors are blocked without new reaction |
| `ImpressionServiceTests.test_retryable_http_errors_remain_unconfirmed_without_immediate_retry(self)` | 검증: retryable http errors remain unconfirmed without immediate retry |
| `ImpressionServiceTests.test_transport_failure_is_unconfirmed_without_secret_text(self)` | 검증: transport failure is unconfirmed without secret text |
| `ImpressionServiceTests.test_malformed_acknowledged_response_keeps_receipt_and_does_not_resend(self)` | 검증: malformed acknowledged response keeps receipt and does not resend |
| `ImpressionServiceTests.test_response_without_acknowledgement_is_blocked(self)` | 검증: response without acknowledgement is blocked |
| `ImpressionServiceTests.test_invalid_input_is_blocked_without_raw_exception(self)` | 검증: invalid input is blocked without raw exception |
| `ImpressionServiceTests.test_reservation_failure_prevents_http(self)` | 검증: reservation failure prevents http |
| `ImpressionServiceTests.test_save_failure_after_receipt_preserves_reservation_and_prevents_resend(self)` | 검증: save failure after receipt preserves reservation and prevents resend |
