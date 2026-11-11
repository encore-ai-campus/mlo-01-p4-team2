# 로컬 이벤트맵 PostgreSQL 통합

- 최신 event `da1dab0`와 로컬 이벤트 기능을 합친 소스임. GitHub PR 갱신·event 병합·운영 배포는 별도임.
- `backend/db.sqlite3`는 이전 데이터 그대로 보존함. 최신 UUID 구조에 그대로 migrate하지 않음.
- 로컬 실행 전 requirements.txt 의존성을 설치하고 backend/.env.example을 참고해 새 PostgreSQL의 POSTGRES_DB·USER·PASSWORD·HOST·PORT를 지정해야 함. 검증용 임시 컨테이너는 일반 실행 DB로 사용하지 않음.
- 기존 데이터를 옮기려면 RUN_GUIDE.md의 export_platform_data/import_platform_data 절차를 검토함. 이번 작업은 기존 사용자 데이터 이전을 수행하지 않음.
- 검증 결과는 deploy/onprem/VALIDATION-20261103-PG.md 참고함.
