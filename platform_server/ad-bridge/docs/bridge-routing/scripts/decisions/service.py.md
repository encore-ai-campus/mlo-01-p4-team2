# scripts/decisions/service.py

- 경로: [ad-bridge/scripts/decisions/service.py](../../../../scripts/decisions/service.py)
- 기능: 광고 발급 후 소유권 저장
- 호출·연결: 게임 뷰 → create_decision → client.create_decision → store.remember_decision
- 파라미터: client: 광고 API; store: 발급 저장소; slot_id: 슬롯; subject_id: 광고 가명; owner_id: 내부 사용자 UUID.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `create_decision(*, client, store, slot_id, subject_id, owner_id) -> Decision` | 광고 API 호출 후 소유권 저장이 끝난 결정만 반환 |
