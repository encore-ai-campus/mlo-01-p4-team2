# pipeline — 광고 outbox → Debezium → Kafka → Bronze(Delta)

광고 서버가 업무 변경과 같은 트랜잭션에 쓰는 `events_outbox` 이벤트를 CDC(Debezium PostgreSQL)로 Kafka `ads.outbox.events`에 내고, Spark 상시 쿼리가 1분마다 Bronze Delta(로컬 RustFS, 운영 S3)에 원본 그대로 쌓는다. 광고 서버 코드(`ads_server/`)와는 따로 두고, 공용 요약 규칙만 광고 서버 쪽 파일을 원본으로 쓴다.

## 폴더

| 경로 | 내용 |
|---|---|
| `connect/ads-outbox-cdc.json` | Debezium 커넥터 설정 정본. 비밀번호는 Connect `FileConfigProvider` 경로 참조만 들어 있다 |
| `bronze/bronze_ads_outbox.py` | Bronze 앱(Kafka → Delta, key·value 바이트·헤더·위치만 — 해석·중복 제거는 Silver) |
| `bronze/spark-submit-ads.sh` | Spark 제출(Standalone client 모드). 의존 jar는 미리 둔 세트를 `extraClassPath`로 쓴다 |
| `bronze/run-bronze.sh` | Bronze 상시 실행(로컬). 운영 `restartPolicy: OnFailure`(최초 1회 + 재시도 3회)를 흉내 내고 잠듦을 막는다 |
| `bronze/hop_manifest_job.py` | Bronze manifest 잡(P2-1): Bronze 한 판을 공용 요약 규칙으로 창마다 요약해 S3 `manifests/hop_pipeline/`에 불변 파일 + 포인터로 게시. 광고 서버 `fetch_hop_manifest`가 받는다. 설정은 모두 환경변수(기본값 없음) |
| `bronze/run-hop-manifest.sh`, `bronze/local.pipeline.hop-manifest-{hour,day}.plist` | 그 잡의 로컬 실행(창 크기마다 잠금 하나, 1코어)과 LaunchAgent 견본(매시 :05 · 매일 00:35Z, 설치는 사용자 승인 뒤) |
| `reconcile/run-reconcile.sh`, `reconcile/local.pipeline.hop-reconcile-{hour,day}.plist` | 창 대사(P2-2, 광고 서버 `reconcile_hops`: 받기 → 같은 since로 outbox manifest → 대조·확정 기록 → 상태 파일)의 로컬 실행과 LaunchAgent 견본(매시 :07 · 매일 00:50Z) |
| `common/hop_rules.py`, `common/ndjson.py` | 광고 서버 `ads_server/events/`의 같은 이름 파일과 **바이트가 같은 사본**(요약 규칙·manifest 바이트 규칙). 원본을 바꾸는 커밋에서 함께 바꾼다(`tests/common/`이 확인) |
| `tools/` | 검증 도구(아래) |
| `monitor/` | 점검·경보(**임시 D18** — 점검 스크립트 + 1분 주기 + 웹훅). `monitor/README.md` |
| `runbooks/` | 복구 절차(RB-0~RB-5) 도구·점검표. 바꾸는 도구는 기본 dry-run. `runbooks/README.md` |
| `requirements.txt` | `pipeline/`의 Python 도구가 직접 쓰는 라이브러리(Spark 도구는 Spark 배포판의 pyspark) |
| `legacy/metaverse/` | 메타버스 파이프라인 Step1·Step2 보관본(Metaverse 쪽 소유). 재사용할 부분은 통합 설계(M2)에서 정해 본체로 옮긴다 |

## 로컬 시험대(2026-11-03)

| 부분 | 주소 |
|---|---|
| PostgreSQL 3노드(Patroni + etcd) | 쓰기는 HAProxy rw `127.0.0.1:5010`(늘 현재 primary), 읽기 ro `5011`, 상태 `http://127.0.0.1:7010/` |
| Kafka 3대 | `127.0.0.1:9092,127.0.0.1:9192,127.0.0.1:9292` |
| Kafka Connect 3대 | REST `127.0.0.1:8083`·`8183`·`8283` |
| Spark Standalone | `spark://127.0.0.1:7077` |
| RustFS | `http://127.0.0.1:9000`, 버킷 `ads-lake` |
| 의존 jar 세트 | `/opt/spark/jobs/deps/spark4.1.4-delta4.3.1-kafka4.1.4-aws3.4.2/`(`SHA256SUMS` 포함, 판이 바뀌면 새 세트 이름) |
| 이 Mac 전제 | 제출 스크립트의 `JAVA_HOME`(Homebrew openjdk@21)·`PYSPARK_PYTHON`(miniforge `spark` 환경), `c5_lib.py`의 `JAVA_HOME`. Spark 도구는 제출 스크립트로만 돌린다(드라이버가 Spark 배포판의 pyspark 4.1.4를 쓴다) |

