# test/game/test_game_impression.py

- 경로: [ad-bridge/test/game/test_game_impression.py](../../../../test/game/test_game_impression.py)
- 기능: 게임 요청부터 실제 노출 서비스까지 검사하고 DB·HTTP 경계만 대체한다.
- 호출·연결: ad_bridge.game.views: report_impression; ad_bridge.api.errors: AdsHTTPError, AdsResponseError, AdsTransportError
- 파라미터: self: 테스트 인스턴스. 테스트별 fixture와 mock을 사용하며 추가 helper 인자는 아래 시그니처에 표시한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `GameImpressionTests(SimpleTestCase)` | 테스트 사례·도우미 묶음 |
| `GameImpressionTests.setUp(self)` | 테스트 입력과 mock 초기화 |
| `GameImpressionTests.request(self, body=None)` | 테스트 보조: request |
| `GameImpressionTests.report(self, request=None, decision_id=DECISION_ID)` | 테스트 보조: report |
| `GameImpressionTests.test_server_identity_time_and_reaction_ignore_browser_forgery(self)` | 검증: server identity time and reaction ignore browser forgery |
| `GameImpressionTests.test_duplicate_returns_existing_receipt_without_second_http(self)` | 검증: duplicate returns existing receipt without second http |
| `GameImpressionTests.test_report_and_duplicate_hide_judgment_but_keep_it_in_storage(self)` | 검증: report and duplicate hide judgment but keep it in storage |
| `GameImpressionTests.test_secret_rotation_keeps_the_decision_subject(self)` | 검증: secret rotation keeps the decision subject |
| `GameImpressionTests.test_anonymous_request_requires_login_without_sending(self)` | 검증: anonymous request requires login without sending |
| `GameImpressionTests.test_get_is_rejected_without_sending(self)` | 검증: get is rejected without sending |
| `GameImpressionTests.test_csrf_middleware_rejects_missing_token_and_accepts_matching_token(self)` | 검증: csrf middleware rejects missing token and accepts matching token |
| `GameImpressionTests.test_foreign_or_unknown_decision_is_404_without_sending(self)` | 검증: foreign or unknown decision is 404 without sending |
| `GameImpressionTests.test_decision_without_ad_is_409_without_sending(self)` | 검증: decision without ad is 409 without sending |
| `GameImpressionTests.test_storage_app_missing_returns_503_without_sending(self)` | 검증: storage app missing returns 503 without sending |
| `GameImpressionTests.test_missing_profile_uuid_returns_409_without_sending(self)` | 검증: missing profile uuid returns 409 without sending |
| `GameImpressionTests.test_invalid_profile_uuid_returns_503_without_sending(self)` | 검증: invalid profile uuid returns 503 without sending |
| `GameImpressionTests.test_http_failure_returns_state_and_retry_metadata_without_resending(self)` | 검증: http failure returns state and retry metadata without resending |
| `GameImpressionTests.test_blocked_http_failure_remains_502_on_duplicate(self)` | 검증: blocked http failure remains 502 on duplicate |
| `GameImpressionTests.test_transport_failure_is_unconfirmed_and_does_not_expose_error_text(self)` | 검증: transport failure is unconfirmed and does not expose error text |
| `GameImpressionTests.test_acknowledged_invalid_body_returns_202_without_claiming_acceptance(self)` | 검증: acknowledged invalid body returns 202 without claiming acceptance |
| `GameImpressionTests.test_reservation_database_failure_is_503_before_http(self)` | 검증: reservation database failure is 503 before http |
| `GameImpressionTests.test_database_failure_after_receipt_preserves_uncertainty_and_reservation(self)` | 검증: database failure after receipt preserves uncertainty and reservation |
| `GameImpressionTests.test_route_accepts_uuid_and_rejects_malformed_decision_path(self)` | 검증: route accepts uuid and rejects malformed decision path |
