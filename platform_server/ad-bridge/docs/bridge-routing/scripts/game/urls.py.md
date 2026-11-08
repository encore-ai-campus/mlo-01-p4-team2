# scripts/game/urls.py

- 경로: [ad-bridge/scripts/game/urls.py](../../../../scripts/game/urls.py)
- 기능: 결정·노출·결과 조회 URL 연결
- 호출·연결: 게임 URL include → request_decision / report_impression / reaction_result
- 파라미터: decision_id: URL의 UUID. 상위 URL 접두사는 이 파일에서 정하지 않는다.

시그니처: 직접 선언한 함수·클래스 없음.

| HTTP 메서드·상대 경로 | 처리 함수 | 입력 |
| --- | --- | --- |
| POST `decision/` | `request_decision` | 로그인 사용자, 서버 슬롯 설정 |
| POST `decisions/<uuid:decision_id>/impression/` | `report_impression` | 경로 UUID, 로그인 사용자 |
| POST `decisions/<uuid:decision_id>/result/` | `reaction_result` | 경로 UUID, 로그인 사용자 |

상위 prefix는 설치한 게임의 URL 설정을 따른다. 이 폴더 버전에는 클릭 저장·확인 경로가 없다.
