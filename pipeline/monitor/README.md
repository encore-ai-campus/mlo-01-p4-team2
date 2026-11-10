# monitor — 파이프라인 점검·경보

> **임시(D18)** — 점검 스크립트 + 주기 실행 + 웹훅. 감시 수단·알림 채널·받는 사람은 운영 배포 전에 정식으로 다시 정한다(계획 02 P8-1).

`check.py` 하나가 아래를 보고, 상태가 바뀐 것만(새 경보·경보 해소) 웹훅으로 알린다. 경보가 하나라도 있으면 rc=1.

| # | 보는 것 | 경보 |
|---|---|---|
| M1 | 슬롯(`MONITOR_SLOTS`)의 `wal_status`·`inactive_since`·붙잡은 WAL | `unreserved`·`lost`, `inactive_since` 기준 `MONITOR_SLOT_INACTIVE_S`(600초)보다 오래 쉼(재시작·재배치 순간은 경보가 아니다), 붙잡은 WAL ≥ `max_slot_wal_keep_size`의 50%, 슬롯 없음. `lost`면 `restart_lsn`이 NULL이라 붙잡은 WAL은 `held=None`으로 적는다 |
| M2 | 대기 서버 복제 지연·동기 후보 수, 논리 슬롯 `failover`, `synchronized_standby_slots` 목록과 붙어 있는 물리 슬롯의 차이, 대기 서버마다 논리 슬롯 `synced`·`temporary` | 동기 후보 0, 지연 > `MONITOR_REPLICA_LAG_BYTES`(임시 64MB), `failover=false`, 목록에 있는데 안 붙은 슬롯(CDC가 기다림), 복제 중(streaming)인데 목록에 없는 슬롯이 10초 넘게(2초마다 다시 봄 — 갱신 스크립트 D12-2가 멈춤, 승격되면 그 대기 서버의 보호가 빠짐), 대기 서버의 허용 목록 슬롯(`MONITOR_SLOTS`)이 `synced` 아님·`temporary`·없음으로 `MONITOR_SYNCED_GRACE_S`(120초) 넘게(slotsync가 새 사본을 만드는 약 1분은 경보가 아님 — 강등된 노드의 옛 슬롯을 자동 정리가 못 지운 경우), primary physical slot의 `catalog_xmin`이 primary의 어느 논리 슬롯보다 오래된 채(`age()` 비교) `MONITOR_CATALOG_XMIN_STALL_S`(600초) 넘게 그대로인데 그사이 primary xid가 `MONITOR_CATALOG_XMIN_STALL_XIDS`(50)개 넘게 늘었음(강등된 노드의 옛 논리 슬롯, 또는 그 대기 서버의 슬롯 동기화 멈춤이 primary 카탈로그 VACUUM을 막음. primary에 논리 슬롯이 없으면 NULL이 아닌 physical 값은 모두 "오래됨"으로 센다. 정상은 가장 오래된 논리 슬롯 값을 따라 xmax보다 2~4 뒤에서 약 20초마다 움직이고, synced 사본이 조금 늦어 "오래됨"이 되는 것은 최대 12초 연속 — 2026-11-04 실측(2초 간격 120초). 커넥터를 멈춰 논리 슬롯과 함께 멈춘 것은 M1이 잡는다. **50은 heartbeat 10초 기준**(트랜잭션 heartbeat 하나로 10분에 약 60개) — heartbeat 주기를 늘리면 함께 줄인다) |
| M3 | Connect 커넥터·Task 상태(+ `MONITOR_R7=1`이면 R7 자동 재시작 — 아래). `MONITOR_CONNECT_REST`는 쉼표 목록이고 회차마다 앞에서부터 처음 응답한 곳을 쓴다(R7 POST도 그곳). 앞 주소가 응답하지 않았으면 `connect_rest` ok 줄에 그 주소만 적는다. 따로 회차마다 목록의 **모든** 주소에 `GET /`(5초)를 보내 주소마다 `connect_rest:<주소>` 줄을 낸다(Planner 2026-11-08 (나)) | FAILED(늘), `connect_rest:<주소>`가 응답하지 않음(alert — Connect Worker 프로세스만 멈추면 PG·Kafka는 멀쩡해 M2·M4가 울리지 않고, 그 Task는 `scheduled.rebalance.max.delay.ms`(기본 300초) 뒤 남은 Worker로 옮겨져 RUNNING이라 커넥터 줄도 울리지 않는다. 노트북이 통째로 꺼지면 M2 줄과 한 알림에 묶인다. 꺼진 주소마다 회차가 최대 5초 늘어난다), R7 다 씀(`r7:<커넥터>` alert, 매 회차), 슬롯 문제·슬롯 모름·기록 저장 실패(`r7:<커넥터>` error — 재시작하지 않음) |
| M4 | 브로커 포트(TCP), Kafka 최소 ISR 미달·오프라인 파티션 | 브로커가 하나도 응답하지 않으면 `kafka_unreachable` "Kafka 응답 없음(브로커 N대 중 0대 응답)"(이때 ISR 두 키는 같은 키에 error — 못 본 것을 해소로 보지 않음), 일부만 응답하는데 명령이 시간 초과면 "Kafka 명령 시간 초과(N대 응답)" error, ISR 미달·오프라인 하나라도 |
| M5 | (`MONITOR_BRONZE=none`이면 `bronze` `unsupported` 한 줄 — S3 키를 읽지 않고 boto3를 부르지 않는다) Bronze(`MONITOR_BRONZE`) checkpoint 의 마지막 확정 위치, Kafka 끝·가장 이른 offset, 아직 읽지 않은 첫 데이터 레코드(read_committed)의 Kafka 타임스탬프(두 토픽 모두 `CreateTime` — Connect 생산자가 보낸 시각, 재발행 op=r도 다시 보낸 시각) | 읽지 않은 첫 데이터 레코드가 `MONITOR_BRONZE_STALE_S`(300초) 넘게 기다림(EOS 표식·중단된 레코드는 read_committed라 빠짐 — 오래 쉰 뒤 막 들어온 데이터는 울리지 않음), 읽어야 할 offset이 Kafka에서 지워짐(보존 만료 — RB-5), 브로커 0대 응답이면 같은 키에 error. **한계**: Bronze가 죽었더라도 새 데이터가 없으면 울리지 않는다(기다리는 데이터가 없으니 유실은 아니다). 데이터가 오면 300초 뒤 울린다(앱 상태 `bronze_app_failed`는 미구현). 첫 레코드는 3초 안에 못 받으면 "없음"으로 본다. 그래서 Kafka가 느린 순간에는 기다리는 데이터가 있어도 그 회차는 지나갈 수 있다(대개 M4가 함께 보인다). CreateTime은 Connect 생산자 서버의 시계라, 감시 장치와 시계가 다르면 그 차이가 기다린 시간에 더해진다(S7은 감시 장치와 DB primary만 본다 — 300초 문턱이라 영향은 작다) |
| M6·M7·M8 | 창 대사·Kafka↔Bronze 위치 대사·outbox 미확정 창 나이 | 미구현(P1·P2-2 뒤) |
| S7 | 점검을 돌리는 **이 장치 자신**: DB primary와의 시계 차이(노드 조회 순간에 잰다 — 뒤 점검의 Kafka 명령 시간은 들어가지 않음), (macOS) 배터리·잠듦 방지, 지난 회차 뒤 공백(`gap` — `MONITOR_GAP_S`가 0보다 클 때만) | 차이 > 1초, primary를 모르면 `clock_skew` error, 배터리 < 20%(AC 아님), 잠듦 방지 없음(`MONITOR_S7_POWER=0`이면 전원 줄은 `unsupported`), 지난 회차 뒤 `MONITOR_GAP_S`초 넘게 지남(`gap` — 아래) |
| D1 | `MONITOR_DISK`의 경로마다 그 파일 시스템의 여유(`shutil.disk_usage` — free / total. df의 Capacity는 used/(used+avail)라 기준이 다르다). 셋째 칸이 `du`면 그 폴더 아래 보통 파일 크기 합도 detail에 적는다(추세용, 링크는 따라가지 않음) | 여유 < `MONITOR_DISK_MIN_FREE_PCT`(20 — runbook deploy.md ①·③ 기준), 경로를 볼 수 없음(설정이 틀렸거나 폴더가 사라짐). `MONITOR_DISK=none`이면 `disk` 줄 하나가 `unsupported`(디스크가 없는 장치 — 관리 장치·감시 서버) |
| D2 | `MONITOR_OUTBOX`의 표마다 primary의 그 DB에서 `pg_total_relation_size`(색인·TOAST 포함)와 추정 행 수(`reltuples` — ANALYZE 전이면 -1) | 크기 > `MONITOR_OUTBOX_MAX_BYTES`(5 GiB — deploy.md ② 추정 90일) "P1-6 정리를 할 때", 표가 없음(alert). primary를 모르거나 그 DB에 못 닿으면 같은 키에 error(지난 값 유지) |

