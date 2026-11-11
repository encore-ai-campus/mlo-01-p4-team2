# 프론트·백엔드 실행 가이드

기준일: 2026-11-03. 대상 저장소의 `dev`를 받은 뒤, 저장소 루트에서 플랫폼 폴더로 이동함.

```powershell
cd platform_server
```

이후 명령과 `루트` 표현은 `platform_server/`를 기준으로 함. 가상환경도 이 폴더에 생성함.

## 함께 실행

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run-split.py
```

프론트 **8004**, 백엔드 **9003**을 실행함. 브라우저는 [캠퍼스 화면](http://127.0.0.1:8004/)으로 접속함. `run-local.cmd`·`run-develop.cmd`도 같은 실행기를 사용하며, Ctrl+C로 두 서비스를 종료함. 사용 중인 포트가 있으면 기존 서버를 종료하지 않고 실행을 중단함.

```powershell
python run-split.py --frontend-port 8005 --backend-port 9004 --bind 127.0.0.1
```

백엔드는 기본 loopback에만 열고, 프론트 기본 bind는 `0.0.0.0`임. `0.0.0.0`은 접속 주소가 아니라 수신 설정이며 인터넷 공개 주소를 자동으로 만들지 않음.

## 개별 실행

공용 가상환경을 설치한 뒤 백엔드 터미널에서 실행함.

```powershell
cd backend
python manage.py migrate --noinput
python run-campus.py --port 9003 --bind 127.0.0.1
```

다른 터미널에서는 같은 가상환경을 활성화한 뒤 프론트를 실행함.

```powershell
cd frontend
$env:BACKEND_URL = 'http://127.0.0.1:9003'
python manage.py runserver 0.0.0.0:8004
```

각 폴더의 `requirements.txt`로 개별 가상환경을 만들어도 됨. 프론트에는 DB 설정·마이그레이션이 필요 없음. 프론트와 백엔드 각각 자신의 `config` 모듈을 사용하므로 명령은 해당 폴더에서 실행함. 루트 `manage.py`는 백엔드 호환 명령, 루트 `run-campus.py`는 백엔드 단독 실행 명령임.

## PostgreSQL 설정과 데이터 이전

`backend/.env.example`을 `backend/.env`로 복사하고 PostgreSQL 연결 정보를 입력한다.
새 DB를 미리 만든다. 아래 값은 예시다.

```dotenv
DATABASE_ENGINE=postgresql
POSTGRES_DB=playdata_platform_new
POSTGRES_USER=platform_user
POSTGRES_PASSWORD=실제_비밀번호
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
```

빈 DB에서는 아래 명령으로 테이블을 만든다.

```powershell
python backend/manage.py migrate --noinput
```

기존 DB를 사용한다면 **원본을 백업하고 모든 쓰기를 중단한 뒤**, 새 PostgreSQL DB로 이전한다.
UUID 계정으로 바뀌므로 기존 DB에서 `migrate`를 바로 실행하면 안 된다.

1. 새 코드의 `backend/.env`를 기존 DB에 연결한다. SQLite는 `CAMPUS_DATA_DIR`, MySQL은 기존 `MYSQL_*`를 지정한다.
2. MySQL 원본이면 `python -m pip install -r backend/requirements-legacy-export.txt`로 이전용 드라이버를 설치한다.
3. 원본 DB를 읽기만 하는 명령을 실행한다.

```powershell
python backend/manage.py export_platform_data --output ../platform-source-private.json
```

4. `backend/.env`를 새 PostgreSQL DB에 연결하고 마이그레이션한다.
5. 먼저 이전을 검증한다. `--apply`가 없으면 입력한 행을 모두 되돌린다.

```powershell
python backend/manage.py migrate --noinput
python backend/manage.py import_platform_data --file ../platform-source-private.json
python backend/manage.py import_platform_data --file ../platform-source-private.json --apply
```

6. 새 DB에서 로그인·게시판·학습·포인트·위치 복원을 확인한 뒤 서버를 전환한다.

기존 `accounts_profile.user_uuid`를 새 `auth_user.id`로 사용한다.
프로필 없는 계정은 UUID를 새로 만들고, 해당 계정의 FK도 함께 옮긴다.
비밀번호 해시, 권한, 시각, 게시글 번호, 댓글 연결, 포인트 기록을 보존한다.
쪽지 참여자 두 명은 새 UUID 순서로 정렬한다.

기존 로그인 세션은 옮기지 않는다. 모든 사용자는 다시 로그인해야 한다.
미종료 접속 이력은 이전 시점에 `SESSION_EXPIRED`로 종료한다.
Outbox·식단 댓글·기존 세션은 원본 내보내기 파일에만 보존한다.
미전송 Outbox는 새 서버가 발행하지 않는다. CDC 전환 전에 기존 기록의 처리 기준을 정해야 한다.

원본 파일에는 비밀번호 해시와 사용자 정보가 포함된다. Git에 추가하지 않는다.
파일은 덮어쓰지 않으며, 생성 권한은 소유자 읽기·쓰기만 허용한다.
행이 많은 운영 DB의 처리 시간과 메모리는 별도로 측정해야 한다.
MySQL의 문자 비교 규칙은 자동 복제되지 않는다. 실제 계정의 대소문자·한글 로그인도 전환 전에 확인한다.

`CAMPUS_DATA_DIR`는 식단 이미지와 세션 정리 상태 등 파일의 경로다.
PostgreSQL 설정에서는 이 경로의 `db.sqlite3`를 사용하지 않는다.
식단 이미지 파일과 비공개 설정도 별도로 보존한다.

## 환경 변수

- 백엔드: `backend/.env` (`backend/.env.example` 참고).
- 프론트: `frontend/.env` (`frontend/.env.example` 참고).
- 루트 호환 실행기는 기존 루트 `.env`도 읽음. 직접 폴더에서 실행할 때는 해당 폴더의 `.env`를 사용함.
- `.env`, DB, 캐시, 로그, 가상환경은 Git에서 제외함.

| 위치 | 변수 | 기본/용도 |
| --- | --- | --- |
| 프론트 | `BACKEND_URL` | `http://127.0.0.1:9003` |
| 프론트 | `FRONTEND_ALLOWED_HOSTS` | localhost·현재 PC 사설 IP; `.env.example` 복사 시 LAN IP 추가 필요 |
| 프론트 | `FRONTEND_DEBUG`, `FRONTEND_SECRET_KEY` | 개발 화면 설정 |
| 백엔드 | `DJANGO_ALLOWED_HOSTS` | 프론트에서 사용자에게 노출하는 호스트도 포함 |
| 백엔드 | `DJANGO_CSRF_TRUSTED_ORIGINS` | HTTPS 프론트 등 신뢰할 공개 origin |
| 백엔드 | `DJANGO_DEBUG`, `DJANGO_SECRET_KEY` | 개발/배포 설정 |
| 백엔드 | `CAMPUS_DATA_DIR` | 기본 backend 폴더 |
| 백엔드 | `DATABASE_ENGINE` | 기본 `postgresql`. SQLite·MySQL은 원본 내보내기용 |
| 백엔드 | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` | 새 PostgreSQL 연결 정보 |
| 백엔드 | `CAMPUS_LOGIN_MAX_AGE_SECONDS` | 28800: 최대 8시간 |
| 백엔드 | `CAMPUS_LOGIN_IDLE_TIMEOUT_SECONDS` | 1800: 비활동 30분 |
| 백엔드 | `CAMPUS_LOGIN_WARNING_SECONDS` | 60: 만료 직전 안내 |
| 백엔드 | `CAMPUS_SESSION_ACTIVITY_WRITE_INTERVAL_SECONDS` | 15: 활동 시간 저장 간격 |
| 백엔드 | `CAMPUS_SESSION_CLEANUP_ENABLED`, `CAMPUS_SESSION_CLEANUP_TIME` | 기본 켬·한국 시간 04:00; 만료 세션 하루 한 번 정리 |
| 백엔드 | `LEARNING_POINTS_BEGINNER`, `LEARNING_POINTS_INTERMEDIATE`, `LEARNING_POINTS_ADVANCED` | 정답별 20·50·80P; 변경 후 재시작 |

HTTP 중계는 로그인 쿠키·CSRF·Origin·원래 Host를 백엔드에 전달함. 프론트가 인증을 다시 판단하지 않음. 백엔드 포트는 프론트 및 신뢰할 인프라에서만 접근하도록 배포함. 공개 HTTPS 환경은 두 서비스 앞의 프록시와 호스트·CSRF·쿠키 설정을 함께 맞춰야 함.

HTTPS를 종료하는 Nginx는 `X-Forwarded-Proto`를 실제 외부 프로토콜로 덮어써야 함. 프론트는 `SECURE_PROXY_SSL_HEADER`로 이를 인식해 백엔드에 같은 프로토콜을 전달함. 해당 헤더를 신뢰하므로 프론트 앱 포트는 루프백 또는 신뢰된 프록시에서만 접근하도록 제한함. 운영 환경의 `/static/`은 Nginx에서 수집된 정적 파일 디렉터리로 연결해야 함. HTTPS 서버 블록에서 이 경로가 빠지면 로그인 CSS·맵 스크립트가 404로 응답함. 배포 후 로그인 페이지 200뿐 아니라 CSS 응답, 로그인 POST, 맵 렌더링과 WSS 연결까지 확인함.

## 내부망 접속

같은 LAN의 사용자는 `http://서버PC의사설IP:8004/`로 접속함. 프론트 bind가 `0.0.0.0`이고 두 서비스의 허용 호스트에 해당 IP가 있어야 함. 필요할 때만 Windows 관리자 권한으로 `allow-lan.cmd`를 실행하면 **프론트 TCP 8004**에 현재 LAN 접속 규칙을 적용함. 포트를 바꾸면 `configure-lan.py --port 변경포트`로 맞춤. 백엔드 9003을 사용자에게 직접 공유하지 않음.

