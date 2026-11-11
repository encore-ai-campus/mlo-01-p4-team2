# 빼빼로 데이 독립 프로젝트

이 브랜치는 최신 캠퍼스 프로젝트를 기반으로 18층과 옥상 모두 빼빼로 이벤트맵으로 실행합니다. 기본맵 전환은 없습니다. 채팅 분리, 이동 패킷 최적화, 최신 UI와 서버 기능을 유지한 채 이벤트 맵·조작·충돌 판정을 통합했습니다. 로컬 독립 테스트와 운영 전환을 구분합니다. 운영에서는 기존맵과 같은 MySQL DB·비밀키·영속 데이터 설정을 유지하고, 두 PC를 동일한 이벤트 커밋으로 전환합니다. 자세한 절차는 [맵 전환 운영](deploy/onprem/MAP_SWITCHING.md)을 따릅니다.

## 맵과 충돌 수정

- 원본: `frontend/maps/pepero-worlds.json`
- 브라우저용 생성 파일: `frontend/dist/event-map.js`
- 서버 충돌 생성 파일: `backend/campus/data/worlds.json`
- 에셋: `frontend/dist/assets/seasonal/pepero/reference/`

배치 변경은 원본 JSON에서 합니다. `solid: true`는 사물의 바닥 면적을 막고, `collision`은 상대 좌표로 면적을 조절합니다. 여러 부위를 따로 막을 때는 `collisionParts`를 사용합니다. 아치는 양쪽 기둥만 차단하며 중앙은 통과합니다. 스프라이트의 빛·꽃잎·그림자 전체를 충돌로 사용하지 않습니다.

```sh
node frontend/tools/build-event-map.cjs
```

이 명령이 화면과 서버 데이터를 함께 재생성합니다. 서버 시작 후 원본을 바꿨다면 재생성하고 백엔드를 재시작하세요. 서버는 메모리에 충돌 데이터를 보관합니다. `run-split.py`는 시작할 때 자동으로 재생성합니다.

## 로컬 실행

