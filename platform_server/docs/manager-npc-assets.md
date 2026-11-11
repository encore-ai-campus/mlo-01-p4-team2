# 매니저 NPC 에셋

희애·정훈·송희 매니저는 사용자가 첨부한 이미지를 외형 참고로 사용했다.
생성 방식은 내장 imagegen이다. 수현 NPC 시트를 스타일·배치 참고로 사용했다.

공통 프롬프트: transparent PNG game NPC idle sprite sheet; identity from the supplied portrait; match the existing cute detailed pixel-art RPG proportions; four equally spaced full-body idle views in one vertical column: front, left, right, back; no text, labels, scenery or borders; transparent background.

| NPC | 외형 프롬프트 | 파일 |
|---|---|---|
| 희애 | Straight dark brown shoulder-length hair with full bangs; pale blue-gray long sleeves; dark trousers and shoes; friendly face. | `frontend/dist/assets/characters/manager-huiae-idle-v1.png` |
| 정훈 | Short side-parted brown hair; cheerful face; black collared long-sleeve shirt; dark trousers and shoes. | `frontend/dist/assets/characters/manager-jeonghun-idle-v1.png` |
| 송희 | Chestnut-brown shoulder-length hair with side part; soft side-swept fringe; dark brown top and dark trousers; friendly face. | `frontend/dist/assets/characters/manager-songhee-idle-v1.png` |

기존 수현·예빈·유형·영석 PNG는 변경하지 않았다.
새 시트는 각 행의 정사각형 영역을 읽어 기존 NPC와 같은 화면 크기로 표시한다.