## 변경한 테이블과 저장 흐름

| 항목 | 현재 동작 |
|---|---|
| 계정 | `auth_user.id`와 계정 참조 FK는 UUID |
| 프로필 | `user_id` 하나가 계정 FK와 프로필 PK를 겸함 |
| 기수 | `accounts_profile.cohort`가 `ClassGroup` UUID를 참조함. 코드는 유지 |
| 가입 시각 | `auth_user.date_joined` 사용. 프로필 시각 컬럼은 추가하지 않음 |
| 위치 | `accounts_position`의 사용자별 한 행을 갱신함 |
| 게시글·댓글 | `post_uuid`·`comment_uuid`가 PK. 숫자 `id`는 UNIQUE 번호로 유지 |
| 댓글의 게시글 연결 | 물리 컬럼 `post_uuid`가 게시글 UUID를 참조함 |
| 게시글 읽기 | `community_post_read_log`에 기록. 같은 `read_uuid` 재시도는 한 번 저장 |
| 콘텐츠 열기 | 허용한 요청마다 `content_open_request_log`에 새 행 추가 |
| 식단 댓글 | 테이블·API·화면 제거. 식단 이미지 조회는 유지 |
| Outbox | 모델·테이블·발행기·정리 작업 제거 |

새 계정 UUID는 자동 발급한다. 서버 코드에서 `create_user(id=uuid값, ...)`로 직접 지정할 수도 있다.
로그인 입력은 기존 `username`과 비밀번호를 사용한다.
`Profile.user_uuid`는 기존 연동용 읽기 속성만 남겼다. DB 컬럼은 없다.

