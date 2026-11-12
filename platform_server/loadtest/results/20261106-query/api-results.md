# API별 요청 집계

요청 개수는 API 호출 시도입니다. 같은 계정의 재요청도 각각 셉니다.
정상 처리는 사전에 정한 응답 코드와 주요 결과값을 비교합니다. 예상과 다른 응답만 실제 오류입니다.
의도한 거절은 별도 검증입니다. 정확한 코드·문구·판정이 일치하면 검증 통과입니다.
P95는 호출 시작부터 본문 수신·오류·시간초과 확정까지입니다. 실행 대기를 포함합니다.
미확정은 P95에서 제외합니다. 요청=성공+오류+미확정을 검사했습니다.

## 거절 검증의 사전 조건

- `typing:over_limit`: 예상 코드 `[400]`, 예상 결과 `{'error_message': '타이핑은 11.11초 안에 완료해야 해요.'}`.
- `ws:guide_talk`: 예상 코드 `['rejected']`, 예상 결과 `{'error_message': '18층 꾸왕이 가까이에서 대화해 주세요.'}`.
- `main:game_finish:topping`: 예상 코드 `[200]`, 예상 결과 `{'success': False}`.

다른400이나 다른 거절 문구는 검증 실패입니다. 정상 처리와 거절 검증의 P95 표본은 섞지 않았습니다.

## 쿼리 튜닝 전 / 일반 부하 / 실제 HTTP API

| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/board/menu/` | 133 | 133 | 0 | 35.37 |
| `GET /api/campus/freeze/` | 12 | 12 | 0 | 57.5 |
| `GET /api/community/` | 152 | 152 | 0 | 38.78 |
| `GET /api/community/notices/` | 6 | 6 | 0 | 65.9 |
| `GET /api/community/posts/` | 152 | 152 | 0 | 39.23 |
| `GET /api/event-main/ranking/` | 328 | 328 | 0 | 93.97 |
| `GET /api/event-main/status/` | 328 | 328 | 0 | 160.54 |
| `GET /api/event-story/achievements/` | 337 | 337 | 0 | 43.0 |
| `GET /api/event-story/recipe/status/` | 329 | 329 | 0 | 38.21 |
| `GET /api/learning/wallet/` | 147 | 147 | 0 | 37.18 |
| `GET /api/learning/wallet/history/` | 147 | 147 | 0 | 45.33 |
| `GET /api/messages/conversations/` | 151 | 151 | 0 | 66.27 |
| `GET /api/messages/recipients/` | 81 | 81 | 0 | 85.53 |
| `GET /api/messages/status/` | 151 | 151 | 0 | 46.54 |
| `POST /accounts/session/activity/` | 939 | 939 | 0 | 45.32 |
| `POST /api/campus/lunch/spin/` | 61 | 61 | 0 | 54.39 |
| `POST /api/messages/send/` | 81 | 81 | 0 | 70.08 |


미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `GET /api/board/menu/` | 200: 133 |
| `GET /api/campus/freeze/` | 200: 12 |
| `GET /api/community/` | 200: 152 |
| `GET /api/community/notices/` | 200: 6 |
| `GET /api/community/posts/` | 200: 152 |
| `GET /api/event-main/ranking/` | 200: 328 |
| `GET /api/event-main/status/` | 200: 328 |
| `GET /api/event-story/achievements/` | 200: 337 |
| `GET /api/event-story/recipe/status/` | 200: 329 |
| `GET /api/learning/wallet/` | 200: 147 |
| `GET /api/learning/wallet/history/` | 200: 147 |
| `GET /api/messages/conversations/` | 200: 151 |
| `GET /api/messages/recipients/` | 200: 81 |
| `GET /api/messages/status/` | 200: 151 |
| `POST /accounts/session/activity/` | 200: 939 |
| `POST /api/campus/lunch/spin/` | 200: 61 |
| `POST /api/messages/send/` | 201: 81 |

## 쿼리 튜닝 전 / 일반 부하 / 소켓 요청

| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `chat` | 1122 | 1122 | 0 | 69.77 |
| `jump` | 713 | 713 | 0 | 5.06 |


미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `chat` | ok: 1122 |
| `jump` | ok: 713 |

## 쿼리 튜닝 전 / 높은 부하 / 실제 HTTP API

| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/board/menu/` | 158 | 158 | 0 | 198.26 |
| `GET /api/campus/freeze/` | 12 | 12 | 0 | 175.6 |
| `GET /api/community/` | 156 | 156 | 0 | 218.29 |
| `GET /api/community/notices/` | 6 | 6 | 0 | 91.9 |
| `GET /api/community/posts/` | 156 | 156 | 0 | 222.53 |
| `GET /api/event-main/ranking/` | 365 | 365 | 0 | 410.67 |
| `GET /api/event-main/status/` | 364 | 364 | 0 | 630.79 |
| `GET /api/event-story/achievements/` | 375 | 375 | 0 | 219.23 |
| `GET /api/event-story/recipe/status/` | 366 | 366 | 0 | 211.89 |
| `GET /api/learning/csrf/` | 419 | 419 | 0 | 169.85 |
| `GET /api/learning/sessions/:session_id/questions/:order/` | 236 | 236 | 0 | 242.73 |
| `GET /api/learning/wallet/` | 588 | 588 | 0 | 164.58 |
| `GET /api/learning/wallet/history/` | 169 | 169 | 0 | 176.66 |
| `GET /api/messages/conversations/` | 141 | 141 | 0 | 291.85 |
| `GET /api/messages/recipients/` | 91 | 91 | 0 | 314.15 |
| `GET /api/messages/status/` | 141 | 141 | 0 | 227.21 |
| `POST /accounts/session/activity/` | 1166 | 1166 | 0 | 243.4 |
| `POST /api/campus/lunch/spin/` | 86 | 86 | 0 | 189.55 |
| `POST /api/learning/sessions/` | 232 | 232 | 0 | 1701.59 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/` | 241 | 241 | 0 | 518.0 |
| `POST /api/messages/send/` | 91 | 91 | 0 | 295.3 |


미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `GET /api/board/menu/` | 200: 158 |
| `GET /api/campus/freeze/` | 200: 12 |
| `GET /api/community/` | 200: 156 |
| `GET /api/community/notices/` | 200: 6 |
| `GET /api/community/posts/` | 200: 156 |
| `GET /api/event-main/ranking/` | 200: 365 |
| `GET /api/event-main/status/` | 200: 364 |
| `GET /api/event-story/achievements/` | 200: 375 |
| `GET /api/event-story/recipe/status/` | 200: 366 |
| `GET /api/learning/csrf/` | 200: 419 |
| `GET /api/learning/sessions/:session_id/questions/:order/` | 200: 236 |
| `GET /api/learning/wallet/` | 200: 588 |
| `GET /api/learning/wallet/history/` | 200: 169 |
| `GET /api/messages/conversations/` | 200: 141 |
| `GET /api/messages/recipients/` | 200: 91 |
| `GET /api/messages/status/` | 200: 141 |
| `POST /accounts/session/activity/` | 200: 1166 |
| `POST /api/campus/lunch/spin/` | 200: 86 |
| `POST /api/learning/sessions/` | 201: 232 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/` | 201: 241 |
| `POST /api/messages/send/` | 201: 91 |

