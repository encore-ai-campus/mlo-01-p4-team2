# 100명 동시 사용자 스트레스 테스트 보고서

- 작성일: 2026-11-06T10:50:35+09:00
- 검토 상태: **두 독립 검토자 모두 승인 · 2차 최종본**
- 대상 코드: `c6290deebbc431e6393396345442bcad77e84c23` · event 브랜치
- 판정: **일반 부하는 양호합니다. 동시 접속·결과 저장 오류로 운영 수용은 보류합니다.**
- 근거: [계산 결과](loadtest/results/20261106-local33/results.json), [검증 기록](loadtest/results/20261106-local33/scenario-checks.json), [측정 원본 목록](loadtest/results/20261106-local33/sha256.json)

## 1. 핵심 결과

일반 부하 HTTP 응답 표본 P95는 **98.58ms**였습니다.
높은 행동 부하 HTTP 응답 표본 P95는 **724.83ms**였습니다.
두 구간 모두 프로토콜 사용자 99명과 실제 Chrome 사용자 1명을 실행했습니다.
Chrome 100개를 실행한 결과는 아닙니다.

100계정 기능 요청에서는 WebSocket 502와 HTTP 500이 발생했습니다.
서버 로그에서 PostgreSQL의 `too many clients already` 오류를 확인했습니다.
연결 상한 100개는 사용자 100명을 뜻하지 않습니다. HTTP·소켓·jobs·관측 연결이 함께 사용합니다.
응답 속도가 빨라도 실패한 요청은 통과로 처리하지 않았습니다.

## 2. 실행 환경과 보호 범위

| 항목 | 실제 구성 |
| --- | --- |
| 장비 | Apple M1 Pro, 논리 CPU 8, RAM 16GiB |
| OS / Python | macOS-14.6.1-arm64-arm-64bit / 3.12.11 |
| 코드 | event c6290de를 git archive로 분리한 소스 |
| 입구 | 127.0.0.1:8096 → 로컬 Nginx → 역할별 프런트 프록시 2개 |
| 이동 | Nginx 8097 → movement ASGI 9551·9552·9553, least_conn |
| HTTP·채팅 | Nginx 8098 → service ASGI 9561·9562·9563, 세션 쿠키 해시 |
| jobs | 별도 campus_jobs 프로세스 1개 |
| PostgreSQL | 17.7 (Debian 17.7-3.pgdg12+1), 55480, 전용 DB local33_20261106 |
| DB 설정 | max_connections=100, shared_buffers=32MB |
| Redis | 이동 56391 / 서비스 알림 56392, Redis 7.0.15, 전용 인스턴스 |
| Nginx | nginx version: nginx/1.31.8 |
| Chrome | Chrome/156.0.8078.81 / headless=new / 독립 임시 프로필 |
| DEBUG / 광고 | DEBUG=0, ADS_ENABLED=0, 광고 외부 연결 미설정 |
| 문제은행 | 문항 3,600개와 선택지를 참조 데이터로 복사. 실제 사용자 기록은 복사하지 않음 |
| 계정 | 서로 다른 테스트 계정 load000~099, 각각 별도 로그인 세션 |
| 보호 | 기존 8004 서버·55474 DB의 사용자·진척·업적·당첨 기록을 수정하지 않음 |

운영과 같은 역할 분리 구조를 로컬에서 구성했습니다.
운영 인프라의 CPU 크기·네트워크·TLS·고가용성까지 동일하게 재현한 결과는 아닙니다.
서버·Chrome·부하 생성기·Docker가 한 장비의 자원을 공유했습니다.
기존 로컬 서버도 보존했습니다. 전용 장비처럼 다른 프로세스를 모두 중지한 조건은 아닙니다.
기존 운영 배포 설정의 예시 기본값은 4+4입니다. 이번 실측은 요청한 3+3입니다.

실제 날짜는 이벤트 시작 전이었습니다. 원래 시간 정책에서는 메인 행동이 423으로 거절됐습니다.
기능 시험에서는 전용 실행 래퍼가 `event_story.main.window()`만 open으로 바꿨습니다.
시스템 시간·세션 만료·게임 경과 시간·DEBUG는 바꾸지 않았습니다.
기존 `event_story.services.window` 정책까지 동일하게 바꾼 것은 아닙니다.
세션 자동 청소는 시험 중 비활성화했습니다. 해당 작업의 부하는 포함하지 않았습니다.

### 실제 프로세스 분산 근거

| 구간 | 같은 순간의 이동 연결(9551/9552/9553) | 서비스 upstream 완료 응답 수 |
| --- | --- | --- |
| 일반 | {'9551': 32, '9552': 35, '9553': 33} | {'8098:127.0.0.1:9563:200': 749, '8098:127.0.0.1:9563:201': 16, '8098:127.0.0.1:9562:200': 1375, '8098:127.0.0.1:9561:200': 1278, '8098:127.0.0.1:9562:201': 22, '8098:127.0.0.1:9561:201': 33} |
| 높은 행동 | {'9551': 33, '9552': 32, '9553': 35} | {'8098:127.0.0.1:9563:200': 1067, '8098:127.0.0.1:9561:200': 1894, '8098:127.0.0.1:9562:200': 1995, '8098:127.0.0.1:9562:201': 179, '8098:127.0.0.1:9563:201': 99, '8098:127.0.0.1:9561:201': 205} |

upstream 응답 수는 Nginx 로그 완료 시각 기준입니다. HTTP 시작 집단과 합산 비교하지 않습니다.
[프로세스 PID·포트·DB·Redis 증명](loadtest/results/20261106-local33/topology.json)을 함께 저장했습니다.

## 3. 방법과 판정 범위

