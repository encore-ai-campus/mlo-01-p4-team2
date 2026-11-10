# Gold 잡 운영

Gold 잡(`pipeline/gold/gold_job.py --domain <원천>`)은 그 원천 Silver의 **마지막 대사가 확인한 판**을 고정해 읽고(`read_events(..., RECONCILED)`), 표마다 통째로 다시 집계해 덮어쓴 뒤 끝난다. 운영은 ScheduledSparkApplication(광고 Silver 예약 + 10분, `concurrencyPolicy: Forbid`)으로만 돌고, 로컬 정기 실행은 두지 않는다. 계획: `temp/pipeline/41_ads-gold-plan.md` r3e.

- 설정(기본값 없음): `GOLD_LAKE_ROOT`(Silver와 같은 뿌리), `GOLD_CODE_ROOT`(`gold_release`를 계산할 코드 뿌리 — 이미지 안 코드). `s3a://` 뿌리면 S3 넷(`S3_ENDPOINT`·`S3_REGION`·`S3_ACCESS_KEY_FILE`·`S3_SECRET_KEY_FILE` — 키는 파일에서만 읽는다, 기록에 옮겨 적지 않는다).
- 표 목록: `pipeline/gold/tables/<원천>.py`의 `TABLES`·`DELAY_SECONDS`. 광고는 `creative_course_daily` v1(ML — 소재 × 과정 × KST 날짜)·`performance_hourly` v1(성과 보고 — UTC 시간 × 캠페인·광고그룹·소재·매체·슬롯·과정). 계약 문서는 `pipeline/gold/contracts/<표>.md`.

## 자리

- Gold 표: `<뿌리>/gold/<원천>/<표>/v<판>` — 날짜로 나누지 않는다(매시 통째로 덮어쓰기 — 계획 r3e §2.2).
- 정정 기록: `<뿌리>/gold/<원천>/corrections` — 한 원천의 Gold 표 모두가 한 표(줄마다 `table_name`).
- 회차 기록: 각 Gold 표 커밋의 userMetadata(`DESCRIBE HISTORY delta.`<표>``) — Silver 세대·판·포인터 판·대사 판, `gold_release`, 기준 시각과 두 입력, 미확정, 칸 수, 이유별 수.

## 종료 코드

| rc | 뜻 | 할 일 |
|---|---|---|
| 0 | 썼다(빈 결과도 판 하나) | — |
| 3 | 확인(지표 합 = Silver 셈)이 어긋나 **아무것도 쓰지 않았다** — 로그 `gold alert check_mismatch table=… metric=… gold=… silver=…` | Gold 코드의 잇기 결함이다(같은 segment_id 에 code 가 둘인 Silver 자료도 여기서 걸린다). 고칠 때까지 Gold는 직전 판에 머문다 |
| 2 | 설정(환경변수·코드 뿌리) | 설정을 고친다 |
| 1 | 그 밖 — 포인터·대사 표 문제(`PointerError`), 돈 열이 형·범위 밖(`GoldError … 페이로드에서 꺼낸 수`), 정정 기록과 표 판이 어긋남(아래) | 메시지대로. 쓰지 않았거나, 표를 쓴 뒤 정정 전에 죽었으면 다음 회차가 빠진 정정부터 채운다 |

## 정정 기록과 회복

- 정정은 직전 회차 판이 이미 쓴 칸(그 기준 시각에 끝난 칸)의 값·확정이 바뀌었거나 생기거나 없어질 때만 한 줄씩 생긴다. 이유는 회차 단위 셋(`generation_changed` > `gold_release_changed` > `silver_changed`).
- 정정이 쓰인 마지막 Gold 판은 정정 표의 Delta txn(appId `gold:<원천>:<표>:v<판>`)이 기억한다. 회차는 시작할 때 그 뒤의 모든 Gold 판을 차례로 견줘 채운다 — 회차가 Gold 표를 쓰고 정정 전에 죽어도 줄을 잃지 않는다.
- **Gold 표 폴더를 지우고 다시 만들지 않는다.** 판이 0부터 다시 시작해 정정 기록보다 앞서면 잡이 rc 1(`… 정정 기록의 마지막 판 … 보다 앞이다`)로 멈춘다. 표 모양·뜻을 바꿔야 하면 코드에서 표 판을 올려(`v<N+1>` — 새 폴더, 새 appId) 새 표로 만든다. 열 추가는 같은 판이다(mergeSchema).
- 옛 판 파일은 정리 잡(`pipeline/vacuum/vacuum_job.py`, `runbooks/silver.md`의 "정리" 절)이 30일 뒤 지운다(14 Q4).
  - 30일은 Gold 잡 세션이 표를 만들 때 붙이는 표 속성 `delta.deletedFileRetentionDuration`이다. 회복(정정 채우기)이 옛 판을 `versionAsOf`로 읽는 한도도 이 30일이다.
  - 정리는 데이터 파일만 지운다. 표 폴더를 지우지 않고, 커밋을 지우거나 더하지 않는다. 그래서 Gold 표 판과 정정 기록의 txn이 그대로다 — 위 "표 폴더를 지우고 다시 만들지 않는다"와 부딪치지 않는다.
- **알려진 한계**(계획 45 §4 F1 — Planner 2026-11-10 결정: 지금은 고치지 않음):
  - Gold가 보존 기간(30일)을 넘겨 멈춘 뒤 다시 돌면, 정정 채우기가 직전 판을 읽지 못한다(최신 판이 아니고 30일보다 오래됨). 그러면 매 회차 rc 1로 계속 멈춘다.
  - 그 전에 D5 신선도 경보로 대응한다(Gold가 몇 시간만 낡아도 경보).
  - 실제로 7일 넘게 멈춘 일이 생기면(한도 30일보다 앞서 다시 보는 계기 — Planner 결정) 계획 45 §4 F1의 (ㄱ)(쓰기 전에 그 회차의 정정 계산)을 다시 본다.
  - Silver 대사가 30일 넘게 돌지 못하는 동안 본 표에 쓰기가 있었으면, Gold가 고정해 읽는 판(마지막 대사의 `silver_version`)이 최신이 아니고 30일보다 오래돼 읽기가 거부된다 — Gold rc 1. 대사가 다시 돌면 풀린다.

## 회차 시간

- 1년치 추정(광고 Silver 1,825만 행 — 결정 445만)에서 광고 표 둘로 한 회차 약 9~11분(코드 Step ② 측정, 1코어 — 다른 Spark 작업과 함께 돈 위쪽 값). **한 회차가 20분을 넘으면** 바뀐 칸만 다시 집계하는 방식(계획 AG3 (나))으로 옮기는 Step을 올린다 — 같은 증가라면 약 2년치에서 닿는다. 회차 시간은 실행 로그의 `gold table …` 줄 시각으로 본다.
- 정정이 가장 많은 회차: 대사 `unplaced` 불일치로 모든 칸의 확정이 뒤집히면(그리고 돌아오면) 1년치에서 회차마다 약 91만 줄(성과 보고 87.6만 + ML 3.65만), 두 번이 약 4 MB다.
