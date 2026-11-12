# scripts/impressions/store.py

- 경로: [ad-bridge/scripts/impressions/store.py](../../../../scripts/impressions/store.py)
- 기능: 노출 예약·소유권·임대·판정 저장
- 호출·연결: 노출 서비스·작업자·결과 서비스 → 저장 함수 → IssuedDecision / Impression ORM
- 파라미터: decision_id: 결정 UUID; owner_id: 내부 소유자; reaction_id: 반응 UUID; occurred_at: 최초 UTC 시각; now: 현재 aware datetime; lease_seconds: 임대 초; lease_token: 작업 임대 UUID; delivery_state: 전송 상태; status / reason_code: 판정·사유; request_id: 추적 ID; next_attempt_at: 재시도 시각; last_error / last_http_status: 진단 정보.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `StoredImpression` | 데이터·설정·예외 클래스; 필드: decision_id, reaction_id, subject_id, occurred_at, delivery_state, status, reason_code, request_id, attempts, next_attempt_at, last_error, last_http_status, lease_token, lease_expires_at |
| `DecisionNotFound(Exception)` | 현재 사용자에게 발급한 결정이 없으며 타 사용자 존재 여부도 공개하지 않는다. |
| `NoAdForDecision(Exception)` | 광고 없는 결정에는 노출을 생성할 수 없다. |
| `_uuid(value: str \| UUID) -> UUID` | UUID 형식을 검증하고 정규화 |
| `_utc_time(value: str) -> datetime` | UTC Z 시각 파싱 |
| `_snapshot(record) -> StoredImpression` | DB 행을 전송용 StoredImpression으로 변환 |
| `_aware_time(value: datetime, field: str) -> datetime` | 타임존이 있는 시각 검증 |
| `_lease_end(now: datetime, lease_seconds: float) -> datetime` | 임대 시간을 검증하고 만료 시각 계산 |
| `reserve_impression( decision_id: str \| UUID, owner_id: str \| UUID, occurred_at: str, *, now: datetime \| None = None, lease_seconds: float = 60, ) -> tuple[StoredImpression, bool]` | 소유권 확인 후 최초 노출·임대 예약 |
| `claim_due_impression(*, now: datetime, lease_seconds: float = 60) -> StoredImpression \| None` | 미확인 또는 임대가 만료된 기록 하나를 새 임대로 확보한 뒤 커밋한다. |
| `claim_blocked_401_impression( reaction_id: str \| UUID, *, now: datetime, lease_seconds: float = 60, ) -> StoredImpression \| None` | 명시한 401 차단 기록만 기존 요청 내용 그대로 새 전송 임대로 확보한다. |
| `finish_impression( reaction_id: str \| UUID, *, delivery_state: str, status: str \| None = None, reason_code: str \| None = None, request_id: str \| None = None, lease_token: str \| UUID \| None = None, next_attempt_at: datetime \| None = None, last_error: str \| None = None, last_http_status: int \| None = None, ) -> StoredImpression` | 임대 결과·접수 여부·재시도 일정 반영 |
| `ImpressionNotReady(Exception)` | 접수 완료되지 않은 노출은 판정 조회 대상으로 삼지 않는다. |
| `get_impression(*, decision_id: str \| UUID, owner_id: str \| UUID) -> StoredImpression` | 사용자 소유의 기존 노출만 읽으며 새 기록을 만들지 않는다. |
| `update_result(*, decision_id: str \| UUID, owner_id: str \| UUID, reaction_id: str \| UUID, status: str, reason_code: str \| None, request_id: str \| None) -> StoredImpression` | 판정만 조건부 갱신한다. 이미 확정된 판정과 전송 정보는 보존한다. |
