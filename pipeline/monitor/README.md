# monitor — 파이프라인 점검·경보

> **임시(D18)** — 점검 스크립트 + 주기 실행 + 웹훅. 감시 수단·알림 채널·받는 사람은 운영 배포 전에 정식으로 다시 정한다(계획 02 P8-1).

`check.py` 하나가 아래를 보고, 상태가 바뀐 것만(새 경보·경보 해소) 웹훅으로 알린다. 경보가 하나라도 있으면 rc=1.

| # | 보는 것 | 경보 |
|---|---|---|
| M1 | 슬롯(`MONITOR_SLOTS`)의 `wal_status`·`inactive_since`·붙잡은 WAL | `unreserved`·`lost`, `inactive_since` 기준 `MONITOR_SLOT_INACTIVE_S`(600초)보다 오래 쉼(재시작·재배치 순간은 경보가 아니다), 붙잡은 WAL ≥ `max_slot_wal_keep_size`의 50%, 슬롯 없음. `lost`면 `restart_lsn`이 NULL이라 붙잡은 WAL은 `held=None`으로 적는다 |
| M2 | 대기 서버 복제 지연·동기 후보 수, 논리 슬롯 `failover`, `synchronized_standby_slots` 목록과 붙어 있는 물리 슬롯의 차이, 대기 서버마다 논리 슬롯 `synced`·`temporary` | 동기 후보 0, 지연 > `MONITOR_REPLICA_LAG_BYTES`(임시 64MB), `failover=false`, 목록에 있는데 안 붙은 슬롯(CDC가 기다림), 복제 중(streaming)인데 목록에 없는 슬롯이 10초 넘게(2초마다 다시 봄 — 갱신 스크립트 D12-2가 멈춤, 승격되면 그 대기 서버의 보호가 빠짐), 대기 서버의 허용 목록 슬롯(`MONITOR_SLOTS`)이 `synced` 아님·`temporary`·없음으로 `MONITOR_SYNCED_GRACE_S`(120초) 넘게(slotsync가 새 사본을 만드는 약 1분은 경보가 아님 — 강등된 노드의 옛 슬롯을 자동 정리가 못 지운 경우), primary physical slot의 `catalog_xmin`이 primary의 어느 논리 슬롯보다 오래된 채(`age()` 비교) `MONITOR_CATALOG_XMIN_STALL_S`(600초) 넘게 그대로인데 그사이 primary xid가 `MONITOR_CATALOG_XMIN_STALL_XIDS`(50)개 넘게 늘었음(강등된 노드의 옛 논리 슬롯, 또는 그 대기 서버의 슬롯 동기화 멈춤이 primary 카탈로그 VACUUM을 막음. primary에 논리 슬롯이 없으면 NULL이 아닌 physical 값은 모두 "오래됨"으로 센다. 정상은 가장 오래된 논리 슬롯 값을 따라 xmax보다 2~4 뒤에서 약 20초마다 움직이고, synced 사본이 조금 늦어 "오래됨"이 되는 것은 최대 12초 연속 — 2026-11-04 실측(2초 간격 120초). 커넥터를 멈춰 논리 슬롯과 함께 멈춘 것은 M1이 잡는다. **50은 heartbeat 10초 기준**(트랜잭션 heartbeat 하나로 10분에 약 60개) — heartbeat 주기를 늘리면 함께 줄인다) |
| M3 | Connect 커넥터·Task 상태 | FAILED |
| M4 | 브로커 포트(TCP), Kafka 최소 ISR 미달·오프라인 파티션 | 브로커가 하나도 응답하지 않으면 `kafka_unreachable` "Kafka 응답 없음(브로커 N대 중 0대 응답)"(이때 ISR 두 키는 같은 키에 error — 못 본 것을 해소로 보지 않음), 일부만 응답하는데 명령이 시간 초과면 "Kafka 명령 시간 초과(N대 응답)" error, ISR 미달·오프라인 하나라도 |
| M5 | Bronze(`MONITOR_BRONZE`) checkpoint 의 마지막 확정 위치, Kafka 끝·가장 이른 offset, 아직 읽지 않은 첫 데이터 레코드(read_committed)의 Kafka 타임스탬프(두 토픽 모두 `CreateTime` — Connect 생산자가 보낸 시각, 재발행 op=r도 다시 보낸 시각) | 읽지 않은 첫 데이터 레코드가 `MONITOR_BRONZE_STALE_S`(300초) 넘게 기다림(EOS 표식·중단된 레코드는 read_committed라 빠짐 — 오래 쉰 뒤 막 들어온 데이터는 울리지 않음), 읽어야 할 offset이 Kafka에서 지워짐(보존 만료 — RB-5), 브로커 0대 응답이면 같은 키에 error. **한계**: Bronze가 죽었더라도 새 데이터가 없으면 울리지 않는다(기다리는 데이터가 없으니 유실은 아니다). 데이터가 오면 300초 뒤 울린다(앱 상태 `bronze_app_failed`는 미구현). 첫 레코드는 3초 안에 못 받으면 "없음"으로 본다. 그래서 Kafka가 느린 순간에는 기다리는 데이터가 있어도 그 회차는 지나갈 수 있다(대개 M4가 함께 보인다). CreateTime은 Connect 생산자 서버의 시계라, 감시 장치와 시계가 다르면 그 차이가 기다린 시간에 더해진다(S7은 감시 장치와 DB primary만 본다 — 300초 문턱이라 영향은 작다) |
| M6·M7·M8 | 창 대사·Kafka↔Bronze 위치 대사·outbox 미확정 창 나이 | 미구현(P1·P2-2 뒤) |
| S7 | 점검을 돌리는 **이 장치 자신**: DB primary와의 시계 차이(노드 조회 순간에 잰다 — 뒤 점검의 Kafka 명령 시간은 들어가지 않음), (macOS) 배터리·잠듦 방지 | 차이 > 1초, primary를 모르면 `clock_skew` error, 배터리 < 20%(AC 아님), 잠듦 방지 없음(`MONITOR_S7_POWER=0`이면 전원 줄은 `unsupported`) |