| 구간 | 규모·시간 | 행동·한계 |
| --- | --- | --- |
| 일반 부하 | 99 프로토콜 + Chrome 1 / 증가15초·준비15초·측정180초 | 평균20% 이동, 간헐적 채팅·점프·찌르기·패널 |
| 높은 행동 부하 | 같은 100계정 / 같은 측정 시간 | 빈번한 이동 입력·점프·상호작용·이동·패널 |
| 동시 기능 요청 | 100계정 / 단계별 동시 시작 | 단말 게임·메인 게임·레시피·학습·결과 재전송 |
| NPC 추가 시나리오 | 100계정 / 접속만 3.96초 증가 | 근접 이동·리필·매니저 인사·잔량1 대화·개인 방 찌르기 |
| 재시작 검증 | 100계정 / HTTP 동시10개 | DB 상태 해시와 재시작 후 API를 비교 |
| 별도 진단 | 본 부하와 분리 | SQL 횟수·소켓 DB 큐와 작업 시간 |
| 기능 회귀 | 별도 테스트 DB | 전체 백엔드 및 프런트 테스트. 실패도 그대로 기록 |

P95는 값을 정렬한 뒤 `ceil(0.95 × N)`번째 값을 사용했습니다.
혼합 HTTP는 fetch 내부 네트워크 실행부터 본문 수신까지 측정했습니다. 실행 대기열의 대기는 제외합니다.
기능 시나리오 HTTP는 to_thread 제출 전부터 측정합니다. 실행 대기열의 대기도 포함합니다.
실패 응답도 응답 시간 표본에 포함했습니다. 두 종류의 시간을 완전히 같은 지표로 비교하지 않습니다.
혼합 부하의 집계 집단은 측정 구간에 호출한 요청입니다. P95는 그중 응답이 있는 표본으로 계산했습니다.
미응답이 있으면 전체 시작 요청의 정확한 P95는 확정하지 않습니다.
종료 시 미응답은 별도로 셌습니다. 미응답에 임의의 응답 시간을 만들지 않았습니다.
구간 종료는 생성기 집계 종료 기준입니다. 진행 중 요청은 서버에서 이후 완료될 수 있습니다.
기존 완료 시점 집계는 비교용 원본에 보존했습니다. 시작 시점 집계와 표본 수가 다릅니다.
시간 초과 제어 요청은 나중에 응답해도 시간 초과로 유지합니다.
완료 요청 P95만으로 전체 요청의 완료 시간을 보장하지 않습니다.
업적 결과 제출은 시작 요청 지연과 게임 대기 때문에 완전히 같은 시각에 도착하지 않습니다.

## 4. 혼합 부하 P95

| 구간 | 요청 | 시작 N | 응답 N | P95 ms | 미응답 | 결과 수 |
| --- | --- | --- | --- | --- | --- | --- |
| 일반 | HTTP | 3437 | 3437 | 98.58 | 0 | {'200': 3366, '201': 71} |
| 일반 | 채팅 | 1129 | 1129 | 62.87 | 0 | {'ok': 1129} |
| 일반 | jump | 699 | 699 | 4.81 | 0 | {'ok': 699} |
| 높은 행동 | HTTP | 5396 | 5391 | 724.83 | 5 | {'200': 4909, '201': 482, 'pending': 5} |
| 높은 행동 | 채팅 | 1497 | 1497 | 616.07 | 0 | {'ok': 1497} |
| 높은 행동 | begin_transfer | 214 | 214 | 690.17 | 0 | {'ok': 214} |
| 높은 행동 | interact | 863 | 863 | 663.10 | 0 | {'ok': 832, 'rejected': 31} |
| 높은 행동 | jump | 8677 | 8677 | 297.26 | 0 | {'ok': 8477, 'rejected': 200} |
| 높은 행동 | open_booth | 35 | 35 | 703.03 | 0 | {'rejected': 31, 'ok': 4} |
| 높은 행동 | transfer | 212 | 212 | 1,081.68 | 0 | {'ok': 212} |

상호작용 거리·점프 상태에 따른 거절은 업무상 응답입니다. 서버 장애와 구분합니다.
높은 부하의 HTTP 미응답은 구간 종료 시 대기 상태입니다. 30초 시간 초과라고 단정하지 않았습니다.

### 일반 부하의 HTTP 경로별 결과

| 경로·패널 | 시작 N | 응답 N | P95 ms | 미응답 | 상태 수 |
| --- | --- | --- | --- | --- | --- |
| /accounts/session/activity/ | 919 | 919 | 55.72 | 0 | {'200': 919} |
| /api/event-main/ranking/ | 313 | 313 | 92.14 | 0 | {'200': 313} |
| /api/event-main/status/ | 313 | 313 | 158.75 | 0 | {'200': 313} |
| /api/event-story/achievements/ | 313 | 313 | 54.28 | 0 | {'200': 313} |
| /api/event-story/recipe/status/ | 313 | 313 | 49.67 | 0 | {'200': 313} |
| community | 304 | 304 | 44.56 | 0 | {'200': 304} |
| dm_send | 142 | 142 | 86.24 | 0 | {'200': 71, '201': 71} |
| lunch | 82 | 82 | 66.88 | 0 | {'200': 82} |
| menu | 136 | 136 | 39.58 | 0 | {'200': 136} |
| messages | 298 | 298 | 75.28 | 0 | {'200': 298} |
| wallet | 304 | 304 | 49.72 | 0 | {'200': 304} |

### 높은 행동 부하의 HTTP 경로별 결과

