# scripts/migrations/0001_initial.py

- 경로: [ad-bridge/scripts/migrations/0001_initial.py](../../../../scripts/migrations/0001_initial.py)
- 기능: 광고 발급·노출 테이블 생성
- 호출·연결: Django migrate → Migration.operations → CreateModel
- 파라미터: dependencies 없음; IssuedDecision·Impression 스키마를 생성한다. 직접 호출할 함수 인자 없음.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `Migration(migrations.Migration)` | 스키마 변경 작업 정의; 필드: initial |