- PostgreSQL 노드는 `MONITOR_PG_NODES`의 노드마다 한 번 물어(`pg_is_in_recovery()`·논리 슬롯·`now()`) primary를 스스로 찾는다. M1·M2 primary 쪽은 그 노드에서, `synced`는 대기 서버에서 본다(키 `synced:<host:port>:<슬롯>`).
- primary가 정확히 하나가 아니면(0개·갈라짐·조회 실패 — 비밀번호 파일을 못 읽는 것 포함) `M1·M2:run` error 한 줄이 되고, 지난 M1·M2 값은 그대로 둔다(해소로 알리지 않음). 메시지에는 primary 목록·닿은 노드 수·닿지 못한 노드 주소와 오류 이름만 있다. switchover 직후 한 회차는 이 error가 날 수 있다.

R7(FAILED Task 자동 재시작)은 **넣지 않았다.** M3는 FAILED 경보만 낸다. E-K2(계획 02 P0-7) 결과로 R7 (나)가 정해지면 그때 넣는다.

- 상태 파일은 같은 폴더에 새 임시 파일(`mkstemp`, O_EXCL·0600 — 이름을 미리 알 수 없음)을 만들어 쓰고 바꿔 끼운다.
- 상태 파일 자리는 이 사용자만 쓴다. 기본은 `~/.local/state/pipeline_monitor/state.json`(`XDG_STATE_HOME`이 있으면 그 아래)이고, 폴더는 0700으로 만든다. 공용 `/tmp`에 두면 다른 로컬 사용자가 폴더·파일·링크를 미리 만들어 경보를 막을 수 있다.
- 폴더·파일이 이 사용자 것이 아니거나, 심볼릭 링크이거나, 그룹·다른 사용자가 쓸 수 있으면 점검하지 않는다. `[오류] 상태 파일 자리가 안전하지 않아 점검하지 않았다` 알림을 보내고 rc=2로 끝난다(조용히 멈추지 않는다).
- rc=2는 "점검하지 않음"이다. JSON 한 줄의 `error`가 까닭이다: `missing_config`(필수 모드에서 운영 변수가 없거나 빔 — 이름만), `bad_config`(설정 형식 — 이름과 까닭만, 값 없음), `unsafe_state`. 셋 모두 웹훅이 있으면 알린다. 확인 순서는 필수 모드 → 설정 형식 → 상태 자리다(상태 자리를 만들기 전에 멈춘다).
- 상태 파일이 깨져 읽지 못하면 빈 상태로 다시 시작한다. 그러면 지금 경보를 다시 알리고, `[알림] 상태 파일 … 새로 시작` 줄이 붙는다.
- 웹훅 전송에 실패하면(주소 파일을 못 읽은 경우 포함) 상태를 갱신하지 않는다. 다음 점검이 같은 변화를 다시 보낸다.
- 점검 묶음이 실패하면 그 묶음의 지난 값을 그대로 둔다. 못 본 것을 해소로 알리지 않는다.
- Kafka 명령은 30초에서 끊는다. Kafka가 멈추면 한 번 도는 데 최대 약 2분(M4 2번 + M5 토픽마다 1번)이 걸린다.