## 쿼리 튜닝 전 / 높은 부하 / 소켓 요청

| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `chat` | 1426 | 1426 | 0 | 312.47 |
| `begin_transfer` | 205 | 205 | 0 | 413.89 |
| `interact` | 880 | 855 | 25 | 439.36 |
| `jump` | 8679 | 8482 | 197 | 296.97 |
| `open_booth` | 35 | 10 | 25 | 310.45 |
| `transfer` | 205 | 205 | 0 | 591.57 |

- `interact`: 기대 불일치 25, 미확정 0. 원본 코드 분류는 comparison.json에 보존했습니다.
- `jump`: 기대 불일치 197, 미확정 0. 원본 코드 분류는 comparison.json에 보존했습니다.
- `open_booth`: 기대 불일치 25, 미확정 0. 원본 코드 분류는 comparison.json에 보존했습니다.

미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `chat` | ok: 1426 |
| `begin_transfer` | ok: 205 |
| `interact` | ok: 855, rejected: 25 |
| `jump` | ok: 8482, rejected: 197 |
| `open_booth` | ok: 10, rejected: 25 |
| `transfer` | ok: 205 |

## 쿼리 튜닝 전 / 반복 접속

| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `ws_movement_connect` | 1000 | 1000 | 0 | 775.92 |


미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `ws_movement_connect` | 101: 1000 |

## 쿼리 튜닝 전 / 기능 API 동작별

| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/event-story/achievements/ [achievements:restore]` | 100 | 100 | 0 | 289.07 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer]` | 1000 | 1000 | 0 | 1178.53 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer_retry]` | 1000 | 1000 | 0 | 826.31 |
| `POST /api/learning/sessions/:session_id/complete/ [learning:complete]` | 100 | 100 | 0 | 911.39 |
| `GET /api/learning/sessions/:session_id/questions/:order/ [learning:question]` | 1000 | 1000 | 0 | 1047.25 |
| `POST /api/learning/sessions/ [learning:start]` | 100 | 100 | 0 | 4441.02 |
| `POST /api/event-main/action/ [main:deliver:first]` | 100 | 100 | 0 | 3993.1 |
| `POST /api/event-main/action/ [main:deliver:retry]` | 100 | 100 | 0 | 4330.53 |
| `POST /api/event-main/action/ [main:game_finish:card_match]` | 100 | 100 | 0 | 498.46 |
| `POST /api/event-main/action/ [main:game_finish:coating]` | 100 | 100 | 0 | 253.03 |
| `POST /api/event-main/action/ [main:game_finish:kkuwang_touch]` | 100 | 100 | 0 | 575.99 |
| `POST /api/event-main/action/ [main:game_finish:sql_memory]` | 100 | 100 | 0 | 783.14 |
| `POST /api/event-main/action/ [main:game_finish:wrapping]` | 100 | 100 | 0 | 1331.47 |
| `POST /api/event-main/action/ [main:game_start:card_match]` | 100 | 100 | 0 | 667.01 |
| `POST /api/event-main/action/ [main:game_start:coating]` | 100 | 100 | 0 | 392.13 |
| `POST /api/event-main/action/ [main:game_start:kkuwang_touch]` | 100 | 100 | 0 | 743.45 |
| `POST /api/event-main/action/ [main:game_start:sql_memory]` | 100 | 100 | 0 | 1310.84 |
| `POST /api/event-main/action/ [main:game_start:topping]` | 100 | 100 | 0 | 240.87 |
| `POST /api/event-main/action/ [main:game_start:wrapping]` | 100 | 100 | 0 | 343.86 |
| `POST /api/event-main/action/ [main:investigate]` | 100 | 100 | 0 | 1546.25 |
| `GET /api/event-main/status/ [main:restore]` | 100 | 100 | 0 | 1726.15 |
| `POST /api/event-main/action/ [main:room1_briefing]` | 100 | 100 | 0 | 1501.48 |
| `POST /api/event-main/action/ [main:sql_answer]` | 100 | 100 | 0 | 727.25 |
| `POST /api/event-story/recipe/action/ [recipe:acquire]` | 100 | 100 | 0 | 729.72 |
| `POST /api/event-story/recipe/action/ [recipe:defer]` | 100 | 100 | 0 | 595.92 |
| `POST /api/event-story/recipe/action/ [recipe:finish]` | 100 | 100 | 0 | 929.73 |
| `POST /api/event-story/recipe/action/ [recipe:recognize]` | 100 | 100 | 0 | 678.28 |
| `GET /api/event-story/recipe/status/ [recipe:restore]` | 200 | 200 | 0 | 894.13 |
| `POST /api/event-story/recipe/action/ [recipe:start]` | 100 | 100 | 0 | 668.93 |
| `POST /api/event-story/recipe/action/ [recipe:topping]` | 100 | 100 | 0 | 627.18 |
| `POST /api/event-story/plays/:uuid/result/ [term_match:result]` | 100 | 100 | 0 | 1365.34 |
| `POST /api/event-story/plays/:uuid/result/ [term_match:retry]` | 100 | 100 | 0 | 1365.64 |
| `POST /api/event-story/plays/:uuid/result/ [timer:result]` | 100 | 100 | 0 | 1226.13 |
| `POST /api/event-story/plays/:uuid/result/ [timer:retry]` | 100 | 100 | 0 | 1323.06 |
| `POST /api/event-story/plays/:uuid/result/ [typing:interrupt]` | 100 | 100 | 0 | 149.71 |
| `POST /api/event-story/plays/:uuid/result/ [typing:result]` | 100 | 100 | 0 | 1217.41 |
| `POST /api/event-story/plays/:uuid/result/ [typing:retry]` | 100 | 100 | 0 | 1237.49 |
| `WS /ws/movement/ [ws:game_start]` | 400 | 400 | 0 | 1044.67 |
| `WS /ws/movement/ [ws_connect]` | 400 | 400 | 0 | 787.77 |


미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `GET /api/event-story/achievements/ [achievements:restore]` | 200: 100 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer]` | 201: 1000 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer_retry]` | 201: 1000 |
| `POST /api/learning/sessions/:session_id/complete/ [learning:complete]` | 200: 100 |
| `GET /api/learning/sessions/:session_id/questions/:order/ [learning:question]` | 200: 1000 |
| `POST /api/learning/sessions/ [learning:start]` | 201: 100 |
| `POST /api/event-main/action/ [main:deliver:first]` | 200: 100 |
| `POST /api/event-main/action/ [main:deliver:retry]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_finish:card_match]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_finish:coating]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_finish:kkuwang_touch]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_finish:sql_memory]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_finish:wrapping]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_start:card_match]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_start:coating]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_start:kkuwang_touch]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_start:sql_memory]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_start:topping]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_start:wrapping]` | 200: 100 |
| `POST /api/event-main/action/ [main:investigate]` | 200: 100 |
| `GET /api/event-main/status/ [main:restore]` | 200: 100 |
| `POST /api/event-main/action/ [main:room1_briefing]` | 200: 100 |
| `POST /api/event-main/action/ [main:sql_answer]` | 200: 100 |
| `POST /api/event-story/recipe/action/ [recipe:acquire]` | 200: 100 |
| `POST /api/event-story/recipe/action/ [recipe:defer]` | 200: 100 |
| `POST /api/event-story/recipe/action/ [recipe:finish]` | 200: 100 |
| `POST /api/event-story/recipe/action/ [recipe:recognize]` | 200: 100 |
| `GET /api/event-story/recipe/status/ [recipe:restore]` | 200: 200 |
| `POST /api/event-story/recipe/action/ [recipe:start]` | 200: 100 |
| `POST /api/event-story/recipe/action/ [recipe:topping]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [term_match:result]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [term_match:retry]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [timer:result]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [timer:retry]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [typing:interrupt]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [typing:result]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [typing:retry]` | 200: 100 |
| `WS /ws/movement/ [ws:game_start]` | ok: 400 |
| `WS /ws/movement/ [ws_connect]` | 101: 400 |

## 쿼리 튜닝 전 / 의도한 거절 검증

| 검증 항목 | 시도 개수 | 예상대로 거절 | 검증 실패 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `POST /api/event-main/action/ [main:game_finish:topping]` | 100 | 100 | 0 | 303.55 |
| `POST /api/event-story/plays/:uuid/result/ [typing:over_limit]` | 100 | 100 | 0 | 411.24 |
| `WS /ws/movement/ [ws:guide_talk]` | 300 | 300 | 0 | 161.51 |


미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `POST /api/event-main/action/ [main:game_finish:topping]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [typing:over_limit]` | 400: 100 |
| `WS /ws/movement/ [ws:guide_talk]` | rejected: 300 |

## 쿼리 튜닝 후 / 일반 부하 / 실제 HTTP API

| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/board/menu/` | 138 | 138 | 0 | 32.9 |
| `GET /api/campus/freeze/` | 12 | 12 | 0 | 32.4 |
| `GET /api/community/` | 147 | 147 | 0 | 45.9 |
| `GET /api/community/notices/` | 6 | 6 | 0 | 50.3 |
| `GET /api/community/posts/` | 147 | 147 | 0 | 36.86 |
| `GET /api/event-main/ranking/` | 320 | 320 | 0 | 38.72 |
| `GET /api/event-main/status/` | 320 | 320 | 0 | 54.91 |
| `GET /api/event-story/achievements/` | 329 | 329 | 0 | 39.74 |
| `GET /api/event-story/recipe/status/` | 320 | 320 | 0 | 37.54 |
| `GET /api/learning/wallet/` | 147 | 147 | 0 | 38.36 |
| `GET /api/learning/wallet/history/` | 147 | 147 | 0 | 46.08 |
| `GET /api/messages/conversations/` | 148 | 148 | 0 | 61.11 |
| `GET /api/messages/recipients/` | 86 | 86 | 0 | 109.86 |
| `GET /api/messages/status/` | 148 | 148 | 0 | 40.34 |
| `POST /accounts/session/activity/` | 939 | 939 | 0 | 45.28 |
| `POST /api/campus/lunch/spin/` | 66 | 66 | 0 | 50.6 |
| `POST /api/messages/send/` | 86 | 86 | 0 | 76.12 |


