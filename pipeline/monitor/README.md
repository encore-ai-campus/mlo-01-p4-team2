# monitor — 파이프라인 점검·경보

> **임시(D18)** — 점검 스크립트 + 주기 실행 + 웹훅. 감시 수단·알림 채널·받는 사람은 운영 배포 전에 정식으로 다시 정한다(계획 02 P8-1).

`check.py` 하나가 아래를 보고, 상태가 바뀐 것만(새 경보·경보 해소) 웹훅으로 알린다. 경보가 하나라도 있으면 rc=1.

| # | 보는 것 | 경보 |
|---|---|---|
| M1 | 슬롯(`MONITOR_SLOTS`)의 `wal_status`·`inactive_since`·붙잡은 WAL | `unreserved`·`lost`, `inactive_since` 기준 `MONITOR_SLOT_INACTIVE_S`(600초)보다 오래 쉼(재시작·재배치 순간은 경보가 아니다), 붙잡은 WAL ≥ `max_slot_wal_keep_size`의 50%, 슬롯 없음. `lost`면 `restart_lsn`이 NULL이라 붙잡은 WAL은 `held=None`으로 적는다 |
| M2 | 대기 서버 복제 지연·동기 후보 수, 논리 슬롯 `failover`, `synchronized_standby_slots` 목록과 붙어 있는 물리 슬롯의 차이, 대기 서버마다 논리 슬롯 `synced`·`temporary` | 동기 후보 0, 지연 > `MONITOR_REPLICA_LAG_BYTES`(임시 64MB), `failover=false`, 목록에 있는데 안 붙은 슬롯(CDC가 기다림), 복제 중(streaming)인데 목록에 없는 슬롯이 10초 넘게(2초마다 다시 봄 — 갱신 스크립트 D12-2가 멈춤, 승격되면 그 대기 서버의 보호가 빠짐), `synced` 아님·`temporary` |
| M3 | Connect 커넥터·Task 상태 | FAILED |
| M4 | Kafka 최소 ISR 미달·오프라인 파티션 | 하나라도 |
| M5 | Bronze(`MONITOR_BRONZE`) checkpoint 의 마지막 확정 배치 시각·위치와 Kafka 끝 offset | 지연 > `MONITOR_BRONZE_LAG_MIN`(EOS 표식 여유) 이고 마지막 배치가 `MONITOR_BRONZE_STALE_S`(300초)보다 오래됨 |
| M6·M7·M8 | 창 대사·Kafka↔Bronze 위치 대사·outbox 미확정 창 나이 | 미구현(P1·P2-2 뒤) |
| S7 | 이 장치와 DB primary 의 시계 차이, (macOS) 배터리·잠듦 방지 | 차이 > 1초, 배터리 < 20%(AC 아님), 잠듦 방지 없음 |

R7(FAILED Task 자동 재시작)은 **넣지 않았다.** M3는 FAILED 경보만 낸다. E-K2(계획 02 P0-7) 결과로 R7 (나)가 정해지면 그때 넣는다.

- 상태 파일은 같은 폴더에 새 임시 파일(`mkstemp`, O_EXCL·0600 — 이름을 미리 알 수 없음)을 만들어 쓰고 바꿔 끼운다.
- 상태 파일 자리는 이 사용자만 쓴다. 기본은 `~/.local/state/pipeline_monitor/state.json`(`XDG_STATE_HOME`이 있으면 그 아래)이고, 폴더는 0700으로 만든다. 공용 `/tmp`에 두면 다른 로컬 사용자가 폴더·파일·링크를 미리 만들어 경보를 막을 수 있다.
- 폴더·파일이 이 사용자 것이 아니거나, 심볼릭 링크이거나, 그룹·다른 사용자가 쓸 수 있으면 점검하지 않는다. `[오류] 상태 파일 자리가 안전하지 않아 점검하지 않았다` 알림을 보내고 rc=2로 끝난다(조용히 멈추지 않는다).
- 상태 파일이 깨져 읽지 못하면 빈 상태로 다시 시작한다. 그러면 지금 경보를 다시 알리고, `[알림] 상태 파일 … 새로 시작` 줄이 붙는다.
- 웹훅 전송에 실패하면(주소 파일을 못 읽은 경우 포함) 상태를 갱신하지 않는다. 다음 점검이 같은 변화를 다시 보낸다.
- 점검 묶음이 실패하면 그 묶음의 지난 값을 그대로 둔다. 못 본 것을 해소로 알리지 않는다.
- Kafka 명령은 30초에서 끊는다. Kafka가 멈추면 한 번 도는 데 최대 약 2분(M4 2번 + M5 토픽마다 1번)이 걸린다.

## 설정(환경 변수, 기본값은 로컬 시험대)

`MONITOR_PG_HOST`·`MONITOR_PG_PORT`(5010 = HAProxy rw), `MONITOR_PG_NODE_PORTS`(5432,5433,5434 — 노드마다 직접 물어 대기 서버면 synced를 본다. 승격 뒤에도 고칠 필요 없음), `MONITOR_PG_PASSWORD_FILE`, `MONITOR_SLOTS`, `MONITOR_SLOT_INACTIVE_S`(600), `MONITOR_CONNECT_REST`, `MONITOR_KAFKA`, `MONITOR_KAFKA_BIN`, `MONITOR_JAVA_HOME`, `MONITOR_BRONZE`(`이름|토픽|checkpoint s3a 경로`를 쉼표로), `S3_ENDPOINT`·`S3_ACCESS_KEY_FILE`·`S3_SECRET_KEY_FILE`, `MONITOR_STATE`(상태 파일, 기본 `~/.local/state/pipeline_monitor/state.json` — 운영 systemd는 `StateDirectory=`가 만든 폴더를 주면 된다), `MONITOR_WEBHOOK_URL` 또는 `MONITOR_WEBHOOK_FILE`.

비밀값(DB 비밀번호·RustFS 키·웹훅 주소)은 저장소에 두지 않는다. 파일 경로나 환경 변수로만 받고 출력하지 않는다.

## 주기 실행

- 로컬: `local.pipeline.monitor.plist`(사용자 LaunchAgent 견본, 1분). 자리 표시 `__PYTHON__`·`__REPO__`·`__HOME__`을 이 장치에 맞게 바꿔 `~/Library/LaunchAgents/`에 두고 `launchctl bootstrap gui/$(id -u) <파일>`.
- 운영: 같은 `check.py`를 systemd 타이머(1분)로. 노트북 3대 운영의 S7(잠듦·배터리)은 macOS 밖에서는 아직 `unsupported`다.

의존성: `pipeline/requirements.txt`(psycopg, boto3).