- PostgreSQL 노드는 `MONITOR_PG_NODES`의 노드마다 한 번 물어(`pg_is_in_recovery()`·논리 슬롯·`now()`) primary를 스스로 찾는다. M1·M2 primary 쪽은 그 노드에서, `synced`는 대기 서버에서 본다(키 `synced:<host:port>:<슬롯>`).
- primary가 정확히 하나가 아니면(0개·갈라짐·조회 실패 — 비밀번호 파일을 못 읽는 것 포함) `M1·M2:run` error 한 줄이 되고, 지난 M1·M2 값은 그대로 둔다(해소로 알리지 않음). 메시지에는 primary 목록·닿은 노드 수·닿지 못한 노드 주소와 오류 이름만 있다. switchover 직후 한 회차는 이 error가 날 수 있다.

- 상태 파일은 같은 폴더에 새 임시 파일(`mkstemp`, O_EXCL·0600 — 이름을 미리 알 수 없음)을 만들어 쓰고 바꿔 끼운다.
- 상태 파일 자리는 이 사용자만 쓴다. 기본은 `~/.local/state/pipeline_monitor/state.json`(`XDG_STATE_HOME`이 있으면 그 아래)이고, 폴더는 0700으로 만든다. 공용 `/tmp`에 두면 다른 로컬 사용자가 폴더·파일·링크를 미리 만들어 경보를 막을 수 있다.
- 폴더·파일이 이 사용자 것이 아니거나, 심볼릭 링크이거나, 그룹·다른 사용자가 쓸 수 있으면 점검하지 않는다. `[오류] 상태 파일 자리가 안전하지 않아 점검하지 않았다` 알림을 보내고 rc=2로 끝난다(조용히 멈추지 않는다).
- rc=2는 "점검하지 않음"이다. JSON 한 줄의 `error`가 까닭이다: `missing_config`(필수 모드에서 운영 변수가 없거나 빔 — 이름만), `bad_config`(설정 형식 — 이름과 까닭만, 값 없음), `code_pin`(코드 사본이 `MONITOR_CODE_COMMIT`과 다르거나 더러움 — 주기 실행만), `unsafe_state`. 확인 순서는 필수 모드 → 설정 형식 → 코드 판 → 상태 자리다(상태 자리를 만들기 전에 멈춘다).
- **rc 2 알림은 처음 한 번 바로, 같은 거부(`error`·`detail`)가 이어지면 한 시간에 한 번**이다(P2-4b 결정 F (나) — 분마다 보내면 하룻밤에 수백 줄이 되어 다른 경보를 묻는다). 마지막 알림은 표시 파일 `<상태 파일>.refused`(0600)에 둔다. 생략한 회차의 JSON `notify`는 `생략(같은 거부가 이어짐 …)`이다.
  - 표시 파일을 믿을 수 없으면 분마다 알린다: 상태 폴더가 아직 없음(만들지 않는다 — 첫 설치에서 설정이 틀린 때), 폴더·표시 파일이 안전하지 않음(위 상태 자리 규칙), 깨짐·미래 시각. `unsafe_state`는 표시 파일을 읽지도 쓰지도 않고 늘 알린다(변조 신호).
  - 웹훅 실패면 표시 파일을 쓰지 않아 다음 회차가 다시 보낸다.
  - 거부가 풀려 다시 도는 첫 회차는 알림 맨 앞에 `[해소] 점검을 다시 한다 — 앞선 거부 <error>(마지막 알림 <시각>)`을 넣고, 보낸 뒤에 표시 파일을 지운다. `unsafe_state`는 표시 파일을 쓰지 않으므로 풀린 회차에 이 줄이 없다 — 5분을 넘었으면 `gap`이 대신 알린다.
