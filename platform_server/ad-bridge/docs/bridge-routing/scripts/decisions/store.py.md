# scripts/decisions/store.py

- 경로: [ad-bridge/scripts/decisions/store.py](../../../../scripts/decisions/store.py)
- 기능: 개인 광고 발급 기록과 소유권 보관
- 호출·연결: decisions.service → remember_decision → IssuedDecision.objects.get_or_create
- 파라미터: decision: 광고 결정; subject_id: 발급 당시 가명; owner_id: 내부 사용자 UUID; value: UUID 또는 UTC 시각 입력.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `_uuid(value: str \| UUID) -> UUID` | UUID 형식을 검증하고 정규화 |
| `_utc_time(value: str) -> datetime` | UTC Z 시각 파싱 |
| `remember_decision(decision: Decision, subject_id: str \| UUID, *, owner_id: str \| UUID) -> None` | 개인 광고 발급 기록 생성·기존 값 불변 확인 |