## 설정(환경 변수, 기본값은 로컬 시험대)

`MONITOR_PG_NODES`(`host:port` 쉼표 목록, 운영 예 `<LAP1_IP>:5432,<LAP2_IP>:5432,<LAP3_IP>:5432` — 승격 뒤에도 고칠 필요 없음. 빈 값·빈 항목·중복·포트 1~65535 밖·IPv6 대괄호 표기는 `bad_config`), 없으면 로컬 기본 `MONITOR_PG_HOST`(127.0.0.1) × `MONITOR_PG_NODE_PORTS`(5432,5433,5434). **`MONITOR_PG_NODES`가 있으면 `MONITOR_PG_HOST`·`MONITOR_PG_NODE_PORTS`는 보지 않는다. `MONITOR_PG_PORT`(HAProxy 입구)는 더 쓰지 않는다(있어도 무시).** `MONITOR_PG_PASSWORD_FILE`, `MONITOR_SLOTS`, `MONITOR_SLOT_INACTIVE_S`(600), `MONITOR_SYNCED_GRACE_S`(120), `MONITOR_CATALOG_XMIN_STALL_S`(600)·`MONITOR_CATALOG_XMIN_STALL_XIDS`(50), `MONITOR_CONNECT_REST`, `MONITOR_KAFKA`, `MONITOR_KAFKA_BIN`, `MONITOR_JAVA_HOME`, `MONITOR_BRONZE`(`이름|토픽|checkpoint s3a 경로`를 쉼표로), `S3_ENDPOINT`·`S3_ACCESS_KEY_FILE`·`S3_SECRET_KEY_FILE`, `MONITOR_STATE`(상태 파일, 기본 `~/.local/state/pipeline_monitor/state.json` — 운영 systemd는 `StateDirectory=`가 만든 폴더를 주면 된다), `MONITOR_WEBHOOK_URL` 또는 `MONITOR_WEBHOOK_FILE`, `MONITOR_S7_POWER`(`1` 기본·`0`이면 S7 전원 줄을 끔 — 관리 장치에서 손으로 돌릴 때), `MONITOR_REQUIRE_ALL`(`0` 기본·`1`이면 필수 모드).

- **필수 모드**(`MONITOR_REQUIRE_ALL=1`): 기본값이 모두 로컬 시험대를 가리키므로, 운영에서 하나만 빠져도 조용히 lab을 보고 ok를 낼 수 있다. 필수 모드에서는 아래가 하나라도 없거나 비면(공백만 포함) 점검하지 않고 rc=2(`missing_config`, 빠진 이름만)로 끝난다: `MONITOR_PG_NODES`, `MONITOR_PG_PASSWORD_FILE`, `MONITOR_CONNECT_REST`, `MONITOR_KAFKA`, `MONITOR_KAFKA_BIN`, `MONITOR_JAVA_HOME`, `MONITOR_BRONZE`, `S3_ENDPOINT`, `S3_ACCESS_KEY_FILE`, `S3_SECRET_KEY_FILE`, `MONITOR_SLOTS`, `MONITOR_STATE`, `MONITOR_S7_POWER`.
- `MONITOR_S7_POWER`·`MONITOR_REQUIRE_ALL`은 `0`·`1`만 받는다. 그 밖(`true`·`yes`·`off`·빈 값)은 `bad_config` — `true`를 끔으로 읽어 필수 모드가 조용히 꺼지지 않게.

비밀값(DB 비밀번호·RustFS 키·웹훅 주소)은 저장소에 두지 않는다. 파일 경로나 환경 변수로만 받고 출력하지 않는다.

## 주기 실행

- 로컬: `local.pipeline.monitor.plist`(사용자 LaunchAgent 견본, 1분). 자리 표시 `__PYTHON__`·`__REPO__`·`__HOME__`을 이 장치에 맞게 바꿔 `~/Library/LaunchAgents/`에 두고 `launchctl bootstrap gui/$(id -u) <파일>`.
- 운영: 같은 `check.py`를 systemd 타이머(1분)로. 노트북 3대 운영의 S7(잠듦·배터리)은 macOS 밖에서는 아직 `unsupported`다.
- 운영 매일 손 점검(주기 감시 P2-4b를 미룬 동안)은 관리 장치에서 `runbooks/deploy.md` ④ 명령으로 돌린다(필수 모드·`MONITOR_S7_POWER=0`).

의존성: `pipeline/requirements.txt`(psycopg, boto3).