미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `GET /api/board/menu/` | 200: 138 |
| `GET /api/campus/freeze/` | 200: 12 |
| `GET /api/community/` | 200: 147 |
| `GET /api/community/notices/` | 200: 6 |
| `GET /api/community/posts/` | 200: 147 |
| `GET /api/event-main/ranking/` | 200: 320 |
| `GET /api/event-main/status/` | 200: 320 |
| `GET /api/event-story/achievements/` | 200: 329 |
| `GET /api/event-story/recipe/status/` | 200: 320 |
| `GET /api/learning/wallet/` | 200: 147 |
| `GET /api/learning/wallet/history/` | 200: 147 |
| `GET /api/messages/conversations/` | 200: 148 |
| `GET /api/messages/recipients/` | 200: 86 |
| `GET /api/messages/status/` | 200: 148 |
| `POST /accounts/session/activity/` | 200: 939 |
| `POST /api/campus/lunch/spin/` | 200: 66 |
| `POST /api/messages/send/` | 201: 86 |

## 쿼리 튜닝 후 / 일반 부하 / 소켓 요청

| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `chat` | 1118 | 1118 | 0 | 76.27 |
| `jump` | 693 | 693 | 0 | 5.62 |


미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `chat` | ok: 1118 |
| `jump` | ok: 693 |

## 쿼리 튜닝 후 / 높은 부하 / 실제 HTTP API

| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/board/menu/` | 153 | 153 | 0 | 179.24 |
| `GET /api/campus/freeze/` | 12 | 12 | 0 | 135 |
| `GET /api/community/` | 151 | 151 | 0 | 188.24 |
| `GET /api/community/notices/` | 6 | 6 | 0 | 544.3 |
| `GET /api/community/posts/` | 151 | 151 | 0 | 191.86 |
| `GET /api/event-main/ranking/` | 365 | 365 | 0 | 174.37 |
| `GET /api/event-main/status/` | 365 | 365 | 0 | 238.74 |
| `GET /api/event-story/achievements/` | 374 | 374 | 0 | 172.6 |
| `GET /api/event-story/recipe/status/` | 365 | 365 | 0 | 185.24 |
| `GET /api/learning/csrf/` | 376 | 376 | 0 | 142.99 |
| `GET /api/learning/sessions/:session_id/questions/:order/` | 217 | 217 | 0 | 288.3 |
| `GET /api/learning/wallet/` | 529 | 529 | 0 | 163.84 |
| `GET /api/learning/wallet/history/` | 152 | 152 | 0 | 178.91 |
| `GET /api/messages/conversations/` | 153 | 153 | 0 | 312.28 |
| `GET /api/messages/recipients/` | 80 | 80 | 0 | 370.72 |
| `GET /api/messages/status/` | 153 | 153 | 0 | 201.58 |
| `POST /accounts/session/activity/` | 1157 | 1157 | 0 | 239.34 |
| `POST /api/campus/lunch/spin/` | 74 | 74 | 0 | 205.02 |
| `POST /api/learning/sessions/` | 215 | 215 | 0 | 2048.32 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/` | 219 | 219 | 0 | 655.52 |
| `POST /api/messages/send/` | 80 | 80 | 0 | 399.36 |


