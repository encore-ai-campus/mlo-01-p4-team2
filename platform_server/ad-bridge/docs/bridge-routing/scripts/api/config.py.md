# scripts/api/config.py

- 경로: [ad-bridge/scripts/api/config.py](../../../../scripts/api/config.py)
- 기능: 광고 연결 환경변수 검증
- 호출·연결: 게임 뷰·관리 명령 → AdsConfig.from_env → AdsConfig.__post_init__
- 파라미터: base_url: /api/media/v1을 포함한 API 주소; media_key: 매체 키; timeout: 제한 초; subject_secret: HMAC 비밀; environ: 선택적 환경변수 매핑.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `AdsConfig` | 직접 주입하거나 from_env()로 생성하는 광고 통신 설정.; 필드: base_url, media_key, timeout, subject_secret |
| `AdsConfig.__post_init__(self) -> None` | 주소·매체 키·가명 비밀·양수 타임아웃 검증 |
| `AdsConfig.from_env(cls, environ: Mapping[str, str] \| None = None) -> "AdsConfig"` | 호출 시 환경변수를 읽는다. 테스트에서는 별도 매핑을 전달할 수 있다. |
