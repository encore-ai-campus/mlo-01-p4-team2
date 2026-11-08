# 운영 배포·매일 점검(P8-2 초안)

> 초안(2026-11-05). 계획 02 P8-2의 광고 쪽 운영 절차를 모으는 곳이다. 이번 판에는 두 가지만 둔다: 매일 점검, 규칙 판이 바뀌는 배포. 나머지 P8-2 항목은 P8-2 Step에서 더한다(끝 절).

## 매일 점검 — 운영에 주기 감시를 적용하기 전

- 하루 한 번, 하루 대사(00:50 UTC) 뒤에 한다. 예: 01:00 UTC(10:00 KST).
- ①~④의 값을 날짜와 함께 사건 기록에 한 줄씩 적는다.
- 알림 기준
  - ①·③: **디스크 여유 < 20%**면 Planner·사용자에게 알린다. 크기 기준은 이것 하나다.
  - ②·③의 크기: 추세만 적는다. 첫 주 실측 자료도 된다.
  - ④: 경보(alert·error)가 하나라도 있으면 알린다.
- 비밀값(DB 비밀번호·RustFS 키·웹훅 주소)은 파일로만 넘긴다. 화면·기록에 옮기지 않는다.
- 주기 감시(P2-4b, `monitor/README.md`)의 D1·D2가 ①~③과 같은 기준(여유 < 20%, outbox 크기)을 경보로 본다. 지금은 로컬 시험대에만 설치돼 있다. 운영에서는 감시 서버에 주기 감시를 적용하고, 디스크가 있는 장치마다 D1을 돌리기 전까지(결정 B — 운영 적용 Step) ①·③을 손으로 그대로 한다. ②의 outbox 크기는 ④가 `MONITOR_OUTBOX`로 함께 본다(D2).

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

- 어디서: **관리 장치**에서 돌린다. 노트북 셋의 PostgreSQL·Kafka·Connect REST에 닿는 곳이다(계획 16 §2.2).
  - 운영 노트북은 NAT라 LAN에서 닿는 포트가 **안쪽 포트 + 5**다: PostgreSQL 5437, Kafka 9097, Connect REST 8088(Ops 확인 2026-11-08).
  - 광고 서버는 9097·8088의 허용 출발지가 아니라 M3~M5가 실패한다.
  - **관리 장치가 정해진 뒤** 그 장치에서 돈다. 관리 장치 주소·DHCP 예약은 사용자 몫이다. 지금 Mac(192.168.0.26)은 노드 포트에 바로 닿지 않고 SSH 점프로만 닿는다.
- `check.py`는 `MONITOR_PG_NODES`의 노드마다 물어 primary를 스스로 찾는다. M1은 primary에서만 된다(대기 서버에서는 `pg_current_wal_lsn()`이 "recovery is in progress"로 실패). 그래서 HAProxy가 없는 관리 장치에서도 장애 전환 뒤 값을 고칠 필요가 없다.
- **관리 장치는 로컬 시험대 Mac이라, `check.py`의 기본값이 모두 lab을 가리킨다.** 그래서 `MONITOR_REQUIRE_ALL=1`을 맨 앞에 둔다. 변수 하나라도 없거나 비면 lab을 보고 ok를 내지 않고 rc 2로 멈춘다.

```bash
env -u MONITOR_WEBHOOK_URL -u MONITOR_WEBHOOK_FILE MONITOR_REQUIRE_ALL=1 \
  MONITOR_STATE="$HOME/.local/state/pipeline_monitor/manual.json" \
  MONITOR_SLOTS=ads_outbox_slot,metaverse_outbox_slot MONITOR_S7_POWER=0 MONITOR_R7=1 MONITOR_DISK=none \
  MONITOR_PG_NODES=<LAP1_IP>:5437,<LAP2_IP>:5437,<LAP3_IP>:5437 MONITOR_PG_USER=pipeline_monitor MONITOR_PG_PASSWORD_FILE=… \
  MONITOR_CONNECT_REST=http://<LAP1_IP>:8088,http://<LAP2_IP>:8088,http://<LAP3_IP>:8088 \
  MONITOR_KAFKA=<LAP1_IP>:9097,<LAP2_IP>:9097,<LAP3_IP>:9097 MONITOR_KAFKA_BIN=… MONITOR_JAVA_HOME=… \
  MONITOR_BRONZE=none \
  MONITOR_OUTBOX="ads|ads|public.events_outbox,metaverse|<메타버스 DB>|public.eventing_eventoutbox" \
  <python> pipeline/monitor/check.py; echo "rc=$?"
```