- **S7 `gap`**: 지난 회차 뒤 `MONITOR_GAP_S`초 넘게 지났으면 "감시 공백 N초(시작~끝) — 이 장치가 잠들었거나, 주기 실행이 멈췄거나, 점검을 거부(rc 2)한 사이 감시가 없었다"(alert). 잠듦은 깨어난 뒤에야 알 수 있다 — 잠든 동안 알리는 바깥 감시는 정식 감시(P8-1)의 몫이다.
  - 한 번 생기는 사건이라 상태에 "열린 공백"으로 두고, 웹훅 실패가 아닌 회차를 지난 뒤에만 닫는다(깨어난 첫 회차는 Wi-Fi가 없어 웹훅이 실패하기 쉽다). 닫힌 다음 회차가 [해소]다. 웹훅이 계속 실패하는 사이 공백이 또 생기면 시작~끝은 첫 공백 그대로 두고 "그 뒤 공백 N번 더(가장 긴 L초)"로 덧붙인다.
  - rc 2는 상태를 저장하기 전에 끝나므로, 5분 넘게 이어진 거부가 풀린 첫 회차에는 재개 줄과 `gap` [경보]가 한 통에 함께 간다(정상).
  - 하루 한 번 손으로 도는 ④에서는 끈다(`MONITOR_GAP_S` 없음 — 날마다 울리지 않게).
