# scripts/models.py

- 경로: [ad-bridge/scripts/models.py](../../../scripts/models.py)
- 기능: 개인 광고 발급·최초 노출 DB 모델
- 호출·연결: 발급·노출 저장소 → IssuedDecision / Impression → Django ORM
- 파라미터: 아래 모델 필드를 사용한다. Impression.decision은 OneToOne 관계이며 결정당 노출 한 건으로 제한한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `IssuedDecision(models.Model)` | 데이터·설정·예외 클래스; 필드: decision_id, owner_id, subject_id, slot_id, decided_at, has_ad |
| `Impression(models.Model)` | 데이터·설정·예외 클래스; 필드: reaction_id, decision, subject_id, occurred_at, delivery_state, status, reason_code, request_id, attempts, next_attempt_at, last_error, last_http_status, lease_token, lease_expires_at |
| `Impression.Meta` | 데이터·설정·예외 클래스; 필드: indexes |