- `…`·`<LAPn_IP>`·`<메타버스 DB>`는 운영 값이다(P8-2에서 정한다). 변수 뜻은 `monitor/README.md` 「설정」 절에 있다.
- `MONITOR_PG_USER`: 읽기 전용 감시 역할 `pipeline_monitor`(pg_monitor 회원 — Ops 2026-11-08). `MONITOR_PG_PASSWORD_FILE`은 그 역할의 비밀번호 파일이다.
- `MONITOR_CONNECT_REST`: 쉼표 목록이다. 회차마다 앞에서부터 처음 응답한 곳을 쓴다(R7 재시작 요청도 그곳). 앞 주소가 응답하지 않았으면 M3 `connect_rest` ok 줄에 그 주소만 남는다. 경보는 따로 낸다: 회차마다 목록의 모든 주소에 `GET /`(5초)를 보내, 응답하지 않는 주소마다 `connect_rest:<주소>` alert(Connect Worker 프로세스만 멈춘 것도 — 그 Task는 남은 Worker로 옮겨져 커넥터 줄은 RUNNING일 수 있다). 셋 모두 응답하지 않으면 그 셋의 alert와 `M3:run` error다.
- `MONITOR_BRONZE=none`: 운영 Bronze(k3s·S3)가 아직 없다. M5는 `bronze` `unsupported` 한 줄이고, S3 세 변수(`S3_ENDPOINT`·`S3_ACCESS_KEY_FILE`·`S3_SECRET_KEY_FILE`)는 필요 없다. Bronze가 생기면 `MONITOR_BRONZE` 목록과 S3 세 변수를 넣는다.
- `MONITOR_DISK=none`: 관리 장치에는 볼 디스크가 없다(D1은 `unsupported` 한 줄). 노트북 디스크는 ①로 본다.
- `MONITOR_OUTBOX`(D2): ②의 outbox 크기를 같은 감시 계정(`MONITOR_PG_USER`)·같은 노드로 함께 본다 — 그 계정에 두 DB의 CONNECT와 `public` 스키마 USAGE(둘 다 기본은 PUBLIC에 있다)가 있어야 한다. 표 권한은 필요 없다(`pg_total_relation_size`·`pg_class`만 읽는다). USAGE가 없으면 표 이름을 풀지 못해 D2가 error(InsufficientPrivilege)다. 5 GiB를 넘으면 "P1-6 정리를 할 때" 경보다.
- `MONITOR_GAP_S`·`MONITOR_CODE_COMMIT`은 두지 않는다(하루 한 번 손 실행 — 공백 경보가 날마다 울리지 않게, 작업 저장소에서 돎).
- `MONITOR_KAFKA_BIN`·`MONITOR_JAVA_HOME`은 꼭 준다.
  - 기본값이 이 Mac의 경로(`/opt/kafka/current/bin`, Homebrew openjdk)다.
  - 빠지면 다른 장치에서 M4·M5가 매일 error가 된다.
- `<python>`은 `pipeline/requirements.txt`(psycopg, boto3)를 설치한 환경이다.
  - 표준 출력에 JSON 한 줄이 없으면(트레이스백만 나옴 — 모듈 없음 등) 점검 자체가 실패한 것이다.
  - 이때도 rc는 1이다. 경보의 rc 1과는 JSON 줄이 있는지로 가른다.
  - 상태 파일을 계속 저장하지 못하는 장치(디스크 가득·권한)도 이렇게 끝난다. 이때 R7은 재시작을 부르지 않는다(시도 기록을 저장한 뒤에만 부름).

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
  - **운영 노트북 3대의 잠자기는 이 점검의 S7 밖이다.** Windows·Linux용 S7이 없다(계획 16 §3.3). 잠든 노트북은 그 회차에 M2 `synced:<노드>:<슬롯>` "노드 접속 실패"(alert)로, primary면 `M1·M2:run` error로 바로 드러난다(P2-4b 판단). 막는 것은 P8-2 잠자기 금지 설정이다. 2026-11-05 사고(아래 끝 절)가 그 종류다.