- 상태 파일이 깨져 읽지 못하면 빈 상태로 다시 시작한다. 그러면 지금 경보를 다시 알리고, `[알림] 상태 파일 … 새로 시작` 줄이 붙는다.
- 웹훅 전송에 실패하면(주소 파일을 못 읽은 경우 포함) 경보 수준(`levels`)을 갱신하지 않는다. 다음 점검이 같은 변화를 다시 보낸다. R7 시도 기록은 그 회차에도 저장한다(이미 재시작을 불렀으므로).
- 점검 묶음이 실패하면 그 묶음의 지난 값을 그대로 둔다. 못 본 것을 해소로 알리지 않는다.
- Kafka 명령은 30초에서 끊는다. Kafka가 멈추면 한 번 도는 데 최대 약 2분(M4 2번 + M5 토픽마다 1번)이 걸린다.
- DB 연결(MON-3): 문장마다 `statement_timeout` 10초(잠금 대기 등 — 서버가 끊음), 답을 기다리는 사이 상대가 사라지면 keepalive로 약 25초에 연결 오류(Linux는 `tcp_user_timeout` 30초도). 그 노드·점검은 error가 된다. 커널은 답하는데 DB 프로세스가 멈춘 경우는 덮지 못한다 — 운영은 감시 유닛 `TimeoutStartSec=120`이 회차를 끊는다.
- M5 S3 호출은 연결 5초·읽기 10초·2번까지(MON-3). S3가 멈추면 약 20초 뒤 M5가 error다(botocore 기본이면 한 호출 약 5분).

