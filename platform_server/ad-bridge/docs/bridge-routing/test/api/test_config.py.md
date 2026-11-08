# test/api/test_config.py

- 경로: [ad-bridge/test/api/test_config.py](../../../../test/api/test_config.py)
- 기능: 외부 연결 없이 광고 연결 설정의 입력 경계와 비밀 보호를 검사한다.
- 호출·연결: ad_bridge.api.config: AdsConfig
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `AdsConfigTests(unittest.TestCase)` | 테스트 사례·도우미 묶음 |
| `AdsConfigTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `AdsConfigTests.test_valid_settings_normalize_base_url_and_timeout(self)` | 검증: valid settings normalize base url and timeout |
| `AdsConfigTests.test_each_environment_variable_is_required_and_nonempty(self)` | 검증: each environment variable is required and nonempty |
| `AdsConfigTests.test_explicit_mapping_is_not_mutated_or_filled_from_process_environment(self)` | 검증: explicit mapping is not mutated or filled from process environment |
| `AdsConfigTests.test_from_env_reads_current_process_values_when_mapping_is_omitted(self)` | 검증: from env reads current process values when mapping is omitted |
| `AdsConfigTests.test_invalid_api_urls_are_rejected_before_any_request(self)` | 검증: invalid api urls are rejected before any request |
| `AdsConfigTests.test_invalid_media_keys_are_rejected_without_leaking_the_key(self)` | 검증: invalid media keys are rejected without leaking the key |
| `AdsConfigTests.test_timeout_must_be_finite_positive_and_not_boolean(self)` | 검증: timeout must be finite positive and not boolean |
| `AdsConfigTests.test_config_representation_does_not_expose_media_key(self)` | 검증: config representation does not expose media key |
| `AdsConfigTests.test_subject_secret_is_required_and_invalid_values_are_redacted(self)` | 검증: subject secret is required and invalid values are redacted |
