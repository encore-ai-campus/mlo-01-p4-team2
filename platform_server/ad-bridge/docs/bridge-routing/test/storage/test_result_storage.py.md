# test/storage/test_result_storage.py

- 경로: [ad-bridge/test/storage/test_result_storage.py](../../../../test/storage/test_result_storage.py)
- 기능: 임시 SQLite에서 판정 갱신과 전송 기록 보존을 확인한다.
- 호출·연결: ad_bridge.models: IssuedDecision, Impression; ad_bridge.impressions.store: get_impression, update_result, DecisionNotFound, ImpressionNotReady; test.support.impression_fakes: DECISION_ID, REACTION_ID, SUBJECT_ID, OCCURRED_AT
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `ResultStorageTests(TestCase)` | 테스트 사례·도우미 묶음 |
| `ResultStorageTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `ResultStorageTests.update(self, status="ACCEPTED", reason=None)` | 테스트 보조: update |
| `ResultStorageTests.test_save(self)` | 검증: save |
| `ResultStorageTests.test_terminal(self)` | 검증: terminal |
| `ResultStorageTests.test_owner(self)` | 검증: owner |
| `ResultStorageTests.test_pending(self)` | 검증: pending |
| `ResultStorageTests.test_failure(self)` | 검증: failure |
| `ResultStorageTests.test_race(self)` | 검증: race |
| `ResultStorageTests.test_race.raced(**kwargs)` | 테스트 보조: raced |
