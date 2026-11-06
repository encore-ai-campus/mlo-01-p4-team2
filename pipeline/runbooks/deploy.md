# 운영 배포·매일 점검(P8-2 초안)

> 초안(2026-11-05). 계획 02 P8-2의 광고 쪽 운영 절차를 모으는 곳이다. 이번 판에는 두 가지만 둔다: 매일 점검, 규칙 판이 바뀌는 배포. 나머지 P8-2 항목은 P8-2 Step에서 더한다(끝 절).

## 매일 점검 — P2-4b 경보를 미룬 동안

- 하루 한 번, 하루 대사(00:50 UTC) 뒤에 한다. 예: 01:00 UTC(10:00 KST).
- ①~④의 값을 날짜와 함께 사건 기록에 한 줄씩 적는다.
- 알림 기준
  - ①·③: **디스크 여유 < 20%**면 Planner·사용자에게 알린다. 크기 기준은 이것 하나다.
  - ②·③의 크기: 추세만 적는다. 첫 주 실측 자료도 된다.
  - ④: 경보(alert·error)가 하나라도 있으면 알린다.
- 비밀값(DB 비밀번호·RustFS 키·웹훅 주소)은 파일로만 넘긴다. 화면·기록에 옮기지 않는다.

### ① DB 노드 디스크(노트북 3대 모두)

WSL 디스크(ext4.vhdx)는 쓰는 만큼 커지고, 안에서 지워도 Windows 쪽 파일이 저절로 줄어든다고 보지 않는다(계획 16 §1.4). 그래서 WSL 안과 Windows C:를 함께 본다.

```bash
df -h /var/lib/postgresql/18/data            # WSL 안
```

```powershell
Get-PSDrive C | Select-Object Used, Free     # Windows. 여유 비율 = Free / (Used + Free)
```

### ② outbox 표 크기(광고 서버에서)

1차 배포 동안 운영 outbox는 늘기만 한다. 중계가 없어 `purge_sent_events`가 지우는 행이 0이고, 창 단위 정리(P1-6)는 운영 뒤다.

- 실측: 행당 약 1.04~1.09 KB(색인 포함)
- 하루 5만 행이면 약 55 MB/일, 90일이면 약 5 GB

```bash
PGPASSFILE=<.pgpass 형식 파일> psql "host=127.0.0.1 port=5000 dbname=ads user=<광고 서버 DB 계정>" -Atc \
  "select pg_total_relation_size('public.events_outbox'), reltuples::bigint from pg_class where oid = 'public.events_outbox'::regclass"
```

- `PGPASSFILE`은 `.pgpass` 형식 파일이어야 한다. 저장소 밖에 두고 권한은 0600이다.
  - 내용은 `127.0.0.1:5000:ads:<계정>:<비밀번호>` 한 줄이다.
  - 비밀번호만 든 파일(`/opt/postgresql/config/postgres-password` 같은 것)은 psql이 읽지 않는다 → "no password supplied".
  - 0600보다 넓은 권한이면 경고를 내고 무시한다.

- 첫 값: 표 바이트(색인·TOAST 포함)
- 둘째 값: 행 수 추정(통계값 — 정확한 수가 아니다)
- 로컬 lab에서 확인(2026-11-05): 62,087,168 B, 추정 54,888행(실제 56,920행), 행당 1,090.8 B

### ③ 대사 작업 폴더(광고 서버) — `fetch_hop_manifest`가 받는 곳

- 폴더: `reconcile_hops --dir`로 준 곳이다. 로컬 래퍼에서는 `HOP_RECONCILE_DIR`이다.
- 모양: 창 크기(`hour`·`day`)마다 폴더 하나와 상태 파일 `reconcile-<창>.json`이 있다.
- 회차마다 아래 세 폴더에 새 파일이 쌓인다(지우지 않는다).
  - `<창>/pipeline/`: 받은 manifest 세대 사본
  - `<창>/outbox/`: 내보낸 outbox·원본 manifest
  - `<창>/report/`: 대조 보고
- 로컬 4시간 실행 뒤 실측(파일 수·바이트 합):

  | 폴더 | 파일 | 바이트 |
  |---|---|---|
  | hour/pipeline | 10 | 53,943 |
  | hour/outbox | 25 | 94,046 |
  | hour/report | 7 | 179,957 |
  | day/pipeline | 4 | 7,895 |
  | day/outbox | 9 | 12,101 |
  | day/report | 3 | 18,445 |

```bash
du -sh "$HOP_RECONCILE_DIR"; df -h "$HOP_RECONCILE_DIR"
```

- `du -sh`는 블록 단위라 바이트 합보다 크게 나온다. 같은 폴더가 hour 416K, day 72K로 나왔다. 추세는 늘 같은 명령으로 적는다.

### ④ 파이프라인 상태 점검(`pipeline/monitor/check.py`를 손으로 한 번)

보는 것: M1 슬롯, M3 Connect task FAILED, M4 Kafka, M5 Bronze 대기(M2·S7도 함께 나온다).

