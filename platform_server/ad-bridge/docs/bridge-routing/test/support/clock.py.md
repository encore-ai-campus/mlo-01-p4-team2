# test/support/clock.py

- 경로: [ad-bridge/test/support/clock.py](../../../../test/support/clock.py)
- 기능: 실제 대기 없는 테스트 시계
- 호출·연결: 재전송 테스트 → FixedClock.__call__ / advance
- 파라미터: seconds: 진행할 초. 생성 시 고정 UTC 시각으로 초기화한다.

| 시그니처·메서드 | 기능 |
| --- | --- |
| `FixedClock` | 실제로 기다리지 않고 재전송 시각 경계를 넘긴다. |
| `FixedClock.__init__(self)` | 테스트 상태 초기화 |
| `FixedClock.__call__(self)` | 고정된 현재 테스트 시각 반환 |
| `FixedClock.advance(self, seconds)` | 고정 시계를 지정한 초만큼 진행 |
