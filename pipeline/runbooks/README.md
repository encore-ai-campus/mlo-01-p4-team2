# runbooks — 복구 절차(RB) 도구

설계 01 §6의 RB-0~RB-5를 도구와 점검표로 둔다. RB-1~RB-5는 "어디서 다시 시작하나"만 다르고, 빈 곳은 모두 RB-0(outbox 재발행)이 메운다.

| 도구 | 하는 일 | 바꾸는가 |
|---|---|---|
| `rb_lsn_check.py` | 재개 전 LSN 점검: 커넥터가 저장한 offset LSN과 primary의 `pg_current_wal_lsn()`을 비교. offset이 앞서면 rc=2(진행 거부) | 읽기만 |
| `rb.py rb1·rb2·rb3` | 커넥터 쪽 절차(정지 → 슬롯 → offset → 재개, RB-3은 새 토픽·route) | **기본 dry-run**, `--apply`일 때만 |
| `rb_offsets.py`(Spark) | 새 Bronze checkpoint의 `startingOffsets`(RB-4·RB-5) | 읽기만 |

공통 규칙

- 커넥터 정지는 `PUT /connectors/{name}/stop`이다(`pause` 아님). offsets API는 STOPPED에서만 되고, EOS 커넥터는 쓰기 전에 좀비 펜싱을 한다.
- 먼저 dry-run으로 읽기 조회 결과와 할 일 목록을 보고, 맞으면 같은 명령에 `--apply`를 붙인다.
- 실행마다 사건 기록을 남긴다: RB 번호, 시각, 커넥터·토픽·checkpoint, dry-run·apply 출력, RB-0 창 목록과 건수.
- 비밀값(DB 비밀번호, RustFS 키)은 파일에서만 읽는다. 기록에 옮겨 적지 않는다.
- **signal groupId 규칙**: `signal.kafka.groupId = <커넥터 이름>-signal`. 커넥터마다 하나씩 두고 다른 커넥터나 사람 도구와 같이 쓰지 않는다. 같이 쓰면 한쪽이 다른 쪽의 signal을 소비해 재발행이 빠진다.

