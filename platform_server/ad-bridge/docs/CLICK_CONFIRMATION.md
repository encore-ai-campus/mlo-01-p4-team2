# 클릭 보고·저장 확인

현재 업적 기준은 **우리 서버의 클릭 보고가 광고 서버 DB에 저장됐다는 응답을 확인하는 것**이다. 광고 서버가 클릭 URL 방문을 직접 관측한 기록을 조회하는 기준에서 변경했다. 기존 반응 PUT API를 사용하므로 새 광고 서버 조회 API는 필요하지 않다. 과금 인정과 이스터에그 지급은 분리한다. 대시보드는 작업 범위 밖이다.

## 흐름

```text
브라우저 POST /api/ads/decisions/<decision_id>/click/
  → 로그인·CSRF·서명·광고 소유권 검증
  → AdClick PENDING 커밋 (최초 ID·사용자·광고·클릭 시각 고정)
  → 해당 클릭만 임대하여 기존 반응 PUT 한 번 시도
  → 200/201 + 본문 일치 확인
  → AdClick CONFIRMED + 기존 record_ad_click() 원자적 커밋
  → 커밋 후 업적 알림
  → 기존 광고 서버 click_url로 303 이동
```

우리 DB 접수 실패는 503으로 끝나며 이동하지 않는다. 접수 이후 보고·업적 처리 실패는 광고 이동을 막지 않는다. 일시 오류는 PENDING으로 남겨 작업자가 재시도한다. 기존 광고 서버 click_url 이동도 유지하므로 광고 서버의 직접 관측·과금 처리는 기존대로 실행된다.

## 라우팅

| 경로·파일 | 역할 |
| --- | --- |
| POST `/api/ads/decisions/<decision_id>/click/` | 접수 커밋 → 해당 클릭 PUT 시도 → 광고로 303 이동 |
| GET `/api/ads/decisions/<decision_id>/click/result/` | 본인 클릭의 decision_id, state, clicked_at, confirmed_at 반환. 없음·타인 기록은 404. 외부 요청 없음 |
| [models.py](../../backend/ad_bridge/models.py) | AdClick: 계정, owner_id, subject_id, 결정, 최초 시각, 확인 출처·반응·상태, 재시도·임대 |
| [click_records.py](../../backend/ad_bridge/click_records.py) | 중복 재사용·임대·확인 검증, 확인 완료와 업적의 원자적 저장 |
| [click_reporting.py](../../backend/ad_bridge/click_reporting.py) | 기존 설정으로 PUT 보고 함수를 구성, 발급 슬롯·고정 ID·시각 사용 |
| [api/client.py](../../backend/ad_bridge/api/client.py) | `report_click`: CLICK PUT, HTTP 상태·응답 본문 검증 |
| [routing.py](../../backend/ad_bridge/routing.py) | 발급 슬롯에 맞는 매체 키 선택 |
| [click_worker.py](../../backend/ad_bridge/click_worker.py) | PENDING 처리 및 재시도. 확인 완료 기록은 처리하지 않음 |
| [confirm_ad_clicks.py](../../backend/ad_bridge/management/commands/confirm_ad_clicks.py) | 기본 실행은 PUT 재전송 한 배치 |
| [0006_adclick.py](../../backend/ad_bridge/migrations/0006_adclick.py) | 클릭 테이블·사용자/광고 결정 유일 제약 |
| [0007_click_confirmation_source.py](../../backend/ad_bridge/migrations/0007_click_confirmation_source.py) | 보고 확인(media)·관측 확인(server) 출처 구분. 기존 값은 추정하지 않음 |
| [PUT 테스트](../../backend/ad_bridge/tests/test_click_reporting.py) | 상태·본문 검증, 응답 유실·재전송·매체 키·작업자 경계 |
| [업적 통합 테스트](../../backend/tests/test_ad_achievement.py) | 실제 업적·알림·롤백·즉시 보고·동시 실행 |

individual의 subject_id는 IssuedDecision에서, 과거 shared는 검증된 서명 URL의 s 값에서 읽는다. 현재 HMAC을 다시 계산하지 않는다. 같은 사용자·decision은 최초 AdClick을 재사용한다. 모든 재클릭 횟수를 세지는 않는다.

## 광고 서버 계약

검토한 광고 서버 dev: `e3acc2f3025f2f191ed2e3bd2e340b77f4414351`.

```http
PUT {ADS_API_BASE_URL}/decisions/{decision_id}/reactions/{AdClick.id}
X-Media-Key: 발급 슬롯에 대응하는 매체 키
Content-Type: application/json

{"type":"CLICK","occurred_at":"최초 클릭 시각 UTC Z","subject_id":"발급 당시 가명 UUID"}
```