## R7 — FAILED 커넥터·Task 자동 재시작(설계 01 R7, 사용자 결정 2026-11-06으로 다시 엶)

- `MONITOR_R7=1`일 때만 한다(기본 끔 — 끄면 M3는 FAILED 경보만). **주기 실행이 없으면 이 스크립트가 돌 때만 효과가 있다** — 로컬 시험대는 P2-4b 주기 감시(1분)가 하고, 운영은 감시 서버에 주기 감시를 적용하기 전까지 매일 손 점검 `runbooks/deploy.md` ④ 한 번이다.
- FAILED면 `POST /connectors/<이름>/restart?includeTasks=true&onlyFailed=true`. **30분에 3번**, 앞 시도 뒤 최소 0·5·15분(이 스크립트가 그보다 늦게 돌면 그때). POST가 실패해도 시도로 센다.
- **3번을 다 쓰면 사람 차례**다. 시간이 지나도 다시 부르지 않고, `r7:<커넥터>` alert "다 씀"을 매 회차 낸다. 기록(상태 파일 `r7.<커넥터>` = 시도 시각)은 "FAILED 아님 + 마지막 시도가 30분보다 오래됨"일 때와 사람이 비울 때만 비운다 — 재시작 → 잠깐 RUNNING → 다시 FAILED도 같은 3번 안에 든다.
- **슬롯 조건(설계와 다른 점)**: 같은 회차 M1에서 그 커넥터의 `slot.name`이 없음·`lost`·`unreserved`이면 재시작하지 않는다(error "RB-1"). M1이 끝까지 돌지 않았거나 `slot.name`이 없거나 `MONITOR_SLOTS` 밖이면 "슬롯 상태를 모름"으로 재시작하지 않는다. 커넥터가 `snapshot.mode=no_data`라 슬롯이 지워진 뒤 재시작하면 새 슬롯으로 조용히 이어 가 그 사이 변경이 빠질 수 있기 때문이다.
- 커넥터 상태와 설정은 `GET /connectors?expand=status&expand=info` 한 번으로 읽고, 설정에서는 `slot.name`만 쓴다(설정 전체는 출력·상태에 넣지 않는다).
- 시도 기록을 **먼저 상태 파일에 저장하고, 저장된 뒤에만** 재시작을 부른다. 저장이 실패하면 부르지 않는다(error 줄). 저장이 계속 실패하는 장치에서는 회차 끝의 저장도 실패해 JSON 없이 트레이스백으로 끝난다 — runbook ④의 "점검 자체 실패". 상태 파일이 깨져 새로 시작하면 기록을 잃어 최대 3번이 더 간다(리셋 한 번에 한정).
- 사람이 비우기(원인을 고친 뒤): `MONITOR_STATE=<같은 상태 파일> python pipeline/monitor/check.py --r7-clear <커넥터>` → JSON(`found`, `state` 경로). `found: false`(rc 1)이면 그 상태 파일에 기록이 없다 — `MONITOR_STATE`가 맞는지 본다.
- **같은 클러스터를 보는 주기 감시가 켜지면 ④ 손 점검은 `MONITOR_R7=0`으로 둔다.** 상태 파일이 둘이면 시도 기록도 둘이라 예산이 두 배가 된다. 이 Mac의 로컬 설치는 운영 클러스터를 보지 않으므로, 운영 ④는 감시 서버에 주기 감시를 적용할 때 바꾼다(Planner 2026-11-08). P2-4b 타이머가 도는 동안 `--r7-clear`를 하면, 이미 상태를 읽어 둔 회차가 끝에 저장하며 지운 것을 되살릴 수 있다(나중 쓰기가 이김) — 지운 뒤 다음 회차에 `r7:` 키가 사라졌는지 본다.

## 설정(환경 변수, 기본값은 로컬 시험대)

