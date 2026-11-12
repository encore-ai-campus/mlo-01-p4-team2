# scripts/static/ad_bridge/display.js

- 경로: [ad-bridge/scripts/static/ad_bridge/display.js](../../../../../scripts/static/ad_bridge/display.js)
- 기능: 광고 이미지를 Canvas에 그리고 클릭 주소를 연다.
- 호출·연결: 게임 화면 → `AdBridgeDisplay` → `Image` / `context.drawImage` / `window.open`

| 시그니처·메서드 | 파라미터 | 기능 |
| --- | --- | --- |
| `AdDisplay.constructor()` | 없음 | 현재 광고와 로딩 버전 초기화 |
| `async AdDisplay.load(decision)` | 광고 결정과 ad.image_url | 이미지 로딩; 이전 요청이 늦게 도착하면 무시 |
| `AdDisplay.draw(context, rect)` | Canvas context; rect의 x·y·width·height | 비율을 유지하며 지정 영역에 표시 |
| `AdDisplay.click()` | 없음 | 현재 광고의 click_url을 새 탭으로 열기 |

현재 이 파일은 클릭 POST·로컬 클릭 기록 저장을 수행하지 않는다.
