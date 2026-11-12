# scripts/game/views.py

- 경로: [ad-bridge/scripts/game/views.py](../../../../scripts/game/views.py)
- 기능: 로그인·소유권 검증과 광고 HTTP 응답
- 호출·연결: game.urls → 뷰 → AdsConfig / create_decision / report_impression_once / refresh_result
- 파라미터: request: 로그인·CSRF가 적용되는 Django 요청; decision_id: URL UUID; user: 로그인 사용자; user_uuid: 내부 UUID; subject_secret: 가명 비밀; record: 저장된 노출.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `request_decision(request)` | 개인 슬롯 요청 한 번. Django 기본 CSRF 미들웨어와 함께 사용한다. |
| `report_impression(request, decision_id)` | 개인 슬롯의 노출 알림을 최초 한 번 보고한다. 실제 화면 감지는 호출자의 책임이다. |
| `_owner_id(user) -> UUID \| None` | 로그인 프로필의 내부 소유자 UUID를 읽고 원문 없는 오류로 검증한다. |
| `_subject_id(user_uuid: UUID, *, subject_secret: str) -> UUID` | 검증된 내부 UUID에서 결정 발급에 사용할 광고 전용 가명을 생성한다. |
| `_result_payload(record)` | 브라우저에 공개할 전송 필드만 선택 |
| `reaction_result(request, decision_id)` | 서버 저장 상태를 확인하고 접수된 반응만 광고 서버에서 한 번 조회한다. |

| HTTP 메서드·상대 경로 | 처리 함수 | 입력 |
| --- | --- | --- |
| POST `decision/` | `request_decision` | 로그인 사용자, 서버 슬롯 설정 |
| POST `decisions/<uuid:decision_id>/impression/` | `report_impression` | 경로 UUID, 로그인 사용자 |
| POST `decisions/<uuid:decision_id>/result/` | `reaction_result` | 경로 UUID, 로그인 사용자 |

상위 prefix는 설치한 게임의 URL 설정을 따른다. 이 폴더 버전에는 클릭 저장·확인 경로가 없다.
