# Silver 잡 운영

Silver 잡(`pipeline/silver/silver_job.py`)은 원천 하나·세대 하나의 Bronze를 한 번 읽고(availableNow) 끝난다. 로컬은 래퍼 `pipeline/silver/run-silver.sh`와 LaunchAgent(매시 :10)로, 운영은 ScheduledSparkApplication으로 돈다. 설정 이름은 `pipeline/README.md`의 Silver 잡 문단에 있다.

- 비밀값(RustFS 키)은 파일에서만 읽는다. 기록에 옮겨 적지 않는다.
- Silver 표·checkpoint 경로: `<뿌리>/silver/<원천>/{events,errors}/g<세대>/`, `<뿌리>/checkpoints/silver/<원천>/events/g<세대>/`, 대사 결과 `<뿌리>/silver/<원천>/reconcile/g<세대>/`, 유령 격리 표 `<뿌리>/silver/<원천>/quarantine/g<세대>/`(SL-9), 세대 포인터 `<뿌리>/silver/<원천>/pointer/`(SL-7 — 원천마다, 세대 번호 없음). 로컬 뿌리는 `s3a://ads-lake`.

## 실행 전용 코드 사본

래퍼는 작업 저장소가 아니라 **리뷰된 커밋에 맞춘 사본**에서 돈다. 제출 스크립트가 실행하는 저장소의 `pipeline/silver`·`pipeline/common`을 그대로 보내므로, 작업 저장소에서 돌면 리뷰 전 코드가 Silver 표에 쓴다. 래퍼는 사본 HEAD가 `SILVER_CODE_COMMIT`과 다르거나 사본에 미커밋·추적 안 된 파일이 있으면 돌지 않는다(rc 2). git 확인이 실패해도 돌지 않는다. 넘김 목록·유령 격리 목록과 코드 뿌리는 늘 사본의 것이고 환경변수로 덮을 수 없다.

- 자리: `$HOME/pipeline-run/silver/mlo_01_p4_team2`(경로에 공백을 두지 않는다 — Spark에 파일 경로로 넘어간다).
- 실행 사본은 소유자마다 따로 둔다. 광고 잡(감시·manifest·대사)은 `$HOME/pipeline-run/ads/mlo_01_p4_team2`다. 한쪽이 커밋을 올려도 다른 쪽 래퍼가 거부되거나 기다리지 않는다. Silver 사본에서는 Silver 래퍼만 돌린다.
- 만들기(한 번):
  ```bash
  git clone <작업 저장소 경로> "$HOME/pipeline-run/silver/mlo_01_p4_team2"
  git -C "$HOME/pipeline-run/silver/mlo_01_p4_team2" checkout --detach <커밋>
  ```
- 올리기(리뷰를 거쳐 커밋된 코드·넘김 목록으로):
  ```bash
  git -C "$HOME/pipeline-run/silver/mlo_01_p4_team2" fetch origin
  git -C "$HOME/pipeline-run/silver/mlo_01_p4_team2" checkout --detach <새 커밋>
  git -C "$HOME/pipeline-run/silver/mlo_01_p4_team2" status --porcelain --untracked-files=all   # 빈 출력
  ```
  그다음 LaunchAgent의 `SILVER_CODE_COMMIT`을 새 커밋으로 바꾸고 다시 싣는다(아래 제거 → 설치). 바꾸지 않으면 다음 회차가 rc 2로 거부한다.
- 새 커밋이 어댑터 규칙이나 공통 열을 바꾸면(같은 Bronze 행이 다른 Silver 행이 됨) 같은 세대로 올리지 않는다. 새 세대로 돌린다(설계 SV6).
- 사본에서는 아무것도 고치지 않는다.

## 손 실행

```bash
SILVER_CODE_COMMIT=<커밋 40자> bash "$HOME/pipeline-run/silver/mlo_01_p4_team2/pipeline/silver/run-silver.sh" metaverse 2
```

- 메타버스 세대 2(MVR-3a — 원천 열 `outbox_uuid`)는 **새 이름 Bronze 경로가 정해진 뒤에만** 돌린다. 지금 래퍼의 메타버스 Bronze 기본 경로(`bronze/metaverse/outbox_events`)에는 옛 이름(`event_uuid`) 행만 있어, 돌리면 모두 오류 표(`contract`, `event_id` 없음)로 간다. 경로가 정해지면 래퍼의 그 줄을 바꾸는 커밋을 먼저 한다(계획 26 결정 G).
- 메타버스 세대 1(옛 이름 원천, SL-5 `6434174`)은 **더 돌리지 않는다**. MVR-3a(`ae755cf`) 뒤 커밋의 사본으로 g1을 돌리면, 옛 Bronze에 새로 들어온 행 가운데 옛 이름(`event_uuid`) 행은 g1 오류 표(`contract`, `event_id` 없음)로 가고, 새 이름(`outbox_uuid`) 행은 g1 Silver 정상 행으로 섞여 들어간다. g1 표는 "되돌리기" 요청이 있을 때까지 그대로 둔다.
- Standalone 1코어다. 다른 예약 잡 시각(:05~:10, 메타버스 manifest·대사 :15·:17, 하루 잡 00:35 UTC)을 피한다.
- 종료 코드:

| rc | 뜻 | 할 일 |
|---|---|---|
| 0 | 끝남(새 Bronze가 없으면 배치 없이 끝남) + 대사 `match` | 없음 |
| 3 | Silver 쓰기는 끝났고, 대사가 불일치를 찾음(결과 표는 썼음) | 아래 "대사" 절. **고칠 때까지 매 회차 3이다** |
| 75 | 같은 표의 앞 회차가 돌고 있어 건너뜀(lockf 한 줄) | 없음. 계속 나오면 앞 회차가 오래 걸리는 까닭을 본다 |
| 2 | 래퍼가 거부(코드 사본·인자) | stderr의 `silver refused` 줄대로 사본을 맞춘다 |
| 그 밖 | 설정 오류(Spark 전) 또는 배치 실패·멈춤, 시작 옮기기 확인 실패(`QuarantineError`) | 로그를 본다. 멈춤이면 아래 "멈췄을 때", `QuarantineError`면 "유령 격리" 절 |

