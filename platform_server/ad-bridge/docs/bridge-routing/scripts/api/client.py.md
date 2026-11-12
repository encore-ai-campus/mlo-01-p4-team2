# scripts/api/client.py

- 경로: [ad-bridge/scripts/api/client.py](../../../../scripts/api/client.py)
- 기능: 광고 결정 POST·노출 PUT·판정 GET
- 호출·연결: 결정 서비스·전송 함수·결과 서비스 → AdsClient → urllib.request
- 파라미터: config: 연결 설정; slot_id: 광고 슬롯; subject_id: 광고 사용자 식별자; decision_id: 결정 ID; reaction_id: 반응 ID; occurred_at: UTC Z 시각; body: 응답 바이트; field: 오류 표시용 필드명; lookup: 조회 응답 검증 여부.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `_NoRedirect(HTTPRedirectHandler)` | 데이터·설정·예외 클래스 |
| `_NoRedirect.redirect_request(self, req, fp, code, msg, headers, newurl)` | HTTP 리다이렉트 차단 |
| `_uuid(value: str \| UUID, field: str) -> str` | UUID 형식을 검증하고 정규화 |
| `_utc(value: str, field: str) -> datetime` | UTC Z 시각 검증 |
| `_url(value: str, *, https_only: bool = False) -> bool` | 광고 URL 형식·프로토콜 검증 |
| `_parse_decision(body: bytes, slot_id: str, audience: str, request_id: str \| None) -> Decision` | 필수 값만 읽고 추가된 응답 필드는 무시한다. 잘못된 응답은 광고 없음과 구분한다. |
| `AdsClient` | 데이터·설정·예외 클래스 |
| `AdsClient.__init__(self, config: AdsConfig) -> None` | 설정 보관 및 리다이렉트를 막는 HTTP opener 생성 |
| `AdsClient._safe_metadata(self, value: str \| None) -> str \| None` | 매체 키를 가리고 진단 길이 제한 |
| `AdsClient._http_error(self, response) -> AdsHTTPError` | HTTP 오류를 공개 상태·추적 ID로 변환 |
| `AdsClient.create_decision(self, slot_id: str \| UUID, subject_id: str \| UUID \| None = None) -> Decision` | POST /decisions로 광고 결정 한 번 요청 |
| `AdsClient._parse_reaction(self, body: bytes, decision_id: str, reaction_id: str, occurred_at: str \| None, subject_id: str \| None, request_id: str \| None, *, lookup: bool = False) -> Reaction` | 보낸 반응과 일치하는 필수 값은 검증하고 새 필드·거부 사유는 허용한다. |
| `AdsClient.report_impression(self, *, decision_id: str \| UUID, reaction_id: str \| UUID, occurred_at: str, subject_id: str \| UUID \| None = None) -> Reaction` | PUT /decisions/{decision}/reactions/{reaction}으로 노출 한 번 보고 |
| `AdsClient.get_reaction(self, *, decision_id: str \| UUID, reaction_id: str \| UUID) -> Reaction` | GET /decisions/{decision}/reactions/{reaction}으로 판정 조회 |

API 기준 주소는 `AdsConfig.base_url`이며 인증 헤더는 `X-Media-Key`다. 이 클라이언트에서 자동 재시도하지 않는다.
