# 계약: `creative_course_daily` v1 — 소재 × 과정 × KST 날짜(과정별 광고 적합도 ML)

- **쓰는 쪽:** 저장소 `machine_learning/`의 부트캠프 과정별 광고·콘텐츠 적합도 모델 — 실제 과정별 반응으로 과정 가중치를 보고 재학습 방향을 정한다.
- **자리:** `<뿌리>/gold/ads/creative_course_daily/v1`(Delta, 나누지 않음). 판 고정 읽기: `spark.read.format("delta").option("versionAsOf", <판>).load(...)` — 그 판이 어느 Silver에서 나왔는지는 그 커밋의 userMetadata(`silver_generation`·`silver_version`·`gold_release`·`cutoff_at`).
- **원천:** 광고 Silver(마지막 대사가 확인한 판). 표 코드 `pipeline/gold/tables/ads.py`.
- **갱신:** 매시(광고 Silver 예약 + 10분). 회차마다 그 Silver 판 전체로 통째로 다시 만든다 — 지난 날짜도 바뀔 수 있고, 바뀌면 정정 기록에 남는다(아래).

## 열

| 열 | 형 | 뜻 |
|---|---|---|
| `kst_date` | date | 키. 반응을 광고 서버가 **받은 시각**(Silver `occurred_at` = 페이로드 `received_at`)의 KST 날짜(KST 자정 = UTC 전날 15:00, 일광 절약 시간 없음) |
| `creative_id` | string | 키. 반응이 속한 결정의 `selected.creative_id`. null = "차원 없음"(결정이 Silver 본 표에 없음) |
| `course` | string | 키. 광고를 본 사람의 대상 그룹 `code`(지금 `aio`·`mlo`·`skn`·`mle`). null = 과정 없음(아래) 또는 "차원 없음" |
| `impressions` | bigint | 인정된(`ACCEPTED`) 노출 반응 수 |
| `clicks` | bigint | 인정된 클릭 반응 수 |
| `conversions` | bigint | 인정된 전환 반응 수 — **전환이 들어온 날**에 센다(클릭한 날이 아님) |
| `confirmed` | boolean | 이 행의 숫자를 믿어도 되나(아래 "하루 칸 미확정") |

- (`kst_date`, `creative_id`, `course`)는 null을 포함해 유일하다. 노출·클릭·전환이 모두 0인 칸은 없다.
- 넣지 않는 것: 광고 원문(ML 쪽이 링크에서 직접 가져온다), `converted_clicks`(계획 AG10 (가) — 필요해지면 같은 판에 열로 더한다), 사람 열, 과금·입찰 값.

## 과정

- 과정 = 결정 3판의 `segment_id`(요청 그룹 — 매체가 그 사용자의 그룹 코드를 보냄)를 Silver `ad.segment_changed`의 `code`로 이은 값이다. 코드는 등록 뒤 바뀌지 않는다.
- **과정 = `code`, 매체 하나 전제** — 코드는 매체 안에서만 유일하다(`masters_segment_code_per_media`). 매체가 늘면 (매체, `code`)로 바꿔야 한다.
- 보관된 그룹(결정 `segment_active` 거짓)도 그 코드로 센다 — 광고 서버는 그 그룹을 "그룹 없음"으로 맞춰 광고를 골랐지만, 본 사람의 과정은 그 그룹이다.
- `mle`는 모델 과정 프로필에 없지만 그대로 둔다. 과정 → 모델 프로필 대응은 ML 쪽 일이다.
- **과정 null 칸**에 세는 반응과 그 이유(이유별 수는 그 판 커밋 userMetadata `counts` — 그 Silver 판 전체 기준):
  - `decision_v2` — 대상 그룹 기능 전 결정(2판)
  - `no_segment` — 그룹 없이 온 요청(3판, `segment_id` null). 일괄(공용) 슬롯 결정은 늘 여기다(일괄 슬롯은 그룹을 받지 않음)
  - `unknown_code` — 그룹은 있는데 Silver에 그 `ad.segment_changed`가 없다(자료 빠짐 신호)
  - `no_dimension` — 결정 자체가 Silver 본 표에 없다(이때 `creative_id`도 null)

## 끝난 날만 쓴다