- 어디서: **관리 장치**에서 돌린다. 5432·9092·8083에 닿는 곳이다(계획 16 §2.2).
  - 광고 서버는 9092·8083의 허용 출발지가 아니라 M3~M5가 실패한다.
- `check.py`는 `MONITOR_PG_NODES`의 노드마다 물어 primary를 스스로 찾는다. M1은 primary에서만 된다(대기 서버에서는 `pg_current_wal_lsn()`이 "recovery is in progress"로 실패). 그래서 HAProxy가 없는 관리 장치에서도 장애 전환 뒤 값을 고칠 필요가 없다.
- **관리 장치는 로컬 시험대 Mac이라, `check.py`의 기본값이 모두 lab을 가리킨다.** 그래서 `MONITOR_REQUIRE_ALL=1`을 맨 앞에 둔다. 변수 하나라도 없거나 비면 lab을 보고 ok를 내지 않고 rc 2로 멈춘다.

```bash
env -u MONITOR_WEBHOOK_URL -u MONITOR_WEBHOOK_FILE MONITOR_REQUIRE_ALL=1 \
  MONITOR_STATE="$HOME/.local/state/pipeline_monitor/manual.json" \
  MONITOR_SLOTS=ads_outbox_slot,metaverse_outbox_slot MONITOR_S7_POWER=0 \
  MONITOR_PG_NODES=<LAP1_IP>:5432,<LAP2_IP>:5432,<LAP3_IP>:5432 MONITOR_PG_PASSWORD_FILE=… \
  MONITOR_CONNECT_REST=… MONITOR_KAFKA=… MONITOR_KAFKA_BIN=… MONITOR_JAVA_HOME=… \
  MONITOR_BRONZE=… S3_ENDPOINT=… S3_ACCESS_KEY_FILE=… S3_SECRET_KEY_FILE=… \
  <python> pipeline/monitor/check.py; echo "rc=$?"
```

- `…`·`<LAPn_IP>`는 운영 값이다(P8-2에서 정한다). 변수 뜻은 `monitor/README.md` 「설정」 절에 있다.
- `MONITOR_KAFKA_BIN`·`MONITOR_JAVA_HOME`은 꼭 준다.
  - 기본값이 이 Mac의 경로(`/opt/kafka/current/bin`, Homebrew openjdk)다.
  - 빠지면 다른 장치에서 M4·M5가 매일 error가 된다.
- `<python>`은 `pipeline/requirements.txt`(psycopg, boto3)를 설치한 환경이다.
  - 표준 출력에 JSON 한 줄이 없으면(트레이스백만 나옴 — 모듈 없음 등) 점검 자체가 실패한 것이다.
  - 이때도 rc는 1이다. 경보의 rc 1과는 JSON 줄이 있는지로 가른다.

웹훅 없이 돌린 결과(로컬 시험대에서 확인 — 2026-11-05, rc 2의 설정 경우·primary를 모르는 회차는 2026-11-06):

- 웹훅이 없으면 보내지 않고 화면(표준 출력의 JSON 한 줄)에만 쓴다. 상태 파일은 쓴다.
  - 바뀐 것이 있으면 `notify`가 `"웹훅 없음(화면에만)"`, 없으면 `null`이다.
- rc
  - 0: 경보 없음
  - 1: 경보(alert·error)가 하나 이상
  - 2: 점검하지 않음(웹훅 유무와 관계없다). JSON의 `error`가 까닭이다.
    - `missing_config`: 필수 모드에서 운영 변수가 없거나 빔. 빠진 이름이 `detail`에 있다. 명령을 고쳐 다시 돌린다.
    - `bad_config`: 설정 형식이 틀림(예: `MONITOR_PG_NODES` 빈 값·중복·포트 범위, `MONITOR_S7_POWER`·`MONITOR_REQUIRE_ALL`이 `0`·`1`이 아님).
    - `unsafe_state`: 상태 파일 자리가 안전하지 않음.
- **`alerts`와, `findings` 가운데 `level`이 `alert`·`error`인 줄을 읽는다. `changes`로 판단하지 않는다.**
  - `changes`는 지난 점검 뒤 바뀐 것만 담는다. 그래서 이어지는 경보는 두 번째 점검부터 비어 있다.
  - 확인: 없는 슬롯 이름을 넣어 두 번 돌리면 둘 다 rc 1이고, 둘째의 `changes`는 `[]`이다.
- 상태 파일은 손 점검 전용(`manual.json`)으로 둔다. 나중에 주기 감시(P2-4b)를 켜면 그쪽 상태와 섞이지 않게 하려는 것이다.
- `unimplemented`·`unsupported` 줄은 경보가 아니다.
  - M5 Bronze 앱 FAILED(`bronze_app_failed`)는 `unimplemented`다.
- switchover 직후에는 `M1·M2:run` error("primary 0개 …" 또는 "갈라짐")가 한 번 날 수 있다. 몇 분 뒤 다시 돌려도 이어지면 알린다.
  - 메시지의 "닿지 못함" 목록으로 "primary에 못 닿음"과 "primary 없음"을 가른다.
  - 이 회차는 지난 M1·M2 값을 그대로 둔다(해소로 보지 않는다).