#### M3 FAILED가 보이면 — 자동 3번(check.py) 뒤에는 사용자 확인 뒤에만

- Connect는 FAILED task를 스스로 다시 띄우지 않는다. 그동안 슬롯이 WAL을 붙잡는다(설계 01 §4.2 C3).
- **자동 재시작(R7)**: ④ 명령은 `MONITOR_R7=1`이라, `check.py`가 FAILED를 30분에 3번까지(앞 시도 뒤 최소 0·5·15분) 사용자 확인 없이 재시작한다(설계 01 R7, 사용자 결정 2026-11-06으로 다시 엶 — 근거: 2026-11-05 잠듦 뒤 FAILED). 자세한 규칙은 `monitor/README.md` R7 절.
  - 손 점검이 하루 한 번이라 자동 시도도 그때뿐이다. 그 회차에는 첫 시도 하나만 되므로, ④를 첫 시도 5분 뒤에 다시 돌리면 둘째, 둘째 15분 뒤(첫 시도 20분 뒤)에 다시 돌리면 셋째 시도가 된다.
  - 같은 회차 M1에서 그 커넥터의 슬롯이 없음·`lost`·`unreserved`이거나 슬롯 상태를 모르면 재시작하지 않는다(`r7:<커넥터>` error).
- **아래 순서는 사람이 할 때다**: `r7:<커넥터>`가 "다 씀"(alert)·슬롯 문제·슬롯 모름·저장 실패(error)일 때, 또는 `MONITOR_R7=0`일 때. RB 쪽에 이 절차는 없다.

1. 원인을 본다: `curl -s "http://<응답하는 노트북>:8088/connectors/<이름>/status"`(`MONITOR_CONNECT_REST`는 목록이라 그대로 쓰지 않는다)의 `connector.trace`(커넥터 자체가 FAILED일 때) 또는 `tasks[].trace` 첫 줄을 사건 기록에 적는다.
2. 원인이 설정·상태 문제면 재시작해도 다시 멈춘다. 재시작하지 말고 Planner·사용자에게 알린다.
   - 설정·상태 문제: 인증 실패, publication 없음, 슬롯 무효, R1 불일치.
   - 슬롯 무효는 `README.md` RB-1이다.
3. 그 밖이면 사용자 확인을 받은 뒤 재시작한다.
   - 설계 01 §4.2 C3의 나머지 둘(LSN flush 30초 초과, Kafka 트랜잭션 실패)이 이쪽이다.
   - 2026-11-05의 `Failed to create TypeRegistry`(잠듦 뒤)도 이쪽이고, 재시작으로 회복했다.
   - C3의 "재시도 불가"는 Connect가 스스로 다시 시도하지 않는다는 뜻이다. 재시작해도 안 된다는 뜻이 아니다.
   ```bash
   curl -s -X POST "http://<응답하는 노트북>:8088/connectors/<이름>/restart?includeTasks=true&onlyFailed=true"
   ```
4. ④를 다시 돌려 그 커넥터가 RUNNING인지, 슬롯이 붙잡은 WAL이 줄어드는지 본다.
5. **원인을 고친 뒤** R7 기록을 비운다. 그래야 다음 FAILED에서 자동 3번을 다시 쓴다(비우지 않아도, FAILED가 아닌 회차에 마지막 자동 시도가 30분보다 오래됐으면 저절로 비워진다).
   ```bash
   MONITOR_STATE="$HOME/.local/state/pipeline_monitor/manual.json" <python> pipeline/monitor/check.py --r7-clear <커넥터>
   ```
   - JSON의 `found: true`인지 본다. `false`(rc 1)이면 그 상태 파일에 기록이 없다는 뜻이다 — `MONITOR_STATE`가 ④와 같은지 본다.
- **감시 서버 주기 감시를 켜면 ④ 손 점검은 `MONITOR_R7=0`**으로 바꾼다(상태 파일이 둘이면 시도 기록도 둘이라 예산이 두 배). 이 Mac의 로컬 설치(P2-4b)는 운영을 보지 않으므로, 감시 서버 주기 감시를 켜기 전까지 ④는 `MONITOR_R7=1` 그대로다. 주기 감시가 도는 동안 비웠으면 다음 회차에 `r7:` 키가 사라졌는지 본다.

