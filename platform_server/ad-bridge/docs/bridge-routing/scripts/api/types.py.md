# scripts/api/types.py

- 경로: [ad-bridge/scripts/api/types.py](../../../../scripts/api/types.py)
- 기능: 광고 소재·결정·반응 응답 데이터
- 호출·연결: api.client → Ad / Decision / Reaction → 서비스·뷰
- 파라미터: 아래 선언 필드가 데이터 객체 생성 입력이다. ID·주소·시각·상태를 보관하며 외부 호출은 없다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `Ad` | 데이터·설정·예외 클래스; 필드: creative_id, title, image_url, redirect_url, click_url |
| `Decision` | 데이터·설정·예외 클래스; 필드: decision_id, slot_id, slot_name, audience, decided_at, ad, request_id |
| `Reaction` | 반응 접수 결과. ACCEPTED도 차감 금액이나 잔액을 뜻하지 않는다.; 필드: reaction_id, decision_id, subject_id, type, occurred_at, received_at, status, reason_code, request_id |