미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `GET /api/board/menu/` | 200: 153 |
| `GET /api/campus/freeze/` | 200: 12 |
| `GET /api/community/` | 200: 151 |
| `GET /api/community/notices/` | 200: 6 |
| `GET /api/community/posts/` | 200: 151 |
| `GET /api/event-main/ranking/` | 200: 365 |
| `GET /api/event-main/status/` | 200: 365 |
| `GET /api/event-story/achievements/` | 200: 374 |
| `GET /api/event-story/recipe/status/` | 200: 365 |
| `GET /api/learning/csrf/` | 200: 376 |
| `GET /api/learning/sessions/:session_id/questions/:order/` | 200: 217 |
| `GET /api/learning/wallet/` | 200: 529 |
| `GET /api/learning/wallet/history/` | 200: 152 |
| `GET /api/messages/conversations/` | 200: 153 |
| `GET /api/messages/recipients/` | 200: 80 |
| `GET /api/messages/status/` | 200: 153 |
| `POST /accounts/session/activity/` | 200: 1157 |
| `POST /api/campus/lunch/spin/` | 200: 74 |
| `POST /api/learning/sessions/` | 201: 215 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/` | 201: 219 |
| `POST /api/messages/send/` | 201: 80 |

## 쿼리 튜닝 후 / 높은 부하 / 소켓 요청

| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `chat` | 1453 | 1453 | 0 | 465.99 |
| `begin_transfer` | 245 | 245 | 0 | 403.93 |
| `interact` | 770 | 760 | 10 | 413.96 |
| `jump` | 8619 | 8460 | 159 | 269.25 |
| `open_booth` | 34 | 15 | 19 | 396.03 |
| `transfer` | 243 | 243 | 0 | 882.36 |

- `interact`: 기대 불일치 10, 미확정 0. 원본 코드 분류는 comparison.json에 보존했습니다.
- `jump`: 기대 불일치 159, 미확정 0. 원본 코드 분류는 comparison.json에 보존했습니다.
- `open_booth`: 기대 불일치 19, 미확정 0. 원본 코드 분류는 comparison.json에 보존했습니다.

미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `chat` | ok: 1453 |
| `begin_transfer` | ok: 245 |
| `interact` | ok: 760, rejected: 10 |
| `jump` | ok: 8460, rejected: 159 |
| `open_booth` | ok: 15, rejected: 19 |
| `transfer` | ok: 243 |

## 쿼리 튜닝 후 / 반복 접속

| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `ws_movement_connect` | 1000 | 1000 | 0 | 763.35 |


미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `ws_movement_connect` | 101: 1000 |

## 쿼리 튜닝 후 / 기능 API 동작별

| 요청 | 요청 개수 | 성공 개수 | 실제 오류 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/event-story/achievements/ [achievements:restore]` | 100 | 100 | 0 | 355.05 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer]` | 998 | 997 | 1 | 1218.98 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer_retry]` | 997 | 997 | 0 | 935.8 |
| `POST /api/learning/sessions/:session_id/complete/ [learning:complete]` | 97 | 97 | 0 | 1990.08 |
| `GET /api/learning/sessions/:session_id/questions/:order/ [learning:question]` | 1000 | 998 | 2 | 1450.9 |
| `POST /api/learning/sessions/ [learning:start]` | 100 | 100 | 0 | 4535.52 |
| `POST /api/event-main/action/ [main:deliver:first]` | 100 | 100 | 0 | 511.85 |
| `POST /api/event-main/action/ [main:deliver:retry]` | 100 | 100 | 0 | 400.49 |
| `POST /api/event-main/action/ [main:game_finish:card_match]` | 100 | 100 | 0 | 167.19 |
| `POST /api/event-main/action/ [main:game_finish:coating]` | 100 | 100 | 0 | 154.76 |
| `POST /api/event-main/action/ [main:game_finish:kkuwang_touch]` | 100 | 100 | 0 | 203.42 |
| `POST /api/event-main/action/ [main:game_finish:sql_memory]` | 100 | 100 | 0 | 235.97 |
| `POST /api/event-main/action/ [main:game_finish:wrapping]` | 100 | 100 | 0 | 296.94 |
| `POST /api/event-main/action/ [main:game_start:card_match]` | 100 | 100 | 0 | 131.98 |
| `POST /api/event-main/action/ [main:game_start:coating]` | 100 | 100 | 0 | 153.47 |
| `POST /api/event-main/action/ [main:game_start:kkuwang_touch]` | 100 | 100 | 0 | 177.98 |
| `POST /api/event-main/action/ [main:game_start:sql_memory]` | 100 | 100 | 0 | 768.86 |
| `POST /api/event-main/action/ [main:game_start:topping]` | 100 | 100 | 0 | 136.78 |
| `POST /api/event-main/action/ [main:game_start:wrapping]` | 100 | 100 | 0 | 152.26 |
| `POST /api/event-main/action/ [main:investigate]` | 100 | 100 | 0 | 760.95 |
| `GET /api/event-main/status/ [main:restore]` | 100 | 100 | 0 | 751.24 |
| `POST /api/event-main/action/ [main:room1_briefing]` | 100 | 100 | 0 | 781.86 |
| `POST /api/event-main/action/ [main:sql_answer]` | 100 | 100 | 0 | 164.23 |
| `POST /api/event-story/recipe/action/ [recipe:acquire]` | 100 | 100 | 0 | 702.19 |
| `POST /api/event-story/recipe/action/ [recipe:defer]` | 100 | 100 | 0 | 545.51 |
| `POST /api/event-story/recipe/action/ [recipe:finish]` | 100 | 100 | 0 | 685.21 |
| `POST /api/event-story/recipe/action/ [recipe:recognize]` | 100 | 100 | 0 | 581.09 |
| `GET /api/event-story/recipe/status/ [recipe:restore]` | 200 | 200 | 0 | 564.76 |
| `POST /api/event-story/recipe/action/ [recipe:start]` | 100 | 100 | 0 | 652.42 |
| `POST /api/event-story/recipe/action/ [recipe:topping]` | 100 | 100 | 0 | 569.92 |
| `POST /api/event-story/plays/:uuid/result/ [term_match:result]` | 100 | 100 | 0 | 906.55 |
| `POST /api/event-story/plays/:uuid/result/ [term_match:retry]` | 100 | 100 | 0 | 810.72 |
| `POST /api/event-story/plays/:uuid/result/ [timer:result]` | 100 | 100 | 0 | 1018.3 |
| `POST /api/event-story/plays/:uuid/result/ [timer:retry]` | 100 | 100 | 0 | 928.65 |
| `POST /api/event-story/plays/:uuid/result/ [typing:interrupt]` | 100 | 100 | 0 | 130.6 |
| `POST /api/event-story/plays/:uuid/result/ [typing:result]` | 100 | 100 | 0 | 825.22 |
| `POST /api/event-story/plays/:uuid/result/ [typing:retry]` | 100 | 100 | 0 | 805.11 |
| `WS /ws/movement/ [ws:game_start]` | 400 | 400 | 0 | 843.89 |
| `WS /ws/movement/ [ws_connect]` | 400 | 400 | 0 | 862.25 |