- 로그 줄: `silver start`(release·넘김 줄 수·격리 줄 수), 시작 옮기기 `silver quarantine moved=…`, 배치마다 `silver udf`·`silver check`·`silver batch`, 이중 적재를 줄였으면 `silver alert bronze_duplicates`, 격리 사본을 넣었으면 `silver quarantine batch=…`, 멈추면 `silver stop`.
  스트림 뒤 대사: `silver reconcile`(회차 요약 — 상태·창 수·불일치 수·중복·판·시간), 불일치 줄마다 `silver alert reconcile_mismatch`·`silver alert reconcile_copies`(10줄까지, 넘으면 `silver alert reconcile_more`), Silver 안 중복이면 `silver alert silver_duplicates`, 격리 경보 `silver alert quarantine_overlap`·`silver alert quarantined_republished`("유령 격리" 절).

## 대사(SL-6 — Bronze ↔ Silver)

스트림이 성공하면 같은 앱이 대사를 돈다(계획 temp/pipeline/35_silver-reconcile-sl6.md, 계획 15 6판 r9 SL-6 줄).

- 범위: Silver checkpoint의 마지막 끝난 배치가 가리키는 Bronze 판 V(`versionAsOf V` = Silver가 처리한 행). 늦게 온 Bronze 행 때문에 거짓 불일치가 나지 않는다.
- 맞추는 법:
  - 오류 표(진단 줄 = 멈춤 코드 + `acknowledged=false`는 뺌)는 Bronze 위치로 맞춘다. Bronze에 없는 위치면 `mismatch`, 같은 위치가 두 번이면 `copies_mismatch`.
  - 오류 표에 위치가 없는 Bronze 행은 UTC 시 창마다 Silver와 맞춘다. 고유 ID 집합(건수·`ids_sha256`)이 같고 고유 위치 수 = `copies` 합이어야 한다. 위치가 더 많으면 `mismatch`(Bronze 행이 어디에도 없음), 적으면 `copies_mismatch`(`copies`가 부풂).
  - 오류 표에 없는데 창·ID를 꺼낼 수 없는 Bronze 행은 `mismatch`(놓을 수 없음 줄).
  - Silver 안 같은 `event_id`가 둘 이상이면 그 행들의 창이 `mismatch`(두 번째 그물 — 매 회차).
- Silver 쪽은 본 표 ∪ 격리 표다(SL-9 — 아래 "유령 격리" 절).
- 결과 표 `<뿌리>/silver/<원천>/reconcile/g<세대>/`: 회차마다 덮어쓴다(창마다 한 행 + `errors` 줄 + `unplaced` 줄 + `quarantine` 줄). 열이 늘면 표 모양째 덮어쓴다(회차 스냅숏). 지난 회차는 `DESCRIBE HISTORY`·`versionAsOf`로 본다. 커밋 `userMetadata`에 회차 요약이 있다. 요약 `status`(로그 줄 `silver reconcile status=`와 같음)는 줄 가운데 가장 무거운 것이다: `mismatch` > `copies_mismatch` > `match`.
- `mismatch` 창은 Gold에서 미확정이다. `copies_mismatch`는 경보만 내고 Gold 확정은 막지 않는다(사건 수는 ID로 센다).

rc 3이 나면:
- 결과 표에서 `status`가 `match`가 아닌 줄과 예시(`missing_examples` = Bronze에만 있는 ID 또는 위치, `extra_examples` = Silver·오류 표에만 있는 ID 또는 위치)를 본다.
- Silver 표·오류 표·checkpoint를 지우거나 고쳐 다시 돌리지 않는다. Planner에게 알린다. 처리는 관리 Step이다.
- 관리 Step이 Silver 행을 오류 표로 옮길 때는 그 행 사본들의 Bronze 위치마다 Bronze 행을 오류 표에 한 줄씩 쓴다. Silver 행에는 첫 사본 위치만 있어서, 그러지 않으면 대사가 `mismatch`로 알린다.
- **rc 3은 고칠 때까지 매 회차 난다.** 운영은 `OnFailure` 재시도까지 매시 네 번 실패한다. 재시도는 해가 없다(새 Bronze가 없으면 배치 없이 대사만 다시).
- 그 사이 진짜 멈춤(rc 1)은 앱 상태로 구별되지 않는다. 로그 줄로 구별한다: `silver stop`이나 예외가 있으면 멈춤, `silver alert reconcile_*`·`silver alert silver_duplicates`·`silver alert quarantine_overlap`만 있으면 대사 불일치다.
- 운영 메타버스 확인용 계정(outbox 12행)도 다른 행과 똑같이 대사한다. 분석에서 빼는 일은 Gold(G3·G11)다.

## 유령 격리(SL-9)

유령은 원천 DB(outbox)에 없는데 Bronze·Silver에 있는 이벤트다(DB를 앞 시점으로 되돌린 뒤 사라진 행 — 계획 temp/pipeline/38_silver-quarantine-sl9.md). 리뷰를 거친 목록 파일의 `event_id`를 Silver 본 표에서 격리 표 `<뿌리>/silver/<원천>/quarantine/g<세대>/`로 옮긴다. 격리는 지우는 일이 아니다. 격리 표 행은 되돌리기 관리 Step 말고는 지우지 않는다.

- 목록 파일: `pipeline/silver/quarantine/<원천>.json` — `{"domain": "<원천>", "lines": [줄, …]}`. 처음에는 빈 목록이다. 잡은 `SILVER_QUARANTINE`(래퍼는 늘 실행 사본의 이 파일)로 읽는다.
- 줄 키는 정확히 다섯이다. 다르면 잡이 시작하지 않는다.
  - `event_id`: 소문자 UUID(Silver `event_id` 표기). 같은 ID 두 줄은 거부한다.
  - `reason`·`incident`(사건 ID)·`listed_by`: 빈 글자가 아닌 문자열
  - `listed_at`: 줄을 쓴 시각, `YYYY-MM-DDTHH:MM:SSZ`(UTC)
