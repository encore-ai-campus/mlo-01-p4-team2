# scripts/delivery/worker.py

- 경로: [ad-bridge/scripts/delivery/worker.py](../../../../scripts/delivery/worker.py)
- 기능: 기한이 된 노출을 배치 재전송
- 호출·연결: 관리 명령 → dispatch_due_impressions → store.claim_due_impression → send_once
- 파라미터: client: 광고 API; store: 저장소; limit: 최대 건수(100); policy: 재시도 정책; now: 시계 함수; should_stop: 종료 여부 함수.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `dispatch_due_impressions( *, client, store, limit: int = 100, policy: RetryPolicy \| None = None, now=utc_now, should_stop=lambda: False, ) -> dict[str, int]` | 기한이 된 미접수 기록 또는 임대가 만료된 기록을 한 묶음 전송한다. |
