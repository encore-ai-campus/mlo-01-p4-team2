# scripts/api/errors.py

- 경로: [ad-bridge/scripts/api/errors.py](../../../../scripts/api/errors.py)
- 기능: HTTP·연결·응답 오류 구분
- 호출·연결: API 클라이언트 → 예외 발생 → 게임 뷰·서비스에서 처리
- 파라미터: status: HTTP 상태; code: 오류 코드; request_id: 추적 ID; retry_after: 재시도 대기 초; message: 공개 오류 설명; acknowledged: 2xx 접수 확인 여부.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `AdsError(Exception)` | 광고 통신 실패의 공통 예외. 원본 요청과 인증 키는 보관하지 않는다. |
| `AdsHTTPError(AdsError)` | 데이터·설정·예외 클래스 |
| `AdsHTTPError.__init__(self, status: int, *, code: str \| None = None, request_id: str \| None = None, retry_after: int \| None = None) -> None` | HTTP 상태·코드·추적 ID·대기 초 보관 |
| `AdsTransportError(AdsError)` | 타임아웃·연결 실패. 서버가 요청을 처리했는지는 확인할 수 없다. |
| `AdsResponseError(AdsError)` | 데이터·설정·예외 클래스 |
| `AdsResponseError.__init__(self, message: str, *, request_id: str \| None = None, acknowledged: bool = False) -> None` | 응답 오류와 2xx 접수 여부 보관 |