비밀값은 저장소에 없다. 코드는 아래 파일을 읽어 연결에만 쓰고 출력하지 않는다.

- PostgreSQL 관리자: `/opt/postgresql/config/postgres-password`
- CDC 계정(`ads_cdc`): `/opt/kafka/connect/config/secrets/ads-cdc.properties`(Connect가 읽음)
- RustFS: `/opt/rustfs/config/access-key`, `/opt/rustfs/config/secret-key`(바꾸려면 `S3_ACCESS_KEY_FILE`·`S3_SECRET_KEY_FILE`)

## 쓰는 법

커넥터 설정 바꾸기(멈춘 동안 바꾸고 다시 켠다. 저장된 offset에서 이어 읽는 것은 source connector의 기본 동작이고, Source EOS는 offset과 메시지를 한 트랜잭션으로 확정해 이어 읽을 때 중복이 없게 한다):

```bash
R=http://127.0.0.1:8083/connectors/ads-outbox-cdc
curl -X PUT $R/stop
python -c 'import json; print(json.dumps(json.load(open("pipeline/connect/ads-outbox-cdc.json"))["config"]))' > /tmp/ads-outbox-cdc.config.json
curl -X PUT -H 'Content-Type: application/json' --data @/tmp/ads-outbox-cdc.config.json $R/config
curl -X PUT $R/resume
```

Bronze 실행(checkpoint 경로마다 하나만 띄운다 — 같은 checkpoint에 writer가 둘이면 Delta 로그가 꼬인다):

```bash
pipeline/bronze/run-bronze.sh            # 비정상 종료면 같은 checkpoint로 다시 띄움(최초 1회 + 재시도 3회), 다 쓰면 rc=1
kill -TERM <run-bronze.sh의 pid>          # 사람이 멈춤: Bronze를 정상 종료하고 다시 띄우지 않음
```

드라이버를 직접 죽이면(`kill -9 <SparkSubmit pid>`, 드라이버에 직접 `kill -TERM`도) 장애로 보고 다시 띄운다(재시도 1회 사용). 사람이 멈출 때는 드라이버가 아니라 `run-bronze.sh`에 TERM을 준다.

환경 변수(기본값은 로컬 시험대): `KAFKA_BOOTSTRAP`, `BRONZE_BUCKET`, `BRONZE_TRIGGER`, `S3_ENDPOINT`, `S3_REGION`, `S3_ACCESS_KEY_FILE`, `S3_SECRET_KEY_FILE`, `SPARK_MASTER`, `SPARK_CORES_MAX`, `SPARK_EXECUTOR_MEMORY`, `SPARK_DEPS`, `SPARK_LOCAL_IP`(기본 127.0.0.1), `BRONZE_TOPIC`·`BRONZE_TABLE`·`BRONZE_CHECKPOINT`(기본은 lab 토픽·표·checkpoint), `BRONZE_MAX_OFFSETS_PER_TRIGGER`(기본 240,000 — E-B4 로컬 실측(메모리 기준), P8에서 운영 장비로 다시 잼), `BRONZE_STARTING_OFFSETS`(기본 `earliest`, 새 checkpoint에서만 쓰임 — RB-4·RB-5에서 `runbooks/rb_offsets.py`가 낸 JSON), `BRONZE_RETRIES`(기본 3), `BRONZE_RETRY_INTERVAL`(기본 10초 — 팀이 아직 정하지 않은 값이라 임시값), 도구의 `PG_PORT`(기본 5010)·`PG_DB`(대사 도구의 DB, 기본 `ads_pipeline_lab`).

Bronze manifest 잡(P2-1 — 창 크기마다 동시에 하나만, 이미 돌고 있으면 rc 75로 건너뜀):