#### 커넥터를 새로 만든 뒤의 WARN 로그(Debezium 3.7.0) — 그대로 둔다

- **현상**: 새로 만든 커넥터의 task가 `WARN [<커넥터>|task-0] Context is null, skipping message processing (PostgresReadOnlyIncrementalSnapshotChangeEventSource)`를 계속 찍는다. 이벤트마다 약 2줄이고, 유입이 없어도 초당 약 9줄 찍힌다(빈 poll마다 — `heartbeat.interval.ms` 10초와 상관없다).
- **언제 생기나**: 커넥터를 처음 만들거나 지우고 다시 만들 때마다(운영에서 처음 만들 때, 로컬 e2e `--rebuild`). 오래 돈 커넥터(lab)에는 없다.
- **멈추는 때**: task가 다시 붙을 때(재시작, DB 연결이 끊긴 뒤 재연결) 또는 incremental snapshot signal을 처리한 뒤. 그 전까지 이어진다. 이 로그 때문에 task를 재시작하지 않는다(M3 FAILED가 아니다).
- **데이터 영향 없음**(로컬 시험대):
  - 2026-11-07 e2e 다시 만들기 뒤 outbox 24,922행과 Bronze 24,922행의 `event_id` 집합이 같았다.
  - 이 WARN이 나는 동안에도 signal은 처리됐다(메타버스 2026-11-04).
- **로그 양**: 한 줄 약 192 B. 로컬 시험대 Connect는 같은 줄을 `connect.log`와 `connect.stdout.log` 두 곳에 쓴다. 유입이 없어도 둘을 합쳐 시간당 약 12 MB, 하루 약 0.3 GB다.
- **운영 배포 때 볼 것**: Connect 로그 돌리기(rotation)가 `connect.log`뿐 아니라 stdout으로 받는 로그 파일도 덮는지 본다. 로컬 시험대의 `connect.stdout.log`는 log4j가 돌리지 않는다. Connect는 DB와 같은 노트북의 WSL에서 돌므로(계획 16), 이 로그도 매일 점검 ①의 WSL 디스크·Windows C: 여유(< 20%)에 잡힌다.

## 규칙 판이 바뀌는 배포(D6 — MVR-1·MVR-3a)

- `rule_sha256`: 공용 요약 규칙 파일(`hop_rules.py`)의 SHA-256이다.
  - 지금 값: `09c7f8c4e2f7ba931f6d9d1a19b4835b8d0913a7482c788c799285e050d765c7`(MVR-3a — 메타버스 outbox 키 열 `event_uuid` → `outbox_uuid`. 앞 값 `4043ed001c944ed5328cc617b590eb5d937e70933e5fcad53340a03335e369c3`)
- 이 값이 바뀌는 커밋은 **세 곳을 함께 배포한다.** 광고 동작이 바뀌지 않는 커밋(MVR-3a처럼 메타버스 설정만 바뀜)이어도 광고 서버를 다시 배포한다.
  - manifest 잡: 이미지 안의 `pipeline/common/hop_rules.py`(광고·메타버스 잡 둘 다)
  - 광고 서버: `ads_server/events/hop_rules.py`
  - 메타버스 대사(MVR-2): 실행 사본의 `pipeline/common/hop_rules.py`. `pipeline/reconcile/metaverse_hops.py`도 받은 manifest의 `rule_sha256`이 자기 판과 다르면 그 회차를 rc 2(입력 실패)로 끝낸다.
- 판이 다르면 광고 서버는 그 manifest를 대조하지 않는다(`events/hopcheck.py`의 `_pipeline_problems` → `HopInputError`).
  - 그 회차 대사는 rc 2(입력 실패, 단계 `verify`)다. 광고 서버 시험 `case_input_failures_are_told_apart_from_mismatches`가 고정한다.
  - 창은 확정되지 않고, 판이 맞은 다음 회차가 판정한다.