답안·성적·지갑·원장·재시도 응답은 같은 트랜잭션에 저장한다.
같은 사용자의 `submission_request_uuid` 재시도는 이전 응답을 돌려주며 중복 지급하지 않는다.
`answer_id`, `attempt_uuid`, `submission_request_uuid`는 그대로 유지한다.

위치 저장 간격과 층 전환·종료 시 즉시 저장하는 동작을 유지한다.
JSON 좌표는 PostgreSQL `json`으로 저장해 숫자 표현을 보존한다.
관리자의 수정·삭제 행위자를 추적하는 컬럼은 추가하지 않았다.
모든 업무 행에 접속 UUID를 추가하는 작업도 포함하지 않았다.

플랫폼은 Kafka로 직접 발행하지 않는다. PostgreSQL CDC 연결과 수집 범위 확장은 다음 단계다.
[기존 Kafka 계약](KAFKA_TOPICS.md)은 과거 Outbox 동작을 보관한 문서다.

## 추가 기능 이용

- 이동·상호작용은 기존 WASD/방향키·E 사용. Z 또는 화면 찌르기 버튼으로 앞의 캐릭터에게 손 동작을 보냄. 길게 누르면 반복하고, 키/버튼을 놓으면 종료함. 입력창·팝업·층 전환·연결 종료 중에는 중단함.
- 같은 층의 가까운 사용자는 같은 효과를 보고, 개인 학습교실의 강사·학생 NPC 반응은 본인에게 표시됨. 벽·가구 너머의 대상은 찌르지 않으며 좌표나 포인트를 변경하지 않음.
- 우상단 보유 포인트를 누르면 본인의 적립 내역을 최근순 10건씩 표시하고 **더 보기**로 이전 내역을 조회함. 읽기만 수행하며 추가 지급하지 않음. 네트워크 오류 시 다시 불러오기를 제공함.
- 게임 카드 총 16종. 업데이트 후 백엔드 `migrate --noinput`으로 추가 8종의 기준정보를 등록함. 기존 콘텐츠 UUID·운영자가 수정한 항목을 덮어쓰지 않음.
- 옥상 콘텐츠 부스와 학습교실 표현을 개선함. 광고용 게시판과 광고 서버 연결은 별도 구성임.
- 한글·영문·IME 상태의 WASD·E·Z는 물리 키 위치를 사용함. 입력창·텍스트 선택·브라우저 단축키는 별도로 처리함.
- 통신이 끊기면 WebSocket 재접속을 시도함. 같은 세션의 새 연결이 이전 연결을 교체하며 현재 위치·개인 학습교실을 복원함. 교체된 이전 탭은 새로고침으로 다시 연결할 수 있음.
- 학습교실 진입 방향키를 계속 누르고 있어도 바로 퇴장하지 않음. 키를 놓고 다시 입력하거나 2초 뒤 나가기 버튼을 사용함. 개인 교실 사용자는 공용 지도·유리문 충돌에서 제외함.
- 유튜브 부스 중앙에서 상호작용하면 YouTube 홈페이지를 별도 팝업으로 열고, 차단되면 부스의 **YouTube 열기** 링크로 다시 시도함.