- `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer]`: 기대 불일치 1, 미확정 0. 원본 코드 분류는 comparison.json에 보존했습니다.
- `GET /api/learning/sessions/:session_id/questions/:order/ [learning:question]`: 기대 불일치 2, 미확정 0. 원본 코드 분류는 comparison.json에 보존했습니다.

미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `GET /api/event-story/achievements/ [achievements:restore]` | 200: 100 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer]` | 201: 997, 504: 1 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/ [learning:answer_retry]` | 201: 997 |
| `POST /api/learning/sessions/:session_id/complete/ [learning:complete]` | 200: 97 |
| `GET /api/learning/sessions/:session_id/questions/:order/ [learning:question]` | 200: 998, 504: 2 |
| `POST /api/learning/sessions/ [learning:start]` | 201: 100 |
| `POST /api/event-main/action/ [main:deliver:first]` | 200: 100 |
| `POST /api/event-main/action/ [main:deliver:retry]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_finish:card_match]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_finish:coating]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_finish:kkuwang_touch]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_finish:sql_memory]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_finish:wrapping]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_start:card_match]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_start:coating]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_start:kkuwang_touch]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_start:sql_memory]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_start:topping]` | 200: 100 |
| `POST /api/event-main/action/ [main:game_start:wrapping]` | 200: 100 |
| `POST /api/event-main/action/ [main:investigate]` | 200: 100 |
| `GET /api/event-main/status/ [main:restore]` | 200: 100 |
| `POST /api/event-main/action/ [main:room1_briefing]` | 200: 100 |
| `POST /api/event-main/action/ [main:sql_answer]` | 200: 100 |
| `POST /api/event-story/recipe/action/ [recipe:acquire]` | 200: 100 |
| `POST /api/event-story/recipe/action/ [recipe:defer]` | 200: 100 |
| `POST /api/event-story/recipe/action/ [recipe:finish]` | 200: 100 |
| `POST /api/event-story/recipe/action/ [recipe:recognize]` | 200: 100 |
| `GET /api/event-story/recipe/status/ [recipe:restore]` | 200: 200 |
| `POST /api/event-story/recipe/action/ [recipe:start]` | 200: 100 |
| `POST /api/event-story/recipe/action/ [recipe:topping]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [term_match:result]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [term_match:retry]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [timer:result]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [timer:retry]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [typing:interrupt]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [typing:result]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [typing:retry]` | 200: 100 |
| `WS /ws/movement/ [ws:game_start]` | ok: 400 |
| `WS /ws/movement/ [ws_connect]` | 101: 400 |

## 쿼리 튜닝 후 / 의도한 거절 검증

| 검증 항목 | 시도 개수 | 예상대로 거절 | 검증 실패 개수 | 반응속도(P95 ms) |
| --- | ---: | ---: | ---: | ---: |
| `POST /api/event-main/action/ [main:game_finish:topping]` | 100 | 100 | 0 | 151.12 |
| `POST /api/event-story/plays/:uuid/result/ [typing:over_limit]` | 100 | 100 | 0 | 340.96 |
| `WS /ws/movement/ [ws:guide_talk]` | 300 | 300 | 0 | 162.89 |


미확정 합계: 0건.

| 요청 | 실제 응답 코드별 개수 |
| --- | --- |
| `POST /api/event-main/action/ [main:game_finish:topping]` | 200: 100 |
| `POST /api/event-story/plays/:uuid/result/ [typing:over_limit]` | 400: 100 |
| `WS /ws/movement/ [ws:guide_talk]` | rejected: 300 |

### 앞 단계 실패로 요청에 도달하지 못한 계정

아래 계정 수는 API 시도·오류 분모에 넣지 않았습니다.

- `learning:complete`: 3계정.

