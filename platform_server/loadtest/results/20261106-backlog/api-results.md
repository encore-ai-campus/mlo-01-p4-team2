# API별 요청 집계

요청 개수는 API 호출 시도입니다. 같은 계정의 재요청도 각각 셉니다.
성공은 HTTP 2xx·WS 정상 결과입니다. 게임 승리나 신규 업적 지급과는 다릅니다.
오류는 업무 거절·서버 오류·전송 오류·시간초과의 합입니다. 의도한 거절 검사도 포함합니다.
P95는 호출 시작부터 본문 수신·오류·시간초과 확정까지입니다. 실행 대기를 포함합니다.
미확정은 P95에서 제외합니다. 요청=성공+오류+미확정을 검사했습니다.

## A: 대기열50 / 일반 부하 / 실제 HTTP API

| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/board/menu/` | 137 | 137 | 0 | 29.56 |
| `GET /api/campus/freeze/` | 12 | 12 | 0 | 27.5 |
| `GET /api/community/` | 147 | 147 | 0 | 37.92 |
| `GET /api/community/notices/` | 6 | 6 | 0 | 40 |
| `GET /api/community/posts/` | 147 | 147 | 0 | 38.77 |
| `GET /api/event-main/ranking/` | 334 | 334 | 0 | 84.93 |
| `GET /api/event-main/status/` | 334 | 334 | 0 | 138.02 |
| `GET /api/event-story/achievements/` | 343 | 343 | 0 | 39.26 |
| `GET /api/event-story/recipe/status/` | 334 | 334 | 0 | 34.06 |
| `GET /api/learning/wallet/` | 149 | 149 | 0 | 34.86 |
| `GET /api/learning/wallet/history/` | 149 | 149 | 0 | 37.98 |
| `GET /api/messages/conversations/` | 148 | 148 | 0 | 64.18 |
| `GET /api/messages/recipients/` | 75 | 75 | 0 | 93.82 |
| `GET /api/messages/status/` | 148 | 148 | 0 | 44.76 |
| `POST /accounts/session/activity/` | 946 | 946 | 0 | 43.35 |
| `POST /api/campus/lunch/spin/` | 61 | 61 | 0 | 44.08 |
| `POST /api/messages/send/` | 75 | 75 | 0 | 65.41 |


미확정 합계: 0건.

## A: 대기열50 / 일반 부하 / 소켓 요청

| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `chat` | 1122 | 1122 | 0 | 63.18 |
| `jump` | 706 | 706 | 0 | 5.05 |


미확정 합계: 0건.

## A: 대기열50 / 높은 부하 / 실제 HTTP API

| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/board/menu/` | 158 | 158 | 0 | 242.17 |
| `GET /api/campus/freeze/` | 12 | 12 | 0 | 340.4 |
| `GET /api/community/` | 136 | 136 | 0 | 293.48 |
| `GET /api/community/notices/` | 6 | 6 | 0 | 264.1 |
| `GET /api/community/posts/` | 136 | 136 | 0 | 235.71 |
| `GET /api/event-main/ranking/` | 379 | 379 | 0 | 485.85 |
| `GET /api/event-main/status/` | 377 | 377 | 0 | 727.14 |
| `GET /api/event-story/achievements/` | 385 | 385 | 0 | 269.5 |
| `GET /api/event-story/recipe/status/` | 376 | 376 | 0 | 283.4 |
| `GET /api/learning/csrf/` | 354 | 354 | 0 | 224.11 |
| `GET /api/learning/sessions/:session_id/questions/:order/` | 191 | 191 | 0 | 284.7 |
| `GET /api/learning/wallet/` | 521 | 521 | 0 | 239.07 |
| `GET /api/learning/wallet/history/` | 166 | 166 | 0 | 298.24 |
| `GET /api/messages/conversations/` | 172 | 172 | 0 | 415.74 |
| `GET /api/messages/recipients/` | 82 | 82 | 0 | 418.93 |
| `GET /api/messages/status/` | 173 | 173 | 0 | 307.69 |
| `POST /accounts/session/activity/` | 1145 | 1145 | 0 | 321.12 |
| `POST /api/campus/lunch/spin/` | 97 | 97 | 0 | 288.97 |
| `POST /api/learning/sessions/` | 191 | 191 | 0 | 1993.83 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/` | 191 | 191 | 0 | 579.52 |
| `POST /api/messages/send/` | 82 | 82 | 0 | 431.36 |


미확정 합계: 0건.

## A: 대기열50 / 높은 부하 / 소켓 요청

| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `chat` | 1511 | 1511 | 0 | 388.37 |
| `begin_transfer` | 213 | 213 | 0 | 512.39 |
| `interact` | 761 | 724 | 37 | 688.85 |
| `jump` | 8543 | 8251 | 292 | 538.91 |
| `open_booth` | 30 | 3 | 27 | 892.65 |
| `transfer` | 213 | 213 | 0 | 835.86 |

- `interact`: 업무 거절 37, 서버 오류 0, 전송 오류 0, 시간초과 0, 미확정 0.
- `jump`: 업무 거절 292, 서버 오류 0, 전송 오류 0, 시간초과 0, 미확정 0.
- `open_booth`: 업무 거절 27, 서버 오류 0, 전송 오류 0, 시간초과 0, 미확정 0.

미확정 합계: 0건.

## A: 대기열50 / 반복 접속

| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `ws_movement_connect` | 1000 | 882 | 118 | 791.88 |

- `ws_movement_connect`: 업무 거절 0, 서버 오류 118, 전송 오류 0, 시간초과 0, 미확정 0.

미확정 합계: 0건.

## A: 대기열50 / 기능 API 동작별

| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/event-story/achievements/ [achievements:restore]` | 100 | 100 | 0 | 292.57 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer]` | 1000 | 1000 | 0 | 1204.33 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer_retry]` | 1000 | 1000 | 0 | 857.39 |
| `POST /api/learning/sessions/:session_id/complete/ [learning:complete]` | 100 | 100 | 0 | 870.26 |
| `GET /api/learning/sessions/:session_id/questions/:order/ [learning:question]` | 1000 | 1000 | 0 | 1153.89 |
| `POST /api/learning/sessions/ [learning:start]` | 100 | 100 | 0 | 4482.67 |
| `POST /api/event-main/action/ [main:deliver:first]` | 100 | 100 | 0 | 3670.52 |
| `POST /api/event-main/action/ [main:deliver:retry]` | 100 | 100 | 0 | 3652.78 |
| `POST /api/event-main/action/ [main:game_finish:card_match]` | 100 | 100 | 0 | 584.02 |
| `POST /api/event-main/action/ [main:game_finish:coating]` | 100 | 100 | 0 | 258.74 |
| `POST /api/event-main/action/ [main:game_finish:kkuwang_touch]` | 100 | 100 | 0 | 599.04 |
| `POST /api/event-main/action/ [main:game_finish:sql_memory]` | 100 | 100 | 0 | 1058.16 |
| `POST /api/event-main/action/ [main:game_finish:topping]` | 100 | 100 | 0 | 261.71 |
| `POST /api/event-main/action/ [main:game_finish:wrapping]` | 100 | 100 | 0 | 1154.58 |
| `POST /api/event-main/action/ [main:game_start:card_match]` | 100 | 100 | 0 | 796.16 |
| `POST /api/event-main/action/ [main:game_start:coating]` | 100 | 100 | 0 | 440.23 |
| `POST /api/event-main/action/ [main:game_start:kkuwang_touch]` | 100 | 100 | 0 | 939.2 |
| `POST /api/event-main/action/ [main:game_start:sql_memory]` | 100 | 100 | 0 | 1249.37 |
| `POST /api/event-main/action/ [main:game_start:topping]` | 100 | 100 | 0 | 199.02 |
| `POST /api/event-main/action/ [main:game_start:wrapping]` | 100 | 100 | 0 | 355.64 |
| `POST /api/event-main/action/ [main:investigate]` | 100 | 100 | 0 | 1656.47 |
| `GET /api/event-main/status/ [main:restore]` | 100 | 100 | 0 | 1609.98 |
| `POST /api/event-main/action/ [main:room1_briefing]` | 100 | 100 | 0 | 1603.09 |
| `POST /api/event-main/action/ [main:sql_answer]` | 100 | 100 | 0 | 837.17 |
| `POST /api/event-story/recipe/action/ [recipe:acquire]` | 100 | 100 | 0 | 785.53 |
| `POST /api/event-story/recipe/action/ [recipe:defer]` | 100 | 100 | 0 | 827.77 |
| `POST /api/event-story/recipe/action/ [recipe:finish]` | 100 | 100 | 0 | 922.97 |
| `POST /api/event-story/recipe/action/ [recipe:recognize]` | 100 | 100 | 0 | 574.97 |
| `GET /api/event-story/recipe/status/ [recipe:restore]` | 200 | 200 | 0 | 857.9 |
| `POST /api/event-story/recipe/action/ [recipe:start]` | 100 | 100 | 0 | 739.89 |
| `POST /api/event-story/recipe/action/ [recipe:topping]` | 100 | 100 | 0 | 645.83 |
| `POST /api/event-story/plays/:uuid/result/ [term_match:result]` | 86 | 86 | 0 | 1534.19 |
| `POST /api/event-story/plays/:uuid/result/ [term_match:retry]` | 86 | 86 | 0 | 1232.09 |
| `POST /api/event-story/plays/:uuid/result/ [timer:result]` | 96 | 96 | 0 | 1408.8 |
| `POST /api/event-story/plays/:uuid/result/ [timer:retry]` | 96 | 96 | 0 | 1380.93 |
| `POST /api/event-story/plays/:uuid/result/ [typing:interrupt]` | 90 | 90 | 0 | 152.83 |
| `POST /api/event-story/plays/:uuid/result/ [typing:over_limit]` | 90 | 0 | 90 | 247.59 |
| `POST /api/event-story/plays/:uuid/result/ [typing:result]` | 90 | 90 | 0 | 1195.47 |
| `POST /api/event-story/plays/:uuid/result/ [typing:retry]` | 90 | 90 | 0 | 1347.72 |
| `WS /ws/movement/ [ws:game_start]` | 362 | 362 | 0 | 968.39 |
| `WS /ws/movement/ [ws:guide_talk]` | 272 | 0 | 272 | 199.15 |
| `WS /ws/movement/ [ws_connect]` | 400 | 350 | 50 | 737.94 |

