# test/results/test_client.py

- 경로: [ad-bridge/test/results/test_client.py](../../../../test/results/test_client.py)
- 기능: GET 계약은 가짜 HTTP 응답으로 검사한다.
- 호출·연결: ad_bridge: AdsClient, AdsConfig, AdsHTTPError, AdsResponseError, AdsTransportError; test.support.impression_fakes: DECISION_ID, REACTION_ID, SUBJECT_ID, OCCURRED_AT
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `ResultClientTests(unittest.TestCase)` | 테스트 사례·도우미 묶음 |
| `ResultClientTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `ResultClientTests.response(self, *, status=200, raw=None, content_type="application/json")` | 테스트 보조: response |
| `ResultClientTests.lookup(self)` | 테스트 보조: lookup |
| `ResultClientTests.test_request(self)` | 검증: request |
| `ResultClientTests.test_states(self)` | 검증: states |
| `ResultClientTests.test_uuid(self)` | 검증: uuid |
| `ResultClientTests.test_schema(self)` | 검증: schema |
| `ResultClientTests.test_response(self)` | 검증: response |
| `ResultClientTests.test_http_error(self)` | 검증: http error |
| `ResultClientTests.test_transport(self)` | 검증: transport |
| `ResultClientTests.test_secret(self)` | 검증: secret |
