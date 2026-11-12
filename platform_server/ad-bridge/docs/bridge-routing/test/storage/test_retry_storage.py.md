# test/storage/test_retry_storage.py

- 경로: [ad-bridge/test/storage/test_retry_storage.py](../../../../test/storage/test_retry_storage.py)
- 기능: 재전송 저장소 통합 검사: 메모리 SQLite에서 예약·임대·마이그레이션을 확인한다.
- 호출·연결: ad_bridge.impressions: store as impression_store; ad_bridge.api.errors: AdsHTTPError; ad_bridge.delivery.policy: RetryPolicy; ad_bridge.delivery.sender: send_once; ad_bridge.delivery.worker: dispatch_due_impressions; test.support.impression_fakes: received_reaction; ad_bridge.models: Impression, IssuedDecision
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `RetryStorageTests(TestCase)` | 테스트 사례·도우미 묶음 |
| `RetryStorageTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `RetryStorageTests.reserve(self)` | 테스트 보조: reserve |
| `RetryStorageTests.fail(self, record, *, due=None)` | 테스트 보조: fail |
| `RetryStorageTests.test_reservation_has_first_attempt_and_active_lease(self)` | 검증: reservation has first attempt and active lease |
| `RetryStorageTests.test_due_time_and_retry_metadata_are_persisted(self)` | 검증: due time and retry metadata are persisted |
| `RetryStorageTests.test_expired_lease_recovers_crashed_sending_record(self)` | 검증: expired lease recovers crashed sending record |
| `RetryStorageTests.test_terminal_records_cannot_be_claimed(self)` | 검증: terminal records cannot be claimed |
| `RetryStorageTests.test_stale_failure_does_not_overwrite_current_owner(self)` | 검증: stale failure does not overwrite current owner |
| `RetryStorageTests.test_late_retry_after_survives_lease_races_and_shorter_failure_delays(self)` | 검증: late retry after survives lease races and shorter failure delays |
| `RetryStorageTests.test_late_retry_after_survives_lease_races_and_shorter_failure_delays.hint_during_claim(queryset, **values)` | 테스트 보조: hint during claim |
| `RetryStorageTests.test_missing_token_cannot_finish_a_new_lease(self)` | 검증: missing token cannot finish a new lease |
| `RetryStorageTests.test_stale_acknowledgement_dominates_current_and_late_failures(self)` | 검증: stale acknowledgement dominates current and late failures |
| `RetryStorageTests.test_stale_acknowledgement_can_confirm_previously_blocked_request(self)` | 검증: stale acknowledgement can confirm previously blocked request |
| `RetryStorageTests.test_duplicate_notification_does_not_extend_or_replace_lease(self)` | 검증: duplicate notification does not extend or replace lease |
| `RetryStorageTests.test_legacy_missing_schedule_or_lease_is_claimable(self)` | 검증: legacy missing schedule or lease is claimable |
| `RetryStorageTests.test_lost_conditional_update_cannot_return_another_workers_lease(self)` | 검증: lost conditional update cannot return another workers lease |
| `RetryStorageTests.test_lost_conditional_update_cannot_return_another_workers_lease.racing_update(queryset, **values)` | 테스트 보조: racing update |
| `RetryStorageTests.test_claim_rollback_preserves_previous_schedule(self)` | 검증: claim rollback preserves previous schedule |
| `RetryStorageTests.test_claim_rejects_external_transaction_before_modification(self)` | 검증: claim rejects external transaction before modification |
| `RetryStorageTests.test_invalid_clock_and_lease_are_rejected(self)` | 검증: invalid clock and lease are rejected |
| `RetryStorageTests.test_diagnostics_reject_exception_text_and_invalid_http_metadata(self)` | 검증: diagnostics reject exception text and invalid http metadata |
| `Blocked401RecoveryTests(TestCase)` | 테스트 사례·도우미 묶음 |
| `Blocked401RecoveryTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `Blocked401RecoveryTests.claim(self, **options)` | 테스트 보조: claim |
| `Blocked401RecoveryTests.test_claim_changes_only_delivery_lease_fields(self)` | 검증: claim changes only delivery lease fields |
| `Blocked401RecoveryTests.test_non_401_and_acknowledged_or_judged_records_are_excluded(self)` | 검증: non 401 and acknowledged or judged records are excluded |
| `Blocked401RecoveryTests.test_unknown_reaction_does_not_claim_another_record(self)` | 검증: unknown reaction does not claim another record |
| `Blocked401RecoveryTests.test_active_lease_is_rejected_and_expired_lease_can_be_replaced(self)` | 검증: active lease is rejected and expired lease can be replaced |
| `Blocked401RecoveryTests.test_second_claim_does_not_take_an_existing_lease(self)` | 검증: second claim does not take an existing lease |
| `Blocked401RecoveryTests.test_rival_claim_and_late_receipt_are_not_overwritten(self)` | 검증: rival claim and late receipt are not overwritten |
| `Blocked401RecoveryTests.test_rival_claim_and_late_receipt_are_not_overwritten.competing_update(queryset, **values)` | 테스트 보조: competing update |
| `Blocked401RecoveryTests.test_again_401_blocks_without_automatic_retry_or_new_request(self)` | 검증: again 401 blocks without automatic retry or new request |
| `Blocked401RecoveryTests.test_success_reuses_the_same_request_and_acknowledges_it(self)` | 검증: success reuses the same request and acknowledges it |
| `Blocked401RecoveryTests.test_snapshot_failure_rolls_back_claim(self)` | 검증: snapshot failure rolls back claim |
| `Blocked401RecoveryTests.test_outer_transaction_is_rejected_before_claim(self)` | 검증: outer transaction is rejected before claim |
| `Blocked401RecoveryTests.test_invalid_inputs_are_rejected_without_mutation(self)` | 검증: invalid inputs are rejected without mutation |
| `RetryMigrationTests(TransactionTestCase)` | 기존 0001 상태에서 실제 0002 전환 후 미확인 기록의 회수를 검사한다. |
| `RetryMigrationTests.test_migration_recovers_pending_and_preserves_terminal_rows(self)` | 검증: migration recovers pending and preserves terminal rows |
| `OwnerMigrationTests(TransactionTestCase)` | 테스트 사례·도우미 묶음 |
| `OwnerMigrationTests.test_migration_preserves_existing_decision_and_impression_payloads(self)` | 검증: migration preserves existing decision and impression payloads |
