# WSL2 역할별 실행

`mlo-01-p4-team2`의 `dev` 브랜치를 내려받아 `platform_server/`에서 실행한다.
Python 앱은 기존 `backend/`·`frontend/`를 사용한다. 이 폴더는 실행 설정·검증 도구·문서다.

## 1. 준비

Windows·WSL 네트워크·자동 시작은 [wsl.md](wsl.md)를 먼저 확인한다.
아래 명령은 WSL2의 Ubuntu 24.04에서 실행한다.

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv python3.12-dev build-essential pkg-config default-libmysqlclient-dev nginx redis-server nodejs git
git clone --branch dev --single-branch https://github.com/encore-ai-campus/mlo-01-p4-team2.git
cd mlo-01-p4-team2/platform_server
git rev-parse HEAD
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp deploy/onprem/.env.example deploy/onprem/.env
chmod 600 deploy/onprem/.env
```

- 두 PC에서 같은 검증 커밋을 사용한다. 커밋 번호를 배포 기록에 남긴다.
- Redis·외부 MySQL은 실행 전에 준비한다. 앱 실행기는 DB를 자동으로 변경하지 않는다.
- 새 실행기가 별도 Nginx를 실행한다. 패키지 기본 Nginx 서버를 사용하지 않으면 중복 시작하지 않는다.
- 이 변경은 기존 `run-split.py`와 `deploy/service-split`을 보존한다.

## 2. 환경변수

각 PC의 `.env`를 실제 값으로 수정한다. 비밀 값은 Git에 추가하지 않는다.

| 설정 | 규칙 |
|---|---|
| `DJANGO_SECRET_KEY` | 두 PC에서 동일한 긴 무작위 값 |
| `FRONTEND_SECRET_KEY` | 개발 기본값을 사용하지 않는 별도 값 |
| `DJANGO_ALLOWED_HOSTS`, `FRONTEND_ALLOWED_HOSTS` | 공개 도메인·필요한 내부 Host |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | 실제 공개 Origin. 예: `https://platform.example.com` |
| `MYSQL_*` | 기존 외부 MySQL 또는 Router 주소·계정·DB |
| `CAMPUS_MOVEMENT_REDIS_URL` | 모든 프로세스에서 같은 이동 Redis 지정 |
| `CAMPUS_SERVICE_REDIS_URL` | 모든 프로세스에서 같은 서비스 Redis 지정 |
| `CAMPUS_DATA_DIR` | 해당 WSL의 영속 데이터 경로 |
| `KAFKA_BOOTSTRAP_SERVERS` | 기존 사용 시 지정. 미사용이면 빈 값 |

- 자기 PC의 Redis는 loopback으로 연결할 수 있다.
- 다른 PC의 Redis는 상대 PC의 사설 연결 주소를 사용한다. 두 주소를 모두 `127.0.0.1`로 복사하지 않는다.
- Redis 비밀번호는 URL 안에서 URL 인코딩한다. [Redis 설정 예시](redis.conf.example)와 일치시킨다.
- 현재 세션 종료 처리는 두 Redis에 접근한다. 교차 접근을 제거하는 작업은 포함하지 않는다.
- 실제 데이터 경로에는 앱 실행 사용자의 쓰기 권한을 준다.
- 기존 `backend/.env`·`frontend/.env`가 있으면 선택 설정이 보충된다. 중복된 과거 설정을 정리한다.

## 3. DB 준비와 실행

마이그레이션은 지정한 PC 한 곳에서만 실행한다. 기존 운영 절차에 따라 DB를 백업한다.
`platform_server/`에 만든 `.venv`와 이 배포의 환경 파일을 사용한다.

```bash
cd backend
../.venv/bin/python - <<'PY'
import os
from dotenv import load_dotenv
from django.core.management import execute_from_command_line
load_dotenv('../deploy/onprem/.env', override=True)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
execute_from_command_line(['manage.py', 'migrate', '--noinput'])
PY
cd ..
```

PC 1에서 이동 역할을 실행한다.

```bash
.venv/bin/python deploy/onprem/run.py movement --env-file deploy/onprem/.env
```

PC 2에서 화면·HTTP·채팅 역할을 실행한다.