`MONITOR_PG_NODES`(`host:port` 쉼표 목록, 운영 예 `<LAP1_IP>:5437,<LAP2_IP>:5437,<LAP3_IP>:5437` — 노트북이 NAT라 LAN에서 닿는 포트는 안쪽 포트 + 5다. 승격 뒤에도 고칠 필요 없음. 빈 값·빈 항목·중복·포트 1~65535 밖·IPv6 대괄호 표기는 `bad_config`), 없으면 로컬 기본 `MONITOR_PG_HOST`(127.0.0.1) × `MONITOR_PG_NODE_PORTS`(5432,5433,5434). **`MONITOR_PG_NODES`가 있으면 `MONITOR_PG_HOST`·`MONITOR_PG_NODE_PORTS`는 보지 않는다. `MONITOR_PG_PORT`(HAProxy 입구)는 더 쓰지 않는다(있어도 무시).** `MONITOR_PG_USER`(기본 `postgres` — 운영은 읽기 전용 감시 역할 `pipeline_monitor`(pg_monitor 회원, Ops 2026-11-08)), `MONITOR_PG_PASSWORD_FILE`(그 계정의 비밀번호 파일), `MONITOR_SLOTS`, `MONITOR_SLOT_INACTIVE_S`(600), `MONITOR_SYNCED_GRACE_S`(120), `MONITOR_CATALOG_XMIN_STALL_S`(600)·`MONITOR_CATALOG_XMIN_STALL_XIDS`(50), `MONITOR_CONNECT_REST`(쉼표 목록 — 앞에서부터 처음 응답한 곳, 운영은 노트북 셋 `:8088`), `MONITOR_KAFKA`, `MONITOR_KAFKA_BIN`, `MONITOR_JAVA_HOME`, `MONITOR_BRONZE`(`이름|토픽|checkpoint s3a 경로`를 쉼표로, 또는 `none` — 볼 Bronze가 아직 없음), `S3_ENDPOINT`·`S3_ACCESS_KEY_FILE`·`S3_SECRET_KEY_FILE`, `MONITOR_STATE`(상태 파일, 기본 `~/.local/state/pipeline_monitor/state.json` — 운영 systemd는 `StateDirectory=`가 만든 폴더를 주면 된다), `MONITOR_WEBHOOK_URL` 또는 `MONITOR_WEBHOOK_FILE`, `MONITOR_S7_POWER`(`1` 기본·`0`이면 S7 전원 줄을 끔 — 관리 장치에서 손으로 돌릴 때), `MONITOR_R7`(`0` 기본·`1`이면 R7 자동 재시작), `MONITOR_REQUIRE_ALL`(`0` 기본·`1`이면 필수 모드).

P2-4b에서 더한 것:
- `MONITOR_DISK`: `이름|절대 경로[|du]` 쉼표 목록(`~`는 홈) 또는 `none`(디스크가 없는 장치 — D1 `unsupported` 한 줄). 기본은 로컬 `pg|/opt/postgresql,reconcile_ads|~/Library/Caches/ads-pipeline/reconcile|du`. `MONITOR_DISK_MIN_FREE_PCT`(0~100, 기본 20).
- `MONITOR_OUTBOX`: `이름|DB|스키마.표` 쉼표 목록. 기본은 로컬 lab·e2e·메타버스 outbox 셋. `MONITOR_OUTBOX_MAX_BYTES`(기본 5 GiB = 5368709120).
- `MONITOR_GAP_S`: 공백 경보 문턱 초(0 = 끔, 기본). 주기 실행은 300.
- `MONITOR_CODE_COMMIT`: 있으면 이 저장소의 HEAD가 이 커밋 40자(소문자)이고 미커밋·추적 안 된 파일이 없을 때만 점검한다(아니면 `code_pin`). 빈 값·자리 표시(`__COMMIT__`)는 `bad_config`. 손 실행(④)에는 두지 않는다.
- 이름 겹침·칸 수·`du`가 아닌 셋째 칸·상대 경로·`스키마.표`가 아닌 표·범위 밖 수는 `bad_config`(이름과 까닭만).