- 잡이 하는 일:
  - 스트림 전 시작 옮기기: 본 표에 남은 목록 ID를 격리 표에 넣고(MERGE 넣기) 본 표에서 지운다(MERGE 지우기). 앞뒤로 목록 ID의 (ID 집합, `copies` 합)이 같은지 본다. 로그 `silver quarantine moved=… already=… pending=… seconds=…`(`pending` = 아직 어디에도 없는 목록 ID — 멈추지 않는다).
  - 두 표에 같은 목록 ID가 **다른 행**으로 있거나(되돌리기 순서를 어김·손으로 고친 표) 앞뒤 값이 다르면 스트림 전에 `QuarantineError`로 멈춘다(rc 1). 지우지 않는다. 두 표를 보고 Planner에게 알린다.
  - 배치 안: 목록 ID의 사본은 언제 오든 격리 표로 간다(같은 내용이면 `copies`만 더함, `quarantined_at`은 처음 넣은 값 그대로). 내용이 다르면 오류 표 `conflict`다. 로그 `silver quarantine batch=… rows=…`.
- 대사: Silver 쪽은 본 표 ∪ 격리 표다(창 줄 `quarantine_ids` = 그 창의 격리 ID 수). 결과 표에 `kind` = `quarantine` 줄이 하나 붙는다.
  - `overlap_ids`: 본 표와 격리 표에 다 있는 ID 수. 그 ID의 창도 `mismatch`다. 경보 `silver alert quarantine_overlap ids=… examples=[…]`.
  - `list_not_in_bronze`: Bronze(V)와 오류 표 어디에도 없는 목록 ID 수(오타·다른 원천). 예시는 `missing_examples`. **목록을 고칠 때까지 rc 3이다.**
  - `republished_ids`: 목록 ID 가운데 Bronze `ingested_at`이 그 줄 `listed_at`보다 뒤인 op `r` 사본이 있는 ID 수. 경보 `silver alert quarantined_republished ids=… incidents=[…] examples=[…]`만 내고 rc에는 닿지 않는다. 목록이 틀렸을 수 있다는 뜻이다 — Planner에게 알리고, 틀렸으면 아래 되돌리기다.
  - `quarantine` 줄 `status`: 겹침 > 0 또는 `list_not_in_bronze` > 0이면 `mismatch`, 아니면 `match`(재발행만이면 `match` + 경보).
- Gold는 본 표만 읽는다. 격리 표의 `quarantined_at`(그 행이 격리 표에 처음 들어온 시각 — 본 표에서 옮겼으면 옮긴 시각, 배치에서 바로 왔으면 그 배치 시각. 바뀌지 않음)으로 다시 집계할 구간을 안다. 새 행을 고르는 기준선(Delta 판 또는 시각)은 Gold Step에서 정한다.
- 본 표 `silver_written_at`의 뜻은 "그 행이 본 표에 들어온 시각"이다. 처음 쓴 시각과 다를 수 있다(되돌리기로 돌아온 행 — 아래).

### 목록에 줄 더하기(리뷰)

1. 사건: RB-2(또는 hop 대사 불일치 "Bronze에만 있음")가 유령 창과 사건 ID를 남긴다.
2. 후보 ID를 만든다. **사건 기록 직후, outbox 정리 전에 만든다.** 창은 사건 기록의 hop 대사 줄과 같은 창(시작·끝, UTC)이다.
   - 앞조건 ① **그 창이 지워지지 않았음.** outbox는 창 단위로 지운다. 지운 창에서 아래 차집합을 돌리면 그 창의 진짜 이벤트가 모두 후보로 나온다.
     - 광고: 감사 `events.outbox_purged`에 그 창이 없다(구현 전이면 정리 작업이 아직 돌지 않았음을 확인한다).
     - 메타버스: 지금은 정리가 없음을 확인한다.
   - DB 쪽 ID(읽기 역할 — 비밀값은 파일로만, 대화·기록에 옮기지 않는다). 광고는 `<outbox 표>` = `public.events_outbox`, `<ID 열>` = `event_id`. 메타버스는 `public.eventing_eventoutbox`, `outbox_uuid`. 시각 열은 둘 다 `created_at`이다.
     ```bash
     psql "<읽기 역할 접속>" -c "\copy (SELECT lower(<ID 열>::text) FROM <outbox 표> WHERE created_at >= '<창 시작>' AND created_at < '<창 끝>') TO 'db_ids.txt'"
     LC_ALL=C sort -u -o db_ids.txt db_ids.txt
     ```
   - Silver 쪽 ID(Spark 세션 — 잡과 같은 Delta·S3 설정, 세션 시간대 UTC). 오류 표 `conflict` ID는 뺀다(hop 대사 Bronze 쪽 건수도 내용이 다른 사본 ID를 뺀 고유 ID다).
     ```sql
     SELECT event_id FROM delta.`<본 표>`
     WHERE produced_at >= TIMESTAMP '<창 시작>' AND produced_at < TIMESTAMP '<창 끝>'
       AND event_id NOT IN (SELECT event_id FROM delta.`<오류 표>` WHERE error_code = 'conflict' AND event_id IS NOT NULL)
     ```
     결과를 한 줄에 하나씩 `silver_ids.txt`로 두고 `LC_ALL=C sort -u -o silver_ids.txt silver_ids.txt`.
   - 앞조건 ② **건수 맞추기.** 둘 다 맞아야 한다. **하나라도 다르면 목록을 만들지 않는다**(사건 기록에 적고 Planner에게 올린다). hop 대사 기록은 (창, 이벤트 이름)마다 한 줄이다. 그래서 그 창의 줄 **모두**의 `count`를 더해 맞춘다(`count`는 내용이 다른 사본이 있는 ID를 뺀 고유 ID 수다).
     - `wc -l < db_ids.txt` = 그 창 hop 줄들의 outbox 쪽 `count` 합
     - `wc -l < silver_ids.txt` = 같은 창 줄들의 파이프라인 쪽(`hop_pipeline`) `count` 합
     - 둘이 맞으면 `LC_ALL=C comm -23 silver_ids.txt db_ids.txt > candidates.txt`가 후보다. `LC_ALL=C comm -13 silver_ids.txt db_ids.txt`의 줄 수(DB에만 있는 ID)는 증거에 적는다(확인 조건은 아니다).
     - Silver 쪽이 작게 나와 다른 경우가 있다. 같은 창에 앞 사건으로 이미 격리한 ID가 있거나(본 표에 없음), 계약 위반으로 오류 표에만 있는 ID가 있을 때다. 그때도 만들지 않는다(안전한 쪽). 사건 기록에 그 까닭(격리 표·오류 표에 있는 그 창 ID 수)을 적고 Planner에게 올린다.