| 경로·패널 | 시작 N | 응답 N | P95 ms | 미응답 | 상태 수 |
| --- | --- | --- | --- | --- | --- |
| /accounts/session/activity/ | 1154 | 1154 | 264.25 | 0 | {'200': 1154} |
| /api/event-main/ranking/ | 377 | 377 | 517.52 | 0 | {'200': 377} |
| /api/event-main/status/ | 379 | 376 | 850.98 | 3 | {'200': 376, 'pending': 3} |
| /api/event-story/achievements/ | 379 | 379 | 227.93 | 0 | {'200': 379} |
| /api/event-story/recipe/status/ | 379 | 379 | 210.07 | 0 | {'200': 379} |
| community | 298 | 297 | 217.07 | 1 | {'200': 297, 'pending': 1} |
| dm_send | 118 | 118 | 413.42 | 0 | {'200': 59, '201': 59} |
| learning | 1454 | 1453 | 1,677.38 | 1 | {'200': 1030, '201': 423, 'pending': 1} |
| lunch | 66 | 66 | 238.20 | 0 | {'200': 66} |
| menu | 156 | 156 | 197.19 | 0 | {'200': 156} |
| messages | 338 | 338 | 355.70 | 0 | {'200': 338} |
| wallet | 298 | 298 | 229.30 | 0 | {'200': 298} |

혼합 부하의 두 측정 구간은 각각 다음 UTC 시각입니다.
```json
{"casual": [1793928161.713481, 1793928341.947896], "stress": [1793928395.972965, 1793928576.147667]}
```

### 이동·Chrome 참고 지표

| 구간 | 자기 상태 간격 P95 | 입력→이동 상태 추정 P95 | 다른 유저 갱신 간격 P95 | 생성기 루프 지연 P95 | Chrome rAF 간격 P95 |
| --- | --- | --- | --- | --- | --- |
| 일반 | 36.27 | 5.45 | 109.61 | 0.75 | 16.80 |
| 높은 행동 | 56.27 | 69.34 | 245.03 | 12.48 | 16.70 |

이동 입력에는 요청별 ID가 없습니다. 입력→상태 값은 정확한 입력별 왕복 시간이 아닙니다.
다른 유저 갱신 간격은 거리별 전송 주기에도 영향을 받습니다.
rAF 간격은 화면 콜백 간격입니다. 실제 그리기 FPS나 입력→화면 표시 지연을 뜻하지 않습니다.
Chrome 요청에는 실제 UI 요청과 20초 간격 업적 조회 probe가 포함됩니다.
Chrome 결과를 전체 100계정 HTTP P95에 섞지 않았습니다.
[측정 중 Chrome 화면](loadtest/results/20261106-local33/chrome-real-during.png)에서 접속100명과 독립 앱 탭1개를 확인했습니다.
측정 종료 후 화면은 일부 생성기 연결이 닫힌 뒤 촬영됐습니다.

## 5. 업적·게임·레시피·학습 요청

메인 진행은 8개 선행 단계 완료를 DB에 사전 입력했습니다. 이동·선행 단계 수행 시간은 포함하지 않았습니다.
`investigate`는 기존 단계 재요청입니다. ROOM1 안내도 이미 받은 계정에서는 재요청입니다.
메인 게임 6종은 실제 시작·제출 API를 호출했습니다.
토핑은 고정 실패 경로를 제출했습니다. 토핑 선행 완료 값으로 포장·최종 전달을 별도로 시험했습니다.
따라서 자연스러운 메인 이벤트 전 과정 성공을 검증했다고 주장하지 않습니다.
단말 타이핑은 성공 시간과 11.11초 초과 거절·중단·다시 제출을 확인했습니다.
단말 페어링은 서버 카드 배치로 짝을 계산했습니다. 사람이 카드를 외우는 난이도 시험은 아닙니다.
학습은 Python·SQL·CS와 객관식·빈칸을 나눠 호출했습니다. 계정별 10문항과 같은 제출 UUID 재요청을 검사했습니다.
레시피는 두 토핑 분기를 나눠 호출했습니다. 최초 획득부터 완료·재조회까지 검사했습니다.
매니저 업적은 다른 매니저의 인사·찌르기 누적값을 사전 입력했습니다.
마지막 인사와 마지막 누적 적중을 실제 요청했습니다. 모든 누적 행동을 처음부터 수행한 시험은 아닙니다.
공개 공간의 잔량1 조건은 실제 리필 후 실제 적중9회를 시도했습니다.
일반 부하 조회 데이터는 최종 완료96명·레시피 보유96명·완료92명·업적10개를 미리 채웠습니다.
주요 기능 시험 전에는 최종 완료·레시피·게임 실행·이스터에그를 전용 DB에서 초기화했습니다.

### 주요 기능 요청별 P95