- **필수 모드**(`MONITOR_REQUIRE_ALL=1`): 기본값이 모두 로컬 시험대를 가리키므로, 운영에서 하나만 빠져도 조용히 lab을 보고 ok를 낼 수 있다. 필수 모드에서는 아래가 하나라도 없거나 비면(공백만 포함) 점검하지 않고 rc=2(`missing_config`, 빠진 이름만)로 끝난다: `MONITOR_PG_NODES`, `MONITOR_PG_PASSWORD_FILE`, `MONITOR_CONNECT_REST`, `MONITOR_KAFKA`, `MONITOR_KAFKA_BIN`, `MONITOR_JAVA_HOME`, `MONITOR_BRONZE`, `S3_ENDPOINT`, `S3_ACCESS_KEY_FILE`, `S3_SECRET_KEY_FILE`, `MONITOR_SLOTS`, `MONITOR_STATE`, `MONITOR_S7_POWER`, `MONITOR_R7`, `MONITOR_DISK`, `MONITOR_OUTBOX`, `MONITOR_PG_USER`. 디스크를 보지 않는 장치는 `MONITOR_DISK=none`으로 "명시적으로 끔"을 적는다. `MONITOR_BRONZE=none`이면 S3 세 변수(`S3_ENDPOINT`·`S3_ACCESS_KEY_FILE`·`S3_SECRET_KEY_FILE`)는 필수가 아니다.
- `MONITOR_S7_POWER`·`MONITOR_REQUIRE_ALL`·`MONITOR_R7`은 `0`·`1`만 받는다. 그 밖(`true`·`yes`·`off`·빈 값)은 `bad_config` — `true`를 끔으로 읽어 필수 모드가 조용히 꺼지지 않게.

비밀값(DB 비밀번호·RustFS 키·웹훅 주소)은 저장소에 두지 않는다. 파일 경로나 환경 변수로만 받고 출력하지 않는다.

## 주기 실행

### 로컬(이 Mac, P2-4b)

- **코드 자리**: 작업 저장소가 아니라 리뷰된 커밋에 맞춘 실행 전용 clone `$HOME/pipeline-run/ads/mlo_01_p4_team2`(광고 Worker 것 — 결정 A. 통합 Silver는 따로)에서 돈다. 그 clone에서는 아무것도 고치지 않는다.
  ```bash
  git clone <작업 저장소> "$HOME/pipeline-run/ads/mlo_01_p4_team2"
  git -C "$HOME/pipeline-run/ads/mlo_01_p4_team2" checkout --detach <리뷰된 커밋>
  ```