## 일반 부하 / 요청별 변화

절감은 튜닝 전에서 튜닝 후를 뺀 값입니다. 음수는 지연 증가입니다.

| 요청 | 전 요청 수 | 후 요청 수 | 전 P95 ms | 후 P95 ms | 절감 ms | 절감 % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `GET /api/board/menu/` | 133 | 138 | 35.37 | 32.9 | 2.47 | 6.98 |
| `GET /api/campus/freeze/` | 12 | 12 | 57.5 | 32.4 | 25.1 | 43.65 |
| `GET /api/community/` | 152 | 147 | 38.78 | 45.9 | -7.12 | -18.36 |
| `GET /api/community/notices/` | 6 | 6 | 65.9 | 50.3 | 15.6 | 23.67 |
| `GET /api/community/posts/` | 152 | 147 | 39.23 | 36.86 | 2.37 | 6.04 |
| `GET /api/event-main/ranking/` | 328 | 320 | 93.97 | 38.72 | 55.25 | 58.8 |
| `GET /api/event-main/status/` | 328 | 320 | 160.54 | 54.91 | 105.63 | 65.8 |
| `GET /api/event-story/achievements/` | 337 | 329 | 43.0 | 39.74 | 3.26 | 7.58 |
| `GET /api/event-story/recipe/status/` | 329 | 320 | 38.21 | 37.54 | 0.67 | 1.75 |
| `GET /api/learning/wallet/` | 147 | 147 | 37.18 | 38.36 | -1.18 | -3.17 |
| `GET /api/learning/wallet/history/` | 147 | 147 | 45.33 | 46.08 | -0.75 | -1.65 |
| `GET /api/messages/conversations/` | 151 | 148 | 66.27 | 61.11 | 5.16 | 7.79 |
| `GET /api/messages/recipients/` | 81 | 86 | 85.53 | 109.86 | -24.33 | -28.45 |
| `GET /api/messages/status/` | 151 | 148 | 46.54 | 40.34 | 6.2 | 13.32 |
| `POST /accounts/session/activity/` | 939 | 939 | 45.32 | 45.28 | 0.04 | 0.09 |
| `POST /api/campus/lunch/spin/` | 61 | 66 | 54.39 | 50.6 | 3.79 | 6.97 |
| `POST /api/messages/send/` | 81 | 86 | 70.08 | 76.12 | -6.04 | -8.62 |

## 높은 부하 / 요청별 변화

절감은 튜닝 전에서 튜닝 후를 뺀 값입니다. 음수는 지연 증가입니다.

| 요청 | 전 요청 수 | 후 요청 수 | 전 P95 ms | 후 P95 ms | 절감 ms | 절감 % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `GET /api/board/menu/` | 158 | 153 | 198.26 | 179.24 | 19.02 | 9.59 |
| `GET /api/campus/freeze/` | 12 | 12 | 175.6 | 135 | 40.6 | 23.12 |
| `GET /api/community/` | 156 | 151 | 218.29 | 188.24 | 30.05 | 13.77 |
| `GET /api/community/notices/` | 6 | 6 | 91.9 | 544.3 | -452.4 | -492.27 |
| `GET /api/community/posts/` | 156 | 151 | 222.53 | 191.86 | 30.67 | 13.78 |
| `GET /api/event-main/ranking/` | 365 | 365 | 410.67 | 174.37 | 236.3 | 57.54 |
| `GET /api/event-main/status/` | 364 | 365 | 630.79 | 238.74 | 392.05 | 62.15 |
| `GET /api/event-story/achievements/` | 375 | 374 | 219.23 | 172.6 | 46.63 | 21.27 |
| `GET /api/event-story/recipe/status/` | 366 | 365 | 211.89 | 185.24 | 26.65 | 12.58 |
| `GET /api/learning/csrf/` | 419 | 376 | 169.85 | 142.99 | 26.86 | 15.81 |
| `GET /api/learning/sessions/:session_id/questions/:order/` | 236 | 217 | 242.73 | 288.3 | -45.57 | -18.77 |
| `GET /api/learning/wallet/` | 588 | 529 | 164.58 | 163.84 | 0.74 | 0.45 |
| `GET /api/learning/wallet/history/` | 169 | 152 | 176.66 | 178.91 | -2.25 | -1.27 |
| `GET /api/messages/conversations/` | 141 | 153 | 291.85 | 312.28 | -20.43 | -7.0 |
| `GET /api/messages/recipients/` | 91 | 80 | 314.15 | 370.72 | -56.57 | -18.01 |
| `GET /api/messages/status/` | 141 | 153 | 227.21 | 201.58 | 25.63 | 11.28 |
| `POST /accounts/session/activity/` | 1166 | 1157 | 243.4 | 239.34 | 4.06 | 1.67 |
| `POST /api/campus/lunch/spin/` | 86 | 74 | 189.55 | 205.02 | -15.47 | -8.16 |
| `POST /api/learning/sessions/` | 232 | 215 | 1701.59 | 2048.32 | -346.73 | -20.38 |
| `POST /api/learning/sessions/:session_id/questions/:order/answers/` | 241 | 219 | 518.0 | 655.52 | -137.52 | -26.55 |
| `POST /api/messages/send/` | 91 | 80 | 295.3 | 399.36 | -104.06 | -35.24 |

