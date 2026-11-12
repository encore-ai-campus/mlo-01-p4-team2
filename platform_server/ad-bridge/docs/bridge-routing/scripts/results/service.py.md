# scripts/results/service.py

- 경로: [ad-bridge/scripts/results/service.py](../../../../scripts/results/service.py)
- 기능: 접수된 노출 판정 조회·대조·저장
- 호출·연결: 게임 뷰 → refresh_result → store.get_impression → client.get_reaction → store.update_result
- 파라미터: client: 광고 API; store: 노출 저장소; decision_id: 결정 UUID; owner_id: 내부 소유자 UUID.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `refresh_result(*, client, store, decision_id: str \| UUID, owner_id: str \| UUID) -> StoredImpression` | 내부 소유자로 권한을 확인하고 저장된 광고 가명으로 결과를 대조한다. |