3. 증거를 모은다: 후보마다 Bronze 위치와 op(위치마다 — 재발행 확인의 기준), DB에 없음(조회한 ID·건수만 — 행 값은 싣지 않음), 창, 앞조건 ①②의 값. Bronze 위치·op:
   ```sql
   SELECT lower(variant_get(v, '$.after.<ID 열>', 'string')) AS id, topic, partition, offset, variant_get(v, '$.op', 'string') AS op, ingested_at
   FROM (SELECT *, try_parse_json(cast(value_binary AS string)) AS v FROM delta.`<Bronze 표>`)
   WHERE lower(variant_get(v, '$.after.<ID 열>', 'string')) IN (<후보 ID 목록>)
   ORDER BY id, topic, partition, offset
   ```
4. Worker가 목록 파일에 줄을 더하고(`listed_at` = 줄을 쓴 시각) 동결 패치와 점검 문서를 만든다. 리뷰는 둘이다: Integration_Reviewer(목록 형식·Silver 영향), 그 원천 Reviewer(DB에 없음의 근거).
5. 반영한다. 순서를 지킨다.
   1. Silver 예약을 내린다(로컬 `launchctl bootout gui/$(id -u)/local.pipeline.silver-<원천>`, 운영 ScheduledSparkApplication `suspend: true`). 돌고 있는 회차가 끝나기를 기다린다(로컬은 잠금 파일 `lockf`가 풀림).
   2. 마지막 회차가 끝까지 돌았는지 본다 — rc 0 또는 3(로그 끝에 `silver reconcile` 줄). rc 1(배치 실패·멈춤)이었으면 원인을 풀고 **지금 사본(목록을 바꾸기 전)**으로 손 실행해 끝까지 돈다.
   3. Planner가 커밋한다. 로컬은 실행 사본을 올린다(위 "올리기"). 운영은 이미지를 올린다.
   4. 예약을 올린다(아래 LaunchAgent 설치, 운영 `suspend: false`).
6. 다음 회차의 로그 `silver quarantine moved=…`와 대사 `quarantine` 줄을 사건 기록에 남긴다. Gold 다시 집계를 기록한다.

- 5의 2를 건너뛰면 격리 `copies`가 부풀 수 있다. 끝나지 않은 배치(지난 목록으로 돌다 events MERGE 뒤에 죽은 배치)가 새 목록으로 다시 돌면, 그 ID 사본이 이번에는 격리 경로로 간다. 격리 MERGE의 멱등 기록에는 그 배치가 없어서 MERGE가 적용된다. 그런데 시작 옮기기가 옮긴 본 표 행의 `copies`에 그 사본이 이미 들어 있다. 대사가 `copies_mismatch`(rc 3)로 알리고, `copies`를 고치는 관리 Step 전까지 매 회차 rc 3이다. 도는 회차와는 경합하지 않는다(옮기기는 잡 안, 스트림 전에 일어난다).
- **줄 빼기는 늘 아래 되돌리기와 함께다. 줄만 빼는 일은 없다.** 줄만 빼면 다음 사본이 본 표에 새 행으로 들어가 겹침 `mismatch`가 되고, 줄을 다시 넣으면 두 표에 다른 행이 생겨 시작 옮기기가 멈춘다.

### 잘못 옮긴 행 되돌리기(관리 Step — Planner 요청 뒤에만)

순서를 지킨다. 줄 빼기는 커밋과 사본·이미지 올리기가 있어야 반영되므로, 그 사이 매시 회차가 돌면 안 된다.

1. Silver 예약을 내린다(로컬 `launchctl bootout gui/$(id -u)/local.pipeline.silver-<원천>`, 운영 ScheduledSparkApplication `suspend: true`). 돌고 있는 회차가 끝나기를 기다린다(로컬은 잠금 파일 `lockf`가 풀림). 마지막 회차가 끝까지 돌았는지 본다(위 "목록에 줄 더하기" 5의 2와 같다 — rc 1이었으면 지금 사본으로 끝까지 돈다). 끝나지 않은 배치가 줄을 뺀 목록으로 다시 돌면, 그 ID 사본이 본 표 쪽으로 간다. 그런데 events MERGE가 이미 그 배치를 적었으면 건너뛰어 사본 하나가 빠진다(대사 `mismatch`).
2. 그 줄을 뺀 목록을 리뷰·커밋하고 실행 사본(운영은 이미지)을 올린다.
3. 아래 SQL 둘을 이 순서로 실행한다(Spark 세션 — 잡과 같은 Delta·S3 설정, 세션 시간대 UTC). `<본 표>` = `<뿌리>/silver/<원천>/events/g<세대>`, `<격리 표>` = `<뿌리>/silver/<원천>/quarantine/g<세대>`, `<ID 목록>` = `'id1', 'id2'` 꼴.
   - 첫째 — 본 표에 넣기. 격리 표 행의 Silver 열 그대로, `silver_written_at`만 되돌린 시각이다(Gold가 늦은 행과 같은 규칙으로 그 구간을 다시 집계한다).
     ```sql
     MERGE INTO delta.`<본 표>` t
     USING (SELECT event_id, domain, event_type, schema_version, occurred_at, produced_at, actor_user_uuid, actor_subject_id, actor_person_id,
                   session_uuid, aggregate_id, payload, envelope, source_meta, src_topic, src_partition, src_offset, src_lsn, src_txid,
                   first_op, copies, ingested_at, current_timestamp() AS silver_written_at, silver_release, event_date, content_sha256
            FROM delta.`<격리 표>` WHERE event_id IN (<ID 목록>)) s
     ON t.event_date = s.event_date AND t.event_id = s.event_id
     WHEN NOT MATCHED THEN INSERT *
     ```
   - 둘째 — 격리 표에서 지우기. 본 표에 들어간 ID만 지운다.
     ```sql
     MERGE INTO delta.`<격리 표>` t
     USING (SELECT DISTINCT event_id FROM delta.`<본 표>` WHERE event_id IN (<ID 목록>)) s
     ON t.event_id = s.event_id
     WHEN MATCHED THEN DELETE
     ```
   - 확인: ``SELECT count(DISTINCT event_id) FROM delta.`<본 표>` WHERE event_id IN (<ID 목록>)`` = ID 수, 격리 표에서 같은 질의 = 0.