Python 3.12 이상과 Node.js가 필요합니다.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run-split.py --frontend-port 14029 --backend-port 14030 --bind 127.0.0.1
```

접속: http://127.0.0.1:14029/

기본 로컬 실행 설정은 별도의 SQLite DB를 사용할 수 있습니다. 이 경우 기존 운영 계정이 자동으로 보이지 않습니다. 운영 전환은 이 로컬 명령 대신 `feat/15`에서 이어받은 `deploy/onprem/run.py`와 기존 운영 환경 파일을 사용합니다.

## 독립 서버 배포

이벤트 통합·배포 브랜치 `event`는 이벤트 변경 전 `feat/15`에서 생성합니다. 현재 `feat/18` 작업은 `event`를 대상으로 Draft PR로 검토하며 `feat/15`에 병합하지 않습니다. 이후 수정은 `event`에서 분기해 `event`로 PR을 올립니다. 기존맵과 이벤트맵을 같은 DB·Redis에 동시에 실행하지 않습니다. `.venv`, `.env`, DB, 로그는 커밋하지 마세요.

두 PC에서 같은 검증 커밋을 준비하고 맵 생성 명령을 실행합니다. 운영 환경 파일과 MySQL·Redis·영속 데이터 경로는 기존 설정을 유지합니다. 코드 변경만으로 DB를 초기화하거나 과거 데이터로 복원하지 않습니다. 실제 전환·마이그레이션·외부 입구 설정 변경은 운영 절차에 따라 별도로 수행합니다.

## 검증

```sh
node --test frontend/tests/worlds-export.test.cjs
cd backend
python manage.py test campus.test_event_map
```

서버가 이벤트 에셋을 막는지, 아치 중앙과 양쪽 기둥이 구분되는지, 시작점에서 부스·계단·포탈·학습 입구로 갈 수 있는지 확인합니다.


## 빼빼로 플레이

- 신규 접속 시 빈손으로 시작합니다. 18층 3개·옥상 2개의 `빼빼로 리필 상자 · E` 앞에서 E를 누르면 10회분을 꺼냅니다.
- Z 연속 찌르기: 서버에서 상대 적중이 확인될 때만 1회분 감소합니다. 일반 적중은 부스러기와 함께 1회분 감소하며, Ctrl 방어(옥상 샌드백 포함)에 막히면 남은 빼빼로가 즉시 파손되어 0이 됩니다. 공격자에게 반쪽 파손과 당황 표정이 표시됩니다. 빗나가면 감소하지 않습니다. 0이어도 빈손으로 계속 휘두를 수 있습니다.
- 상자 리필은 거리 60 미만, 같은 공간, 이동 가능한 경로일 때만 허용합니다. 반복해서 눌러도 최대 10회분입니다.
- Ctrl 유지: 리본·하트 보호막. 방어 성공 시 초콜릿 조각이 퍼집니다. 동작 줄이기 설정을 존중합니다.
- 잔량은 접속 동안만 유지됩니다. 재접속/서버 재시작 시 빈손으로 시작하며 포인트는 사용하지 않습니다. 별도 Kafka 로그를 생성하지 않습니다.
- 동작 미리보기: `/static/campus/pepero-play-preview.html` (서버 적중 판정 대신 화면 연출을 확인하는 페이지).
- 테스트: `python manage.py test campus.test_pepero_game campus.test_event_map` (backend 폴더).


## 이벤트 조작 수정
- 모든 아바타에 각 외형의 왕복 휘두르기 에셋을 사용합니다. 이동 중에는 걷는 자세를 유지합니다.
- 리필 상태는 전용 알림, 현재 캐릭터 상태, 요청 응답으로 확인합니다.
- 맵의 진열대·테이블·화단·조형물은 실제 스프라이트 바닥 윤곽의 다각형으로 충돌을 판정합니다. 아치 중앙 통로는 유지합니다.
- 학습 입구 표시는 통과할 수 있는 바닥 표식입니다.
- 추가 통합 검사: campus.test_pepero_refill_socket (테스트 DB로 인증 소켓 리필 검사).


## 보유 자세·제자리 연속 공격·앞뒤 에셋
- 빼빼로 보유 시 대기/이동 중에도 세워 들고 있습니다.
- 최대 길이 42px, 적중마다 7px씩 짧아집니다. 잔량 0이면 빈손 공격을 유지합니다.
- 네 아바타 모두 앞·뒤 전용 8프레임 에셋을 사용합니다. 좌우 에셋과 방향별로 선택됩니다.
- 찌르기 유지 상태를 0.5초마다 동기화합니다. 위치 갱신이 없어도 모션은 계속되고 공격 상태가 끊기면 1.6초 내 정리됩니다.
- 에셋 원본과 내장 이미지 생성 프롬프트: frontend/dist/assets/seasonal/pepero/previews/direction-prompts.json


## 주변 사용자 상태 공유

- 현재 브라우저는 이동 소켓에 `view=nearby`를 지정합니다. 이동·방향·달리기·방어 상태를 같은 층의 주변 사용자에게만 보냅니다. 기존 입력 유지 알림과 0.1초 묶음 전송은 유지합니다.
- 일반 사용자는 캐릭터 위치 기준 1200 이내에서 상태 공유를 시작하고 1400을 넘으면 화면에서 정리합니다. 경계에서 깜빡임을 줄이는 여유 범위입니다. 관리자 개요 화면은 같은 층 전체를 받습니다.
- 범위 안으로 새로 들어오면 현재 위치·외형·방어·달리기·빼빼로 잔량을 포함한 전체 상태를 받습니다. 수신자가 움직여 정지한 캐릭터에 접근하는 경우도 처리합니다. 교실 진입과 층 변경 시 기존 화면의 캐릭터를 정리합니다.
- 전체 접속자 목록·닉네임 변경·입장·퇴장은 거리와 관계없이 유지합니다. 최초 접속의 목록 스냅샷과 호환용 구버전 소켓은 기존 구조를 유지합니다. 기존에 열린 탭은 새로고침해야 주변 공유를 사용합니다.
- 빼빼로 소모는 주변 명중 결과, 리필은 주변 전용 효과에 실립니다. 변화가 없는 방어 상태를 유지하려고 전체 방송을 반복하지 않습니다.
- 이번 변경은 브라우저로 보내는 실시간 상태의 수신 범위를 줄입니다. 서버 간 Redis 동기화, DB 저장과 Kafka 이벤트 수집 정책은 변경하지 않습니다.
- 검증: `python manage.py test campus.test_nearby_interest campus.test_compact_packets campus.test_load_reduction campus.test_pepero_game campus.test_pepero_refill_socket campus.test_cluster`; `node --test frontend/tests/presence.test.cjs frontend/tests/actor-culling.test.cjs frontend/tests/render-limits.test.cjs`.