- S7은 **점검을 돌리는 장치 자신**을 본다. 노트북 3대가 아니다.
  - 시계 차이: 그 장치와 찾은 primary의 차이(노드 조회 순간). primary를 모르면 `clock_skew` error다.
  - 전원(배터리·잠듦): `MONITOR_S7_POWER=0`이라 `unsupported`(끔)다. 관리 장치의 전원은 파이프라인과 관계없다.
  - **운영 노트북 3대의 잠자기는 이 점검 밖이다.** Windows·Linux용 S7이 없다(계획 16 §3.3). P8-2 잠자기 금지와 P2-4b에서 따로 다룬다. 2026-11-05 사고(아래 끝 절)가 그 종류다.

#### M3 FAILED가 보이면 — 재시작은 사용자 확인 뒤에만

- Connect는 FAILED task를 스스로 다시 띄우지 않는다. 그동안 슬롯이 WAL을 붙잡는다(설계 01 §4.2 C3).
- 자동 재시작(R7)은 두지 않았다. E-K2에서 FAILED가 0이었다(설계 01 R7 (가), 계획 02 진행 기록 2026-11-04).
- RB 쪽에 이 절차는 없다. 아래 순서로 한다.

1. 원인을 본다: `curl -s "$MONITOR_CONNECT_REST/connectors/<이름>/status"`의 `connector.trace`(커넥터 자체가 FAILED일 때) 또는 `tasks[].trace` 첫 줄을 사건 기록에 적는다.
2. 원인이 설정·상태 문제면 재시작해도 다시 멈춘다. 재시작하지 말고 Planner·사용자에게 알린다.
   - 설정·상태 문제: 인증 실패, publication 없음, 슬롯 무효, R1 불일치.
   - 슬롯 무효는 `README.md` RB-1이다.
3. 그 밖이면 사용자 확인을 받은 뒤 재시작한다.
   - 설계 01 §4.2 C3의 나머지 둘(LSN flush 30초 초과, Kafka 트랜잭션 실패)이 이쪽이다.
   - 2026-11-05의 `Failed to create TypeRegistry`(잠듦 뒤)도 이쪽이고, 재시작으로 회복했다.
   - C3의 "재시도 불가"는 Connect가 스스로 다시 시도하지 않는다는 뜻이다. 재시작해도 안 된다는 뜻이 아니다.
   ```bash
   curl -s -X POST "$MONITOR_CONNECT_REST/connectors/<이름>/restart?includeTasks=true&onlyFailed=true"
   ```
4. ④를 다시 돌려 그 커넥터가 RUNNING인지, 슬롯이 붙잡은 WAL이 줄어드는지 본다.

## 규칙 판이 바뀌는 배포(D6 — MVR-1)

- `rule_sha256`: 공용 요약 규칙 파일(`hop_rules.py`)의 SHA-256이다.
  - 지금 값: `4043ed001c944ed5328cc617b590eb5d937e70933e5fcad53340a03335e369c3`
- 이 값이 바뀌는 커밋은 **manifest 잡과 광고 서버를 함께 배포한다.**
  - manifest 잡: 이미지 안의 `pipeline/common/hop_rules.py`
  - 광고 서버: `ads_server/events/hop_rules.py`
- 판이 다르면 광고 서버는 그 manifest를 대조하지 않는다(`events/hopcheck.py`의 `_pipeline_problems` → `HopInputError`).
  - 그 회차 대사는 rc 2(입력 실패, 단계 `verify`)다. 광고 서버 시험 `case_input_failures_are_told_apart_from_mismatches`가 고정한다.
  - 창은 확정되지 않고, 판이 맞은 다음 회차가 판정한다.
- 배포 시각
  - 시간 잡은 :05, 대사는 :07이다. 하루 잡은 00:35, 대사는 00:50 UTC다.
  - 둘을 한 번에 배포하면서 :05~:07과 00:35~00:50 UTC를 피하면 rc 2 회차가 없다.
  - 따로 배포하면 그 사이 매시 회차가 rc 2다.
  - 배포 뒤 첫 시간 잡이 늦거나 실패하면, :07 회차는 지난 포인터(옛 판)를 받아 rc 2가 한 번 더 난다(늦음 rc 3이 아니다).

## P8-2에서 더할 것(이번 판에는 없음)

- 계획 02 P8-2 목록은 P8-2 Step에서 이 파일에 더한다. 예: 커넥터를 트래픽보다 먼저 켜기(D6), 노트북 잠자기 금지, D23 비밀 파일 권한·REST 방화벽.
- **잠자기 금지의 근거(2026-11-05, 로컬 시험대)**
  - 맥이 잠든 뒤 18:55:26 KST에 Connect task 둘(`ads-outbox-e2e`, `metaverse-outbox-cdc`)이 `Failed to create TypeRegistry`로 FAILED가 됐다. 로그: "Task is being killed and will not recover until manually restarted".
  - 감시가 돌지 않아 손으로 볼 때까지 몰랐고, 그동안 슬롯이 WAL을 붙잡았다.
  - 사용자 승인으로 11:38Z에 REST로 재시작했다.