```bash
pipeline/bronze/run-hop-manifest.sh hour                 # 지금 창에서 48시간 앞부터, 이름 hop_pipeline_hour
pipeline/bronze/run-hop-manifest.sh day                  # 오늘에서 8일 앞부터, 이름 hop_pipeline_day
HOP_MANIFEST_URI=s3a://ads-lake/tmp/<시험>/manifests/hop_pipeline/ pipeline/bronze/run-hop-manifest.sh hour --since 2026-11-02T13:00:00Z --as-of 2026-11-02T15:02:00Z   # 시험·다시 요약
```

창 대사(P2-2 — 창 크기마다 동시에 하나만, 종료 코드 0 정상·1 확정 불일치·2 입력 실패·3 늦음·75 건너뜀, 상태 파일 `$HOP_RECONCILE_DIR/reconcile-<창>.json`):

```bash
pipeline/reconcile/run-reconcile.sh hour      # lab DB ↔ manifests/hop_pipeline/hop_pipeline_hour, MongoDB 구간 없음(운영 모양)
pipeline/reconcile/run-reconcile.sh day
```

종료 코드 1은 확정 불일치이거나 상태 파일을 쓰기 전에 멈춘 것이다(래퍼의 비밀 파일 없음·설정 오류·작업 폴더 오류 등). 둘은 상태 파일로 구별한다 — 그 회차의 `status`가 `mismatch`인지, `finished_at`이 갱신됐는지 본다. `--as-of` 형식 오류는 2다. outbox 창 닫힘 시각은 받은 `checkpoint.started_at`과 대사의 지금 가운데 이른 쪽이다. 그래서 잡 쪽 시계가 앞서도, 이쪽 기준으로 아직 여유 안에 있는 창은 확정하지 않는다.

하루 주기는 UTC로 정의한다(하루 잡 00:35Z, 하루 대사 00:50Z). LaunchAgent `StartCalendarInterval`은 이 Mac의 현지 시각이라 견본은 Asia/Seoul(UTC+9) 기준 09:35·09:50으로 두었다. 시간대가 다르면 Hour를 바꾼다. 운영 예약(ScheduledSparkApplication·systemd 타이머)은 UTC로 둔다.

잡의 설정(제출 스크립트가 넘긴다 — 잡 본문에는 기본값이 없다): `HOP_BRONZE_TABLE`, `HOP_MANIFEST_URI`(`/`로 끝나는 `s3a://` 접두사), `HOP_SOURCE_DB`(Debezium `source.db`), `HOP_GRACE_SECONDS`(광고 서버 `EVENTS_HOP_GRACE_SECONDS`와 같게), 선택 `HOP_MAX_PARTITION_BYTES`(기본 16m). 로컬 래퍼의 기본값은 lab 표·운영 접두사·`ads_pipeline_lab`·120초이고, 잠금 폴더는 `HOP_LOCK_DIR`(기본 `~/Library/Caches/ads-pipeline`). 시험은 Spark 없이 `python -m unittest discover -s pipeline/tests/common -t pipeline/tests`·`… -s pipeline/tests/bronze …`.

## 검증 도구(`tools/`)

Spark 도구는 제출 스크립트로 돌린다(`pipeline/bronze/spark-submit-ads.sh pipeline/tools/<도구>.py …`). 나머지는 Python으로 바로 돈다(`psycopg`·광고 서버 Django 설정이 필요).

