# Silver 잡 운영

Silver 잡(`pipeline/silver/silver_job.py`)은 원천 하나·세대 하나의 Bronze를 한 번 읽고(availableNow) 끝난다. 로컬은 래퍼 `pipeline/silver/run-silver.sh`와 LaunchAgent(매시 :10)로, 운영은 ScheduledSparkApplication으로 돈다. 설정 이름은 `pipeline/README.md`의 Silver 잡 문단에 있다.

- 비밀값(RustFS 키)은 파일에서만 읽는다. 기록에 옮겨 적지 않는다.
- Silver 표·checkpoint 경로: `<뿌리>/silver/<원천>/{events,errors}/g<세대>/`, `<뿌리>/checkpoints/silver/<원천>/events/g<세대>/`. 로컬 뿌리는 `s3a://ads-lake`.

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
SILVER_CODE_COMMIT=<커밋 40자> bash "$HOME/pipeline-run/silver/mlo_01_p4_team2/pipeline/silver/run-silver.sh" metaverse 1
```

- Standalone 1코어다. 다른 예약 잡 시각(:05~:10, 메타버스 manifest·대사 :15·:17, 하루 잡 00:35 UTC)을 피한다.
- 종료 코드:

| rc | 뜻 | 할 일 |
|---|---|---|
| 0 | 끝남(새 Bronze가 없으면 배치 없이 끝남) | 없음 |
| 75 | 같은 표의 앞 회차가 돌고 있어 건너뜀(lockf 한 줄) | 없음. 계속 나오면 앞 회차가 오래 걸리는 까닭을 본다 |
| 2 | 래퍼가 거부(코드 사본·인자) | stderr의 `silver refused` 줄대로 사본을 맞춘다 |
| 그 밖 | 설정 오류(Spark 전) 또는 배치 실패·멈춤 | 로그를 본다. 멈춤이면 아래 "멈췄을 때" |

- 로그 줄: `silver start`(release·넘김 줄 수), 배치마다 `silver udf`·`silver check`·`silver batch`, 이중 적재를 줄였으면 `silver alert bronze_duplicates`, 멈추면 `silver stop`.

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

세대 하나의 세 경로를 **함께** 지운다. 하나만 지우면 다음 실행이 시작 검사 ②·④로 멈춘다(의도한 동작이다).

- `s3a://ads-lake/silver/<원천>/events/g<세대>/`
- `s3a://ads-lake/silver/<원천>/errors/g<세대>/`
- `s3a://ads-lake/checkpoints/silver/<원천>/events/g<세대>/`

방법과 그 뒤:
- 지우기 전에 LaunchAgent를 내린다.
- 지우는 것은 Hadoop `FileSystem.delete(경로, recursive=True)`로 한다(Spark 세션, 키는 파일에서). 지운 뒤 목록으로 객체 0을 본다.
- 로컬 잠금 파일 `~/Library/Caches/silver-pipeline/silver-<원천>-g<세대>.lock`도 지운다.
- 다시 만들면 같은 세대가 처음부터 돈다(시작 검사 ① — 세 경로가 모두 없음).

## 운영(k8s)에서

- 같은 잡 코드를 이미지에 넣고 같은 환경변수 이름을 쓴다.
  - `SILVER_*` 아홉
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
