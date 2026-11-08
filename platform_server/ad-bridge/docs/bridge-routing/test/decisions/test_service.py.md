# test/decisions/test_service.py

- 경로: [ad-bridge/test/decisions/test_service.py](../../../../test/decisions/test_service.py)
- 기능: 광고 결정 요청과 소유권 저장의 순서를 외부 연결 없이 검사한다.
- 호출·연결: ad_bridge.api.types: Ad, Decision; ad_bridge.decisions.service: create_decision
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `DecisionServiceTests(unittest.TestCase)` | 테스트 사례·도우미 묶음 |
| `DecisionServiceTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `DecisionServiceTests.create(self)` | 테스트 보조: create |
| `DecisionServiceTests.test_request_is_saved_before_decision_is_returned(self)` | 검증: request is saved before decision is returned |
| `DecisionServiceTests.test_storage_failure_is_propagated_without_requesting_again(self)` | 검증: storage failure is propagated without requesting again |
| `DecisionServiceTests.test_no_ad_decision_is_also_saved(self)` | 검증: no ad decision is also saved |
