# PLAYDATA METAVERSE

플랫폼 코드는 `encore-ai-campus/mlo-01-p4-team2` 저장소의 `platform_server/`에서 관리한다.
아래 폴더와 실행 명령은 `platform_server/`를 기준으로 한다.

PLAYDATA 캠퍼스를 옮겨 온 실시간 메타버스 서비스입니다. 닉네임과 캐릭터로 18층 캠퍼스와 옥상을 돌아다니며 채팅·말풍선·찌르기로 동료와 인사하고, Python·SQL 학습교실에서 문제를 풀어 포인트를 모읍니다. 게시판, 쪽지, 점심 메뉴, 옥상 콘텐츠(게임 부스·심리 테스트·YouTube 스튜디오)와 캠퍼스 광고판이 함께 있습니다.

## 브랜치

| 브랜치 | 용도 |
|---|---|
| `main` | 배포 기준. `dev` 에서 검증한 것만 합친다 |
| `dev` | 개발. 기능 브랜치는 `dev` 에서 나와 `dev` 로 합친다 |

## 구성 (`dev` 브랜치)

| 폴더 | 내용 |
|---|---|
| `backend/` | 실시간 서버(Django Channels·daphne): 이동 판정, 채팅·찌르기·점프, 학습교실, 포인트. 여러 소켓 프로세스를 Redis 로 묶는다 |
| `frontend/` | 웹 프론트(Django)와 캔버스 클라이언트(`dist/`), 지도 데이터와 서버 판정용 지도 내보내기(`tools/`) |
| `loadtest/` | 시험 환경: 운영과 같은 조건의 부하 시험, 실사용 모방 봇, 개발 서버(`dev-server.sh`), 운영 측정 도구 |
| `deploy/` | 운영 반영·롤백 스크립트(소켓 프로세스·nginx·정적 파일) |
| `ad-bridge/` | 광고 서비스 연동 |
| `docs/` | 설계·성능 개선 기록(여러 프로세스 구조, 최적화 계획) |

## 실행

대상 저장소의 `dev`를 받은 뒤 플랫폼 폴더로 이동한다.

```powershell
cd platform_server
```

`dev` 브랜치의 [RUN_GUIDE.md](RUN_GUIDE.md) 를 따른다. 운영과 같은 구조(소켓 프로세스 여럿 + Redis + nginx)는 `loadtest/dev-server.sh` 로 띄운다.
