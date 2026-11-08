# test/delivery/test_retry.py

- 경로: [ad-bridge/test/delivery/test_retry.py](../../../../test/delivery/test_retry.py)
- 기능: 고정 시계와 메모리 저장소로 노출 재전송 정책을 검사한다.
- 호출·연결: ad_bridge.api.errors: AdsHTTPError, AdsResponseError, AdsTransportError; ad_bridge.impressions.store: StoredImpression; ad_bridge.delivery.worker: dispatch_due_impressions; ad_bridge.impressions.service: report_impression_once; test.support.clock: FixedClock; ad_bridge.delivery.policy: RetryPolicy
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `RetryTests(unittest.TestCase)` | 테스트 사례·도우미 묶음 |
| `RetryTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `RetryTests.report(self)` | 테스트 보조: report |
| `RetryTests.dispatch(self, **kwargs)` | 테스트 보조: dispatch |
| `RetryTests.seed_due(self, count)` | 테스트 보조: seed due |
| `RetryTests.test_failed_report_waits_until_due_and_reuses_identical_payload(self)` | 검증: failed report waits until due and reuses identical payload |
| `RetryTests.test_retry_after_remains_a_minimum_even_beyond_backoff_cap(self)` | 검증: retry after remains a minimum even beyond backoff cap |
| `RetryTests.test_backoff_doubles_caps_and_handles_very_large_attempt_count(self)` | 검증: backoff doubles caps and handles very large attempt count |
| `RetryTests.test_429_retry_after_zero_still_waits_for_local_backoff(self)` | 검증: 429 retry after zero still waits for local backoff |
| `RetryTests.test_retry_delay_starts_after_failed_http_finishes(self)` | 검증: retry delay starts after failed http finishes |
| `RetryTests.test_retry_delay_starts_after_failed_http_finishes.slow_failure(**payload)` | 테스트 보조: slow failure |
| `RetryTests.test_worker_reschedules_transient_failures_using_incremented_attempts(self)` | 검증: worker reschedules transient failures using incremented attempts |
| `RetryTests.test_terminal_http_errors_never_get_new_ids_or_worker_retries(self)` | 검증: terminal http errors never get new ids or worker retries |
| `RetryTests.test_worker_counts_permanent_failure_as_blocked(self)` | 검증: worker counts permanent failure as blocked |
| `RetryTests.test_expired_sending_lease_is_recovered_with_same_event(self)` | 검증: expired sending lease is recovered with same event |
| `RetryTests.test_legacy_missing_schedule_or_lease_is_recoverable(self)` | 검증: legacy missing schedule or lease is recoverable |
| `RetryTests.test_malformed_acknowledged_body_stops_future_puts(self)` | 검증: malformed acknowledged body stops future puts |
| `RetryTests.test_malformed_response_without_acknowledgement_is_blocked(self)` | 검증: malformed response without acknowledgement is blocked |
| `RetryTests.test_batch_limit_leaves_remaining_records_for_next_run(self)` | 검증: batch limit leaves remaining records for next run |
| `RetryTests.test_stop_signal_prevents_claim_and_stops_between_items(self)` | 검증: stop signal prevents claim and stops between items |
| `RetryTests.test_result_save_failure_recovers_only_after_lease_expiry(self)` | 검증: result save failure recovers only after lease expiry |
| `RetryTests.test_late_retry_after_from_expired_worker_delays_next_claim(self)` | 검증: late retry after from expired worker delays next claim |
| `RetryTests.test_late_retry_after_from_expired_worker_delays_next_claim.overlapping_sends(**payload)` | 테스트 보조: overlapping sends |
| `RetryTests.test_policy_rejects_nonpositive_nonfinite_and_boolean_parameters(self)` | 검증: policy rejects nonpositive nonfinite and boolean parameters |
| `RetryTests.test_delay_rejects_invalid_attempts_and_retry_after(self)` | 검증: delay rejects invalid attempts and retry after |
| `RetryTests.test_invalid_batch_limit_fails_before_claiming_or_sending(self)` | 검증: invalid batch limit fails before claiming or sending |