4. Silver를 손으로 한 번 돌려(위 "손 실행") 대사 `match`를 본다. 로그는 목록에 줄이 남았으면 `silver quarantine moved=0 …`이고, 목록이 비었으면 이 줄 없이 `silver start … quarantine_lines=0`이다(빈 목록은 시작 옮기기가 아무것도 읽지 않는다).
5. 예약을 올린다(아래 LaunchAgent 설치, 운영 `suspend: false`).

- 순서를 어기면:
  - SQL 먼저 하면 그 사이 회차의 시작 옮기기가 다시 옮겨 되돌리기가 조용히 취소된다(`quarantined_at`도 바뀜).
  - 줄 빼기 먼저 하면 그 사이 온 사본(되돌리기를 부른 재발행)이 본 표에 새 행으로 들어간다. 이어지는 첫째 SQL은 건너뛰고 둘째가 원래 행과 `copies`를 없앤다.
- 사건 기록에 ID·이유를 남긴다.
- SQL 두 문장은 시험(`pipeline/tests/silver/spark_quarantine.py`)이 이 문서에서 글자 그대로 꺼내 돈다. Silver 열이 늘면 시험이 먼저 깨진다.

## 세대 포인터(SL-7)

광고·메타버스 Silver·Gold는 원천별로 따로 간다(합친 보기 없음, 로직만 공통). 원천별 Gold는 그 원천 Silver 본 표를 `pipeline.silver.pointers.read_events(spark, <뿌리>, <원천>)`로만 읽는다. 경로를 직접 만들지 않는다(설계 SV6).

- 포인터 표 `<뿌리>/silver/<원천>/pointer/`: 원천마다 한 행 Delta 표. 원천 사이 공용 표는 없다 — 한 원천의 바꾸기·확인은 그 원천 폴더 안만 읽고 그 원천 포인터 표에만 쓴다.
  - 열: `domain`·`generation`(지금 세대)·`set_at`·`set_by`·`reason`과 근거 `reconcile_version`·`silver_version`·`silver_release`·`checkpoint_id`·`bronze_table`·`bronze_version`(확인에 쓴 대사 요약의 값).
  - 바꿀 때마다 한 행을 덮어쓰는 커밋 하나다. 커밋 `userMetadata` = `domain`·`from_generation`·`to_generation`·`set_by`·`reason`·`bronze_changed`. **표 이력이 바꾼 기록이다**(`DESCRIBE HISTORY`, 그때 행은 `versionAsOf`). 행을 지우지 않는다.
  - 포인터 행의 판들은 바꾼 순간의 근거일 뿐이다. 읽기는 그때 포인터 표 판과 본 표 판을 다시 정한다.
  - 표가 한 행이 아니거나, 그 행 원천이 다르거나, 열 모양이 다르면(손으로 고친 표) 읽기·바꾸기가 거부한다. 되살리기(관리 Step): `` DESCRIBE HISTORY delta.`<포인터 표>` ``로 마지막 좋은 판을 찾아 ``RESTORE TABLE delta.`<포인터 표>` TO VERSION AS OF <판>``(시험이 같은 방법으로 확인한다). 되살릴 수 있는 판은 30일 안의 판이다(표 속성 — 아래 "정리" 절).
- 읽기 `read_events`: 그 원천 포인터 표 판과 포인터가 가리키는 세대 본 표 판을 먼저 정해 그 판으로 읽는다. 본 표 열(이름·순서·형)이 지금 코드의 Silver 열과 같아야 한다(다르면 거부 — 옛 열 모양 세대). 본 표만 읽는다(격리·오류 표는 넣지 않음). 반환에 원천·세대·경로·판(`View`)이 함께 오고, Gold는 그것을 실행 기록에 남긴다.
- 명령(잡과 같은 `SILVER_LAKE_ROOT`, 뿌리가 `s3a://`면 S3 넷 — Silver 잡 문단과 같은 이름):
  ```bash
  spark-submit-silver.sh pipeline/silver/pointers.py show --domain <원천>
  spark-submit-silver.sh pipeline/silver/pointers.py set --domain <원천> --generation <N> --by <이름> --reason <이유·사건·Step ID> [--bronze-changed <이유>]
  ```
  - `show`는 포인터 행과 마지막 커밋 10개를 찍는다. 쓰지 않는다.
  - rc: 0 바꿈·보임, 2 인자·설정 오류(Spark 전), 1 그 밖 — 거부는 stderr `silver pointer refused` 한 줄에 사유를 모두 적는다.
  - 로그 줄: `silver pointer domain=… from=… to=… reconcile_version=… silver_version=… bronze_version=… bronze_changed=yes|no by=…`.