- `POST /api/event-story/plays/:uuid/result/ [typing:over_limit]`: 업무 거절 90, 서버 오류 0, 전송 오류 0, 시간초과 0, 미확정 0.
- `WS /ws/movement/ [ws:guide_talk]`: 업무 거절 272, 서버 오류 0, 전송 오류 0, 시간초과 0, 미확정 0.
- `WS /ws/movement/ [ws_connect]`: 업무 거절 0, 서버 오류 50, 전송 오류 0, 시간초과 0, 미확정 0.

미확정 합계: 0건.

### 앞 단계 실패로 요청에 도달하지 못한 계정

아래 계정 수는 API 시도·오류 분모에 넣지 않았습니다.

- `term_match:result`: 14계정.
- `term_match:retry`: 14계정.
- `timer:result`: 4계정.
- `timer:retry`: 4계정.
- `typing:interrupt`: 10계정.
- `typing:over_limit`: 10계정.
- `typing:result`: 10계정.
- `typing:retry`: 10계정.

## B: 대기열128 / 일반 부하 / 실제 HTTP API

| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/board/menu/` | 140 | 140 | 0 | 139.5 |
| `GET /api/campus/freeze/` | 12 | 12 | 0 | 42.2 |
| `GET /api/community/` | 146 | 146 | 0 | 101.66 |
| `GET /api/community/notices/` | 6 | 6 | 0 | 116.4 |
| `GET /api/community/posts/` | 146 | 146 | 0 | 97.14 |
| `GET /api/event-main/ranking/` | 352 | 352 | 0 | 198.09 |
| `GET /api/event-main/status/` | 353 | 353 | 0 | 423.1 |
| `GET /api/event-story/achievements/` | 360 | 360 | 0 | 83.19 |
| `GET /api/event-story/recipe/status/` | 351 | 351 | 0 | 70.05 |
| `GET /api/learning/wallet/` | 142 | 142 | 0 | 72.4 |
| `GET /api/learning/wallet/history/` | 142 | 142 | 0 | 66.09 |
| `GET /api/messages/conversations/` | 131 | 131 | 0 | 98.08 |
| `GET /api/messages/recipients/` | 72 | 72 | 0 | 208.25 |
| `GET /api/messages/status/` | 131 | 131 | 0 | 76.89 |
| `POST /accounts/session/activity/` | 938 | 938 | 0 | 166.6 |
| `POST /api/campus/lunch/spin/` | 67 | 67 | 0 | 119.7 |
| `POST /api/messages/send/` | 72 | 72 | 0 | 118.81 |


미확정 합계: 0건.

## B: 대기열128 / 일반 부하 / 소켓 요청

| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `chat` | 1128 | 1128 | 0 | 179.6 |
| `jump` | 682 | 682 | 0 | 14.44 |


미확정 합계: 0건.

## B: 대기열128 / 높은 부하 / 실제 HTTP API

| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/board/menu/` | 175 | 175 | 0 | 143.88 |
| `GET /api/campus/freeze/` | 12 | 12 | 0 | 129.8 |
| `GET /api/community/` | 162 | 162 | 0 | 205.21 |
| `GET /api/community/notices/` | 6 | 6 | 0 | 133.7 |
| `GET /api/community/posts/` | 162 | 162 | 0 | 193.86 |
| `GET /api/event-main/ranking/` | 428 | 428 | 0 | 363.06 |
| `GET /api/event-main/status/` | 426 | 426 | 0 | 605.67 |
| `GET /api/event-story/achievements/` | 437 | 437 | 0 | 223.51 |
| `GET /api/event-story/recipe/status/` | 429 | 429 | 0 | 222.41 |
| `GET /api/learning/csrf/` | 405 | 405 | 0 | 153.81 |
| `GET /api/learning/sessions/:session_id/questions/:order/` | 195 | 195 | 0 | 220.11 |
| `GET /api/learning/wallet/` | 556 | 556 | 0 | 174.47 |
| `GET /api/learning/wallet/history/` | 151 | 151 | 0 | 162.21 |
| `GET /api/messages/conversations/` | 149 | 149 | 0 | 281.45 |
| `GET /api/messages/recipients/` | 82 | 82 | 0 | 380.6 |
| `GET /api/messages/status/` | 149 | 149 | 0 | 236.89 |
| `POST /accounts/session/activity/` | 1173 | 1173 | 0 | 219.07 |
| `POST /api/campus/lunch/spin/` | 78 | 78 | 0 | 198.99 |
| `POST /api/learning/sessions/` | 194 | 194 | 0 | 1695.3 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/` | 195 | 195 | 0 | 459.32 |
| `POST /api/messages/send/` | 82 | 81 | 1 | 252.92 |

- `POST /api/messages/send/`: 업무 거절 1, 서버 오류 0, 전송 오류 0, 시간초과 0, 미확정 0.

미확정 합계: 0건.

## B: 대기열128 / 높은 부하 / 소켓 요청

| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `chat` | 1470 | 1470 | 0 | 300.73 |
| `begin_transfer` | 197 | 197 | 0 | 415.26 |
| `interact` | 845 | 829 | 16 | 427.9 |
| `jump` | 8724 | 8529 | 195 | 318.49 |
| `open_booth` | 41 | 12 | 29 | 341.29 |
| `transfer` | 197 | 197 | 0 | 596.2 |

- `interact`: 업무 거절 16, 서버 오류 0, 전송 오류 0, 시간초과 0, 미확정 0.
- `jump`: 업무 거절 195, 서버 오류 0, 전송 오류 0, 시간초과 0, 미확정 0.
- `open_booth`: 업무 거절 29, 서버 오류 0, 전송 오류 0, 시간초과 0, 미확정 0.

미확정 합계: 0건.

## B: 대기열128 / 반복 접속

| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `ws_movement_connect` | 1000 | 1000 | 0 | 780.79 |


미확정 합계: 0건.

## B: 대기열128 / 기능 API 동작별

| 요청 | 요청 개수 | 성공 개수 | 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/event-story/achievements/ [achievements:restore]` | 100 | 100 | 0 | 426.97 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer]` | 1000 | 1000 | 0 | 1623.51 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer_retry]` | 1000 | 1000 | 0 | 1078.27 |
| `POST /api/learning/sessions/:session_id/complete/ [learning:complete]` | 100 | 100 | 0 | 956.01 |
| `GET /api/learning/sessions/:session_id/questions/:order/ [learning:question]` | 1000 | 1000 | 0 | 1368.43 |
| `POST /api/learning/sessions/ [learning:start]` | 100 | 100 | 0 | 5107.36 |
| `POST /api/event-main/action/ [main:deliver:first]` | 100 | 100 | 0 | 3988.83 |
| `POST /api/event-main/action/ [main:deliver:retry]` | 100 | 100 | 0 | 4070.16 |
| `POST /api/event-main/action/ [main:game_finish:card_match]` | 100 | 100 | 0 | 665.68 |
| `POST /api/event-main/action/ [main:game_finish:coating]` | 100 | 100 | 0 | 317.26 |
| `POST /api/event-main/action/ [main:game_finish:kkuwang_touch]` | 100 | 100 | 0 | 581.04 |
| `POST /api/event-main/action/ [main:game_finish:sql_memory]` | 100 | 100 | 0 | 1008.89 |
| `POST /api/event-main/action/ [main:game_finish:topping]` | 100 | 100 | 0 | 563.77 |
| `POST /api/event-main/action/ [main:game_finish:wrapping]` | 100 | 100 | 0 | 2616.11 |
| `POST /api/event-main/action/ [main:game_start:card_match]` | 100 | 100 | 0 | 679.48 |
| `POST /api/event-main/action/ [main:game_start:coating]` | 100 | 100 | 0 | 583.12 |
| `POST /api/event-main/action/ [main:game_start:kkuwang_touch]` | 100 | 100 | 0 | 969.78 |
| `POST /api/event-main/action/ [main:game_start:sql_memory]` | 100 | 100 | 0 | 1527.13 |
| `POST /api/event-main/action/ [main:game_start:topping]` | 100 | 100 | 0 | 288.57 |
| `POST /api/event-main/action/ [main:game_start:wrapping]` | 100 | 100 | 0 | 471.87 |
| `POST /api/event-main/action/ [main:investigate]` | 100 | 100 | 0 | 1580.72 |
| `GET /api/event-main/status/ [main:restore]` | 100 | 100 | 0 | 2349.65 |
| `POST /api/event-main/action/ [main:room1_briefing]` | 100 | 100 | 0 | 1818.27 |
| `POST /api/event-main/action/ [main:sql_answer]` | 100 | 100 | 0 | 978.16 |
| `POST /api/event-story/recipe/action/ [recipe:acquire]` | 100 | 100 | 0 | 960.74 |
| `POST /api/event-story/recipe/action/ [recipe:defer]` | 100 | 100 | 0 | 778.74 |
| `POST /api/event-story/recipe/action/ [recipe:finish]` | 100 | 100 | 0 | 1185.0 |
| `POST /api/event-story/recipe/action/ [recipe:recognize]` | 100 | 100 | 0 | 816.75 |
| `GET /api/event-story/recipe/status/ [recipe:restore]` | 200 | 200 | 0 | 1088.74 |
| `POST /api/event-story/recipe/action/ [recipe:start]` | 100 | 100 | 0 | 722.95 |
| `POST /api/event-story/recipe/action/ [recipe:topping]` | 100 | 100 | 0 | 635.69 |
| `POST /api/event-story/plays/:uuid/result/ [term_match:result]` | 100 | 100 | 0 | 1239.21 |
| `POST /api/event-story/plays/:uuid/result/ [term_match:retry]` | 100 | 100 | 0 | 1385.5 |
| `POST /api/event-story/plays/:uuid/result/ [timer:result]` | 100 | 100 | 0 | 1270.74 |
| `POST /api/event-story/plays/:uuid/result/ [timer:retry]` | 100 | 100 | 0 | 1329.86 |
| `POST /api/event-story/plays/:uuid/result/ [typing:interrupt]` | 100 | 100 | 0 | 159.06 |
| `POST /api/event-story/plays/:uuid/result/ [typing:over_limit]` | 100 | 0 | 100 | 294.38 |
| `POST /api/event-story/plays/:uuid/result/ [typing:result]` | 100 | 100 | 0 | 1237.22 |
| `POST /api/event-story/plays/:uuid/result/ [typing:retry]` | 100 | 100 | 0 | 1311.15 |
| `WS /ws/movement/ [ws:game_start]` | 400 | 400 | 0 | 1040.21 |
| `WS /ws/movement/ [ws:guide_talk]` | 300 | 0 | 300 | 172.52 |
| `WS /ws/movement/ [ws_connect]` | 400 | 400 | 0 | 849.99 |

- `POST /api/event-story/plays/:uuid/result/ [typing:over_limit]`: 업무 거절 100, 서버 오류 0, 전송 오류 0, 시간초과 0, 미확정 0.
- `WS /ws/movement/ [ws:guide_talk]`: 업무 거절 300, 서버 오류 0, 전송 오류 0, 시간초과 0, 미확정 0.

미확정 합계: 0건.