- KST 날짜 D는 **D의 끝(다음 KST 자정) + 5분 30초**가 기준 시각 이하일 때 처음 쓴다. 오늘(KST) 칸은 쓰지 않는다. 5분 30초는 반응 판정이 늦을 수 있는 최대 시간(보류 300초 + 다시 판정 30초)이다.
- 기준 시각 = min(고정한 Silver 본 표의 가장 늦은 outbox 시각, 그 대사 회차 시각)이고, 직전 회차보다 뒤로 가지 않는다. 같은 Silver 판이면 언제 돌아도 같은 칸을 쓴다.
- 사건이 없는 시간(밤)에는 기준 시각이 멈춘다 — 그 앞 날짜의 칸은 다음 사건이 들어온 뒤 회차에 나간다.

## 하루 칸 미확정(`confirmed`)

- **미확정 시간이란**
  - Silver 대사에서 Bronze와 Silver가 맞지 않은 시간 w다(`mismatch` 창). 그 시간의 이벤트가 빠졌거나 겹쳤을 수 있다.
  - 판정이 최대 5분 30초 늦을 수 있어, w와 바로 앞 시간을 미확정으로 본다.
  - 평소에는 거의 생기지 않는다. 파이프라인 장애로 이벤트가 유실됐을 때 생긴다.
- **왜 하루 전체인가**
  - 이 표의 한 칸 = 소재 × 과정 × KST 하루, 곧 24시간의 합이다.
  - 미확정 시간이 하루에 하나라도 있으면, 빠진 이벤트가 어느 소재의 것인지 모르므로 그날의 모든 소재 × 과정 행을 `confirmed` = 거짓으로 둔다.
  - 예: 11-12 14시(KST)가 미확정이면 11-12의 모든 행이 거짓이다.
  - KST 00시대가 미확정이면 "바로 앞 시간" 규칙 때문에 전날(KST 23시대가 든 날)도 거짓이 된다.
  - 같은 방식으로 날에 옮겨지는 규칙 둘:
    - 대사에서 창을 정할 수 없는 Bronze 행이 Silver에 없을 수 있으면(대사 `unplaced` 줄이 `mismatch`) 어느 시간인지 몰라 모든 날의 행이 거짓이다.
    - 그 회차 대사에 창 `mismatch`가 하나라도 있으면, "차원 없음" 칸과 `unknown_code` 과정 null 칸에 센 반응이 있는 시간도 미확정 시간이고, 그 시간이 든 날의 행이 모두 거짓이다.
- **그다음**
  - 장애가 복구되어 다음 회차 대사가 `match`가 되면, 매시 통째로 다시 집계하면서 그날 행이 저절로 참으로 돌아온다.
  - 그 변화는 정정 기록에 남는다(숫자가 그대로이고 확정 값만 바뀐 칸도 — 전후 확정 두 칸).
- **ML 쪽이 쓰는 법**
  - 학습 자료는 `confirmed` = 참인 날만 쓰거나, 확정될 때까지 기다린다.
- **알려진 한계와 다른 길**
  - 하루 중 한 시간만 문제여도 그날 전체가 빠진다.
  - 판정이 5분 30초보다 늦은 반응(처리기 멈춤 등)은 확정 열로 잡지 못한다 — 광고 서버 경보로 보이고, 늦게 들어오면 다시 집계로 정정된다.
  - 필요해지면 "그날 미확정 시간 수" 열을 더해 ML 쪽이 판단하게 바꿀 수 있다(같은 표 판에서 열 추가).
  - 지금은 단순한 쪽으로 두고, 운영하며 결과를 보고 고친다.

## 정정

- 표 `<뿌리>/gold/ads/corrections`에서 `table_name = 'creative_course_daily'`. 직전 회차 판이 이미 쓴 날의 칸이 바뀌면(값·확정이 바뀜, 칸이 생김·없어짐) 칸마다 한 줄: `cell_time`(그 KST 날짜의 시작 = UTC 전날 15:00), `cell_key`(`creative_id`·`course`), `metrics_before`·`metrics_after`, `confirmed_before`·`confirmed_after`, `reason`(`generation_changed` > `gold_release_changed` > `silver_changed`), 전후 Gold 판·Silver 세대·Silver 판, `run_at`.
- 학습에 쓴 날짜가 바뀌었는지는 이 표에서 `cell_time`으로 찾는다.
