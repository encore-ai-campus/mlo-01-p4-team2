# 게임 광고 브리지 — #71

이 패키지는 `backend/ad_bridge/`에서 실행한다. `ad-bridge/scripts/`는 기존 원본과 회귀 테스트 참고용이며 이번 신규 구현의 실행 경로가 아니다.

## 현재 연결

- 옥상: 로그인 사용자별로 UTC 10초 회차마다 individual 슬롯 결정을 한 번 요청한다. 저장 키는 `roof:<owner_id>:<epoch>`이며 같은 사용자·회차는 재사용하고 다른 사용자는 별도 발급한다. 광고 서버에는 현재 HMAC subject_id를 보내고 내부 owner_id는 DB에서 소유권을 확인하는 데 사용한다. 브라우저는 서버의 종료 시각에 다음 광고를 조회하되, 새 이미지가 준비될 때까지 기존 이미지와 클릭 링크를 유지한다. 실제 교체 순간에만 이미지·클릭 주소·노출 측정 대상을 바꾼다. 실패하면 기존 광고를 유지하고 재시도한다.
- 포털: 서버의 `begin_transfer` 승인 결과로 사용자에 묶인 서명 토큰을 발급한다. 위치 변경은 기존처럼 3초 화면 이후 실행한다. 승인 전에 광고 요청을 보내지 않는다.
- 이동 한 번에 individual 슬롯 두 개를 각각 요청한다. 같은 `creative_id`면 첫 결정을 양쪽에서 공유한다. 노출과 클릭 URL도 같은 결정으로 통일하여 다른 결정의 선행 노출 누락을 방지한다. 표시하지 않은 두 번째 결정에는 노출을 보내지 않는다.
- 브라우저: 실제 이미지 영역의 50% 이상이 연속 1초 보이면 보고한다. 50% 미만·결정 교체·숨김·포커스 상실 시 시간을 초기화한다. 좌우 영역의 시간은 합산하지 않는다. 옥상 회차 경계에서는 유지 중인 광고의 연속 노출을 끊지 않는다. 새 광고 교체 시 옥상 측정만 초기화하여 포털의 연속 노출 시간을 유지한다.
- 중복: 옥상은 결정·사용자별, 포털은 이동별 저장된 결정 및 동일 소재 통합으로 보고를 한 번만 예약한다. 다른 이동이나 새 회차까지 과금을 제한하지 않는다.
- 보고 실패: 최초 반응 ID·가명·시각을 보존하고 기존 재전송 작업자를 사용한다. 브라우저에는 최종 판정과 사유를 반환하지 않는다.

옥상 요청은 화면 접근 시 미리 받고, 화면 표시 중에만 회차를 갱신한다. 브라우저의 옥상 `batch_id`는 내부 키의 SHA-256 값이며 사용자 UUID를 포함하지 않는다. 광고 없음과 발급 실패도 사용자별 회차 안에서는 재발급하지 않는다. 기존 `roof:<epoch>` shared 배치와 과거 발급·노출 기록은 덮어쓰지 않는다. 이번 사용자별 발급 변경에는 DB 스키마 변경이 없다.

## 요청 경로

게임 프런트의 기존 `/api/*` 중계와 쿠키·CSRF 전달을 사용한다.

| 경로 | 입력·동작 |
| --- | --- |
| `POST /api/ads/placements/rooftop/` | 현재 사용자의 10초 회차 조회. 서버 시각·종료 시각·광고 반환 |
| `POST /api/ads/placements/portal/` | 서버가 발급한 `token`. 같은 이동은 같은 저장 결과 반환 |
| `POST /api/ads/decisions/<decision_id>/impression/` | 로그인 사용자 기준 최초 노출 접수. 사용자 ID·반응 ID·시각을 브라우저에서 받지 않음 |
| `POST /api/ads/decisions/<decision_id>/click/` | CSRF 토큰과 서버가 발급한 `token`. PENDING 커밋 후 CLICK PUT 저장 확인 시도, 기존 `click_url`로 303 이동 |

| `GET /api/ads/decisions/<decision_id>/click/result/` | 본인 클릭의 로컬 확인 상태 조회. 광고 서버 조회는 실행하지 않음 |

기존 개인 결정 요청과 결과 조회 API도 유지한다. 노출 접수 성공은 최종 인정·차감 성공을 뜻하지 않는다.

## 광고 이스터에그