| 요청 | N | P50 ms | P95 ms | P99 ms | 예상 밖 응답 | 상태 수 |
| --- | --- | --- | --- | --- | --- | --- |
| achievements:restore | 96 | 413.56 | 556.76 | 662.95 | 0 | {'200': 96} |
| learning:answer | 960 | 1325.79 | 1870.81 | 2098.94 | 0 | {'201': 960} |
| learning:answer_retry | 960 | 643.25 | 958.79 | 1093.01 | 0 | {'201': 960} |
| learning:complete | 96 | 835.1 | 1132.96 | 1301.0 | 0 | {'200': 96} |
| learning:question | 960 | 647.41 | 995.21 | 1154.92 | 0 | {'200': 960} |
| learning:start | 100 | 4859.99 | 5193.94 | 5262.48 | 4 | {'500': 4, '201': 96} |
| main:deliver | 192 | 2878.41 | 4364.46 | 4445.12 | 0 | {'200': 192} |
| main:game_finish:card_match | 96 | 623.18 | 1252.06 | 1479.61 | 0 | {'200': 96} |
| main:game_finish:coating | 96 | 272.1 | 522.51 | 1226.02 | 0 | {'200': 96} |
| main:game_finish:kkuwang_touch | 96 | 296.79 | 1263.4 | 1344.13 | 0 | {'200': 96} |
| main:game_finish:sql_memory | 96 | 1101.35 | 1639.11 | 1774.1 | 0 | {'200': 96} |
| main:game_finish:topping | 96 | 200.24 | 782.35 | 837.68 | 0 | {'200': 96} |
| main:game_finish:wrapping | 96 | 377.35 | 626.05 | 791.46 | 0 | {'200': 96} |
| main:game_start:card_match | 96 | 679.79 | 1257.1 | 1391.29 | 0 | {'200': 96} |
| main:game_start:coating | 96 | 425.67 | 1053.65 | 1134.24 | 0 | {'200': 96} |
| main:game_start:kkuwang_touch | 96 | 791.56 | 1329.05 | 1495.04 | 0 | {'200': 96} |
| main:game_start:sql_memory | 96 | 1130.61 | 1455.74 | 1618.03 | 0 | {'200': 96} |
| main:game_start:topping | 96 | 221.92 | 495.78 | 573.44 | 0 | {'200': 96} |
| main:game_start:wrapping | 96 | 278.99 | 672.25 | 829.95 | 0 | {'200': 96} |
| main:investigate | 100 | 1444.41 | 2124.75 | 2256.09 | 4 | {'500': 4, '200': 96} |
| main:restore | 100 | 2072.03 | 2261.72 | 2292.84 | 4 | {'500': 4, '200': 96} |
| main:room1_briefing | 96 | 1713.09 | 1995.42 | 2107.07 | 0 | {'200': 96} |
| main:sql_answer | 96 | 1191.1 | 1398.28 | 1446.57 | 0 | {'200': 96} |
| recipe:acquire | 100 | 721.15 | 1211.54 | 1254.08 | 7 | {'200': 93, '500': 3, '409': 4} |
| recipe:defer | 93 | 759.16 | 970.83 | 1139.72 | 0 | {'200': 93} |
| recipe:finish | 93 | 901.11 | 1049.53 | 1196.25 | 0 | {'200': 93} |
| recipe:recognize | 93 | 808.15 | 917.9 | 1027.97 | 0 | {'200': 93} |
| recipe:restore | 189 | 297.92 | 538.78 | 627.79 | 0 | {'200': 189} |
| recipe:start | 93 | 737.65 | 951.25 | 1025.1 | 0 | {'200': 93} |
| recipe:topping | 93 | 625.66 | 756.63 | 820.0 | 0 | {'200': 93} |
| term_match:result | 98 | 982.02 | 1617.04 | 1688.86 | 0 | {'200': 98} |
| term_match:retry | 98 | 1095.59 | 1444.54 | 1517.59 | 2 | {'500': 2, '200': 96} |
| timer:result | 76 | 874.18 | 1222.56 | 1283.89 | 0 | {'200': 76} |
| timer:retry | 76 | 820.1 | 1123.34 | 1187.82 | 0 | {'200': 76} |
| typing:interrupt | 81 | 82.22 | 125.57 | 270.97 | 0 | {'200': 81} |
| typing:over_limit | 81 | 71.96 | 125.21 | 819.61 | 0 | {'400': 81} |
| typing:result | 81 | 757.0 | 1156.04 | 1195.43 | 0 | {'200': 81} |
| typing:retry | 81 | 854.41 | 1100.51 | 1175.51 | 0 | {'200': 81} |
| ws:game_start | 336 | 445.88 | 1138.5 | 1279.4 | 0 | {'ok': 336} |
| ws:guide_talk | 255 | 96.36 | 181.34 | 204.25 | 0 | {'rejected': 255} |
| ws_connect | 341 | 774.7 | 1921.66 | 1975.54 | 0 | {'101': 341} |

### NPC 추가 요청별 P95

| 요청 | N | P50 ms | P95 ms | P99 ms | 예상 밖 응답 | 상태 수 |
| --- | --- | --- | --- | --- | --- | --- |
| ws:guide_talk | 99 | 729.29 | 1097.76 | 1133.65 | 0 | {'ok': 99} |
| ws:manager_greet | 100 | 874.6 | 1433.18 | 1522.69 | 0 | {'ok': 100} |
| ws:pepero_refill | 100 | 132.87 | 239.4 | 249.82 | 0 | {'ok': 100} |
| ws:poke | 892 | 16.74 | 245.25 | 358.86 | 0 | {'ok': 892} |
| ws:quick_travel | 200 | 130.85 | 228.91 | 248.11 | 0 | {'ok': 200} |
| ws_connect | 100 | 31.96 | 1224.54 | 1415.26 | 0 | {'101': 100} |

### 개인 방 매니저 찌르기 요청별 P95

| 요청 | N | P50 ms | P95 ms | P99 ms | 예상 밖 응답 | 상태 수 |
| --- | --- | --- | --- | --- | --- | --- |
| ws:mission_room_enter | 100 | 121.2 | 226.62 | 239.46 | 0 | {'ok': 100} |
| ws:pepero_refill | 100 | 130.7 | 272.74 | 301.63 | 0 | {'ok': 100} |
| ws:poke | 100 | 651.04 | 1033.93 | 1078.37 | 0 | {'ok': 100} |
| ws:quick_travel | 100 | 461.75 | 659.66 | 671.91 | 0 | {'ok': 100} |
| ws_connect | 100 | 35.09 | 1215.16 | 1383.38 | 0 | {'101': 100} |

타격 챌린지는 성공 bool 대신 FINISHED와 점수로 종료합니다. 다른 게임의 SUCCESS와 구분합니다.
메인 전달 main:deliver는 최초 요청과 같은 결과 재요청을 합친 표본입니다.
### 서버 게임 판정

| 요청 | 성공·실패·중단 판정 수 |
| --- | --- |
| timer:result | {'SUCCESS': 76} |
| timer:retry | {'SUCCESS': 76} |
| typing:result | {'SUCCESS': 81} |
| typing:retry | {'SUCCESS': 81} |
| typing:interrupt | {'INTERRUPTED': 81} |
| term_match:result | {'SUCCESS': 98} |
| term_match:retry | {'SUCCESS': 96} |
| main:game_finish:sql_memory | {'True': 96} |
| main:game_finish:card_match | {'True': 96} |
| main:game_finish:coating | {'True': 96} |
| main:game_finish:topping | {'False': 96} |
| main:game_finish:wrapping | {'True': 96} |