- `set`이 쓰기 전에 보는 것(하나라도 어긋나면 쓰지 않는다):
  - 그 세대 본 표가 있고 열이 지금 코드의 Silver 열과 같다.
  - 그 세대 대사 결과 표의 마지막 커밋 요약에 필요한 키가 있고, `status`가 `match`다. 이 코드 전에 돈 대사의 요약에는 `bronze_table`이 없다 — 이 코드로 한 회차 돈 뒤 다시 한다.
  - 요약의 `silver_version`이 지금 본 표 판과 같다. 대사 뒤 본 표에 쓰기가 있었으면(다음 회차가 도는 중 등) 거부한다. 다음 회차가 끝난 뒤 다시 한다.
  - 따라잡음: 지금 포인터가 있으면, 그 세대의 마지막 대사와 같은 Bronze 표일 때 대상 세대 마지막 대사의 `bronze_version`이 뒤지지 않아야 한다. **뒤지면 우회로도 지나가지 않는다**(사용자 결정 2026-11-09 "기다렸다 전환"). 대상 세대 예약을 올려 rc 0 회차가 끝난 뒤 다시 한다.
    - `bronze_table`은 끝 `/`를 떼고 견준다. 그 밖은 글자 그대로다 — 세대마다 `SILVER_BRONZE_TABLE`을 같은 글자로 둔다.
    - 견줄 수 없으면(두 대사의 Bronze 표가 다름, 지금 세대 요약에 `bronze_table`이 없음, 지금 세대 대사 결과 표가 없음) 거부한다. Bronze 경로가 바뀐 세대 전환(MVR-3a처럼)이면 `--bronze-changed <이유>`로 지난다. 지금 세대 요약이 이 코드 전 것이라 키가 없으면 이유 "키 없음"으로 지난다. 이유는 커밋 `userMetadata`에 남는다.
    - `--bronze-changed`는 견줄 수 없을 때만 쓴다. 처음 쓰는 포인터(따라잡음을 보지 않음)나 견줄 수 있는 경우에 주면 거부한다.
    - 이 확인은 "지금 포인터보다 뒤지지 않음"까지다. 지금 세대 자체가 낡았는지(예약이 멈춤)는 예약·감시가 본다.
- 예약 시각(따라잡음 확인 때문): `set`은 대상 세대 마지막 대사의 Bronze 판이 지금 세대 마지막 대사보다 판 1만 뒤져도 거부한다. 그 판은 회차 스트림이 시작할 때의 Bronze 최신 판이라, 운영처럼 Bronze가 1분마다 커밋되면 같은 시각에 예약한 두 세대 가운데 나중에 시작한 쪽의 판이 1~2 크다. 대상 세대가 늘 먼저 시작하면 "다음 회차 뒤 다시"가 끝나지 않는다. 그래서 대상 세대는 지금 세대보다 **뒤 시각**에 돌리고, 대상 세대 회차가 끝난 뒤·지금 세대 다음 회차 전에 `set`을 한다.
- 세대 바꾸기(SV6, 원천마다):
  1. 새 세대 `g<N+1>`을 지금 세대보다 뒤 시각 예약으로 돌린다(예: 지금 세대 `10 * * * *`이면 새 세대 `25 * * * *`. 새 checkpoint, Bronze 처음부터). 옛 세대 예약은 그대로 둔다.
  2. 새 세대 회차가 rc 0(대사 `match`)이 될 때까지 기다린다.
  3. 새 세대 회차가 끝난 뒤, 지금 세대 다음 회차 전에 `set --domain <원천> --generation <N+1>`. 거부하면 다음 회차의 같은 창에서 다시 한다.
  4. 그 원천 Gold가 새 세대를 읽는지 본다(`View`의 세대). 세대가 바뀌면 그 원천 Gold는 전부 다시 집계한다.
  5. 옛 세대 예약을 내린다. 옛 세대 표는 되돌리기를 위해 남긴다(지우는 것은 관리 Step — 아래 "되돌리기" 절). 바꾼 뒤 한동안 옛 세대 예약을 그대로 두면, 되돌릴 때 따라잡을 양이 작다(선택).
- 되돌리기(옛 세대로) — 급할 때도 이 순서다(사용자 결정 2026-11-09 "기다렸다 전환"):
  1. 그 원천 Gold 예약을 먼저 멈춘다.
  2. 옛 세대 예약을 지금 세대보다 뒤 시각으로 올려(또는 지금 세대 회차가 끝난 뒤 손으로 한 번 돌려) 한 회차 따라잡게 한다.
  3. 그 회차 대사가 `match`인지 본다(rc 0).
  4. 그 회차가 끝난 뒤, 지금 세대 다음 회차 전에 `set --domain <원천> --generation <N>`으로 포인터를 넘긴다.
  5. Gold 예약을 다시 올린다.
- 공통 열(Silver 열)이 바뀌는 세대 전환: 열 확인은 지금 코드의 열과 견주므로, 옛 열 모양 세대는 새 코드로 읽을 수도 되돌릴 수도 없다. 공통 코드라 두 원천이 같은 코드 판을 쓴다.
  1. 새 열 코드는 먼저 새 세대 Silver 잡에만 둔다(운영은 새 이미지 + 새 예약, 로컬은 별도 실행 사본).
  2. 옛 세대 Silver·Gold·포인터 도구는 옛 코드로 돈다.
  3. 두 원천의 새 세대가 모두 `match`가 된 뒤, Gold·포인터 도구를 새 코드로 바꾸며 원천마다 포인터를 옮긴다. 되돌리기도 코드와 함께 한다.
  - 로컬 실행 사본이 한 자리뿐인 것(위 "실행 전용 코드 사본")은 그때 정할 일이다.
  - 어댑터 규칙만 바뀐 세대(열 같음)는 원천마다 위 "세대 바꾸기" 그대로다.
- 로컬 RustFS에는 포인터를 쓰지 않는다(SL-7 D2). 운영 첫 포인터는 2차 배포에서, 원천마다 그 원천 첫 회차가 rc 0(대사 `match`)이 된 뒤 쓴다. 그 원천 Gold 예약은 그 원천 첫 포인터 뒤에 올린다(포인터 표가 없으면 `read_events`가 거부한다).

## LaunchAgent(설치는 사용자 승인 뒤)

견본 `pipeline/silver/local.pipeline.silver-metaverse.plist`의 자리 표시 셋을 바꿔 둔다:
- `__REPO__`: 코드 사본 경로
- `__COMMIT__`: 사본이 돌 커밋
- `__HOME__`: 홈 폴더

