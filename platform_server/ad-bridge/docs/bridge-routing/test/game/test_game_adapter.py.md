# test/game/test_game_adapter.py

- 경로: [ad-bridge/test/game/test_game_adapter.py](../../../../test/game/test_game_adapter.py)
- 기능: DB나 광고 서버 없이 실제 Django 요청 경계와 브리지 호출을 검사한다.
- 호출·연결: ad_bridge: AdsConfig; ad_bridge.game.views: request_decision, report_impression, reaction_result, _subject_id, _owner_id; ad_bridge.api.errors: AdsHTTPError, AdsResponseError, AdsTransportError; ad_bridge.api.types: Ad, Decision; ad_bridge.clicks: click_target; ad_bridge.impressions: store as impression_store
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `GameAdapterTests(SimpleTestCase)` | 테스트 사례·도우미 묶음 |
| `GameAdapterTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `GameAdapterTests.request(self, body=None)` | 테스트 보조: request |
| `GameAdapterTests.test_success_uses_server_identity_and_returns_only_public_fields(self)` | 검증: success uses server identity and returns only public fields |
| `GameAdapterTests.test_no_ad_is_a_successful_decision(self)` | 검증: no ad is a successful decision |
| `GameAdapterTests.test_decision_storage_failure_does_not_return_an_untracked_ad(self)` | 검증: decision storage failure does not return an untracked ad |
| `GameAdapterTests.test_authentication_and_post_are_required_before_upstream_call(self)` | 검증: authentication and post are required before upstream call |
| `GameAdapterTests.test_csrf_middleware_rejects_missing_token_and_accepts_matching_token(self)` | 검증: csrf middleware rejects missing token and accepts matching token |
| `GameAdapterTests.test_missing_slot_or_profile_does_not_call_upstream(self)` | 검증: missing slot or profile does not call upstream |
| `GameAdapterTests.test_invalid_environment_or_uuid_is_a_configuration_error(self)` | 검증: invalid environment or uuid is a configuration error |
| `GameAdapterTests.test_upstream_http_error_preserves_status_and_request_id(self)` | 검증: upstream http error preserves status and request id |
| `GameAdapterTests.test_transport_and_invalid_response_fail_safely_without_retry(self)` | 검증: transport and invalid response fail safely without retry |
| `GameAdapterTests.test_actual_client_rejects_invalid_server_uuids_before_http(self)` | 검증: actual client rejects invalid server uuids before http |
| `GameAdapterTests.test_actual_client_sends_each_logged_in_profile_to_the_configured_endpoint(self)` | 검증: actual client sends each logged in profile to the configured endpoint |
| `GameAdapterTests.test_subject_pseudonym_is_stable_and_normalizes_uuid_input(self)` | 검증: subject pseudonym is stable and normalizes uuid input |
| `GameAdapterTests.test_missing_or_invalid_subject_secret_blocks_all_game_calls(self)` | 검증: missing or invalid subject secret blocks all game calls |
| `GameAdapterTests.test_result_lookup_uses_internal_owner(self)` | 검증: result lookup uses internal owner |
| `GameAdapterTests.test_result_response_hides_both_saved_and_refreshed_judgments(self)` | 검증: result response hides both saved and refreshed judgments |
| `GameAdapterTests.test_result_pending_delivery_returns_local_state_without_upstream_lookup(self)` | 검증: result pending delivery returns local state without upstream lookup |

이 파일은 `ad_bridge.clicks.click_target`도 import하지만 대응하는 clicks.py는 현재 scripts/에 없다. 이 폴더만으로 테스트가 실행된다고 가정하지 않는다.
