# 광고 라우팅과 호출 흐름

기준: `7603ebf` + 2026-11-09 event-74의 사용자별 광고 발급 변경. 이 문서는 **현재 작업 코드**를 설명한다. 예정된 변경은 마지막 절에 구분한다. 아래 소스 링크는 `src/` 작업용 복사본을 가리킨다.

## 브라우저 → 게임 서버

루트 등록: [config/urls.py](../src/backend/config/urls.py)의 `api/ads/` → [ad_bridge/game/urls.py](../src/backend/ad_bridge/game/urls.py).

모두 로그인과 POST가 필요한 경로이며, 기본 Django CSRF 검증을 사용한다. `result/`도 GET이 아닌 POST이다.

| 경로 | 처리 함수 | 현재 역할 |
| --- | --- | --- |
| `/api/ads/placements/rooftop/` | `game.placements.placement` | 현재 사용자별 옥상 광고 회차 조회·발급 |
| `/api/ads/placements/portal/` | `game.placements.placement` | 서명된 이동 토큰을 검증하고 포털 개인 광고 발급 |
| `/api/ads/decision/` | `game.views.request_decision` | 설정된 개인 슬롯에서 광고 1건 발급 |
| `/api/ads/decisions/<decision_id>/impression/` | `game.views.report_impression` | 사용자 노출 기록 생성·재사용 및 광고 서버 전송 |
| `/api/ads/decisions/<decision_id>/result/` | `game.views.reaction_result` | 저장된 노출 상태 확인, 필요하면 광고 서버 노출 반응 조회 |
| `/api/ads/decisions/<decision_id>/click/` | `game.views.report_click` | 사용자·클릭 토큰 검증, PENDING 커밋·CLICK PUT 저장 확인 시도 후 광고 URL로 303 이동 |

| `/api/ads/decisions/<decision_id>/click/result/` (GET) | `game.views.click_result` | 본인 클릭의 로컬 상태만 조회 |

알 수 없는 placement는 404이다. `result/`는 현재 **노출** 조회 경로이며 광고 클릭 저장을 확인하는 API가 아니다.

## 광고 발급·표시

```text
campus.js (게임 프레임)
  → CampusAds.tick (campus-ads.js)
  → roofGeometry: 이미지 없는 광고판도 화면 좌표로 변환
  → 접근 영역·표시 여부·회차·진행 중 요청 검사
  → refreshRoof
  → POST /api/ads/placements/rooftop/
  → game.placements.placement
  → placements.get_batch
  → decisions.service.create_decision
  → RoutedAdsClient.create_decision
  → AdsClient.create_decision → 광고 서버 POST {base_url}/decisions
  → decisions.store → IssuedDecision 저장
  → PlacementBatch에 응답 보관
  → placements.public_batch (사용자에 묶인 클릭 토큰 생성)
  → 브라우저 이미지 로딩 → renderer.setDisplayImage
```

현재 옥상은 `roof:<owner_id>:<10초 회차 시작 epoch>` 키와 individual 슬롯을 사용한다. 같은 사용자·회차는 저장된 배치를 재사용하고 다른 사용자는 별도 발급한다. 광고 요청에는 현재 HMAC subject_id를 전달하며 DB에는 owner_id와 발급 당시 subject_id를 저장한다. 배치 조회·응답 변환 시 내부 소유자를 검증한다. 브라우저에 반환하는 옥상 batch_id는 키의 SHA-256 값이다. 기존 shared 키·기록은 수정하지 않는다. 포털은 기존 `portal:<transfer_id>` 키와 individual 슬롯을 유지한다.

`tick()`은 광고가 있는 층, 탭 숨김·모달 상태, 광고판 화면 좌표, 진행 중 요청과 갱신 시각을 확인한다. 사전 요청은 표시 가능한 캔버스 영역 바깥 256 CSS px에서 시작하며, 접근 상태는 384 CSS px 밖으로 벗어나면 종료한다. 화면 밖에서는 접근 시 한 번 미리 받고, 화면과 겹치는 동안만 다음 회차를 계속 요청한다. PENDING 응답과 일시적 오류는 근처에서 재시도한다. 이탈이나 렌더러 교체 후의 늦은 응답·이미지는 표시하지 않는다. 이미 표시 중인 광고는 유지하며, 유효 회차 내 재진입은 다시 요청하지 않는다.

관련 파일: [campus.js](../src/frontend/dist/campus.js), [campus-ads.js](../src/frontend/dist/campus-ads.js), [placements.py](../src/backend/ad_bridge/placements.py), [decisions/service.py](../src/backend/ad_bridge/decisions/service.py).

## 좌표 계산과 실제 노출

```text
isometric.js: displayPolygon('roof-ad-board')
  → campus-ads.js: 카메라 위치·줌·canvas 크기로 화면 좌표 변환
  → ad-visibility.js: visibleRatio
  → ContinuousExposure: 50% 이상이 연속 1초 유지됐는지 검사
  → POST /api/ads/decisions/<decision_id>/impression/
  → impressions.service.report_impression_once
  → Impression 저장/중복 재사용
  → delivery.sender.send_once
  → 광고 서버 PUT {base_url}/decisions/<decision_id>/reactions/<reaction_id>
```

