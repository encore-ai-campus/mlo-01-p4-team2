# 계약: `performance_hourly` v1 — 광고 성과 보고(UTC 시간 칸)

- **쓰는 쪽:** 광고 팀 분석(노트북·Spark SQL — 계획 AG1 (가)). 광고주 콘솔은 지금처럼 광고 서버 DB로 계산하고 이 표를 읽지 않는다.
- **자리:** `<뿌리>/gold/ads/performance_hourly/v1`(Delta, 나누지 않음). 판 고정 읽기: `option("versionAsOf", <판>)` — 그 판의 원천은 커밋 userMetadata(`silver_generation`·`silver_version`·`gold_release`·`cutoff_at`).
- **원천:** 광고 Silver(마지막 대사가 확인한 판). 표 코드 `pipeline/gold/tables/ads.py`.
- **갱신:** 매시(광고 Silver 예약 + 10분). 회차마다 그 Silver 판 전체로 통째로 다시 만든다 — 지난 시간도 바뀔 수 있고, 바뀌면 정정 기록에 남는다.
- **규칙:** 광고 서버 콘솔 보고(`ads_server/records/report.py`)와 같은 규칙으로 시작했다(사용자 2026-11-10 "콘솔 기준으로 시작"). 더 필요한 지표는 같은 판에 열로 더한다.

## 열

| 열 | 형 | 뜻 |
|---|---|---|
| `hour` | timestamp | 키. UTC 1시간 칸의 시작. 반응은 광고 서버가 **받은 시각**(`received_at`), 과금은 원장 시각(= 그 반응의 수신 시각)으로 나눈다 |
| `campaign_id`·`ad_group_id`·`creative_id` | string | 키. 반응·과금이 속한 결정의 `selected` |
| `media_id`·`slot_id` | string | 키. 그 결정의 매체·슬롯 |
| `course` | string | 키. 그 결정의 대상 그룹 `code`(`creative_course_daily`와 같은 규칙 — 과정 = `code`, 매체 하나 전제). null = 과정 없음(이유 `decision_v2`·`no_segment`·`unknown_code`) |
| `impressions`·`clicks`·`conversions` | bigint | 인정된(`ACCEPTED`) 반응 수(유형별). 일괄 슬롯은 사람마다 한 건씩(판정이 사람마다) |
| `converted_clicks` | bigint | 이 칸의 인정된 클릭 가운데, 같은 (결정, 사람)에 인정된 전환이 있는 클릭 수(클릭 코호트 — 콘솔 전환율의 분자). **클릭의 칸**에 센다 |
| `spent` | bigint | 원장 과금의 `charged_amount` 합 — 실제로 받은 포인트(부족분 제외) |
| `confirmed` | boolean | 이 시간 칸의 숫자를 믿어도 되나(아래) |

- 결정이 Silver 본 표에 없는 반응·과금은 버리지 않고 차원 열이 모두 null인 칸("차원 없음")에 센다 — 그래서 시간 합이 Silver와 맞는다. "차원 없음" 칸의 수는 그 시간의 다른 칸에서 모자란 수다. 이유별 수는 커밋 userMetadata `counts`.
- 비율은 저장하지 않는다. 읽을 때 합을 나눈다: 클릭률 = `clicks / impressions`, 전환율 = `converted_clicks / clicks`(콘솔과 같은 정의).
- `converted_clicks`는 **클릭 칸 뒤 30일 동안 바뀔 수 있다** — 전환은 클릭 뒤 30일 안에만 인정되고(`REACTION_CONVERSION_WINDOW_SECONDS`), 전환이 들어오면 지난 클릭 칸이 다시 집계되어 정정 기록이 남는다. 클릭 뒤 30일(+ 판정 지연)이 지나야 최종이다. 이 "최종"은 `confirmed`에 섞지 않는다.
- 사람 열은 없다. 광고주(owner)는 이벤트에 없어(일부러 싣지 않음) 캠페인까지만 묶는다.

## 하루(예산 일)로 묶기

