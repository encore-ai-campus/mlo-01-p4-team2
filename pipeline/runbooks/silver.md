# Silver 잡 운영

Silver 잡(`pipeline/silver/silver_job.py`)은 원천 하나·세대 하나의 Bronze를 한 번 읽고(availableNow) 끝난다. 로컬은 래퍼 `pipeline/silver/run-silver.sh`와 LaunchAgent(매시 :10)로, 운영은 ScheduledSparkApplication으로 돈다. 설정 이름은 `pipeline/README.md`의 Silver 잡 문단에 있다.

- 비밀값(RustFS 키)은 파일에서만 읽는다. 기록에 옮겨 적지 않는다.
- Silver 표·checkpoint 경로: `<뿌리>/silver/<원천>/{events,errors}/g<세대>/`, `<뿌리>/checkpoints/silver/<원천>/events/g<세대>/`, 대사 결과 `<뿌리>/silver/<원천>/reconcile/g<세대>/`. 로컬 뿌리는 `s3a://ads-lake`.

## 실행 전용 코드 사본

래퍼는 작업 저장소가 아니라 **리뷰된 커밋에 맞춘 사본**에서 돈다. 제출 스크립트가 실행하는 저장소의 `pipeline/silver`·`pipeline/common`을 그대로 보내므로, 작업 저장소에서 돌면 리뷰 전 코드가 Silver 표에 쓴다. 래퍼는 사본 HEAD가 `SILVER_CODE_COMMIT`과 다르거나 사본에 미커밋·추적 안 된 파일이 있으면 돌지 않는다(rc 2). git 확인이 실패해도 돌지 않는다. 넘김 목록과 코드 뿌리는 늘 사본의 것이고 환경변수로 덮을 수 없다.

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
| 그 밖 | 설정 오류(Spark 전) 또는 배치 실패·멈춤 | 로그를 본다. 멈춤이면 아래 "멈췄을 때" |

- 로그 줄: `silver start`(release·넘김 줄 수), 배치마다 `silver udf`·`silver check`·`silver batch`, 이중 적재를 줄였으면 `silver alert bronze_duplicates`, 멈추면 `silver stop`.
  스트림 뒤 대사: `silver reconcile`(회차 요약 — 상태·창 수·불일치 수·중복·판·시간), 불일치 줄마다 `silver alert reconcile_mismatch`·`silver alert reconcile_copies`(10줄까지, 넘으면 `silver alert reconcile_more`), Silver 안 중복이면 `silver alert silver_duplicates`.

## 대사(SL-6 — Bronze ↔ Silver)

스트림이 성공하면 같은 앱이 대사를 돈다(계획 temp/pipeline/35_silver-reconcile-sl6.md, 계획 15 6판 r9 SL-6 줄).

- 범위: Silver checkpoint의 마지막 끝난 배치가 가리키는 Bronze 판 V(`versionAsOf V` = Silver가 처리한 행). 늦게 온 Bronze 행 때문에 거짓 불일치가 나지 않는다.
- 맞추는 법:
  - 오류 표(진단 줄 = 멈춤 코드 + `acknowledged=false`는 뺌)는 Bronze 위치로 맞춘다. Bronze에 없는 위치면 `mismatch`, 같은 위치가 두 번이면 `copies_mismatch`.
  - 오류 표에 위치가 없는 Bronze 행은 UTC 시 창마다 Silver와 맞춘다. 고유 ID 집합(건수·`ids_sha256`)이 같고 고유 위치 수 = `copies` 합이어야 한다. 위치가 더 많으면 `mismatch`(Bronze 행이 어디에도 없음), 적으면 `copies_mismatch`(`copies`가 부풂).
  - 오류 표에 없는데 창·ID를 꺼낼 수 없는 Bronze 행은 `mismatch`(놓을 수 없음 줄).
  - Silver 안 같은 `event_id`가 둘 이상이면 그 행들의 창이 `mismatch`(두 번째 그물 — 매 회차).
- 결과 표 `<뿌리>/silver/<원천>/reconcile/g<세대>/`: 회차마다 덮어쓴다(창마다 한 행 + `errors` 줄 + `unplaced` 줄). 지난 회차는 `DESCRIBE HISTORY`·`versionAsOf`로 본다. 커밋 `userMetadata`에 회차 요약이 있다. 요약 `status`(로그 줄 `silver reconcile status=`와 같음)는 줄 가운데 가장 무거운 것이다: `mismatch` > `copies_mismatch` > `match`.
- `mismatch` 창은 Gold에서 미확정이다. `copies_mismatch`는 경보만 내고 Gold 확정은 막지 않는다(사건 수는 ID로 센다).