`displayPolygon(id, {placeholder:true})`는 이미지가 없어도 광고판 영역을 반환하므로 사전 요청에 사용한다. 고정 크기가 없는 벽 광고는 로딩 전 16:9 비율을 추정값으로 사용한다. 기본 `displayPolygon(id)`는 여전히 이미지가 없으면 null이며, 실제 이미지 영역을 사용한 노출 측정에만 쓰인다. 사전 요청 영역은 노출 비율 계산에 사용하지 않는다.

전송 실패 후 재처리는 `dispatch_ad_reactions` 관리 명령 → `delivery.worker`가 담당한다. 조회는 `results.service.refresh_result` → 광고 서버 GET `{base_url}/decisions/<decision_id>/reactions/<reaction_id>` 흐름이다. 현재 응답 해석기는 IMPRESSION을 검증한다.

관련 파일: [isometric.js](../src/frontend/dist/isometric.js), [ad-visibility.js](../src/frontend/dist/ad-visibility.js), [impressions/service.py](../src/backend/ad_bridge/impressions/service.py), [delivery/worker.py](../src/backend/ad_bridge/delivery/worker.py), [results/service.py](../src/backend/ad_bridge/results/service.py).

## 클릭과 이스터에그 — PUT 저장 접수 기준

```text
campus-ads.js: 서명 토큰·CSRF 포함 POST 폼
  → game.views.report_click: 로그인·서명·발급 광고 검증
  → click_records.record_click: AdClick PENDING 커밋
  → configured_report → check_pending_clicks(record_id=...): 해당 클릭 한 번만 임대
  → RoutedAdsClient.report_click → AdsClient.report_click
  → 광고 서버 PUT /decisions/<decision_id>/reactions/<AdClick.id>
  → 200/201 + 본문 일치 검증
  → confirm_click: CONFIRMED(media) + record_ad_click 원자적 커밋
  → 커밋 후 업적 알림 → 기존 click_url로 303 이동

실패분: confirm_ad_clicks --limit 100
  → 같은 사용자·decision·AdClick.id·최초 시각으로 PUT 재시도
```

접수 실패는 503, 접수 후 보고·업적 장애는 이동을 유지한다. 통신 오류·429/5xx·손상 응답은 PENDING, 인증·충돌 등은 BLOCKED다. 확인 완료 기록은 다시 보고하지 않는다. 본인 기록 조회는 로컬 DB만 읽는다.

매체 보고 CLICK은 광고 서버에서 click_not_reported로 거부되지만 저장 접수 기준의 업적은 지급할 수 있다. 서버 직접 관측·과금 인정과 구분하며 `confirmation_source=media`로 저장한다. 업적 ID `c7024a2d-9f1a-46c2-b7c5-99fe57afd104`와 기존 전역 최초·계정별 제한은 유지한다. [상세 계약·재시도·설정](../ad-bridge/docs/CLICK_CONFIRMATION.md).

## 사용자와 매체 키 라우팅

- `game.views._owner_id()`는 로그인 사용자에서 내부 UUID를 읽는다. 기본 설정 경로는 `campus_profile.user_uuid`다.
- `_subject_id()`는 비밀키와 내부 UUID로 광고용 HMAC UUID를 만든다. 현재 owner_id와 subject_id는 역할이 다르다.
- `IssuedDecision`에는 발급 슬롯과 개인 광고의 owner_id·subject_id가 저장된다. shared 광고의 개별 시청자는 노출 기록에서 구분된다.
- [routing.py](../src/backend/ad_bridge/routing.py)의 `RoutedAdsClient`는 **슬롯에 맞는 매체 API 키 선택**을 담당한다. Django URL 라우터와는 별개다.
- 조회·노출·재전송은 저장된 decision의 slot_id로 매체를 선택한다. 현재 슬롯 설정만 보고 과거 광고의 매체를 추측하지 않는다.
- 실제 API 키나 .env 값은 이 문서와 src에 복사하지 않는다.

## 남은 적용·검증 작업

1. 실제 ADS_ROOFTOP_SLOT_ID를 individual 슬롯으로 설정하고 매체 키 연결을 확인한다. 사용자 UUID 직접 전송은 별도 작업이다.
2. 0006·0007 마이그레이션 적용 및 confirm_ad_clicks 재시도 스케줄링, 실제 광고 서버 PUT·게임 화면 검증.

기본 PUT 흐름은 신규 조회 API가 필요하지 않다. 향후 직접 관측 조회 어댑터는 --lookup 옵션과 ADS_CLICK_LOOKUP_BACKEND/PATH를 명시할 때만 사용하며 실제 API는 아직 미연결이다. 대시보드 작업은 제외한다.