```bash
.venv/bin/python deploy/onprem/run.py service --env-file deploy/onprem/.env
```

| 역할 | 실행 내용 |
|---|---|
| movement | Backend `9101~9104`·뒤 Nginx `8000`·Front `8004` |
| service | Backend `9201~9204`·뒤 Nginx `8000`·Front `8004`·jobs 1개 |

- 서비스 시작 시 `collectstatic`으로 전체 파일을 수집한다.
- Front·Backend는 같은 가상환경의 Python으로 실행한다.
- Backend는 loopback에서만 수신한다. Front·Nginx의 기본 바인딩도 loopback이다.
- 사설 주소로 바인딩하면 Front는 해당 PC의 Nginx 사설 주소로 연결한다.
- 다른 입구 장비가 연결하면 `--bind`에 사설 주소를 지정하고 방화벽을 제한한다.
- `Ctrl+C`는 실행기가 시작한 자식 프로세스를 함께 종료한다.
- 런타임 Nginx 설정·PID는 Git에서 제외한 `deploy/onprem/var/` 아래에 생성한다.
- Nginx 접근 로그도 해당 역할의 런타임 경로에 쌓인다. 운영자는 로그 순환을 설정한다.

```bash
curl --fail http://127.0.0.1:8004/health/
# PC 2
curl --fail http://127.0.0.1:8000/static/campus/presence.js -o /dev/null
```

프로세스 확인과 DB·Redis·사용자 기능 확인은 별도로 수행한다.
외부 연결 담당자에게는 [ingress.md](ingress.md)를 전달한다.

## 4. 테스트

운영 DB·Redis·Kafka를 사용하지 않는 테스트다. 세션 정리 파일도 임시 디렉터리로 격리한다.

```bash
PYTHON="$PWD/.venv/bin/python" sh deploy/onprem/test.sh
sh deploy/onprem/test.sh node
```

전체 Backend 테스트도 같은 격리 설정으로 실행할 수 있다.

```bash
cd backend
PYTHONPATH="$PWD/.." ../.venv/bin/python manage.py test --settings=deploy.onprem.test_settings --noinput
cd ..
```

| 현재 검증 | 결과 |
|---|---|
| Front Django 전체 | 15개 통과 |
| 정적 파일 수집·원본 비교 | 110개 일치 |
| 부하 도구 단위 테스트 | 2개 통과 |
| 실행기 단위 테스트 | 4개 통과 |
| 관련 Backend 회귀 | 98개 통과·기존 생략 1개 |
| 계단 이동·승인 관련 | 3개 통과 |
| Front Node 전체 | 113개 통과 |
| Backend 전체·Python 3.12 분리 환경 | 589개 실행·581개 통과·생략 8개 |

기준 `dev 9390cc6`에서도 재현한 계단 테스트의 `KeyError('result')`를 수정했다.
실제 지도의 계단 위치를 사용하고, 계단 선택·승인 응답의 성공 여부를 각각 검사한다.
같은 고정 좌표를 사용하던 모션 테스트 2개도 갱신했다. 서버의 이동 규칙은 유지한다.

부스 테스트는 실제 상호작용 영역을 검사한다. 좌표 기반 위치 저장 테스트는 기존 호환 모드를 명시한다.
운영 키 입력·좌표 거절·로그아웃 ACK는 별도의 서버 이동 테스트에서 검사한다. 운영 코드는 변경하지 않았다.

Python 3.12 전용 가상환경에 `requirements.txt`를 설치하고 전체 검증을 다시 수행했다.
Python 버전 혼용으로 발생한 MySQLdb·Kafka 모듈 오류도 해소했다. 전체 결과에 실패·오류는 없다.

- 로컬 Python·Node 검증 결과다. WSL·Nginx 실제 기동·두 PC 연동 결과는 아니다.
- 실제 PC에서는 패키지 설치 후 Nginx 문법·프로세스 시작·종료를 다시 검증한다.

## 5. 150명 부하 확인

**별도 테스트 MySQL·Redis만 사용한다.** 실제 사용자 DB에서는 아래 시드를 실행하지 않는다.
시험용 `.env`로 두 역할을 먼저 실행한다. 같은 WSL에서는 서비스 입구 포트를 다르게 지정한다.