## 운영 반영 전 확인

먼저 새 PostgreSQL DB에서 이전을 검증한다. 아래 MySQL 기록은 2026-10-15 당시의 이력이다.

프론트 `playdata-frontend`, 백엔드 `playdata-backend` 서비스를 사용함. 소스 반영 전 DB와 변경 파일을 백업하고 추가 마이그레이션·정적 파일 수집·서비스 재시작을 수행함. 실패 시 기존 소스와 설정으로 복구할 수 있도록 백업을 유지함.

공개 호스트는 프론트 `FRONTEND_ALLOWED_HOSTS`, 백엔드 `DJANGO_ALLOWED_HOSTS`와 필요한 `DJANGO_CSRF_TRUSTED_ORIGINS`로 설정함. 배포 시 기존 `.env`, DB, 식단 캐시를 덮어쓰지 않음.

2026-10-15 AWS 운영 DB를 MySQL로 전환함. Router `127.0.0.1:6446`의 쓰기 연결로 전용 `playdata_campus` DB를 사용하며, 문자 비교는 기존 아이디 중복 검사와 맞는 `utf8mb4_0900_as_ci`임. 3대 모두 ONLINE 상태와 데이터 복제를 확인함. Kafka 3대·7개 토픽·실제 메시지 조회와 Outbox 대기 0건도 확인함. 비밀번호·Vault·개인키·운영 DB는 Git이나 소스 배포 묶음에 포함하지 않음.

위치·Raw Outbox 정밀도 보존에는 `accounts.0007_preserve_json_precision`, `eventing.0009_preserve_json_precision` 마이그레이션이 필요함. MySQL에서만 JSON 원문을 LONGTEXT + JSON_VALID 검사로 보존하고 SQLite 동작은 유지함. Kafka payload와 fingerprint의 재조회 일치를 회귀 검증함.

전환 전 SQLite·설정을 비공개 배포 백업에 보관하고 전체 ORM 데이터 1,908건을 마이크로초·좌표 정밀도까지 동일하게 대조함. SQLite 백업은 전환 시점 데이터이며, 이후 복구 시에는 MySQL에서 추가된 데이터도 함께 보존해야 함.

분리된 임시 QA 사본과 테스트 DB에서 MySQL 회귀 444개를 실행해 통과함. 이 중 SQLite 전용 동시 처리 6개는 MySQL용으로 별도 실행해 모두 통과함. 로컬 SQLite 이동·JSON 회귀 24개도 통과함. 합성 테스트 계정에는 테스트용 해시를 사용했고 운영 비밀번호 해시는 변경하지 않음.

## 검증

격리한 PostgreSQL 연결 정보를 설정하고 테스트한다. 테스트 계정에는 테스트 DB 생성·삭제 권한이 필요하다.
`test_<POSTGRES_DB>`라는 임시 DB를 만들므로, 실제 사용자 DB를 테스트 설정에 사용하지 않는다.

