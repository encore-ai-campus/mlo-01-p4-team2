# test/results/test_service.py

- 경로: [ad-bridge/test/results/test_service.py](../../../../test/results/test_service.py)
- 기능: 조회 서비스의 소유권·실패·저장 순서를 격리해 검사한다.
- 호출·연결: ad_bridge.api.errors: AdsHTTPError, AdsResponseError, AdsTransportError; ad_bridge.impressions.store: StoredImpression, DecisionNotFound, ImpressionNotReady; ad_bridge.results.service: refresh_result; test.support.impression_fakes: DECISION_ID, REACTION_ID, SUBJECT_ID, OCCURRED_AT, received_reaction
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `ResultServiceTests(TestCase)` | 테스트 사례·도우미 묶음 |
| `ResultServiceTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `ResultServiceTests.refresh(self)` | 테스트 보조: refresh |
| `ResultServiceTests.check_state(self, state)` | 테스트 보조: check state |
| `ResultServiceTests.test_received(self)` | 검증: received |
| `ResultServiceTests.test_accepted(self)` | 검증: accepted |
| `ResultServiceTests.test_rejected(self)` | 검증: rejected |
| `ResultServiceTests.test_owner(self)` | 검증: owner |
| `ResultServiceTests.test_pending(self)` | 검증: pending |
| `ResultServiceTests.test_lookup_error(self)` | 검증: lookup error |
| `ResultServiceTests.test_mismatch(self)` | 검증: mismatch |
| `ResultServiceTests.test_save_error(self)` | 검증: save error |
