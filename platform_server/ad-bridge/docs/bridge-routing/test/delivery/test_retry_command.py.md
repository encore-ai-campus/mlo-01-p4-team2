# test/delivery/test_retry_command.py

- 경로: [ad-bridge/test/delivery/test_retry_command.py](../../../../test/delivery/test_retry_command.py)
- 기능: 재전송 명령의 반복·종료·실패 처리를 HTTP·DB·대기 없이 확인한다.
- 호출·연결: ad_bridge.management.commands: dispatch_ad_reactions as command; ad_bridge.delivery.sender: ImpressionReport; ad_bridge.impressions.store: StoredImpression
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `RetryCommandTests(SimpleTestCase)` | 테스트 사례·도우미 묶음 |
| `RetryCommandTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `RetryCommandTests.run_command(self, *args, **kwargs)` | 테스트 보조: run command |
| `RetryCommandTests.prepare_recovery(self)` | 테스트 보조: prepare recovery |
| `RetryCommandTests.test_recovery_sends_only_the_explicit_record_and_exits(self)` | 검증: recovery sends only the explicit record and exits |
| `RetryCommandTests.test_recovery_invalid_uuid_is_redacted_before_config_or_claim(self)` | 검증: recovery invalid uuid is redacted before config or claim |
| `RetryCommandTests.test_recovery_invalid_config_does_not_claim_or_send(self)` | 검증: recovery invalid config does not claim or send |
| `RetryCommandTests.test_recovery_excluded_record_fails_without_sending(self)` | 검증: recovery excluded record fails without sending |
| `RetryCommandTests.test_recovery_without_receipt_fails_after_one_send(self)` | 검증: recovery without receipt fails after one send |
| `RetryCommandTests.test_recovery_receipt_without_final_judgment_is_success(self)` | 검증: recovery receipt without final judgment is success |
| `RetryCommandTests.test_recovery_database_failures_are_redacted_and_do_not_repeat(self)` | 검증: recovery database failures are redacted and do not repeat |
| `RetryCommandTests.test_recovery_record_validation_failure_is_redacted(self)` | 검증: recovery record validation failure is redacted |
| `RetryCommandTests.test_recovery_first_ctrl_c_finishes_request_and_restores_handler(self)` | 검증: recovery first ctrl c finishes request and restores handler |
| `RetryCommandTests.test_recovery_first_ctrl_c_finishes_request_and_restores_handler.send(**kwargs)` | 테스트 보조: send |
| `RetryCommandTests.test_recovery_keyboard_interrupt_still_restores_handler_and_connections(self)` | 검증: recovery keyboard interrupt still restores handler and connections |
| `RetryCommandTests.test_recovery_stop_before_start_does_not_claim_or_send(self)` | 검증: recovery stop before start does not claim or send |
| `RetryCommandTests.test_recovery_cli_returns_failure_code_when_receipt_is_not_confirmed(self)` | 검증: recovery cli returns failure code when receipt is not confirmed |
| `RetryCommandTests.test_recovery_cli_completes_successfully_when_receipt_is_confirmed(self)` | 검증: recovery cli completes successfully when receipt is confirmed |
| `RetryCommandTests.test_once_dispatches_exactly_one_batch_with_default_options(self)` | 검증: once dispatches exactly one batch with default options |
| `RetryCommandTests.test_custom_batch_and_interval_are_used(self)` | 검증: custom batch and interval are used |
| `RetryCommandTests.test_custom_batch_and_interval_are_used.dispatch(**kwargs)` | 테스트 보조: dispatch |
| `RetryCommandTests.test_invalid_options_fail_before_config_or_dispatch(self)` | 검증: invalid options fail before config or dispatch |
| `RetryCommandTests.test_invalid_config_fails_without_claiming_or_exposing_secret(self)` | 검증: invalid config fails without claiming or exposing secret |
| `RetryCommandTests.test_default_loop_uses_stop_callback_between_batches(self)` | 검증: default loop uses stop callback between batches |
| `RetryCommandTests.test_default_loop_uses_stop_callback_between_batches.dispatch(**kwargs)` | 테스트 보조: dispatch |
| `RetryCommandTests.test_first_ctrl_c_finishes_batch_then_stops_and_restores_handler(self)` | 검증: first ctrl c finishes batch then stops and restores handler |
| `RetryCommandTests.test_first_ctrl_c_finishes_batch_then_stops_and_restores_handler.dispatch(**kwargs)` | 테스트 보조: dispatch |
| `RetryCommandTests.test_second_ctrl_c_propagates_keyboard_interrupt(self)` | 검증: second ctrl c propagates keyboard interrupt |
| `RetryCommandTests.test_second_ctrl_c_propagates_keyboard_interrupt.dispatch(**kwargs)` | 테스트 보조: dispatch |
| `RetryCommandTests.test_once_database_failure_is_nonzero_and_redacted(self)` | 검증: once database failure is nonzero and redacted |
| `RetryCommandTests.test_loop_database_failure_waits_and_reconnects_before_next_batch(self)` | 검증: loop database failure waits and reconnects before next batch |
| `RetryCommandTests.test_loop_database_failure_waits_and_reconnects_before_next_batch.dispatch(**kwargs)` | 테스트 보조: dispatch |
| `RetryCommandTests.test_idle_batches_do_not_emit_repeated_output(self)` | 검증: idle batches do not emit repeated output |
| `RetryCommandTests.test_unexpected_exception_and_keyboard_interrupt_are_not_swallowed(self)` | 검증: unexpected exception and keyboard interrupt are not swallowed |
| `RetryCommandTests.test_cleanup_database_error_is_redacted(self)` | 검증: cleanup database error is redacted |
