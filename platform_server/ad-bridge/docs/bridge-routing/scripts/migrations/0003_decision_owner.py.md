# scripts/migrations/0003_decision_owner.py

- 경로: [ad-bridge/scripts/migrations/0003_decision_owner.py](../../../../scripts/migrations/0003_decision_owner.py)
- 기능: 발급 기록에 내부 소유자 UUID 추가
- 호출·연결: Django migrate → Migration.operations → AddField
- 파라미터: 선행: 0002_impression_retry; owner_id는 nullable UUID로 추가하며 기존 값은 추정해 채우지 않는다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `Migration(migrations.Migration)` | 스키마 변경 작업 정의 |