| 도구 | 실행 | 하는 일 |
|---|---|---|
| `c3_check.py` | Spark | Bronze ↔ Kafka(`read_committed`) 위치별 바이트 대조, 중복, 배치·지연, Delta 이력 |
| `c4_bronze_dump.py <out.ndjson> [--since ISO]` | Spark | Bronze 최신 판의 위치·value 원문·적재 시각을 NDJSON으로. `--since`: 원천 생성 시각이 그 뒤인 행만(생성 시각이 없는 행 — 삭제·다른 표·툼스톤 — 은 그대로 남겨 대사가 본다) |
| `c4_reconcile.py <dump.ndjson> <out_dir> [--as-of ISO] [--window hour\|day] [--since ISO]` | Python | outbox ↔ Bronze 창별 manifest(DRD §5.6)와 대조. Bronze 쪽은 광고 서버 공용 요약 규칙 `ads_server/events/hop_rules.py`의 `summarize_changes`(P2-1 잡과 같은 함수)로 요약한다: op c(스트리밍)·r(증분 스냅샷 재발행)를 같은 문서로 세고, 같은 내용의 남는 행은 `duplicates`, after 6열이 다른 사본이 있으면 `conflicts`, c·r가 아닌 행은 `not_insert`, 놓을 수 없는 행·다른 출처 행은 checkpoint(0보다 크면 대조 실패). 비교는 outbox의 미전송을 지연 근거로 쓰지 않는다. P2-1 전 J4 판정용. **덤프와 대사에는 같은 `--since`(UTC 정시)를 준다** |
| `er1.py snap\|signal\|check` | Python | E-R1(재발행 신호 시험): 토픽 끝 offset 기록, `execute-snapshot`(INCREMENTAL, `created_at` 창) 신호, 두 기록 사이 메시지로 창별 빠짐·창 밖 op=r·스트리밍 이어짐·키 모양(`{"event_id"}`) 판정. 기본값은 e2e 이름 |
| `bronze_latency.py --since ISO --until ISO` | Spark | 원천 생성 시각이 [since, until)인 이벤트의 단계별 지연(생성→Kafka, Kafka→Bronze, 생성→Bronze) p50·p95·p99·최대, 분당 생성 건수, 적재 배치 수 |
| `c5_lib.py` | — | 아래 두 도구의 공용 관측(DB·Kafka·Connect REST·슬롯) |
| `c5_connect.py <접미사>` | Python | 커넥터 REST stop 동안 이벤트를 만들고, 슬롯 WAL·Kafka offset을 관측한 뒤 resume해서 빠짐·중복을 본다. 결과는 `$TMPDIR/ads_pipeline_lab/c5a_report.json` |
| `c5_dup.py <event_id…>` | Python | 이미 확정된 메시지를 같은 key·value로 한 번 더 발행(재전송 중복 흉내) |
| `real_events2.py <MEDIA_ROOT> <STATIC_ROOT> <접미사>` | Python | lab DB에 업무 흐름으로 이벤트를 만든다 |
| `deps_check.py` | Spark | 의존 jar 세트의 클래스 출처, RustFS Delta 쓰기·읽기(시험 경로는 지움), Bronze·Kafka 읽기 |

Django를 쓰는 도구의 `MEDIA_ROOT`·`STATIC_ROOT`는 저장소 밖(`$TMPDIR/ads_pipeline_lab/`·`$TMPDIR/ads_pipeline_e2e/`)에 둔다.

## 종단 시험 전용 환경(설계 §11.1)

lab(`ads_pipeline_lab`·커넥터 `ads-outbox-cdc`·토픽 `ads.outbox.events`)과 섞이지 않게 따로 둔다.

| 부분 | 이름 |
|---|---|
| DB | `ads_pipeline_e2e`(HAProxy 5010) |
| 커넥터 | `ads-outbox-e2e`(정본 `connect/ads-outbox-cdc.json`에서 이름·DB·슬롯·토픽만 바꿈), `topic.prefix` `adse2e`, 슬롯 `ads_e2e_slot`(failover) |
| 토픽 | `ads.e2e.outbox.events`(3), `ads.e2e.outbox.signal`(1 — signal 키는 `adse2e`), `__debezium-heartbeat.adse2e`(1) — 모두 복제 3·`min.insync.replicas=2` |
| Bronze | `BRONZE_TOPIC=ads.e2e.outbox.events BRONZE_TABLE=s3a://ads-lake/bronze_e2e/ads_outbox_events BRONZE_CHECKPOINT=s3a://ads-lake/checkpoints/bronze_e2e_ads_outbox pipeline/bronze/run-bronze.sh` |

```bash
python pipeline/tools/e2e_setup.py                 # DB·마이그레이션·권한·publication·토픽·커넥터(다시 돌려도 안전)
python pipeline/tools/e2e_load.py --duration 1800   # 광고 서버·처리기를 띄우고 초당 20쌍 유입 → J0~J3 판정(J0 시험 유효: 이벤트 8종·광고 없는 결정 ≤ 1%), 결과 JSON. 긴 정지 시험은 --give-up 을 늘린다
```

`e2e_load.py`는 처음 한 번 시드를 만들고 문맥(매체 키 포함)을 `$TMPDIR/ads_pipeline_e2e/ctx.json`(0600)에 둔다. 변경 이벤트는 정해진 간격마다 입찰 정책·입찰·캠페인 예산·일괄 슬롯 그룹 켜기/끄기를 돌아가며 낸다(이름 변경은 이벤트에 싣지 않는다).
