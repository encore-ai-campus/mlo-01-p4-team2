# scripts/migrations/0002_impression_retry.py

- 경로: [ad-bridge/scripts/migrations/0002_impression_retry.py](../../../../scripts/migrations/0002_impression_retry.py)
- 기능: 재전송 필드·인덱스 추가와 기존 대기 기록 복구
- 호출·연결: Django migrate → AddField / AddIndex / RunPython(recover_pending)
- 파라미터: apps: 과거 모델 레지스트리; schema_editor: 적용 DB 연결; 선행 마이그레이션: 0001_initial.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `recover_pending(apps, schema_editor)` | 이전 버전의 미확인 기록도 같은 반응 ID로 다시 처리할 수 있게 만든다. |
| `Migration(migrations.Migration)` | 스키마 변경 작업 정의 |
