# 외부 연결 담당자용 명세

이 문서는 서버 연결 조건이다. Cloudflare Worker·Tunnel·DNS 설정 파일은 포함하지 않는다.
서버 설치·검증은 [README](README.md), 전체 구조는 [diagram.md](../../diagram.md)를 따른다.

## 1. 전달 경로

공개 도메인은 하나로 유지한다. 경로와 쿼리 문자열을 변경하지 않는다.

| 공개 요청 | 서버 입구 | 처리 |
|---|---|---|
| `/ws/movement/` | PC 1의 Front `8004` | 이동 WebSocket |
| `/ws/chat/` | PC 2의 Front `8004` | 채팅·개인 알림 WebSocket |
| `/static/*` | PC 2의 Nginx `8000` | 수집한 정적 파일 전체 |
| `/`, `/accounts/*`, `/api/*`, `/learning/*`, `/content/*` | PC 2의 Front `8004` | 화면 HTML·업무 API |

- `/ws/campus/`는 새 역할별 Front에서 거부한다.
- HTTP 화면을 Backend Nginx로 직접 보내면 화면 대신 JSON을 받을 수 있다.
- PC 2 Nginx의 외부 연결 용도는 `/static/*`다. 업무 요청은 Front로 보낸다.
- Front의 `/health/`는 프로세스 확인용이다. DB·Redis·백엔드 정상까지 보장하지 않는다.

## 2. 주소와 접근 범위

| 연결 담당자가 결정할 값 | 서버 담당자와 맞출 항목 |
|---|---|
| 공개 도메인 | Front·Backend 허용 호스트, CSRF Origin |
| 연결 프로그램의 실행 PC | 같은 PC이면 `127.0.0.1`, 다른 장비이면 사설 IP |
| 원본 서버 접속 주소·포트 | WSL 내부 서버의 바인딩 주소·포트 |
| TLS 종료 위치 | 원래 HTTPS 정보를 전달하는 신뢰 경계 |
| 외부 연결 장비의 주소 | 방화벽 허용 범위 |

- 기본 게시 주소는 loopback이다. 다른 PC에서 `127.0.0.1`로 접속할 수 없다.
- 사설 IP에 게시하면 방화벽으로 연결 담당 장비만 허용한다.
- Backend ASGI 포트와 DB·Redis 포트를 공개 인터넷에 게시하지 않는다.
- 외부 연결 프로그램이 같은 WSL에서 실행되면 loopback을 사용한다.
- Windows나 다른 장비에서 연결하면 WSL 네트워크 방식과 방화벽을 확인한다.
- WSL NAT 주소는 재시작 후 바뀔 수 있다. [WSL 운영 조건](wsl.md)을 확인한다.

## 3. HTTP·WebSocket 전달 조건

| 항목 | 전달 규칙 |
|---|---|
| `Host` | 공개 도메인 유지. 필요한 경우 공개 포트 포함 |
| `Cookie` | 로그인·CSRF 쿠키 유지 |
| `Origin`, `Referer`, `X-CSRFToken` | 브라우저의 원본 값 유지 |
| `X-Forwarded-Proto` | TLS 종료 지점이 실제 공개 요청에 따라 확정 |
| 메서드·본문·쿼리 | 변경 없이 전달 |
| 응답 | 상태 코드·본문·리다이렉트·복수 `Set-Cookie` 유지 |
| WebSocket | Upgrade·양방향 메시지·연결 유지·종료 전달 지원 |

- 공개 HTTPS 요청은 `X-Forwarded-Proto: https`로 전달한다.
- 외부 사용자가 보낸 전달 헤더를 신뢰하지 않는다. 신뢰하는 입구에서 삭제한 뒤 다시 설정한다.
- 서버는 해당 입구만 접근하도록 제한한다. Django는 전달된 HTTPS 정보를 신뢰한다.
- HTTP와 두 WebSocket은 같은 공개 도메인의 쿠키를 사용한다.
- 이동·채팅 연결을 열 때 같은 로그인 쿠키를 전달한다.
- 두 터널의 복제본을 섞어 역할을 구분하는 방식으로 구성하지 않는다.

근거: [Django 프록시 HTTPS 설정](https://docs.djangoproject.com/en/5.2/ref/settings/#secure-proxy-ssl-header),
[Nginx WebSocket 전달](https://nginx.org/en/docs/http/websocket.html).

## 4. 연결 후 확인

- 화면 `/`가 HTML을 반환하는지 확인한다.
- 로그인 쿠키 발급과 인증된 HTTP API 응답을 확인한다.
- 정상 CSRF POST의 성공과 잘못된 Origin의 거부를 확인한다.
- `/static/campus/presence.js`와 accounts·learning 파일의 응답을 확인한다.
- 두 WebSocket의 연결·메시지·끊김 후 재연결을 확인한다.
- 이동 Front의 일반 HTTP·채팅 경로가 거부되는지 확인한다.
- 로그아웃 시 이동 저장 응답 이후 HTTP 로그아웃이 실행되는지 확인한다.
- 새로 로그인한 세션과 이전 세션의 종료를 확인한다.

Cloudflare 구성·실제 공개 HTTPS/WSS 연결은 이번 작업의 검증 대상에서 제외한다.
서버만 실행한 결과와 외부 연결까지 확인한 결과를 구분한다.

## 5. 변경 이력

| 날짜 | 변경 | 변경한 절 |
|---|---|---|
| 2026-10-23 | 외부 설정을 제외한 연결 명세 작성 | 1. 전달 경로 · 2. 주소와 접근 범위 · 3. HTTP·WebSocket 전달 조건 · 4. 연결 후 확인 · 5. 변경 이력 |
