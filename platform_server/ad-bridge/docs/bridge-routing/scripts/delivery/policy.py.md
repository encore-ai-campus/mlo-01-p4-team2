# scripts/delivery/policy.py

- 경로: [ad-bridge/scripts/delivery/policy.py](../../../../scripts/delivery/policy.py)
- 기능: 재시도 간격과 임대 시간 계산
- 호출·연결: 노출 서비스·재전송 작업자 → RetryPolicy / utc_now
- 파라미터: base_delay: 최초 대기 초; max_delay: 지수 대기 상한; lease_seconds: 임대 초; attempts: 시도 횟수; retry_after: 서버가 요구한 최소 대기 초.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `utc_now() -> datetime` | 현재 UTC aware datetime 반환 |
| `RetryPolicy` | 데이터·설정·예외 클래스; 필드: base_delay, max_delay, lease_seconds |
| `RetryPolicy.__post_init__(self)` | 양수 시간과 대기 상한 검증 |
| `RetryPolicy.delay_for(self, attempts: int, retry_after: int \| None = None) -> float` | 지수 대기와 Retry-After로 다음 대기 초 계산 |