현재 기준은 **우리 클릭 보고가 광고 서버 DB에 저장됐다는 응답 확인**이다. 서버가 클릭 URL 방문을 직접 관측한 기록의 조회는 기본 흐름에서 사용하지 않는다.

클릭 POST에서 사용자·서명·소유권을 검증하고 AdClick을 PENDING으로 먼저 커밋한다. 이어 기존 반응 PUT API에 type=CLICK으로 한 번 보고한다. 반응 ID는 AdClick.id, 시각은 최초 clicked_at이며 재시도에도 고정한다. 광고 서버 200/201과 본문의 광고·사용자·반응 ID·시각을 검증하면 CONFIRMED 변경과 record_ad_click()을 원자적으로 커밋한다. 성공 후 알림을 보내며, 보고 실패 여부와 관계없이 기존 click_url로 303 이동한다. 단, 우리 DB 접수 실패는 이동하지 않는다.

광고 서버는 매체 보고 CLICK을 click_not_reported로 과금 판정에서 거부한다. 이번 업적 기준은 저장 접수이므로 RECEIVED/ACCEPTED/REJECTED 응답 모두 처리한다. confirmation_source=media로 저장하여 직접 관측 클릭과 구분한다. 기존 광고 서버 click_url 이동·노출 보고·과금 흐름은 유지한다.

같은 사용자·decision은 최초 클릭을 재사용한다. 확인 완료 기록은 재보고하지 않는다. 업적의 전역 최초·계정당 제한은 유지하되 클릭 이력은 업적 지급 여부와 관계없이 보관한다. 이전 지급 기록을 취소하거나 소급 생성하지 않는다.

기존 광고 연결 설정을 사용하며 ADS_CLICK_LOOKUP_BACKEND/PATH는 필요하지 않다. 실패분은 `python backend/manage.py confirm_ad_clicks --limit 100` 명령을 주기적으로 실행해 처리한다. [클릭 보고·재시도 라우팅](../../ad-bridge/docs/CLICK_CONFIRMATION.md)을 참고한다. 0006_adclick과 0007_click_confirmation_source 적용이 필요하며 운영 적용·스케줄러 설치는 아직 하지 않았다.

## 환경 설정 및 적용

배포 경로 확인과 재전송 서비스 설치 절차는 [배포 안내](../../deploy/ad71/README.md)에 정리했다. 운영 키·실제 서버 주소·백업 묶음은 저장소에 넣지 않는다.

`backend/.env.example`의 광고 항목을 실제 운영 환경에 설정한다.

- `ADS_ENABLED`: 기본 0. 슬롯과 이미지 접근 검증 후 1로 변경.
- `ADS_API_BASE_URL`: 광고 서버의 `/api/media/v1` 주소.
- `ADS_MEDIA_KEY`, `ADS_SUBJECT_SECRET`: 백엔드 전용 비밀.
- `ADS_PORTAL_MEDIA_KEY`, `ADS_PORTAL_MEDIA_SLOT_IDS`: 포털이 별도 매체이면 함께 설정한다. 슬롯 목록은 쉼표로 구분하며, 현재 포털 좌우 슬롯을 포함해야 한다. 목록에 없는 기존 슬롯은 `ADS_MEDIA_KEY`를 사용한다. 두 항목을 모두 비우면 기존 단일 매체 방식이다.
- 광고 발급은 요청 슬롯으로, 노출 보고·결과 조회·재전송·401 복구는 `IssuedDecision.slot_id`로 키를 선택한다. 과거 포털 기록을 새 매체로 보내지 않는다. 포털 매체 안에서 표시 슬롯을 바꿔도 과거 슬롯 ID는 `ADS_PORTAL_MEDIA_SLOT_IDS`에 남겨야 한다. 키는 같은 매체의 새 키로만 교체한다. 매체 자체를 다시 바꾸는 경우에는 별도 이행이 필요하다.
- `ADS_API_TIMEOUT`: 요청당 제한 시간(초). 포털 두 요청은 순차 실행하므로 3초 안의 이미지 표시 시간을 고려해야 한다. 예시값은 1초다.
- `ADS_ROOFTOP_SLOT_ID`: individual 슬롯 UUID. 기존 shared 슬롯 ID를 계속 사용하면 광고 요청이 실패하므로 전환 전에 새 슬롯과 매체 키의 연결을 확인한다.
- `ADS_PORTAL_LEFT_SLOT_ID`, `ADS_PORTAL_RIGHT_SLOT_ID`: individual 슬롯 UUID. 같은 슬롯을 사용할 수도 있다.

