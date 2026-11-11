# 이벤트 스토리·운영 제어 검증 — 2026-11-03

기준은 GitHub `event`의 `cd5b19d`, 작업 브랜치는 `feat/27`, PR 대상은 `event`임. 원본 `C:\playdatametavers_v2 - event`는 수정하지 않고 별도 체크아웃에 새 변경만 통합함.

## 실행 결과

- Backend 관련 검사 283개 실행: 276개 통과·환경 의존 7개 생략임. 스토리·일시정지, 계정, 학습, eventing, 빼빼로·리필 소켓·근접 공유·맵 검사 포함함.
- Frontend Django 15개 통과함.
- Node 화면 로직 119개 통과함. 에셋 앞뒤 배치와 일시정지 revision·입력/명령 차단 검사 포함함.
- `makemigrations --check --dry-run`: 변경 없음임. 신규 앱의 initial 마이그레이션 파일 2개가 모델과 일치함.
- 임시 SQLite에 마이그레이션 적용 후 실제 브라우저로 검사함. 운영 DB에 적용하지 않음.
- Frontend `collectstatic --clear`: 166개 수집 후 원본과 해시 대조 통과함. 화면 정적 파일은 Backend 수집물이 아닌 Frontend 수집물을 사용함.
- 이벤트 JSON에서 화면·서버 충돌 맵을 재생성했고 생성 파일 변경 없음 확인함.
- 실제 Chrome: 로그인 302, 이동·채팅 WS 연결, E키 리필 10개, Ctrl 방어·Shift 달리기, 키 설정/복원, 옥상 빠른 이동 확인함.
- 미션 API가 DEBUG 미리보기에서 열림 확인함. 관리자 UI로 정지하면 미션 API가 423으로 차단되고 재개하면 정지 화면이 해제됨 확인함.
- JS 오류·정적 파일 실패 0건임. 접속 직후 채팅 입력 상태를 해제한 뒤 맵 키 조작을 검사함.

## 검증 범위와 제한

- Backend 명령: `python manage.py test campus_control event_story accounts learning eventing campus.test_pepero_game campus.test_pepero_refill_socket campus.test_nearby_interest campus.test_event_map --settings=deploy.onprem.test_settings --noinput`임.
- 임시 데이터·가짜 테스트 계정만 사용함. 운영 MySQL·Redis·Kafka·외부 Google 서비스·AWS 배포에 연결하지 않음.
- 전체 Backend 회귀·실제 WSL/Nginx 두 PC·운영 MySQL 마이그레이션·실제 부하·이벤트 전환/복귀는 이번 검사 범위 밖임.
- 기존 [2026-10-26 전체 회귀 미통과 기록](VALIDATION-20261026.md)은 해결됐다고 간주하지 않음. 이번 관련 검사 통과를 전체 회귀 통과로 표시하지 않으며 PR은 Draft로 검토함.

## 통합 시 유지한 사항

- 역할별 ASGI·Nginx·실행기 설정과 기존 event의 테스트 격리 수정을 보존함.
- 새 JS/CSS에도 Frontend의 기존 콘텐츠 해시 갱신을 사용함. 고정 날짜 쿼리로 캐시 갱신을 막지 않음.
- 기존 계단 검사에서 입력 패킷에 추가된 freezeRevision을 허용하고, 실제 정지·재개 입력 계약을 별도 검사함.
- 스토리·일시정지 신규 앱은 서비스 DB 테이블 4개를 추가함. 운영 전환 절차는 [MAP_SWITCHING.md](MAP_SWITCHING.md), 관리자 사용은 [FREEZE_GUIDE.md](../../FREEZE_GUIDE.md)를 따름.
