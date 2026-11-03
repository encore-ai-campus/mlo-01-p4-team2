# pipeline — 광고 outbox → Debezium → Kafka → Bronze(Delta)

광고 서버가 업무 변경과 같은 트랜잭션에 쓰는 `events_outbox` 이벤트를 CDC(Debezium PostgreSQL)로 Kafka `ads.outbox.events`에 내고, Spark 상시 쿼리가 1분마다 Bronze Delta(로컬 RustFS, 운영 S3)에 원본 그대로 쌓는다. 광고 서버 코드(`ads_server/`)와는 따로 두고, 공용 요약 규칙만 광고 서버 쪽 파일을 원본으로 쓴다.

## 폴더

| 경로 | 내용 |
|---|---|
| `connect/ads-outbox-cdc.json` | Debezium 커넥터 설정 정본. 비밀번호는 Connect `FileConfigProvider` 경로 참조만 들어 있다 |
| `bronze/bronze_ads_outbox.py` | Bronze 앱(Kafka → Delta, key·value 바이트·헤더·위치만 — 해석·중복 제거는 Silver) |
| `bronze/spark-submit-ads.sh` | Spark 제출(Standalone client 모드). 의존 jar는 미리 둔 세트를 `extraClassPath`로 쓴다 |
| `bronze/run-bronze.sh` | Bronze 상시 실행(로컬). 운영 `restartPolicy: OnFailure`(최초 1회 + 재시도 3회)를 흉내 내고 잠듦을 막는다 |
| `tools/` | 검증 도구(아래) |

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

환경 변수(기본값은 로컬 시험대): `KAFKA_BOOTSTRAP`, `BRONZE_BUCKET`, `BRONZE_TRIGGER`, `S3_ENDPOINT`, `S3_REGION`, `S3_ACCESS_KEY_FILE`, `S3_SECRET_KEY_FILE`, `SPARK_MASTER`, `SPARK_CORES_MAX`, `SPARK_EXECUTOR_MEMORY`, `SPARK_DEPS`, `SPARK_LOCAL_IP`(기본 127.0.0.1), `BRONZE_MAX_OFFSETS_PER_TRIGGER`(기본 10,000 — 임시값, E-B4로 정함), `BRONZE_RETRIES`(기본 3), `BRONZE_RETRY_INTERVAL`(기본 10초 — 팀이 아직 정하지 않은 값이라 임시값), 도구의 `PG_PORT`(기본 5010).

## 검증 도구(`tools/`)

Spark 도구는 제출 스크립트로 돌린다(`pipeline/bronze/spark-submit-ads.sh pipeline/tools/<도구>.py …`). 나머지는 Python으로 바로 돈다(`psycopg`·광고 서버 Django 설정이 필요).

| 도구 | 실행 | 하는 일 |
|---|---|---|
| `c3_check.py` | Spark | Bronze ↔ Kafka(`read_committed`) 위치별 바이트 대조, 중복, 배치·지연, Delta 이력 |
| `c4_bronze_dump.py <out.ndjson>` | Spark | Bronze 최신 판의 위치·value 원문·적재 시각을 NDJSON으로 |
| `c4_reconcile.py <dump.ndjson> <out_dir> [--as-of ISO] [--window hour\|day]` | Python | outbox ↔ Bronze 창별 manifest(DRD §5.6, 규칙은 `ads_server/events/hops.py`)와 대조. 중복·충돌을 따로 센다 |
| `c5_lib.py` | — | 아래 두 도구의 공용 관측(DB·Kafka·Connect REST·슬롯) |
| `c5_connect.py <접미사>` | Python | 커넥터 REST stop 동안 이벤트를 만들고, 슬롯 WAL·Kafka offset을 관측한 뒤 resume해서 빠짐·중복을 본다. 결과는 `$TMPDIR/ads_pipeline_lab/c5a_report.json` |
| `c5_dup.py <event_id…>` | Python | 이미 확정된 메시지를 같은 key·value로 한 번 더 발행(재전송 중복 흉내) |
| `real_events2.py <MEDIA_ROOT> <STATIC_ROOT> <접미사>` | Python | lab DB에 업무 흐름으로 이벤트를 만든다 |
| `deps_check.py` | Spark | 의존 jar 세트의 클래스 출처, RustFS Delta 쓰기·읽기(시험 경로는 지움), Bronze·Kafka 읽기 |

Django를 쓰는 도구의 `MEDIA_ROOT`·`STATIC_ROOT`는 저장소 밖(`$TMPDIR/ads_pipeline_lab/`)에 둔다.