- 이 표는 시간 칸만 둔다(계획 AG6 (가)). 하루 합은 읽는 쪽이 시간 칸을 더해 만든다 — 지표가 모두 합이라 경계를 바꿔도 다시 만들 것이 없다.
- **예산 일 = 광고 서버 설정 `BUDGET_DAY_OFFSET_HOURS`를 따른다. 운영 값은 9(KST 자정), 그 전은 0(UTC 자정). 적용 시각: 2026-11-10T00:05:11Z(09:05:11 KST, 운영 판 `1d3cef4` — 운영 환경 파일에 이 키가 없어 코드 기본값 9).**
  - 예산 일 D = `hour + 오프셋 시간`의 UTC 날짜가 D인 칸들. 오프셋 9이면 `to_date(hour + INTERVAL 9 HOURS)`.
  - 적용 시각 앞의 날을 콘솔과 맞춰 볼 때는 그때의 오프셋(0)으로 묶는다. 운영 원장 과금은 적용 전 0줄이었다.

## 끝난 시간만 쓴다

- 시간 칸 [h, h+1시간)은 **h+1시간 + 5분 30초**가 기준 시각 이하일 때 처음 쓴다. 5분 30초는 반응 판정이 늦을 수 있는 최대 시간(보류 300초 + 다시 판정 30초)이다.
- 기준 시각 = min(고정한 Silver 본 표의 가장 늦은 outbox 시각, 그 대사 회차 시각)이고 직전 회차보다 뒤로 가지 않는다. 사건이 없는 시간(밤)에는 기준 시각이 멈춰, 마지막 칸이 다음 사건 뒤 회차에 나간다.

## 확정(`confirmed`)

- Silver 대사에서 Bronze와 Silver가 맞지 않은 시간 w(`mismatch` 창)가 있으면, w와 바로 앞 시간의 모든 칸이 거짓이다(판정이 최대 5분 30초 늦어 한 시간 안).
- 대사에서 창을 정할 수 없는 Bronze 행이 Silver에 없을 수 있으면(대사 `unplaced` 줄이 `mismatch`) 모든 칸이 거짓이다.
- 그 회차 대사에 창 `mismatch`가 하나라도 있으면, "차원 없음" 칸과 `unknown_code` 과정 null 칸에 센 반응·과금이 있는 시간의 모든 칸도 거짓이다(그 결정이 Silver에서 빠졌을 수 있다).
- 다음 회차 대사가 `match`가 되면 다시 집계로 저절로 참이 되고, 정정 기록에 확정 전후가 남는다.
- 한계: 판정이 5분 30초보다 늦은 반응은 확정 열로 잡지 못한다 — 광고 서버 경보로 보이고, 늦게 들어오면 다시 집계로 정정된다.

## 알려진 시험 데이터

운영 자료에 들어 있는 시험용 데이터다. 코드로 거르지 않으니, 분석하는 사람이 이 목록으로 거른다.

| 캠페인 | 들어간 것 | 이 표에서 |
|---|---|---|
| `01a1c2c7-5943-704a-b5e7-8e2f52a0a199` | 운영 시험 캠페인(2026-11-10 00:06~00:07Z). 광고그룹이 없어 결정·노출·과금이 없고, Silver에 `ad.campaign_changed` 두 건(CREATED·PERIOD_CHANGED)만 있다 | 칸이 생기지 않는다(이 표는 반응·과금만 센다). 캠페인 목록을 다른 원천에서 붙일 때 거른다 |

## 정정

- 표 `<뿌리>/gold/ads/corrections`에서 `table_name = 'performance_hourly'`. 직전 회차 판이 이미 쓴 시간의 칸이 바뀌면(값·확정, 생김·없어짐) 칸마다 한 줄: `cell_time`(= `hour`), `cell_key`(차원 여섯), `metrics_before`·`metrics_after`(지표 다섯), `confirmed_before`·`confirmed_after`, `reason`(`generation_changed` > `gold_release_changed` > `silver_changed`), 전후 Gold 판·Silver 세대·Silver 판, `run_at`.
- 늦은 판정·격리·클릭 뒤 전환(`converted_clicks`)·세대 바꿈으로 지난 칸이 바뀐 이력을 여기서 본다.