### 단계 전체 검증 결과

| 구간 | 검사 | 시도 계정 | 통과 계정 | 실패 이유 |
| --- | --- | --- | --- | --- |
| 주요 | connect:timer | 100 | 76 | {'server rejected WebSocket connection: HTTP 502': 24} |
| 주요 | standalone:timer | 100 | 76 | {"'NoneType' object has no attribute 'send'": 24} |
| 주요 | far_guide_rejection:timer | 100 | 76 | {"'NoneType' object has no attribute 'send'": 24} |
| 주요 | disconnect:timer | 100 | 100 | {} |
| 주요 | connect:typing | 100 | 81 | {'server rejected WebSocket connection: HTTP 502': 19} |
| 주요 | standalone:typing | 100 | 81 | {"'NoneType' object has no attribute 'send'": 4, 'sent 1000 (OK); then received 1000 (OK)': 15} |
| 주요 | far_guide_rejection:typing | 100 | 81 | {"'NoneType' object has no attribute 'send'": 4, 'sent 1000 (OK); then received 1000 (OK)': 15} |
| 주요 | disconnect:typing | 100 | 100 | {} |
| 주요 | connect:term_match | 100 | 98 | {'server rejected WebSocket connection: HTTP 502': 2} |
| 주요 | standalone:term_match | 100 | 96 | {"term_match:retry: 500 {'error': 'non-json response'}": 2, 'sent 1000 (OK); then received 1000 (OK)': 2} |
| 주요 | far_guide_rejection:term_match | 100 | 98 | {'sent 1000 (OK); then received 1000 (OK)': 2} |
| 주요 | main_games_and_final_rank | 100 | 96 | {"main:investigate: 500 {'error': 'non-json response'}": 4} |
| 주요 | recipe_full_branches | 100 | 93 | {"recipe:acquire: 409 {'error': 'main_quest_required', 'message': '메인 미션을 완료하면 주울 것 같이 생겼어요'}": 4, "recipe:acquire: 500 {'error': 'non-json response'}": 3} |
| 주요 | learning_all_subjects_and_types | 100 | 96 | {"learning:start: 500 {'error': 'non-json response'}": 4} |
| 주요 | disconnect:final | 100 | 100 | {} |
| 주요 | reconnect:restore | 100 | 86 | {'server rejected WebSocket connection: HTTP 502': 14} |
| 주요 | state_after_reconnect | 100 | 96 | {"main:restore: 500 {'error': 'non-json response'}": 4} |
| 주요 | disconnect:cleanup | 100 | 100 | {} |
| NPC | connect:npc | 100 | 100 | {} |
| NPC | pepero_refill | 100 | 100 | {} |
| NPC | quick_travel:market | 100 | 100 | {} |
| NPC | walk:manager | 100 | 100 | {} |
| NPC | manager_greet | 100 | 100 | {} |
| NPC | manager_poke_with_inventory | 100 | 99 | {'unblocked hit required': 1} |
| NPC | quick_travel:gallery | 100 | 100 | {} |
| NPC | walk:guide | 100 | 100 | {} |
| NPC | guide_talk_one_remaining | 100 | 99 | {'server inventory=10': 1} |
| NPC | close:npc | 100 | 100 | {} |
| 찌르기 | connect:private_poke | 100 | 100 | {} |
| 찌르기 | refill:private_poke | 100 | 100 | {} |
| 찌르기 | travel:private_poke | 100 | 100 | {} |
| 찌르기 | walk:room1 | 100 | 100 | {} |
| 찌르기 | enter:room1 | 100 | 100 | {} |
| 찌르기 | walk:private_manager | 100 | 100 | {} |
| 찌르기 | poke:eleventh_manager_hit | 100 | 100 | {} |
| 찌르기 | close:private_poke | 100 | 100 | {} |

접속에 실패한 계정의 후속 소켓 오류는 접속 실패에서 이어진 결과입니다.
그 계정에서 게임 요청이 실제 실행됐다고 세지 않았습니다. 표본 N을 그대로 제시했습니다.

주요 기능 HTTP 500 수와 전체 응답 수는 아래와 같습니다. 소켓 연결502는 이 분모에 포함하지 않습니다.
```json
{
  "responses": 6334,
  "statuses": {
    "200": 4216,
    "400": 81,
    "500": 17,
    "409": 4,
    "201": 2016
  },
  "server_5xx": 17
}
```

### 신규 업적 지급 표본

| 요청 | 신규 지급 수 | 신규 지급 응답 N | 신규 지급 P95 ms | 추가 지급 없는 응답 N | 추가 지급 없는 P95 ms |
| --- | --- | --- | --- | --- | --- |
| ws:manager_greet | 1 | 1 | 327.24 | 99 | 1,465.59 |
| ws:guide_talk | 1 | 1 | 90.34 | 98 | 1,097.76 |
| timer:result | 1 | 1 | 406.45 | 75 | 1,222.56 |
| timer:retry | 0 | 0 | — | 76 | 1,123.34 |
| typing:result | 1 | 1 | 461.67 | 80 | 1,156.04 |
| typing:retry | 0 | 0 | — | 81 | 1,100.51 |
| typing:interrupt | 0 | 0 | — | 81 | 125.57 |
| term_match:result | 1 | 1 | 641.69 | 97 | 1,617.04 |
| term_match:retry | 0 | 0 | — | 96 | 1,444.54 |
| recipe:finish | 1 | 1 | 703.71 | 92 | 1,049.53 |
| learning:complete | 3 | 3 | 924.09 | 93 | 1,132.96 |