```powershell
cd backend
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test tests accounts community direct_messages learning eventing --settings=config.postgres_test_settings --noinput
python manage.py test campus --settings=config.postgres_test_settings --noinput
```

새 터미널 또는 루트에서 프론트 폴더로 이동해 렌더링과 HTTP 중계를 검증함.

```powershell
cd frontend
python manage.py check
python manage.py test pages --noinput
node --test tests/*.test.cjs
node learning/tests/test-kafka-ui.cjs
```

JavaScript 검증에는 Node.js가 필요함. 마지막 학습 UI 검증은 Playwright와 Chrome을 사용하고 API 응답을 대체해 실제 사용자 데이터에 영향을 주지 않음. 실제 두 서비스의 브라우저 확인은 별도 데이터 폴더·포트로 수행해야 함. 테스트 설정의 좌표 수신 호환 모드는 기존 회귀 검증용이며, 운영 서버는 키 입력 기반 서버 판정을 사용함.

현재 이전 검증은 합성 데이터를 사용한 격리 PostgreSQL 기준이다. 실제 운영 데이터 이전과 CDC 통합은 아직 검증하지 않았다.
변경 전 `event` 브랜치에도 기존 캠퍼스 회귀 실패가 있다. 전체 회귀 통과와 실제 배포 검증을 구분해야 한다.


### 2026-10-15 이용 안내·맵 UI 갱신

- 신규 회원은 닉네임·아바타 설정 뒤 최초 이용 안내를 받음. 완료·건너뛰기 상태를 운영 DB에 저장하며 설정에서 다시 열 수 있음. 기존 설정 완료 계정은 배포 마이그레이션에서 확인 완료로 처리함.
- 맵을 브라우저 창 전체에 표시하고 계정·설정·이동 키 안내를 하단으로 이동함. 공간 안내는 좌측 상단 버튼에서 접고 펼침.
- 18층 바깥 배경을 베이지색 아이소메트릭 배경으로 정리함. 강의실 유리문 7개를 제거하고 출입구를 바로 통과하도록 서버 충돌 판정도 맞춤. 창고 슬라이드문은 유지함.
- 층 전환의 3초 이동 차단은 유지함. 계단에서는 캐릭터의 오르내림, 포탈에서는 순간이동 애니메이션을 표시함.
- Python 초급 빈칸 10문항의 설명만 명확히 수정함. 문제 UUID·정답·기존 응답·포인트·Kafka 이벤트 형식은 유지함.
- 적용 마이그레이션: accounts 0008_profile_guide_completed, learning 0004_beginner_prompt_wording.


### 개인 쪽지함 활성화 (2026-10-16)

1. 백엔드에서 python manage.py migrate 실행함. direct_messages.0001_initial이 Conversation·Message 테이블을 추가하며 기존 데이터를 초기화하지 않음.
2. 쪽지함을 사용할 환경의 backend/.env에 CAMPUS_DM_ENABLED=1을 설정하고 백엔드를 재시작함. 기본값 0이면 API와 UI 모두 비활성화됨. 별도 발신자 허용 목록은 사용하지 않음.
3. 프런트 정적 파일을 다시 수집·배포하고 프런트를 재시작함. 일반적인 /api/ 프록시와 기존 /ws/campus/를 사용하며 추가 서버는 필요하지 않음.
4. 서로 다른 계정으로 닉네임 검색 → 전송 → 미확인 수 → 대화 열기·읽음 → 답장을 확인함. 제삼자 계정은 대화 조회·읽음 변경이 404여야 함.

- 한 메시지는 최대 2000자이며 계정당 1초 이내 연속 전송을 제한함. 같은 requestId의 동일 재시도는 기존 메시지를 반환함.
- UI는 대화 목록 최대 50개·대화별 최근 메시지 최대 100개를 표시함. 표시 범위는 DB 저장 수명을 뜻하지 않으며 자동 삭제 정책은 추가하지 않음.
- 웹소켓이 연결되면 수신자 계정 전용 갱신 알림을 사용함. 15초 주기 조회와 창 포커스 복귀 시 확인으로 알림 누락을 보완함. 다중 서버 운영 시 Django Channels 계층 공유가 필요함.
- 쪽지창에서는 이동·점프·상호작용 입력을 차단함. 내용은 HTML로 실행하지 않고 텍스트로 표시함.
- 점프는 Space 또는 하단 버튼으로 실행함. 일반 사용자 줌은 150% 고정이며 관리자만 버튼·키보드·휠로 조절함.