반응 ID는 전송 전에 커밋한 AdClick.id를 그대로 사용한다. 재시도 때 새 ID나 새 시각을 만들지 않는다. 최초 정상 접수 201과 중복 정상 접수 200을 모두 처리한다. 2xx 상태만으로 지급하지 않고 본문의 decision_id, reaction_id, subject_id, type=CLICK, occurred_at 및 접수 시각·상태 형식까지 검증한다. 시각의 `.123`과 `.123000`은 같은 값으로 인정한다.

광고 서버는 [반응 PUT 처리](https://github.com/encore-ai-campus/mlo-01-p4-team2/blob/e3acc2f3025f2f191ed2e3bd2e340b77f4414351/ads_server/serving_api/views.py)에서 커밋 후 200/201을 보낸다. 매체가 보고한 CLICK은 [클릭 출처 규칙](https://github.com/encore-ai-campus/mlo-01-p4-team2/blob/e3acc2f3025f2f191ed2e3bd2e340b77f4414351/ads_server/records/rules/click_source.py)에 따라 `click_not_reported`로 거부된다. 과금·전환 전제에 쓰이지 않지만, 이스터에그는 저장 접수 기준이므로 RECEIVED/ACCEPTED/REJECTED 응답 모두 저장 확인으로 인정한다.

PUT 응답에는 source가 없다. `confirmation_source=media`는 우리 서버가 수행한 매체 보고의 출처를 나타내며 광고 서버가 직접 관측했다고 주장하는 값이 아니다. `server_status`는 저장 확인 당시의 응답 상태이며 이후의 최종 과금 판정을 계속 조회하지 않는다.

## 실패·중복·업적

- 통신 실패, 응답 유실·손상, HTTP 429/5xx: PENDING. 동일 ID·본문으로 재시도한다. 5초부터 최대 300초 간격이며 Retry-After가 더 크면 따른다.
- HTTP 400/401/403/404/409 등: BLOCKED. 자동 재전송·업적 지급을 하지 않는다. 특히 409에서 새 반응 ID를 만들지 않는다.
- DB·업적 처리 실패: 확인과 업적을 롤백한다. 60초 임대 만료 후 동일 보고를 재시도할 수 있다.
- 설정 누락: 임대 전에 중단하여 기존 PENDING을 보존한다. 즉시 보고는 건너뛰고 광고 이동은 유지한다.
- CONFIRMED: 재보고하지 않는다. 이스터에그 달성 후 새로운 광고 결정의 클릭도 별도 기록·접수 확인을 진행한다.
- 기존 전역 최초·계정별 이스터에그 제한은 유지한다. CONFIRMED가 모든 사용자에게 업적 지급을 보장하지는 않는다. 먼저 확인·업적 커밋에 성공한 사용자가 최초 지급 대상이다.
- 자동 만료 기한은 없다. 장기 PENDING 및 BLOCKED 정리는 별도 운영 정책이 필요하다.

## 설정·적용

1. `python backend/manage.py migrate`로 0007까지 적용한다. 기존 광고·노출·지급 기록을 덮어쓰지 않는다.
2. 기존 ADS_API_BASE_URL, ADS_MEDIA_KEY, ADS_API_TIMEOUT, ADS_SUBJECT_SECRET 및 포털 매체 키/슬롯 연결을 설정한다. 클릭 보고용 새 URL·조회 설정은 필요하지 않다. 타임아웃은 임대 60초보다 짧아야 한다.
3. 요청 경로에서는 즉시 한 번 시도한다. 실패분 처리를 위해 스케줄러에서 다음 명령을 주기적으로 실행한다.

```sh
python backend/manage.py confirm_ad_clicks --limit 100
```

이 명령은 한 배치 후 종료한다. 스케줄러 설치·운영 DB 마이그레이션·실제 광고 서버 보고는 이번 로컬 작업에서 실행하지 않았다. 실서버 키·슬롯으로 클릭 → PUT 저장 응답 → 업적 → 이동 흐름을 최종 검증해야 한다.

## 향후 직접 관측 조회 (현재 기본 흐름에서 사용하지 않음)

기존에 준비한 [click_lookup.lookup](../../backend/ad_bridge/click_lookup.py)과 [HTTP 조회 경계](../../backend/ad_bridge/api/click_lookup.py)는 남겨 두었다. `ADS_CLICK_LOOKUP_BACKEND`, `ADS_CLICK_LOOKUP_PATH`는 `confirm_ad_clicks --lookup`으로 명시적으로 실행할 때만 사용한다. 실제 조회 API는 여전히 광고 서버에 없으며 응답 계약도 미확정이다. 기본 PUT 처리에는 이 설정을 비워 둔다.

## 검증

- 분리된 메모리 SQLite 광고 회귀 301개 통과.
- 전용 PostgreSQL 18 임시 DB 업적 통합·동시 실행 19개 통과.
- HTTP는 모의 응답으로 검증했다. 실제 광고 서버에 클릭 기록을 생성하지 않았다.