이스터에그당 1명·계정당 1개 정책을 유지한 결과입니다.
지급 수 1개의 시간은 단일 관측값입니다. 일반적인 신규 지급 P95라고 해석하지 않습니다.
신규 지급이 없는 성공 응답은 이미 소유했거나, 다른 계정이 먼저 받은 경우를 포함합니다.
찌르기 업적 알림은 별도 메시지입니다. 이번 생성기는 그 알림의 신규 지급 시각을 기록하지 않았습니다.
따라서 찌르기는 제어 응답 P95와 최종 DB 기록으로 확인했습니다. 신규 지급 응답 표본 합계에서 빠집니다.
게임 성공과 업적 신규 지급은 별개입니다. 메인 최종 순위는 별도 표에 포함됩니다.

## 6. 저장·재시작·여러 프로세스 검증

다른 이동 프로세스 사이의 이동·점프·찌르기와 다른 서비스 프로세스 사이의 채팅을 확인했습니다.
업적 알림 공유와 세 번째 서비스 프로세스에서 실행한 로그아웃 전파도 확인했습니다.
로그아웃한 계정의 이동·채팅은 4401로 닫혔고, 다른 계정의 연결은 유지됐습니다.
사전 검증의 시험용 업적 1행은 본 측정 전에 전용 DB에서 제거했습니다.

### DB 정합성

```json
{
  "egg_count": 10,
  "duplicate_users": [],
  "duplicate_eggs": [],
  "invalid_egg_metadata": 0,
  "egg_order_not_one": 0,
  "eggs_missing_session": 0,
  "main_completed": 96,
  "main_rank_unique": true,
  "main_ranks_contiguous": true,
  "main_missing_session": 0,
  "recipe_completed": 93,
  "plays": {
    "FAILED": 96,
    "INTERRUPTED": 81,
    "SUCCESS": 639,
    "FINISHED": 96
  },
  "learning_completed": 96,
  "answers": 1257,
  "ledger_rows": 993
}
```

### 서버 재시작

```json
{
  "accounts": 100,
  "http_concurrency": 10,
  "passed": 100,
  "errors": [],
  "progress_and_history_unchanged": true,
  "before": {
    "event_progress": {
      "rows": 1002,
      "sha256": "35493607780e14cd512fa9f95619a0dc7406fe11892acecd08ccc0544d2ace34"
    },
    "event_story_storyprogress": {
      "rows": 191,
      "sha256": "8ef67511d49c4adb1459f9471259d7dce11206845610c8f41338c50ed3b0345a"
    },
    "event_story_storyrecord": {
      "rows": 656,
      "sha256": "0b32815af8bee06636add55f45884e4aaf6f68cadde25333b24f40e9e4587ff1"
    }
  },
  "after": {
    "event_progress": {
      "rows": 1002,
      "sha256": "35493607780e14cd512fa9f95619a0dc7406fe11892acecd08ccc0544d2ace34"
    },
    "event_story_storyprogress": {
      "rows": 191,
      "sha256": "8ef67511d49c4adb1459f9471259d7dce11206845610c8f41338c50ed3b0345a"
    },
    "event_story_storyrecord": {
      "rows": 656,
      "sha256": "0b32815af8bee06636add55f45884e4aaf6f68cadde25333b24f40e9e4587ff1"
    }
  }
}
```

### 역할·공유 사전 검사

```json
{
  "attestations": [
    {
      "worker": "movement-9552",
      "pid": 30305,
      "database": "local33_20261106",
      "db_port": "55480",
      "movement_redis": "redis://127.0.0.1:56391/0",
      "service_redis": "redis://127.0.0.1:56392/0",
      "secret_fingerprint": "dd7c62b4d7c82fd8e2eea826544dd0e3e672f8c0e050fbc996dddb8d38200d7b",
      "debug": false,
      "event_window_override": false
    },
    {
      "worker": "service-9562",
      "pid": 30308,
      "database": "local33_20261106",
      "db_port": "55480",
      "movement_redis": "redis://127.0.0.1:56391/0",
      "service_redis": "redis://127.0.0.1:56392/0",
      "secret_fingerprint": "dd7c62b4d7c82fd8e2eea826544dd0e3e672f8c0e050fbc996dddb8d38200d7b",
      "debug": false,
      "event_window_override": false
    },
    {
      "worker": "service-9563",
      "pid": 30309,
      "database": "local33_20261106",
      "db_port": "55480",
      "movement_redis": "redis://127.0.0.1:56391/0",
      "service_redis": "redis://127.0.0.1:56392/0",
      "secret_fingerprint": "dd7c62b4d7c82fd8e2eea826544dd0e3e672f8c0e050fbc996dddb8d38200d7b",
      "debug": false,
      "event_window_override": false
    },
    {
      "worker": "movement-9553",
      "pid": 30306,
      "database": "local33_20261106",
      "db_port": "55480",
      "movement_redis": "redis://127.0.0.1:56391/0",
      "service_redis": "redis://127.0.0.1:56392/0",
      "secret_fingerprint": "dd7c62b4d7c82fd8e2eea826544dd0e3e672f8c0e050fbc996dddb8d38200d7b",
      "debug": false,
      "event_window_override": false
    },
    {
      "worker": "jobs",
      "pid": 30310,
      "database": "local33_20261106",
      "db_port": "55480",
      "movement_redis": "redis://127.0.0.1:56391/0",
      "service_redis": "redis://127.0.0.1:56392/0",
      "secret_fingerprint": "dd7c62b4d7c82fd8e2eea826544dd0e3e672f8c0e050fbc996dddb8d38200d7b",
      "debug": false,
      "event_window_override": false
    },
    {
      "worker": "movement-9551",
      "pid": 30304,
      "database": "local33_20261106",
      "db_port": "55480",
      "movement_redis": "redis://127.0.0.1:56391/0",
      "service_redis": "redis://127.0.0.1:56392/0",
      "secret_fingerprint": "dd7c62b4d7c82fd8e2eea826544dd0e3e672f8c0e050fbc996dddb8d38200d7b",
      "debug": false,
      "event_window_override": false
    },
    {
      "worker": "service-9561",
      "pid": 30307,
      "database": "local33_20261106",
      "db_port": "55480",
      "movement_redis": "redis://127.0.0.1:56391/0",
      "service_redis": "redis://127.0.0.1:56392/0",
      "secret_fingerprint": "dd7c62b4d7c82fd8e2eea826544dd0e3e672f8c0e050fbc996dddb8d38200d7b",
      "debug": false,
      "event_window_override": false
    }
  ],
  "real_event_window_rejects_early_action": 423,
  "movement_rejects_http_api": true,
  "distinct_movement_workers": [
    "movement-9551",
    "movement-9552"
  ],
  "cross_worker_movement": true,
  "cross_worker_jump": true,
  "cross_worker_poke": true,
  "distinct_chat_workers": [
    "service-9561",
    "service-9562"
  ],
  "cross_worker_chat": true,
  "cross_worker_achievement_discovery": true,
  "logout_from_third_service_worker_closes_movement_and_chat": [
    4401,
    4401
  ],
  "other_account_remains_connected": true
}
```