## 기능 요청별 변화

절감은 튜닝 전에서 튜닝 후를 뺀 값입니다. 음수는 지연 증가입니다.

| 요청 | 전 요청 수 | 후 요청 수 | 전 P95 ms | 후 P95 ms | 절감 ms | 절감 % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `achievements:restore` | 100 | 100 | 289.07 | 355.05 | -65.98 | -22.82 |
| `learning:answer` | 1000 | 998 | 1178.53 | 1218.98 | -40.45 | -3.43 |
| `learning:answer_retry` | 1000 | 997 | 826.31 | 935.8 | -109.49 | -13.25 |
| `learning:complete` | 100 | 97 | 911.39 | 1990.08 | -1078.69 | -118.36 |
| `learning:question` | 1000 | 1000 | 1047.25 | 1450.9 | -403.65 | -38.54 |
| `learning:start` | 100 | 100 | 4441.02 | 4535.52 | -94.5 | -2.13 |
| `main:deliver:first` | 100 | 100 | 3993.1 | 511.85 | 3481.25 | 87.18 |
| `main:deliver:retry` | 100 | 100 | 4330.53 | 400.49 | 3930.04 | 90.75 |
| `main:game_finish:card_match` | 100 | 100 | 498.46 | 167.19 | 331.27 | 66.46 |
| `main:game_finish:coating` | 100 | 100 | 253.03 | 154.76 | 98.27 | 38.84 |
| `main:game_finish:kkuwang_touch` | 100 | 100 | 575.99 | 203.42 | 372.57 | 64.68 |
| `main:game_finish:sql_memory` | 100 | 100 | 783.14 | 235.97 | 547.17 | 69.87 |
| `main:game_finish:wrapping` | 100 | 100 | 1331.47 | 296.94 | 1034.53 | 77.7 |
| `main:game_start:card_match` | 100 | 100 | 667.01 | 131.98 | 535.03 | 80.21 |
| `main:game_start:coating` | 100 | 100 | 392.13 | 153.47 | 238.66 | 60.86 |
| `main:game_start:kkuwang_touch` | 100 | 100 | 743.45 | 177.98 | 565.47 | 76.06 |
| `main:game_start:sql_memory` | 100 | 100 | 1310.84 | 768.86 | 541.98 | 41.35 |
| `main:game_start:topping` | 100 | 100 | 240.87 | 136.78 | 104.09 | 43.21 |
| `main:game_start:wrapping` | 100 | 100 | 343.86 | 152.26 | 191.6 | 55.72 |
| `main:investigate` | 100 | 100 | 1546.25 | 760.95 | 785.3 | 50.79 |
| `main:restore` | 100 | 100 | 1726.15 | 751.24 | 974.91 | 56.48 |
| `main:room1_briefing` | 100 | 100 | 1501.48 | 781.86 | 719.62 | 47.93 |
| `main:sql_answer` | 100 | 100 | 727.25 | 164.23 | 563.02 | 77.42 |
| `recipe:acquire` | 100 | 100 | 729.72 | 702.19 | 27.53 | 3.77 |
| `recipe:defer` | 100 | 100 | 595.92 | 545.51 | 50.41 | 8.46 |
| `recipe:finish` | 100 | 100 | 929.73 | 685.21 | 244.52 | 26.3 |
| `recipe:recognize` | 100 | 100 | 678.28 | 581.09 | 97.19 | 14.33 |
| `recipe:restore` | 200 | 200 | 894.13 | 564.76 | 329.37 | 36.84 |
| `recipe:start` | 100 | 100 | 668.93 | 652.42 | 16.51 | 2.47 |
| `recipe:topping` | 100 | 100 | 627.18 | 569.92 | 57.26 | 9.13 |
| `term_match:result` | 100 | 100 | 1365.34 | 906.55 | 458.79 | 33.6 |
| `term_match:retry` | 100 | 100 | 1365.64 | 810.72 | 554.92 | 40.63 |
| `timer:result` | 100 | 100 | 1226.13 | 1018.3 | 207.83 | 16.95 |
| `timer:retry` | 100 | 100 | 1323.06 | 928.65 | 394.41 | 29.81 |
| `typing:interrupt` | 100 | 100 | 149.71 | 130.6 | 19.11 | 12.76 |
| `typing:result` | 100 | 100 | 1217.41 | 825.22 | 392.19 | 32.22 |
| `typing:retry` | 100 | 100 | 1237.49 | 805.11 | 432.38 | 34.94 |
| `ws:game_start` | 400 | 400 | 1044.67 | 843.89 | 200.78 | 19.22 |
| `ws_connect` | 400 | 400 | 787.77 | 862.25 | -74.48 | -9.45 |

## 거절 검증별 변화

절감은 튜닝 전에서 튜닝 후를 뺀 값입니다. 음수는 지연 증가입니다.

| 요청 | 전 요청 수 | 후 요청 수 | 전 P95 ms | 후 P95 ms | 절감 ms | 절감 % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `main:game_finish:topping` | 100 | 100 | 303.55 | 151.12 | 152.43 | 50.22 |
| `typing:over_limit` | 100 | 100 | 411.24 | 340.96 | 70.28 | 17.09 |
| `ws:guide_talk` | 300 | 300 | 161.51 | 162.89 | -1.38 | -0.85 |