```bash
# 이동 역할은 8004/8000 유지. 서비스 역할은 다른 포트 사용.
.venv/bin/python deploy/onprem/run.py service --env-file deploy/onprem/.env --frontend-port 28004 --nginx-port 28000
```

시험용 환경 파일을 확인한 뒤, 기존 시드 코드를 사용한다.

```bash
umask 0077
cd backend
../.venv/bin/python - <<'PY'
import os, runpy, sys
from dotenv import load_dotenv
load_dotenv('../deploy/onprem/.env', override=True)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.argv = ['seed.py', '150', '--unusable-password', '--out=/tmp/onprem-test-sessions.json']
runpy.run_path('../loadtest/seed.py', run_name='__main__')
PY
cd ..
.venv/bin/python deploy/onprem/load.py \
  --service-http-base http://127.0.0.1:28004 \
  --movement-ws-url 'ws://127.0.0.1:8004/ws/movement/?control=server&moves=compact' \
  --chat-ws-url ws://127.0.0.1:28004/ws/chat/ \
  --origin http://app.test --sessions-json /tmp/onprem-test-sessions.json \
  --users 150 --ramp 30 --duration 60 --chat-interval 15
```

- 시험 환경의 Front·Backend 허용 호스트에 `app.test`를 추가한다.
- 두 PC 시험에서는 목적지 URL을 실제 사설 접속 주소로 바꾸고, 파일을 권한 제한하여 전달한다.
- 한 사용자당 이동·채팅 WS 2개다. 이동 입력은 100ms마다 계속 전송한다.
- HTTP 세션 상태를 조회한다. 채팅 전송은 `--chat-interval 15`로 활성화한다.
- 기본 채팅 간격 `0`은 메시지를 보내지 않는다. ping만 수행한다.
- 결과 JSON은 준비한 사용자 수·연결 실패·HTTP 지연·오류율을 기록한다.
- 자원 사용량·DB 상태·메시지 지연·재연결은 별도로 관측한다.
- 실제 150명 부하는 아직 실행하지 않았다. 준비한 테스트 도구를 처리 용량 보장으로 해석하지 않는다.

## 6. 서비스 등록·갱신·복구

- [systemd 예시](playdata-platform@.service)의 사용자·레포 경로를 실제 값으로 수정한다.
- 해당 파일을 `/etc/systemd/system/playdata-platform@.service`로 등록한다.
- 각 PC의 환경 파일을 `/etc/playdata/movement.env` 또는 `/etc/playdata/service.env`로 준비한다.
- 환경 파일은 앱 사용자만 읽게 제한한다. `systemctl daemon-reload` 후 해당 역할을 시작한다.
- Windows 부팅 후 WSL 시작은 [wsl.md](wsl.md)의 별도 작업으로 준비한다.
- 갱신 전 두 PC의 현재 커밋·환경 파일·DB 백업 위치를 기록한다.
- 두 PC에서 같은 검증 커밋으로 갱신한 뒤 역할별 서비스를 재시작한다.
- 복구 시 이전 커밋으로 되돌리고 두 역할을 재시작한다. 정적 파일도 해당 코드에서 다시 수집한다.
- 기존 배포 파일은 보존돼 있다. 실제 입구 전환·복구는 연결 담당자와 수행한다.
- 이 변경에는 새 DB 마이그레이션이 없다. 운영 데이터를 삭제하거나 자동으로 되돌리지 않는다.

## 7. 변경 이력

| 날짜 | 변경 | 변경한 절 |
|---|---|---|
| 2026-10-23 | dev 병합 없이 WSL2에서 직접 실행할 배포·테스트 절차 작성 | 1. 준비 · 2. 환경변수 · 3. DB 준비와 실행 · 4. 테스트 · 5. 150명 부하 확인 · 6. 서비스 등록·갱신·복구 · 7. 변경 이력 |
| 2026-10-23 | 계단 테스트 좌표·응답 검증 수정과 회귀 결과 반영, 전체 Backend 테스트 명령의 실행 위치 보완 | 4. 테스트 · 7. 변경 이력 |
| 2026-10-23 | 부스·위치 테스트 조건 수정, Python 3.12 분리 환경의 전체 검증 결과 반영 | 4. 테스트 · 7. 변경 이력 |