DB 정합성 수치는 혼합 부하·기능·별도 진단을 거친 전용 DB의 누적값입니다.
학습 답변 수를 특정100계정 시나리오의 신규 답변 수로 해석하지 않습니다.
동시 WebSocket 재접속은86/100계정이 성공했습니다. 이어진 HTTP 상태 조회는96/100계정이 성공했습니다.
HTTP 조회는 소켓 재접속 성공 여부와 무관하게100계정 모두에서 실행했습니다.
HTTP 조회의 나머지4계정은500 응답으로 실패했습니다. 소켓 재접속 성공96명이라는 뜻은 아닙니다.
서버 재시작 후100/100 복구 확인은 HTTP 동시10개로 실행한 별도 검사입니다.
저장 상태는 DB에 실제 남은 상태를 기준으로 비교했습니다.
실패한 계정이 완료되지 않은 상태를 유지하는 것도 정상 복구로 구분했습니다.
재시작 비교는 앱 6개와 jobs를 재시작한 결과입니다. PostgreSQL·Redis 장애 복구 시험은 아닙니다.

## 7. 지연·실패 원인과 근거

### 확인된 원인

[서버 오류 발췌](loadtest/results/20261106-local33/server-errors.txt)와 [Nginx 오류](loadtest/results/20261106-local33/nginx-error.log)를 함께 확인할 수 있습니다.

1. PostgreSQL 연결 상한에 걸려 저장 요청이 500으로 실패했습니다.
   세 이동 프로세스의 소켓 DB 연결·jobs·관측 연결도 같은 100개 상한을 사용합니다.
   사용자 100명이 HTTP를 함께 보내면 사용자 수보다 많은 연결을 요구할 수 있습니다.
2. 초기 Docker 프록시 시험에서 frontend9582 연결 단계의 504가 302건 발생했습니다.
   Docker→호스트 연결 경로를 제거한 로컬 Nginx로 다시 측정했습니다. 실패 원본은 보존했습니다.
   이 초기 시험에서는 Chrome 이동 소켓도 4409로 종료됐습니다. 최종 100명 유지 결과에서 제외했습니다.
   최종 Chrome은 headless 독립 프로필이며, 측정 중 앱 탭1개와 소켓 유지 상태를 확인했습니다.
3. 순간 100개 WebSocket 연결에서 프런트9581 구간의 연결 reset과 502가 발생했습니다.
   접속을 3.96초에 나눈 NPC 시험은 별도 조건입니다. 초기 접속 실패를 지우는 재시험이 아닙니다.
   reset의 운영체제·listen backlog별 기여도는 이번 자료만으로 확정하지 않았습니다.

### 코드와 별도 진단으로 확인할 지연 구조

순위 조회는 최대25명 닉네임을 읽으며 프로필을 추가 조회합니다.
`event_story/main.py:146`의 ranking과 `accounts/models.py`의 get_nickname이 연결됩니다.
메인 status도 순위25명을 구성한 뒤 상위3명만 반환합니다. 요청마다 불필요한 조회가 남습니다.
`campus/socket_db.py`는 이동 프로세스마다 DB 작업 스레드 하나를 둡니다.
3+3에서는 큐가 프로세스별로 나뉩니다. HTTP 전체가 같은 스레드에서 실행되지는 않습니다.
점프 자체가 매번 SQL을 실행하는 구조라고 해석하면 안 됩니다.
업적은 계정과 해당 업적 정의 행을 잠급니다. 최종 순위는 공통 최종 단계 정의 행을 잠급니다.
동일 업적·최종 순위 요청은 서로 기다릴 수 있습니다. Redis는 이 SQL과 락을 제거하지 않습니다.
프로세스 수·프록시·Redis가 함께 달라졌으므로 개선을 Redis 하나의 효과로 단정하지 않습니다.

SQL 진단은 본 부하와 별도입니다. 본 부하 P95에 합산하지 않았습니다.
| 별도 순차 HTTP 진단 | N | SQL 횟수 | 프로필 조회 수 | SQL P95 ms | 핸들러 P95 ms |
| --- | --- | --- | --- | --- | --- |
| /api/event-main/ranking/ | 10 | [33] | [25] | 29.79 | 52.12 |
| /api/event-main/status/ | 10 | [62, 63] | [25] | 55.42 | 142.43 |
| /api/event-story/achievements/ | 10 | [8, 9] | [0] | 12.31 | 28.60 |
| /api/event-story/recipe/status/ | 10 | [7, 8] | [0] | 11.19 | 27.20 |

