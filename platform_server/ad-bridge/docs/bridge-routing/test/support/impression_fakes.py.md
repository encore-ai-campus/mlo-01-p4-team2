# test/support/impression_fakes.py

- 경로: [ad-bridge/test/support/impression_fakes.py](../../../../test/support/impression_fakes.py)
- 기능: 메모리 노출 저장소와 반응 fixture
- 호출·연결: 서비스·게임·재전송 테스트 → MemoryImpressionStore / received_reaction
- 파라미터: owner: 내부 소유자; subject: 광고 가명; has_ad: 광고 존재; decision_id / reaction_id: 테스트 ID; occurred_at: 고정 발생 시각; now / lease_seconds: 임대 시각·기간; changes / payload: 대체할 필드.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `received_reaction(**payload)` | payload를 적용한 RECEIVED 응답 생성 |
| `MemoryImpressionStore` | 예약 완료 상태와 쓰기 실패 위치를 관측하는 테스트 전용 저장소. |
| `MemoryImpressionStore.__init__(self, *, owner=SUBJECT_ID, subject=SUBJECT_ID, has_ad=True)` | 테스트 상태 초기화 |
| `MemoryImpressionStore.record(self)` | 메모리 저장소의 첫 기록 조회·교체 |
| `MemoryImpressionStore.record(self, value)` | 메모리 저장소의 첫 기록 조회·교체 |
| `MemoryImpressionStore.reserve_impression(self, decision_id, owner_id, occurred_at, *, now=None, lease_seconds=60)` | 소유권 확인 후 최초 노출·임대 예약 |
| `MemoryImpressionStore.finish_impression(self, reaction_id, **changes)` | 임대 결과·접수 여부·재시도 일정 반영 |
| `MemoryImpressionStore.claim_due_impression(self, *, now, lease_seconds)` | 기한이 된 미확정·만료 임대 한 건 확보 |
