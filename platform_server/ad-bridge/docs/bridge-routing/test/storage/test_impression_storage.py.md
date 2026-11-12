# test/storage/test_impression_storage.py

- 경로: [ad-bridge/test/storage/test_impression_storage.py](../../../../test/storage/test_impression_storage.py)
- 기능: 메모리 SQLite로 검사하는 저장소 통합 테스트. 단위 테스트 묶음과 분리한다.
- 호출·연결: ad_bridge.decisions.store: remember_decision; ad_bridge.models: Impression, IssuedDecision; ad_bridge.api.types: Ad, Decision; ad_bridge.api.errors: AdsHTTPError; ad_bridge.delivery.worker: dispatch_due_impressions; ad_bridge.impressions.service: report_impression_once; ad_bridge.impressions.store: get_impression; ad_bridge.results.service: refresh_result; ad_bridge.impressions: store as impression_store; test.support.impression_fakes: received_reaction
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `ImpressionStorageTests(TestCase)` | 테스트 사례·도우미 묶음 |
| `ImpressionStorageTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `ImpressionStorageTests.test_remember_decision_persists_owner_and_is_idempotent(self)` | 검증: remember decision persists owner and is idempotent |
| `ImpressionStorageTests.test_existing_owner_and_decision_fields_cannot_be_overwritten(self)` | 검증: existing owner and decision fields cannot be overwritten |
| `ImpressionStorageTests.test_shared_decision_is_rejected_before_storage(self)` | 검증: shared decision is rejected before storage |
| `ImpressionStorageTests.test_missing_and_other_user_decisions_are_not_disclosed(self)` | 검증: missing and other user decisions are not disclosed |
| `ImpressionStorageTests.test_no_ad_decision_cannot_create_impression(self)` | 검증: no ad decision cannot create impression |
| `ImpressionStorageTests.test_first_reservation_fixes_uuid_subject_and_original_timestamp(self)` | 검증: first reservation fixes uuid subject and original timestamp |
| `ImpressionStorageTests.test_duplicate_reservation_keeps_original_request_after_reload(self)` | 검증: duplicate reservation keeps original request after reload |
| `ImpressionStorageTests.test_unconfirmed_impression_is_preserved_without_creating_a_new_request(self)` | 검증: unconfirmed impression is preserved without creating a new request |
| `ImpressionStorageTests.test_finish_changes_only_result_and_does_not_overwrite_first_outcome(self)` | 검증: finish changes only result and does not overwrite first outcome |
| `ImpressionStorageTests.test_acknowledgement_can_be_saved_without_a_parseable_reaction(self)` | 검증: acknowledgement can be saved without a parseable reaction |
| `ImpressionStorageTests.test_database_unique_constraint_prevents_a_second_impression(self)` | 검증: database unique constraint prevents a second impression |
| `ImpressionStorageTests.test_failed_transaction_leaves_no_partial_impression(self)` | 검증: failed transaction leaves no partial impression |
| `ImpressionStorageTests.test_outer_transaction_is_rejected_before_reserving(self)` | 검증: outer transaction is rejected before reserving |
| `ImpressionStorageTests.test_invalid_timestamp_is_rejected_without_a_record(self)` | 검증: invalid timestamp is rejected without a record |
| `ImpressionStorageTests.test_invalid_finish_metadata_does_not_change_pending_request(self)` | 검증: invalid finish metadata does not change pending request |
| `ImpressionStorageTests.test_ad_subject_cannot_be_used_as_internal_owner(self)` | 검증: ad subject cannot be used as internal owner |
| `ImpressionStorageTests.test_owner_is_immutable_and_legacy_owner_is_not_adopted(self)` | 검증: owner is immutable and legacy owner is not adopted |
| `ImpressionStorageTests.test_mismatched_saved_subject_is_not_returned(self)` | 검증: mismatched saved subject is not returned |
| `ImpressionStorageTests.test_retry_uses_saved_subject_and_preserves_request(self)` | 검증: retry uses saved subject and preserves request |
| `ImpressionStorageTests.test_result_uses_owner_for_access_and_saved_subject_for_validation(self)` | 검증: result uses owner for access and saved subject for validation |