```bash
sed -e "s#__REPO__#$HOME/pipeline-run/silver/mlo_01_p4_team2#g" -e "s#__COMMIT__#<커밋 40자>#g" -e "s#__HOME__#$HOME#g" \
  "$HOME/pipeline-run/silver/mlo_01_p4_team2/pipeline/silver/local.pipeline.silver-metaverse.plist" > ~/Library/LaunchAgents/local.pipeline.silver-metaverse.plist
plutil -lint ~/Library/LaunchAgents/local.pipeline.silver-metaverse.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.pipeline.silver-metaverse.plist
```

- 상태: `launchctl print gui/$(id -u)/local.pipeline.silver-metaverse`
- 로그: `~/Library/Logs/pipeline-silver-metaverse.log`
- 제거: `launchctl bootout gui/$(id -u)/local.pipeline.silver-metaverse` 뒤 `~/Library/LaunchAgents/local.pipeline.silver-metaverse.plist`를 지운다.

## 멈췄을 때

- 로그의 `silver stop` 줄과 오류 표의 진단 행(`acknowledged` = false)을 본다. 이 행은 멈춤 기록 appId `stops`로 쓰인다. 멈춤 코드 `foreign_source`·`not_insert`는 행 하나의 문제가 아니라 커넥터·원천 설정이 깨졌다는 뜻이다.
- **멈춘 뒤 checkpoint를 지우지 않는다.**
  - 지우면 다음 실행이 시작 검사 ②로 멈춘다.
  - 새 checkpoint로 같은 표에 돌면 지난 배치를 다시 넣어 `copies`가 두 배가 된다.
- 넘어가야 하는 행이면, 사람이 확인한 Bronze 위치를 넘김 목록 `pipeline/silver/acknowledged/<원천>.json`에 줄로 더한다. 줄을 더할 때마다 리뷰를 받는다.
  - 그다음 커밋하고 사본을 올린 뒤 **같은 명령으로 다시 돈다.**
  - 같은 checkpoint에서 멈춘 배치를 다시 돌고, 목록에 오른 행은 오류 표에 `acknowledged`로 남는다.
- 원인이 설정이면 고치기 전에 계획 담당(Planner)에게 알린다.

## 되돌리기(Planner 요청 뒤에만)

세대 하나의 다섯 경로를 **함께** 지운다. 앞 셋 가운데 하나만 지우면 다음 실행이 시작 검사 ②·④로 멈춘다(의도한 동작이다). 대사 결과 표는 시작 검사에 들지 않는다(남아도 다음 대사가 덮어쓴다). 격리 표는 할 일이 있을 때만 쓰여 시작 검사가 잡지 못한다 — 격리 표만 지우면 다음 대사가 그 ID의 Bronze 행을 `mismatch`로 알린다.

- `s3a://ads-lake/silver/<원천>/events/g<세대>/`
- `s3a://ads-lake/silver/<원천>/errors/g<세대>/`
- `s3a://ads-lake/checkpoints/silver/<원천>/events/g<세대>/`
- `s3a://ads-lake/silver/<원천>/reconcile/g<세대>/`
- `s3a://ads-lake/silver/<원천>/quarantine/g<세대>/`

방법과 그 뒤:
- 지우기 전에 LaunchAgent를 내린다.
- 지우는 것은 Hadoop `FileSystem.delete(경로, recursive=True)`로 한다(Spark 세션, 키는 파일에서). 지운 뒤 목록으로 객체 0을 본다.
- 로컬 잠금 파일 `~/Library/Caches/silver-pipeline/silver-<원천>-g<세대>.lock`도 지운다.
- 다시 만들면 같은 세대가 처음부터 돈다(시작 검사 ① — 세 표·checkpoint가 모두 없음). 목록 ID 사본은 배치에서 바로 격리 표로 간다.
- 지우는 세대를 그 원천 포인터가 가리키면 먼저 포인터를 다른 세대로 옮긴다("세대 포인터" 절). 그러지 않으면 `read_events`가 거부한다(그 원천 Gold가 멈춘다). 포인터 표 `silver/<원천>/pointer/`는 세대 경로가 아니라 지우지 않는다.

## 정리(옛 판 파일 — DEP3-VAC)

계획 `temp/pipeline/45_vacuum-plan.md` r2. 잡: `pipeline/vacuum/vacuum_job.py --domain <원천>`.

- **행은 지우지 않는다.** 표가 쓰지 않는 옛 판 데이터 파일만 30일 뒤 지운다(14 Q4). Bronze·checkpoint는 대상이 아니다.
- **30일은 표 속성이다**: `delta.deletedFileRetentionDuration = interval 30 days`.
  - Silver 잡·포인터 도구·Gold 잡의 세션(`silver_job.session`)이 새로 만드는 표에 붙인다. 이미 있는 표에는 붙지 않는다.
  - Delta 4.3.1은 `VACUUM … RETAIN n HOURS`(n이 0이 아님)를 무시하고 이 속성을 쓴다.
  - 같은 속성이 옛 판 읽기(`versionAsOf`, 포인터 `RESTORE`)의 한도다. 최신 판을 빼고, 커밋이 30일보다 오래된 판은 읽을 수 없다.
  - 로그 보존(`delta.logRetentionDuration`)은 기본 30일 그대로다. 둘이 같아야 한다 — 로그 보존을 더 짧게 하면 Delta가 VACUUM을 오류로 멈춘다.
- **하지 않는 것**:
  - 손으로 `VACUUM`을 돌리거나 RETAIN을 주지 않는다.
  - `spark.databricks.delta.retentionDurationCheck.enabled`를 끄지 않는다.
  - 표 속성을 바꾸지 않는다. SparkApplication에 `spark.databricks.delta.properties.defaults.deletedFileRetentionDuration`을 다른 값으로 두지 않는다.
- **대상**(원천 하나 — 다른 원천의 폴더는 열지 않는다):
  - Silver 세대마다 `events`·`errors`·`reconcile`·`quarantine`. 세대는 폴더 `g<N>`을 나열해 찾는다.
  - 포인터.
  - Gold 표 폴더 전부의 판 `v<N>`(표 목록에서 뺀 표 폴더도).
  - 정정 기록.
  - 경로는 Silver·Gold 규칙 함수로 만든다. 규칙 밖 이름의 폴더는 `vacuum skip … reason=unknown_name` 줄로만 남기고 건드리지 않는다.