순차 HTTP 진단은 service9561에 직접40회 요청했습니다. 프런트 지연을 포함하지 않는 SQL 확인용입니다.
| 소켓 DB 함수 | N | 큐 대기 P95 ms | 함수 실행 P95 ms |
| --- | --- | --- | --- |
| CampusConsumer.identity_now | 364 | 398.17 | 67.58 |
| CampusConsumer.store_position_now | 384 | 326.41 | 116.22 |
| ChatConsumer.identity_now | 374 | 234.64 | 67.65 |
| ChatConsumer.store_chat | 175 | 282.73 | 107.73 |
| check_session | 211 | 372.87 | 34.94 |
| read_state | 68 | 361.75 | 17.13 |
| touch_platform_session | 106 | 403.01 | 20.13 |

소켓 진단 표본은 별도20초 측정 구간에 완료된 작업입니다. 증가·준비 구간은 제외했습니다.
진단은100개 프로토콜 사용자를 실행했습니다. 본 부하의99+Chrome1 구성과 별도입니다.

### 자원 관측

| 구간 | 관측 수 | DB 연결 최대 | 활성 연결 최대 | 락 대기 최대 | 이동 연결 최대(각 프로세스) | Redis ops/s 최대(이동/서비스) |
| --- | --- | --- | --- | --- | --- | --- |
| 일반 | 86 | 10 | 3 | 0 | {'9551': 32, '9552': 35, '9553': 33} | {'movement': 170, 'service': 24} |
| 높은 행동 | 83 | 23 | 6 | 0 | {'9551': 33, '9552': 32, '9553': 35} | {'movement': 794, 'service': 26} |

약2초 간격 관측이므로 순간 피크를 놓칠 수 있습니다. 관측 연결 자체도 DB 수에 포함됩니다.
원본 ps %cpu는 프로세스 시작 이후 평균입니다. 측정 구간 순간 CPU P95로 사용하지 않았습니다.
CPU 포화·SQL·락이 전체 지연에서 차지하는 비율은 이번 자료로 확정하지 않았습니다.

## 8. 회귀 검사와 미검증 범위

백엔드 747개 검사: 실패25개, 오류29개, 건너뜀7개입니다.
프런트 235개 검사: 통과235개, 실패0개입니다.

| 백엔드 실패·오류 묶음 | 수 |
| --- | --- |
| ad_bridge.tests.test_routing:ERROR | 4 |
| campus.test_authoritative_socket:ERROR | 1 |
| campus.test_continuous_poke:ERROR | 10 |
| campus.test_motion:ERROR | 2 |
| campus.test_poke:ERROR | 5 |
| campus.test_positions:ERROR | 2 |
| campus.test_reconnect_transitions:ERROR | 1 |
| campus.test_session_policy:ERROR | 1 |
| campus.test_single_login:ERROR | 1 |
| campus.tests:ERROR | 2 |
| learning.tests.test_campus_integration:FAIL | 1 |
| campus.test_jump:FAIL | 1 |
| campus.test_positions:FAIL | 3 |
| campus.test_session_policy:FAIL | 4 |
| campus.tests:FAIL | 13 |
| tests.test_recipe_live:FAIL | 3 |

실패가 기존 기대값 문제인지 실제 결함인지 모두 분류하지 않았습니다. 회귀 검사는 통과하지 못했습니다.
[실패한 검사54개 목록](loadtest/results/20261106-local33/regression-summary.json)을 첨부했습니다.
[백엔드 테스트 로그](loadtest/results/20261106-local33/backend-regression.log), [프런트 테스트 로그](loadtest/results/20261106-local33/frontend-regression.log)를 그대로 보존했습니다.
회귀 실패는 운영 수용 전 확인해야 합니다. 실패를 부하 요청 성공 수에 섞지 않았습니다.
광고 외부 서버·200/201 실제 연동은 설정이 없어 시험하지 않았습니다.
장시간 누적 부하·네트워크 단절·Redis/DB 장애 조치·모든 사용자 조작 화면은 검증하지 않았습니다.
Chrome 화면 검증은 1개 브라우저입니다. 나머지 99개는 프로토콜 사용자입니다.
100명 운영 승인을 위해서는 접속 실패와 DB 연결 관리부터 해결하고 같은 조건으로 재측정해야 합니다.
기능 코드·테이블 구조 변경과 성능 개선 패치는 이번 작업에 포함하지 않았습니다.

## 9. 재계산과 검토

전체 근거는 `loadtest/results/20261106-local33/`에 저장했습니다. 쿠키·비밀키·서명 토큰·Chrome 프로필은 제외했습니다.
다음 명령은 원본을 읽어 P95·표본 수·해시를 검산합니다.
저장 파일을 변경하거나 서버·DB에 요청하지 않습니다.
```sh
python3 loadtest/results/20261106-local33/harness/verify_evidence.py loadtest/results/20261106-local33/
```

metrics.py는 results.json을 덮어씁니다. 결과 재생성은 원본 폴더를 복사한 뒤 실행해야 합니다.
어제의 단일 백엔드 보고서는 별도 보존했습니다.
[어제 보고서](loadtest/results/20261105-100-users/report.md)는 같은 구조의 비교 시험이 아닙니다.
1차 검토의 두 수정 요구를 반영했습니다. 두 검토자가 수정본 전체를 다시 확인하고 승인했습니다.

| 검토자 | 최종 검토 |
| --- | --- |
| stress_report_review | 2차 승인 |
| stress_report_review2 | 2차 승인 |

[두 차례 검토 기록과 검토본 해시](loadtest/results/20261106-local33/reviews.json)를 보존했습니다.
검토 승인 이후에는 승인 표기와 검토 기록 링크만 추가했습니다. 측정 수치와 해석은 유지했습니다.
이는 보고서 승인입니다. 운영 성능 통과를 뜻하지 않습니다.
