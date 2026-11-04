# 페어링 정원 연결

원본: platform-server/easter-egg/demo/card_demo. 원본 데모 파일은 수정하지 않습니다.

- game_code: term_match
- Python·SQL 용어 48개에서 무작위 8종, 같은 카드 두 장씩 16장
- 1라운드, 11.11초 제한, 오답 5회 실패
- 미리보기 4초와 카드 덮기는 플레이 시간에서 제외
- 코드 타이핑 단말기 아래, 18층 x=2226.5, y=337
- 지도와 게임창 이름: 페어링 정원. 카드·정원 장식이 있는 전용 에셋 사용
- 성공하면 기억력 도둑 업적과 완료 순위를 기존 공통 경로로 저장
- 서버가 카드 배치·선택 순서·짝·제한 시간과 중복 요청을 확인
- 브라우저가 보고한 개별 선택 시각을 사용합니다. 경품 연동 시 서버에서 실시간 입력을 검증해야 합니다.

`platform_server/`에서 `node frontend/tools/build-minigames.mjs`로 배포 HTML을 생성합니다.
검증 코드는 frontend/tests/term-match.test.cjs와 backend/tests/test_term_match.py에 있습니다.