- 배포 시각
  - 시간 잡은 :05, 대사는 :07이다. 하루 잡은 00:35, 대사는 00:50 UTC다.
  - 메타버스는 로컬 견본 기준 시간 잡 :15·대사 :17, 하루 잡 00:40·대사 00:55 UTC(09:40·09:55 KST)다.
  - 셋을 한 번에 배포하면서 :05~:07, :15~:17, 00:35~00:55 UTC를 피하면 rc 2 회차가 없다.
  - 따로 배포하면 그 사이 매시 회차가 rc 2다.
  - 배포 뒤 첫 시간 잡이 늦거나 실패하면, :07 회차는 지난 포인터(옛 판)를 받아 rc 2가 한 번 더 난다(늦음 rc 3이 아니다).

## 메타버스 앱 DB 접속(06 T3 ②)

메타버스 앱 서버 두 대(서비스·이동)가 운영 PostgreSQL에 붙는 값이다. 근거는 계획 06 5판 T3 ②와 계획 25 §5.1 ④(C6 (a), 사용자 결정 2026-11-08)다. 비밀번호와 비밀번호 파일의 내용은 여기 적지 않고, 화면·기록에도 옮기지 않는다.

- 접속 값(각 앱 서버의 `deploy/onprem/.env`, Ops 2026-11-08)
  - `POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=5000`(앱 서버의 HAProxy → 클러스터 쓰기 노드)
  - `POSTGRES_DB=metaverse`, `POSTGRES_USER=metaverse_app`
  - `POSTGRES_PASSWORD`는 비운다. `PGPASSFILE`에는 `playdata`의 `~/.secrets/metaverse-app.pgpass` 절대 경로를 둔다.
  - `POSTGRES_POOL_MAX_SIZE=10`(계획 25 D-D (가)). 클러스터 `max_connections` 200이 먼저 적용돼 있어야 한다(Ops).
  - `KAFKA_BOOTSTRAP_SERVERS`는 빈 값이다. 사건은 outbox → CDC로만 나간다.
  - TLS: 첫 운영은 LAN 평문이다. `verify-full`로 바꿀 때 `POSTGRES_SSLMODE=verify-full`과 `POSTGRES_SSLROOTCERT`(CA 파일 경로)만 더한다(코드 변경 없음).
- 비밀번호 파일(Ops가 만든다)
  - 모드 0600, 주인 `playdata`다.
  - 한 줄의 앞 네 칸이 `127.0.0.1:5000:metaverse:metaverse_app`(= 위 접속 값)이고 다섯째 칸이 비밀번호다.
  - `deploy/onprem/run.py`는 `POSTGRES_PASSWORD`가 비어 있으면 시작 전에 세 가지를 본다: 모드 0600, 실행 사용자 소유, 앞 네 칸이 접속 값과 같은 줄. 어긋나면 파일 내용을 출력하지 않고 rc 1로 멈춘다.
- **배포 확인 — 앱 프로세스는 `playdata`로 돈다**
  - `run.py movement`·`run.py service`(그 아래 daphne·jobs 포함)를 `playdata`로 실행한다. 다른 계정이면 파일을 읽지 못하거나 주인이 달라 `run.py`가 멈춘다.
  - 손으로 부르는 `manage.py`(`migrate`·`import_learning_bank`·`createsuperuser` 등)도 `playdata`로 부르고, 위와 같은 `POSTGRES_*`·`PGPASSFILE`을 환경에 둔다.
  - 확인(값을 출력하지 않는다):

```bash
id -un                          # playdata
stat -c '%a %U' "$PGPASSFILE"   # 600 playdata
```

## 광고 1차 배포 범위와 대상 그룹(과정) 운영(TG-S7, 2026-11-08)

