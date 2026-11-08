# test/integration_tests/test_provider.py

- 경로: [ad-bridge/test/integration_tests/test_provider.py](../../../../test/integration_tests/test_provider.py)
- 기능: ads-server manage.py에서 실행한다. 개발 DB의 광고·계정·매체 키는 사용하지 않는다.
- 호출·연결: ad_bridge: AdsClient, AdsConfig, AdsHTTPError
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `ProviderDecisionTests(LiveServerTestCase)` | 테스트 사례·도우미 묶음 |
| `ProviderDecisionTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `ProviderDecisionTests.add_ad(self, slot)` | 테스트 보조: add ad |
| `ProviderDecisionTests.test_real_ad_response_is_stored_and_does_not_charge(self)` | 검증: real ad response is stored and does not charge |
| `ProviderDecisionTests.test_real_no_ad_response(self)` | 검증: real no ad response |
| `ProviderDecisionTests.test_real_authentication_and_subject_errors(self)` | 검증: real authentication and subject errors |
| `ProviderDecisionTests.test_real_shared_response_preserves_person_placeholder(self)` | 검증: real shared response preserves person placeholder |
| `ProviderDecisionTests.test_redirect_does_not_forward_key_or_reissue_request(self)` | 검증: redirect does not forward key or reissue request |
| `ProviderDecisionTests.test_redirect_does_not_forward_key_or_reissue_request.redirecting(request)` | 테스트 보조: redirecting |
| `ProviderDecisionTests.test_redirect_does_not_forward_key_or_reissue_request.destination(request)` | 테스트 보조: destination |
| `ProviderDecisionTests.test_redirect_does_not_forward_key_or_reissue_request.RedirectUrls` | 테스트 사례·도우미 묶음; 필드: urlpatterns |

광고 서버의 billing·campaigns·masters·records와 tests.factories에 의존하며 광고 서버 테스트 환경에서 실행한다.