환경 변수(기본값은 로컬 시험대): `CONNECT_REST`(http://127.0.0.1:8083), `PG_HOST`·`PG_PORT`(5010 = HAProxy rw), `PG_PASSWORD_FILE`, `KAFKA_BOOTSTRAP`, `KAFKA_BIN`, `JAVA_HOME`, Bronze 쪽은 `BRONZE_TOPIC`·`BRONZE_TABLE`·`BRONZE_CHECKPOINT`·`BRONZE_STARTING_OFFSETS`.

## RB-0 창 재발행(공통)

| 순서 | 할 일 | 입력 → 출력 |
|---|---|---|
| 1 | `mismatch` 창 목록을 얻는다(D11 규칙 — `unsent`를 지연 근거로 쓰지 않음) | `c4_reconcile` 보고 → 창 목록 |
| 2 | 창마다 `created_at`이 그 창 안인 outbox 행을 D13 수단(Kafka signal 증분 스냅샷)으로 다시 보낸다. 앞뒤 1창 여유는 더해도 된다(중복은 무해) | 창 목록 → signal 메시지 |
| 3 | 다음 대사에서 그 창이 `match`인지 본다. 중복은 `duplicates`로 따로 나오고 `match`를 막지 않는다. **끝났는지는 이 대사로만 판정한다** — Debezium의 "incremental snapshotting … finished" 로그로 판정하지 않는다(스냅샷 중 오류가 나면 Debezium이 "skipping and continuing streaming"으로 남은 청크를 버리고 RUNNING인 채로 갈 수 있다 — E-R1 C 관찰) | 대사 보고 → `match` |
| 4 | 감사 로그에 남긴다 | 창 목록·건수·사유 RB 번호 |

재발행 도구는 아직 없다. 요청·감사 기록은 광고 서버 `request_resend`(계획 02 P1-9), signal 쓰기는 파이프라인 쪽 스크립트(P2-3)로 생긴다. 그 전까지는 이 표가 절차다.

## RB-1 슬롯·offset 불일치(P5·K3·C4 둘 다 유실)

```bash
python pipeline/runbooks/rb.py rb1 --connector ads-outbox-cdc            # dry-run
python pipeline/runbooks/rb.py rb1 --connector ads-outbox-cdc --apply
```

정지 → 슬롯 `wal_status` 확인(`lost`면 슬롯 삭제, 살아 있으면 그대로) → offset 삭제 → 재개(슬롯이 없으면 커넥터가 지금 위치에 `failover=true`로 새로 만든다) → 불일치가 시작된 창부터 RB-0.

## RB-2 PostgreSQL 복원·비동기 승격(P4·P2) — 새 원천 세대

```bash
python pipeline/runbooks/rb_lsn_check.py --connector ads-outbox-cdc       # 단독 점검(rc 0 offset ≤ WAL 끝, 2 거부, 3 offset 없음, 4 조회 실패) — 기록용
python pipeline/runbooks/rb.py rb2 --connector ads-outbox-cdc [--apply]
```

정지 → 재개 전 LSN 점검(결과를 기록에 남김) → 슬롯이 있으면 삭제 → offset 삭제 → 재개 → 복원 시점이 든 창부터 RB-0 → "Bronze에만 있음"(유령) 창 목록을 사건 기록으로 남기고 팀 Silver 표시 규칙(D9·D10)에 넘긴다.

- offset이 앞서면(rc=2) 반드시 지운다. 그대로 켜면 PostgreSQL이 시작 위치보다 앞 LSN의 커밋을 오류 없이 0번 보내고, Debezium도 저장 위치 이하를 걸러 조용히 빠진다.
- 앞서지 않아도 지운다. 새 슬롯은 지금 위치에서 시작하므로, 옛 offset이 남으면 R1(`trust_offset`)이 "슬롯이 앞섬"으로 멈춘다.
- **복원·승격 뒤에는 `rb.py rb2`로만 다시 켠다.** `rb_lsn_check.py`는 기록용이다. rc=0이어도 안전하다는 뜻이 아니다. 크기 비교는 타임라인이 갈린 것을 가리지 못한다. 복원 지점(승격 분기점) R이 저장 offset X보다 앞인데 새 primary가 X를 넘게 쓰면 rc=0이 나온다. 그래도 그대로 켜면 새 타임라인의 (R, X] 트랜잭션이 걸러져 빠진다.

## RB-3 Kafka 데이터 상실·꼬리 유실(K3·K4)

```bash
# 1. Bronze 정지: run-bronze.sh 에 TERM
python pipeline/runbooks/rb.py rb3 --connector ads-outbox-cdc --generation g2 \
  --bronze-checkpoint s3a://ads-lake/checkpoints/bronze_ads_outbox --topic-config retention.ms=<옛 토픽과 같게>   # dry-run(min.insync.replicas=2는 기본으로 붙는다)
python pipeline/runbooks/rb.py rb3 --connector ads-outbox-cdc --generation g2 --bronze-checkpoint s3a://ads-lake/checkpoints/bronze_ads_outbox --apply --bronze-stopped
```

정지 → 새 토픽(`<옛 토픽>.g2`, 파티션·복제·설정은 옛 토픽과 같게) → 커넥터 `transforms.route.replacement`를 새 토픽으로 → RB-1과 같은 슬롯·offset 정리 → 재개. 도구가 끝에 적는 할 일:

1. 정본 설정의 `transforms.route.replacement`를 새 토픽으로 고쳐 저장소에 남긴다(운영 `pipeline/connect/ads-outbox-cdc.json`, 시험 커넥터는 `tools/e2e_setup.py`).
2. Bronze를 새 토픽·새 checkpoint(`<checkpoint>/g2`)로 처음부터 띄운다.
3. Silver 구독에 새 토픽을 더한다. 옛 행과 새 행은 Bronze의 `topic` 열로 갈린다.
4. 상실 시점이 든 창부터 RB-0.

- 설정을 바꾼 뒤(재개 전) 끊겼으면 같은 명령을 다시 돌린다. route가 이미 그 세대이고 커넥터가 STOPPED면 토픽 확인부터 이어서 한다(설정 PUT은 건너뜀). RUNNING이면 이미 끝난 세대로 보고 거부한다.
- STOPPED 커넥터에 `PUT /connectors/{name}/config`를 해도 STOPPED가 유지된다. 설정만 바뀌고 `resume` 전까지 돌지 않는다. Connect 4.3.1 설치본에서 확인했다: REST의 설정 PUT은 목표 상태 `null`로 `putConnectorConfig`를 부르고, `KafkaConfigBackingStore`는 목표 상태가 `null`이면 설정 기록만 쓴다.
- 세대 이름 `.gN`(토픽)·`/gN`(checkpoint)은 임시다. **팀 통합 때 확정**한다(R6-2 (나)).
- signal·heartbeat 토픽도 잃었으면 같은 이름·같은 설정으로 다시 만든다. signal 소비자 그룹(`<커넥터>-signal`)의 offset이 새 토픽 끝보다 앞서 있으면 그룹을 지우고 다시 켠다.

## RB-4 Bronze checkpoint 상실·질의 모양 변경(S3·B5)

```bash
# 1. Bronze 정지: run-bronze.sh 에 TERM
BRONZE_TOPIC=<지금 토픽> BRONZE_TABLE=<표> pipeline/bronze/spark-submit-ads.sh pipeline/runbooks/rb_offsets.py
#    → RB_STARTING_OFFSETS {"<토픽>": {"0": …, "1": …, "2": …}}
BRONZE_STARTING_OFFSETS='<위 JSON>' BRONZE_CHECKPOINT=<새 checkpoint 경로> bash pipeline/bronze/run-bronze.sh
```

시작 = 파티션마다 Bronze 표의 `max(offset) + 1`이다(Bronze에 행이 없는 파티션은 earliest). 상실 시점이 든 창을 대사로 확인한다. `BRONZE_STARTING_OFFSETS`는 새 checkpoint에서만 쓰인다. checkpoint가 있으면 Spark가 무시한다.

`rb_offsets.py`는 같은 토픽인지 먼저 본다. 파티션마다 Bronze의 마지막 위치에 있는 레코드를 Kafka의 같은 위치에서 읽어 key·value를 비교한다. 다르거나 없거나, Bronze 위치가 Kafka 끝보다 앞서면 토픽이 다시 만들어진 것(K4)이다. 이때는 멈추고 RB-3으로 간다. 그 위치가 보존 밖이라 비교할 수 없으면 `UNVERIFIED` 줄을 낸다. 그때 K4 여부는 사건 사실(토픽 삭제·재생성, topic id)로 정한다.

## RB-5 Kafka 보존 만료(K5)

RB-4와 같은 명령이다. `rb_offsets.py`는 파티션마다 `max(Bronze 다음 offset, Kafka에 남은 가장 이른 offset)`을 시작으로 준다. 지워진 구간이 있으면 시작이 가장 이른 offset이 되고, 그 구간은 `EXPIRED partition=… from=… to=…` 줄로 나온다.

토픽은 그대로 둔다(offset은 다시 쓰이지 않는다). 새 checkpoint로 띄운 뒤, 만료된 구간이 든 창부터 RB-0.
