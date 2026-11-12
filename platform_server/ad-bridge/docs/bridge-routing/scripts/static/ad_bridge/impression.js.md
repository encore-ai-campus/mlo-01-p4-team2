# scripts/static/ad_bridge/impression.js

- 경로: [ad-bridge/scripts/static/ad_bridge/impression.js](../../../../../scripts/static/ad_bridge/impression.js)
- 기능: 광고 영역의 화면 내 포함 여부와 결정별 최초 관측 확인.
- 호출·연결: 게임 화면 → `AdBridgeImpressionDetector`; 외부 HTTP 호출 없음.

| 시그니처·메서드 | 파라미터 | 기능 |
| --- | --- | --- |
| `ImpressionDetector.constructor()` | 없음 | 관측한 결정 ID 집합 초기화 |
| `static ImpressionDetector.isRegionVisible({bounds, region, canvasWidth, canvasHeight, viewportWidth, viewportHeight})` | bounds: Canvas CSS 영역; region: 광고 영역; 나머지: Canvas·화면 크기 | CSS 배율을 적용해 광고 영역 전체가 화면 안인지 검사 |
| `ImpressionDetector.observe({decisionId, drawn, inZone, visible})` | 결정 ID; 실제 그림·영역 진입·화면 표시 여부 | 조건을 만족하는 결정마다 최초 한 번 true 반환 |
