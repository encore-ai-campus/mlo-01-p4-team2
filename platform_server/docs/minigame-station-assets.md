# 18층 타이머·타이핑 단말 전용 에셋

제작: 내장 imagegen. 기존 단말의 두 게임을 서로 다른 외형으로 식별할 수 있도록 제작했다.

| 게임 | 에셋 | 크기/용량 |
|---|---|---|
| 11.11초 타이머 | `frontend/dist/assets/minigames/timer-station-v1.webp` | 최대 640px, 약 70KB |
| 코드 타이핑 | `frontend/dist/assets/minigames/typing-station-v1.webp` | 최대 640px, 약 82KB |

공통 프롬프트: transparent standalone isometric game prop; premium Pepero Day chocolate atelier palette; rose pink, ivory, champagne gold and chocolate brown; polished detailed outlined 2D RPG style; front/right three-quarter view; complete object inside the frame, no environment or characters.

타이머 프롬프트: waist-high rose-pink/ivory cabinet, gold trim, large round stopwatch with ivory dial and gold crown, inset 11.11 display, large STOP button, biscuit sticks and a small bow; no keyboard or monitor.

타이핑 프롬프트: chocolate wooden desk with ivory top and gold trim, rose-pink retro typewriter with ivory round keys and gold spacebar, cream paper with code-like indented lines, ink bottle, ribbon-tied recipe papers and biscuit sticks; no clock, monitor or chair.

연결: `frontend/maps/minigames.json`의 seasonalArt/artWidth/artHeight를 통해 기존 시즌 이미지 캐시를 사용한다. 원래 단말 ID, 게임 코드, 74x54 바닥 충돌 범위와 상호작용 좌표는 유지했다. 맵 생성기로 브라우저 지도와 서버 충돌 지도를 함께 갱신했다.

검증: 프런트 164개, 서버 단말 검사 4개 통과. 실제 로그인 후 이미지 로드, 타이머·타이핑 실행, 닫기 중단 기록 저장 확인. 기존 로컬 DB의 typing 행이 비활성·미배치였으므로 현재 배치와 일치하도록 해당 두 플래그만 활성화했다. 기존 플레이 기록은 유지했다.

## 2026-11-04 긴 벽을 등지는 방향

직원실 안쪽 긴 벽(x=2206.5) 쪽으로 단말을 옮기고 동쪽 통로를 향하게 했습니다. 바닥 크기는 54x74로 회전, 상호작용은 동쪽 앞 28px 지점입니다. 타이머 (2211.5,180), 타이핑 (2211.5,340). 작은 표시 크기와 위쪽 이름표는 유지합니다.

활성 에셋은 `frontend/dist/assets/minigames/timer-station-east-v1.webp`, `typing-station-east-v1.webp`입니다. 내장 imagegen으로 기존 소스의 정체성·색상·비율을 유지하면서 upright vertical-axis quarter turn, front faces lower-right, readable STOP/11.11, no mirrored text, transparent background 조건으로 방향 변형을 제작했습니다. 기존 방향 파일은 보존하고 활성 방향 이미지만 로드합니다.

벽 밀착 보정: 벽 안쪽 면에서 가구 뒷면까지 2 단위 간격으로 줄이고, 이미지 바닥 기준점을 0.99로 조정했습니다. 가구 그림을 벽 쪽으로 4 단위 보정하고 약한 접촉 그림자를 추가했습니다. 이름표에도 같은 그림 위치 보정을 적용합니다. 관련 프런트 15개·접근 검사 4개와 실제 두 게임 실행·닫기 저장 확인.


## 2026-11-04 가구 모서리와 벽 투영축 정렬

단순한 위치 밀착과 별도로 `artPlaneSlopes`로 이미지의 바닥 모서리 기울기를 보정했습니다. 가구의 앞·뒤 모서리가 실제 맵 벽 투영축과 평행해지도록 가로 축척과 세로 전단을 적용하며, 이미지 세로 방향은 수직을 유지합니다. 이름표와 스프라이트 캐시 범위도 같은 보정을 반영합니다. 다른 시즌 에셋은 기본 투영을 유지합니다.

프런트 검사 166개 통과. 두 가지 맵 기울기에서 보정된 양쪽 바닥 축의 기울기가 벽 축과 일치하는지 검사했고, 실제 서버 화면에서도 확인했습니다. 새 래스터 에셋 생성은 없으며 기존 방향 이미지를 사용합니다.

직원실 안쪽 긴 벽(x=2206.5) 쪽으로 단말을 옮기고 동쪽 통로를 향하게 했습니다. 바닥 크기는 54x74로 회전, 상호작용은 동쪽 앞 28px 지점입니다. 타이머 (2220,180), 타이핑 (2220,340). 작은 표시 크기와 위쪽 이름표는 유지합니다.

활성 에셋은 `frontend/dist/assets/minigames/timer-station-east-v1.webp`, `typing-station-east-v1.webp`입니다. 내장 imagegen으로 기존 소스의 정체성·색상·비율을 유지하면서 upright vertical-axis quarter turn, front faces lower-right, readable STOP/11.11, no mirrored text, transparent background 조건으로 방향 변형을 제작했습니다. 기존 방향 파일은 보존하고 활성 방향 이미지만 로드합니다.

2026-11-05 통합: 기존 두 장치의 벽면 투영·그림자 보정을 보존하고 pairing-garden-east 에셋을 추가했습니다. 페어링 정원은 직원실 (2211.5,60), 상호작용 (2293.5,97)에 배치하여 타이머·타자기와 겹치지 않습니다.