rc 3이 나면:
- 결과 표에서 `status`가 `match`가 아닌 줄과 예시(`missing_examples` = Bronze에만 있는 ID 또는 위치, `extra_examples` = Silver·오류 표에만 있는 ID 또는 위치)를 본다.
- Silver 표·오류 표·checkpoint를 지우거나 고쳐 다시 돌리지 않는다. Planner에게 알린다. 처리는 관리 Step이다.
- 관리 Step이 Silver 행을 오류 표로 옮길 때는 그 행 사본들의 Bronze 위치마다 Bronze 행을 오류 표에 한 줄씩 쓴다. Silver 행에는 첫 사본 위치만 있어서, 그러지 않으면 대사가 `mismatch`로 알린다.
- **rc 3은 고칠 때까지 매 회차 난다.** 운영은 `OnFailure` 재시도까지 매시 네 번 실패한다. 재시도는 해가 없다(새 Bronze가 없으면 배치 없이 대사만 다시).
- 그 사이 진짜 멈춤(rc 1)은 앱 상태로 구별되지 않는다. 로그 줄로 구별한다: `silver stop`이나 예외가 있으면 멈춤, `silver alert reconcile_*`·`silver alert silver_duplicates`만 있으면 대사 불일치다.
- 운영 메타버스 확인용 계정(outbox 12행)도 다른 행과 똑같이 대사한다. 분석에서 빼는 일은 Gold(G3·G11)다.

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

세대 하나의 네 경로를 **함께** 지운다. 앞 셋 가운데 하나만 지우면 다음 실행이 시작 검사 ②·④로 멈춘다(의도한 동작이다). 대사 결과 표는 시작 검사에 들지 않는다(남아도 다음 대사가 덮어쓴다).

- `s3a://ads-lake/silver/<원천>/events/g<세대>/`
- `s3a://ads-lake/silver/<원천>/errors/g<세대>/`
- `s3a://ads-lake/checkpoints/silver/<원천>/events/g<세대>/`
- `s3a://ads-lake/silver/<원천>/reconcile/g<세대>/`

방법과 그 뒤:
- 지우기 전에 LaunchAgent를 내린다.
- 지우는 것은 Hadoop `FileSystem.delete(경로, recursive=True)`로 한다(Spark 세션, 키는 파일에서). 지운 뒤 목록으로 객체 0을 본다.
- 로컬 잠금 파일 `~/Library/Caches/silver-pipeline/silver-<원천>-g<세대>.lock`도 지운다.
- 다시 만들면 같은 세대가 처음부터 돈다(시작 검사 ① — 세 경로가 모두 없음).

## 운영(k8s)에서

- 같은 잡 코드를 이미지에 넣고 같은 환경변수 이름을 쓴다.
  - `SILVER_*` 아홉(대사는 새 설정이 없다 — 결과 표 경로는 `SILVER_LAKE_ROOT`에서 만든다. Silver 앱이 `silver/<원천>/reconcile/`에도 쓴다)
  - S3 넷: 키는 Secret을 파일로 붙이고 `S3_ACCESS_KEY_FILE`·`S3_SECRET_KEY_FILE`에 그 경로를 둔다.
- 예약은 `schedule: "10 * * * *"`(UTC), `concurrencyPolicy: Forbid`, `restartPolicy` OnFailure 3회다. 잠금 래퍼·caffeinate는 쓰지 않는다.
- ConfigMap 값은 YAML `|-` 또는 일반 문자열로 둔다. `|`처럼 끝 줄바꿈이 붙으면 잡이 시작 전에 거부한다(설정 검사는 앞뒤 공백을 지우지 않는다 — 이름이 조용히 바뀌지 않게).
- SparkApplication에 `spark.databricks.delta.properties.defaults.setTransactionRetentionDuration`을 두지 않는다.
  - 그 속성이 붙은 Silver·오류 표는 시작 검사가 멈춘다.
  - 멱등 기록이 보관 기한으로 지워지면, 다시 돈 배치가 `copies`를 두 배로 만든다.
- 읽기 상한은 원천별 값이다.
  - 메타버스: `SILVER_MAX_FILES_PER_TRIGGER=1000`, `SILVER_MAX_BYTES_PER_TRIGGER=512m`
  - 광고: SL-8에서 잰다.
  - 운영 장비에서 다시 잰다.
