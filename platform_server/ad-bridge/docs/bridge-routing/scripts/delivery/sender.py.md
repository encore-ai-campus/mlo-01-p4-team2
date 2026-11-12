# scripts/delivery/sender.py

- 경로: [ad-bridge/scripts/delivery/sender.py](../../../../scripts/delivery/sender.py)
- 기능: 노출 한 번 전송 후 상태·재시도 일정 저장
- 호출·연결: 노출 서비스·작업자·401 복구 → send_once → client.report_impression / store.finish_impression
- 파라미터: client: 광고 API; store: 노출 저장소; record: 확보한 임대 스냅샷; policy: 재시도 정책; now: 현재 시각 함수.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `ImpressionReport` | 데이터·설정·예외 클래스; 필드: record, created, error, upstream_status, retry_after |
| `send_once(*, client, store, record: StoredImpression, policy: RetryPolicy, now) -> ImpressionReport` | 커밋된 임대 하나를 전송한다. 오류 원문 대신 정해진 진단 코드만 저장한다. |
