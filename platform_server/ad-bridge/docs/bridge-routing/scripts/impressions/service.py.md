# scripts/impressions/service.py

- 경로: [ad-bridge/scripts/impressions/service.py](../../../../scripts/impressions/service.py)
- 기능: 최초 노출 예약 후 한 번 전송
- 호출·연결: 게임 뷰 → report_impression_once → store.reserve_impression → send_once
- 파라미터: client: 광고 API; store: 저장소; decision_id: 결정; owner_id: 내부 사용자; occurred_at: 최초 알림 UTC 시각; policy: 재시도 정책; now: 시계 함수.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `report_impression_once( *, client, store, decision_id, owner_id, occurred_at: str, policy: RetryPolicy \| None = None, now=utc_now, ) -> ImpressionReport` | 최초 알림만 즉시 전송한다. 중복 알림의 재전송은 별도 작업자가 담당한다. |