- **로그와 종료 코드**:
  - 줄: `vacuum start`, 표마다 `vacuum table path=… files=<지운 수> seconds=…` · `vacuum refused` · `vacuum skip … reason=no_table` · `vacuum error`, 끝에 `vacuum end … deleted_files=… refused=… errors=…`.
  - 지운 수 = 바로 앞 `DRY RUN`의 후보 수다.
  - rc 0 = 모두 치웠거나 표가 없음. rc 1 = 거부·오류가 있음(나머지 표는 끝까지 치운다). rc 2 = 설정(`VACUUM_LAKE_ROOT`, s3a면 S3 넷).
- **`vacuum refused … retention=…`**: 표 속성이 30일이 아니다. 이 코드 전에 만든 로컬·lab 표다. 지우지 않는다.
  - **운영에는 그런 표가 없다**(광고 운영 Silver·Gold 표는 이 코드가 든 이미지로 처음 만든다).
  - 운영에서 나오면 Planner에게 알린다. 손으로 속성을 붙이지 않는다 — 표에 커밋을 쓰는 일이라 돌고 있는 잡과 겹치면 안 된다.
- **표에 커밋을 쓰지 않는다**(`spark.databricks.delta.vacuum.logging.enabled=false` — 코드가 정한다). 그래서 판·txn·이력·대사 요약·Gold 회차 기록이 그대로다.
  - Delta는 `<표>/_delta_log/_last_vacuum_info` 하나를 덮어쓰려 한다. 커밋이 아니고, LITE 모드만 읽는다.
  - 쓰지 못해도 VACUUM은 계속된다. Delta 4.3.1 `LastVacuumInfo.persistLastVacuumInfo`가 치명적이지 않은 실패를 잡아 사용 기록(`delta.lastVacuumInfo.write.failure`)만 남긴다.
  - 운영 정리 키에는 쓰기 권한이 없다. **그래서 그 쓰기 실패 기록(드라이버 로그·S3 접근 거부 기록)이 매 회차 나와도 정상이다.**
- **돌고 있는 Silver·Gold와 겹쳐도 된다.** 지우는 파일은 지금 판이 쓰지 않고, 지운 표시가 30일보다 오래됐고, 수정 시각도 30일보다 오래된 것뿐이다. 진행 중인 쓰기의 새 파일과 30일 안 판의 파일은 남는다. 예약 시각은 자원 때문에 다른 잡과 겹치지 않게 둔다.
- **운영(k8s)**:
  - ScheduledSparkApplication `45 3 * * *`(UTC, 12:45 KST), `concurrencyPolicy: Forbid`, restartPolicy OnFailure 3회. 인자 `--domain ads`.
  - 설정: `VACUUM_LAKE_ROOT`(Silver·Gold와 같은 뿌리)와 S3 넷. 정리 전용 키 — `silver/<원천>/`·`gold/<원천>/` 나열·읽기·지우기, 쓰기 없음.
  - 이미지에 `pipeline/vacuum/`이 들어 있어야 한다.
  - 배포 뒤 30일 안에 켠다(그 전에는 지울 것이 없다). 첫 회차에서 표마다 `vacuum table` 줄과 `vacuum end … refused=0 errors=0`을 본다.
  - 로컬 정기 실행은 두지 않는다.

## 운영(k8s)에서

- 같은 잡 코드를 이미지에 넣고 같은 환경변수 이름을 쓴다.
  - `SILVER_*` 열(대사·격리 표는 새 설정이 없다 — 경로는 `SILVER_LAKE_ROOT`에서 만든다. Silver 앱이 `silver/<원천>/reconcile/`·`silver/<원천>/quarantine/`에도 쓴다). `SILVER_QUARANTINE`은 이미지 안 목록 파일 경로다(SL-9 — 2차 배포).
  - S3 넷: 키는 Secret을 파일로 붙이고 `S3_ACCESS_KEY_FILE`·`S3_SECRET_KEY_FILE`에 그 경로를 둔다.
- 예약은 `schedule: "10 * * * *"`(UTC), `concurrencyPolicy: Forbid`, `restartPolicy` OnFailure 3회다. 잠금 래퍼·caffeinate는 쓰지 않는다.
- 포인터 바꾸기(SL-7)는 `pointers.py set`을 한 번 도는 SparkApplication이다(잡과 같은 이미지·`SILVER_LAKE_ROOT`·S3 Secret, 재시도 없음 — 거부는 rc 1). 새 세대는 ScheduledSparkApplication을 하나 더 두고, 포인터를 바꾼 뒤 옛 것을 `suspend: true`로 둔다. `silver/<원천>/pointer/`는 `silver/` 접두사 안이라 새 권한이 없다.
- ConfigMap 값은 YAML `|-` 또는 일반 문자열로 둔다. `|`처럼 끝 줄바꿈이 붙으면 잡이 시작 전에 거부한다(설정 검사는 앞뒤 공백을 지우지 않는다 — 이름이 조용히 바뀌지 않게).
- SparkApplication에 `spark.databricks.delta.properties.defaults.setTransactionRetentionDuration`을 두지 않는다.
  - 그 속성이 붙은 Silver·오류·격리 표는 시작 검사가 멈춘다.
  - 멱등 기록이 보관 기한으로 지워지면, 다시 돈 배치가 `copies`를 두 배로 만든다.
- 읽기 상한은 원천별 값이다.
  - 메타버스: `SILVER_MAX_FILES_PER_TRIGGER=1000`, `SILVER_MAX_BYTES_PER_TRIGGER=512m`
  - 광고: SL-8 기록의 제안 `SILVER_MAX_FILES_PER_TRIGGER=1000`, `SILVER_MAX_BYTES_PER_TRIGGER=96m`(Bronze parquet 행당 약 365 B — 메타버스의 약 0.38배라 `512m`이면 배치가 너무 크다). 값은 2차 배포 인계에서 정한다. 로컬 래퍼 `run-silver.sh`에는 광고 줄이 없다.
  - 운영 장비에서 다시 잰다.