- **설치**: 견본 `local.pipeline.monitor.plist`의 자리 표시 넷(`__PYTHON__` = psycopg·boto3가 있는 python, `__REPO__` = 위 clone, `__HOME__`, `__COMMIT__` = checkout한 커밋 40자)을 바꿔 `~/Library/LaunchAgents/local.pipeline.monitor.plist`로 두고 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.pipeline.monitor.plist`.
- **로컬은 웹훅 없이 둔다**(사용자 결정 2026-11-08 — 웹훅은 운영 감시 서버만). 경보는 로그의 JSON 줄(`changes`·`findings`)로만 남고, 바뀐 것이 있는 회차의 `notify`는 `웹훅 없음(화면에만)`이다. 견본에 `MONITOR_WEBHOOK_FILE` 키를 두지 않는 까닭: 키가 있고 파일이 없으면 회차마다 "웹훅 실패"가 되어 경보 수준·공백·rc 2 표시가 갱신되지 않는다.
- **올리기**: clone을 새 리뷰된 커밋으로 checkout하고 plist의 `MONITOR_CODE_COMMIT`을 바꾼 뒤 `launchctl bootout gui/$(id -u)/local.pipeline.monitor` → `bootstrap`. 한쪽만 바꾸면 다음 회차가 `code_pin`으로 거부하고 알린다. 새 판이 필수 변수를 더했으면 plist에도 더한다(예: 운영 적용 판의 `MONITOR_PG_USER`) — 빠지면 rc 2 `missing_config`이고, 로컬은 웹훅이 없어 로그에만 남는다.
- **끄기**: `launchctl bootout gui/$(id -u)/local.pipeline.monitor`, plist 파일 삭제. 상태 파일(`periodic.json`)·로그는 남겨도 된다.
- **래퍼** `run-monitor.sh <python>`: 출력을 `MONITOR_LOG`(견본 `~/Library/Logs/pipeline-monitor.log`)에 이어 쓰고, 시작할 때 10 MiB를 넘으면 `.1`로 하나만 옮긴다(결정 D (나)). `caffeinate`를 쓰지 않는다(감시가 잠듦을 막으면 S7 sleep이 늘 ok). `python -I`로 부른다.
- **비용**: 한 회차 약 6초(CPU 약 6초) — 대부분 Kafka 명령 4번(M4 2번 + M5 Bronze 둘)의 JVM 시작이다. 1분마다면 한 코어의 약 10%다. JSON 한 줄은 약 6 KB다.
- **R7이 이 Mac의 커넥터를 재시작한다**(견본 `MONITOR_R7=1`). 일부러 FAILED를 만드는 시험(장애 시험 E-C1 등)은 그 시험 계획에서 감시를 켜 둘지(R7 동작까지 관찰) 끌지를 정한다. 다른 세션의 시험(커넥터·Bronze 멈춤)에도 웹훅이 울린다 — 시험 전에 Planner가 알린다.
- 이 Mac에서 보는 것: 슬롯·커넥터 셋(lab·e2e·메타버스), Bronze 둘(e2e·메타버스 — lab Bronze는 멈춰 있다, 다시 켤 때 `MONITOR_BRONZE`에 더함), 디스크(PG·광고 대사 폴더 — 메타버스 대사 폴더는 그 LaunchAgent를 설치하는 Step에서 더함), outbox 셋.

### 운영

- 같은 `check.py`를 감시 서버(alarnt)에서 systemd 타이머(1분)로 돌린다(Ops 반영). 웹훅(Discord)은 이 서버에만 두고, 주소는 사용자가 넣는다.
  - 지금 범위의 운영 값: `MONITOR_PG_USER=pipeline_monitor`, `MONITOR_CONNECT_REST`에 노트북 셋의 `:8088`, `MONITOR_BRONZE=none`(운영 Bronze(k3s·S3) 전 — 생기면 Bronze 목록과 S3 세 변수를 넣는다), `MONITOR_DISK=none`.
  - 디스크는 감시 서버가 직접 볼 수 없으므로, 디스크가 있는 장치마다(노트북 셋의 WSL — 데이터 폴더와 `/mnt/c`, 광고 서버의 대사 폴더, 메타버스 대사 장치) 같은 `check.py`를 "D1만" 돌린다(결정 B). 노드·앱 서버의 D1은 이번 운영 적용 범위 밖이다 — 점검 고르기 설정은 그때 더한다.
- 노트북 3대의 S7(잠듦·배터리)은 macOS 밖이라 `unsupported`다. 감시 서버에서는 잠든 노트북이 그 회차에 바로 경보가 된다 — M2 `synced:<노드>:<슬롯>` "노드 접속 실패"(alert), 그 노트북이 primary면 `M1·M2:run` error. 막는 것은 P8-2 잠자기 금지 설정이다(16 :193 — 판단만, 코드 없음).
- 감시 서버에 주기 감시를 켜기 전까지 운영 매일 손 점검은 관리 장치에서 `runbooks/deploy.md` ④ 명령으로 돌린다(필수 모드·`MONITOR_S7_POWER=0`·`MONITOR_DISK=none`·`MONITOR_BRONZE=none`·`MONITOR_R7=1`). 감시 서버 주기 감시를 켜면 ④ 손 점검은 `MONITOR_R7=0`이다(위 R7 절).

의존성: `pipeline/requirements.txt`(psycopg, boto3).