### 오늘(1차) 배포 범위
- 1차는 **outbox → Kafka까지**다(사용자 2026-11-08). 광고 서버는 `migrate`와 파이프라인 연결(publication·CDC 계정 GRANT·커넥터)까지 한다.
- 웹(gunicorn, `process_reactions` systemd, nginx·TLS)은 **1차 직후 다음 단계**다. D23 방화벽도 1차 직후다.
- 서버 쪽 Bronze는 나중에 선다. 서면 Kafka `earliest`부터 따라잡는다 — 운영 Kafka 보존은 7일(브로커 기본값 168시간, 토픽별 덮어쓰기 없음, 2026-11-08 Ops 확인)이므로 **그 안에** Bronze를 세운다. 넘기면 그 구간은 outbox에서 다시 낸다(RB-0).
- MongoDB는 운영에서 꺼 둔다(설정 `MONGO_HOSTS` 없음, `relay_events` 없음). 코드에서 빼는 일(MDB-R)은 Silver·Gold 작업 때 한다.
- **배포 뒤 `ops_check`의 ④ MongoDB와 ⑤ 상시 처리의 outbox(`sent_at` 미전송) 실패는 MongoDB를 끈 운영 설정에서 생기는 알려진 거짓 실패이고, 제거(MDB-R) 때 고친다.** 나머지 점검(설정·파일 저장소, 의존성, PostgreSQL)이 OK인지 본다. ⑤는 한 줄(`FAIL 상시 처리: …`)로 나오고 통과한 하위 점검은 따로 찍히지 않는다 — **⑤의 FAIL 이유에 `outbox:`만 있고 `반응 처리:`가 없으면 반응 처리는 정상**이다. 웹 단계 전에는 반응 처리 프로세스가 없으므로 반응 처리 쪽은 그 단계에서 다시 본다.

### 대상 그룹(과정) 등록 — 광고 서버 먼저, 그다음 매체
1. 관리자 계정(운영 화면에 들어가는 `role = admin`)으로 운영 화면 → 매체 상세 → "대상 그룹"에서 매체가 보낼 코드를 **그대로** 등록한다(메타버스: `mlo`·`skn`·`aio`, 이름은 화면 표시용). 코드는 소문자로 시작하는 32자 이하(`^[a-z][a-z0-9-]{0,31}$`)이고 등록 뒤 바꿀 수 없다.
2. 등록을 마친 뒤 매체(메타버스 어댑터)가 결정 요청에 `segment`를 싣는다(C1 1.6.0). **순서가 바뀌면** 그 과정 사용자의 요청은 400 `unknown_segment`이고 광고를 받지 못한다.
3. 새 과정을 열 때도 같다 — 광고 서버 등록 먼저.
4. 과정을 없앨 때는 **보관**한다(행을 지우지 않는다). 보관된 코드가 계속 와도 400이 아니다 — 그 사용자는 "그룹 없음"으로 맞춰져 전체 대상 광고만 받는다. 되살리기도 같은 화면이다.
5. 광고주가 대상 그룹을 고르는 화면(광고 만들기·광고 상세)은 매체가 그룹을 보내기 시작한 뒤에 안내한다 — 그 전에 고른 광고는 어디에도 맞지 않는다(과다 과금은 없다).

### 일괄(shared) 구성
- 화면에서는 새로 만들거나 고를 수 없다(TG-S4). 이미 있는 일괄 구성은 그대로 동작한다(대상 그룹을 고른 광고는 나가지 않는다).
- 매체가 개인 송출로 바꾼 뒤 운영자가 운영 화면에서 그 구성을 끈다(사용자 Q10).

### 내부 콘텐츠 계정
- 콘텐츠는 광고와 같다(등록·송출·과금). 내부용 광고주 계정을 가입시키고, 관리자 화면(운영 화면 → 광고주 상세)의 포인트 조정(`ADMIN_ADJUST`)으로 지갑을 채운다.
- 그 계정으로 캠페인·광고·소재를 만들고 필요하면 대상 그룹을 고른다. 같은 구성에서 유료 광고와 가중 추첨으로 경쟁하고, 지갑이 비면 잔액 필터가 뺀다.
- 분석에서 내부 소비를 빼려면 그 광고주 계정으로 거른다.

## P8-2에서 더할 것(이번 판에는 없음)

- 계획 02 P8-2 목록은 P8-2 Step에서 이 파일에 더한다. 예: 커넥터를 트래픽보다 먼저 켜기(D6), 노트북 잠자기 금지, D23 비밀 파일 권한·REST 방화벽.
- **잠자기 금지의 근거(2026-11-05, 로컬 시험대)**
  - 맥이 잠든 뒤 18:55:26 KST에 Connect task 둘(`ads-outbox-e2e`, `metaverse-outbox-cdc`)이 `Failed to create TypeRegistry`로 FAILED가 됐다. 로그: "Task is being killed and will not recover until manually restarted".
  - 감시가 돌지 않아 손으로 볼 때까지 몰랐고, 그동안 슬롯이 WAL을 붙잡았다.
  - 사용자 승인으로 11:38Z에 REST로 재시작했다.