저장소 루트에서 실행한다.

```powershell
# 실제 운영 DB는 백업 후 적용한다. 로컬 검증에서는 테스트 DB를 사용한다.
python backend/manage.py migrate
# 대기 중인 노출 전송은 기존 작업자 운영 방식에 연결한다.
python backend/manage.py dispatch_ad_reactions --help
```

광고 모듈만 복사하지 말고, 이번 변경에 포함된 게임 설정·URL·이동 승인·프런트 JS/HTML/CSS와 마이그레이션을 함께 배포한다. EC2의 별도 변경은 먼저 비교한다. 앱·API·이동 명령이 함께 배포되기 전 프런트만 교체하면 이동 승인 요청이 실패할 수 있다.

## 실패 처리와 운영 제한

- 기존 이미지와 링크 유지가 광고 서버의 클릭 인정 기간을 연장하지는 않는다. 최종 인정·차감은 광고 서버 규칙을 따른다.

- `PlacementBatch`의 고유 키로 발급 전에 예약한다. 동시 요청의 패자는 PENDING을 받는다. 광고 결정 POST는 비멱등이므로 실패·응답 유실 또는 프로세스 중단 후 같은 회차·이동을 자동 재발급하지 않는다. 옥상은 다음 회차로 진행하고 포털은 해당 이동에서 광고를 생략한다.
- 포털 요청이 늦거나 실패해도 이동은 기다리지 않는다. 닫힌 이동 화면에 도착한 광고·이미지는 표시하지 않는다.
- 옥상 이미지에는 Canvas 출처 오염을 막기 위해 anonymous CORS를 사용한다. 외부 광고 이미지 서버가 CORS를 허용하지 않으면 로딩에 실패하며 노출을 보고하지 않는다. 임의 이미지 프록시는 추가하지 않았다.
- 옥상 노출은 투영된 이미지 다각형과 Canvas·브라우저 영역의 교차 면적을 계산한다. 게임 모달·입력 차단 중에는 인정하지 않는다. 게임 내부 캐릭터 등으로 가려지는 개별 픽셀과 다른 OS 창의 가림은 계산하지 않는다.
- 최소 표시 픽셀 크기는 아직 정책이 정해지지 않아 추가하지 않았다. 면적 비율만으로 아주 작은 광고의 가독성을 보장하지 않는다.
- 오래된 회차·이동 예약의 자동 삭제는 구현하지 않았다. 운영 보관 기간을 정한 뒤 데이터 정리 정책이 필요하다.
- `0004_shared_impressions`에는 기존 개인 광고와 전송 상태를 보존하는 데이터 이행이 있다. 공유 기록 생성 후 되돌리기는 DB 백업·데이터 처리 계획 없이 실행하지 않는다. `0005_placementbatch`는 회차·이동 예약을 추가한다.

## 검증

```powershell
# 운영 DB·광고 서버에 접속하지 않는 공유 저장·회차·이동 API 테스트
python backend/manage.py test ad_bridge.tests --settings=ad_bridge.tests.settings --noinput
# 서버 이동·재접속 검증용 SQLite 테스트 DB
python backend/manage.py test campus.test_motion campus.test_authoritative_socket campus.test_reconnect_transitions --settings=config.kafka_test_settings --noinput
# 화면 계산과 기존 게임 JavaScript 회귀 테스트
node --test frontend/tests/*.test.cjs
```

기존 브리지 회귀 테스트는 저장소 루트에서 `PYTHONPATH`에 `backend`와 `ad-bridge`를 넣고 `ad_bridge.tests.settings`로 `test.api test.decisions test.impressions test.delivery test.results test.game test.storage`를 실행한다. 마이그레이션 테스트는 과거 모델을 사용하고 최신 마이그레이션으로 복구한다.

실제 광고 서버의 슬롯·키·이미지 출처, 실제 브라우저 두 개에서의 교체·클릭·차감, EC2 배포는 별도 검증 대상이다. 테스트의 모의 응답과 SQLite 통과를 운영 검증 완료로 해석하지 않는다.

클릭 회귀 검증: 분리 광고 테스트 301개, 별도 PostgreSQL 18 DB의 업적 통합·동시 실행 테스트 19개 통과. HTTP는 모의 응답이며 실서버 보고·배포 검증은 남아 있다.
