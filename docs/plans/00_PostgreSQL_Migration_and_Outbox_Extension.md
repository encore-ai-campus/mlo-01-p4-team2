# 00. 광고 서버 PostgreSQL 전환 및 Outbox 확장 계획

> **상태:** P1 완료(2026-10-31) · P2 완료(2026-11-01) — 결정 D1~D13 반영
> **작성:** 2026-10-31
> **대상:** `ads_server/` — 이 문서의 코드 경로는 모두 `ads_server/` 기준이고, 줄 번호는 체크포인트 커밋 `54f731a` 기준이다
> **근거:** 공통 레이크하우스 설계안 `temp/shared-multidomain-lakehouse-architecture.md`(저장소 밖) §3·§5·§6·§8, 광고 서버 설계 `docs/legacy/ads-server/ads-server-design.md` §6.2·§10, DRD `docs/legacy/ads-server/ads-server-drd.md` §4·§5.6
> **범위 밖:** Kafka·Debezium·Spark·Iceberg 연동, MongoDB 종료, 가격 모델·C4 Insight 구현(§11)

## 1. 목적

1. 운영 DB를 MySQL 8.4에서 PostgreSQL로 옮긴다. 기존 업무 불변식은 그대로 지킨다.
   - 업무 변경과 outbox 이벤트·감사 로그를 한 트랜잭션에 쓴다.
   - 상태 전이는 조건부 UPDATE로 한다.
   - 중복은 유일 제약으로 막는다.
   - 정해진 잠금 순서를 지킨다.
2. MySQL의 한계 때문에 넣은 우회 로직을 PostgreSQL 기능으로 바꿔 단순화한다.
3. Outbox에 정책·입찰·캠페인·광고그룹의 변경 이력 이벤트 4종을 추가한다. 모두 업무 변경과 같은 트랜잭션에서 쓴다.
4. 이후 가격 최적화(ML/DL)와 광고 분석이 붙을 때 DB 구조를 크게 바꾸지 않도록 준비한다.

## 2. 원칙

| # | 원칙 |
|---|---|
| 1 | **계약 불변.** C1(서빙)·C6(콘솔) 응답과 기존 C2 이벤트 4종(`ad.decision` v2, `ad.reaction_received`·`ad.reaction`·`ad.charge` v1)의 이름·의미·`event_id` 산출을 바꾸지 않는다. 바뀌는 것은 manifest `scope` 값(D5)과 신규 이벤트 추가뿐이다 |
| 2 | **우회 로직은 대체한 뒤 지운다.** 같은 보장을 하는 PostgreSQL 제약·기능으로 바꾸고, 동시성 시험이 통과한 뒤에 지운다(설계안 §6.2-1) |
| 3 | **데이터는 줄이지 않는다.** 줄이는 것은 쓰이지 않거나 겹치는 색인과 DB 우회 구조뿐이다(§7) |
| 4 | **이후 변경은 추가형으로.** 새 표·NULL 열로 붙일 수 있게 둔다(§8) |
| 5 | **단계마다 점검 통과.** 각 단계가 끝나면 `tests/tools/check.sh` 전체를 통과한다. 단, P1-1~P1-5는 함께 끝나야 처음 통과한다(전환 묶음) |

## 3. 결정 사항

2026-10-31 사용자 결정. D1~D9는 권장안을 채택했고, D10은 (c)를 골랐다. D11·D12는 P1-1·P1-2 검토에서 나와 같은 날 정했다(D11은 ICU `und`로 정했다가 D12에서 builtin `C.UTF-8`로 다시 정했다). D6·D9·D13은 P1-10 측정 뒤 같은 날 정했다.

| # | 항목 | 결정 |
|---|---|---|
| D1 | masters와 events가 같은 층이라 정책 이벤트를 masters에서 기록할 수 없다 | 층 정의에서 events를 foundation 바로 위 독립 층으로 내린다(events는 foundation만 import한다). 설계 §6.2 수정 |
| D2 | 캠페인 변경 이벤트의 원천 ID | 전용 추가 전용 변경 로그 표를 새로 만든다. 감사 로그 ID는 쓰지 않는다 — 변경 전후 값의 원천이 없어 정합성 점검·백필이 안 된다 |
| D3 | `accepted_key`(인정 1건 보장 키) 단순화 | 유지. 지금 방식도 PostgreSQL에서 그대로 성립한다 |
| D4 | 마이그레이션 이력 | 앱마다 PostgreSQL용 `0001_initial`을 새로 만든다 |
| D5 | 정합성 manifest의 `scope` 값 | `"mysql-repeatable-read-snapshot"` → `"postgresql-repeatable-read-snapshot"`. 과거 manifest는 원래 값 그대로 |
| D6 | 연결 수(PostgreSQL 기본 `max_connections` 100, MySQL은 151) | **운영은 기본 100 유지, 시험 compose만 200.** 운영 gunicorn은 동기 워커(`2×CPU+1`, 스레드 1)라 동시 연결 ≈ 워커 수 + 상시 프로세스 2(처리·중계) + 주기 작업으로 100보다 훨씬 적다. 시험 하네스(`load_test.py`)는 요청마다 스레드·연결 하나라 한도에 닿는다: 150 동시에서 첫 시도 503 180건(전부 `too many clients`, 재시도로 모두 성공), check.sh의 400 동시 버스트(재시도 없이 201·429만 허용)는 12회 중 1회 503 13건으로 실패했다. **요청이 늘 때의 확장 경로는 PgBouncer(transaction 모드)** — 앱 서버·워커를 늘려도 DB 연결은 풀 크기로 고정되고 요청마다 연결을 여는 비용도 없어진다. 그때 할 일은 인프라 추가·`DB_HOST` 변경과 `lock_timeout` 한 가지다: 접속 시작 매개변수(`options`)는 PgBouncer가 기본으로 거부하므로 ⓐ DB 역할 설정(`ALTER ROLE … SET lock_timeout = '5s'`)으로 옮기고 `options`를 지우거나 ⓑ PgBouncer에 `track_extra_parameters = lock_timeout`을 두고 앱의 `-c lock_timeout=5s`를 그대로 쓴다(값을 앱이 계속 쥔다. 서버가 보고하지 않는 매개변수라 시작 값만 복원되고 도중의 `SET`은 다른 클라이언트로 새지만, 앱 코드에는 `SET`이 없다) — 그때 고른다. 지금 옮기지 않는 이유: 역할 설정을 빠뜨린 DB에서는 경고 없이 무한 대기가 된다. 그 밖의 transaction 모드 조건은 지금 코드로 충족된다(P1-10 검토): 격리 수준·`SET TRANSACTION`은 트랜잭션 안, Django는 prepared statement를 쓰지 않는다(`prepare_threshold` None), `.iterator()`의 서버 쪽 커서는 모두 `consistent_read()`(atomic) 안이라 WITH HOLD가 아니다 — **atomic 밖에서 `.iterator()`를 써야 하면 그때 `DISABLE_SERVER_SIDE_CURSORS = True`**. (버린 안: `max_connections` 상향만 — 늘 때마다 다시 올려야 하고 수백 연결부터 PostgreSQL 성능이 떨어진다 / psycopg 풀 — 프로세스 안에서만 묶여 서버가 늘면 합계 문제가 남는다) |
| D7 | 신규 이벤트 payload 범위 | 정책 변경 사유(관리자 자유문)는 싣지 않고 `change_source` 코드를 싣는다. 캠페인 종료 시각(`ends_at`) 변경은 `PERIOD_CHANGED`로 기록한다 |
| D8 | 신규 이벤트 백필 | 백필 대상에서 제외하고 시작한다. 과거 이력은 설계안 §3.2.1의 별도 백필로 다룬다. **전제(P2-3 검토)**: 신규 종류의 누락 점검(`event_gaps`)·구간 대조(`hop_source`)는 원천 행 **전부**를 보므로, 기록이 시작되기 전에 만든 행(정책 버전·입찰·캠페인·광고그룹)은 이벤트가 없는 채 영구 누락으로 남는다(`export_integrity` 실패, 옛 창 대조 불일치). 지금은 운영 데이터가 없고(P1-11 해당 없음) 개발 DB도 비어 있어 성립한다 — **기존 데이터가 있는 DB에 올릴 때는 종류별 시작 시각(cutoff)을 두어 그 뒤의 행만 점검하거나, 별도 백필을 먼저 한다**(§4.2의 "이관 시점의 캠페인·광고그룹에는 `CREATED` 행이 없다"와 같은 뿌리) |
| D9 | `lock_timeout` 값 | **5s로 확정**(P1-10): 150 동시 부하와 장애 시험 5종에서 잠금 대기 초과(55P03)·교착(40P01) 0건. 503 + `Retry-After`가 계약상 재시도 경로다 |
| D10 | 변경 로그 표의 모양 | 캠페인·광고그룹 **공용 표 1개**로 하고, `ad.ad_group_changed`도 이번에 함께 구현한다. 설계안은 광고그룹 이벤트를 후속으로, 표를 2개로 제안했다(§10에서 설계안 수정) |
| D11 | PostgreSQL 정렬(collation) | builtin `C.UTF-8`(`POSTGRES_INITDB_ARGS=--locale-provider=builtin --locale=C.UTF-8` — libc 쪽 LC_COLLATE·LC_CTYPE도 C.UTF-8이라 OS의 다른 로케일에 기대지 않는다). 정렬이 코드값만 써서 ICU·glibc 버전이 바뀌어도 문자열 색인의 순서가 바뀌지 않는다(§4.3). 같음 비교는 대소문자·악센트를 구분한다(MySQL `utf8mb4_0900_as_cs`와 같다). 감수하는 것: 화면의 이름순이 코드값 순서가 된다(대문자가 소문자보다 앞, 악센트 문자는 `z` 뒤. 한글끼리는 가나다순 그대로). 처음에는 ICU `und`로 정했으나 D12에서 바꿨다. (버린 안: ICU `und` — 이름순은 MySQL과 가장 가깝지만 ICU 버전이 바뀌면 색인을 다시 만들어야 함 / 기본값 en_US.utf8 — glibc 버전에 같은 위험) |
| D13 | 판정의 질의 사이 창(`accepted_key` 경쟁) | **설계에 기록만 한다**(P1-9 검토에서 발견). `records/facts.py` `gather`의 두 조회(인정된 유형 / 먼저 접수된 같은 유형의 미처리)가 READ COMMITTED에서 서로 다른 스냅샷을 봐, 그 사이에 앞 반응의 판정이 커밋되면 둘 다 놓친다 → 뒤 반응을 인정하려다 `accepted_key` 유일 제약이 막고(과금도 함께 되돌림) 다음 재처리에서 `once-per-type`으로 거부된다. 영향은 `attempts` +1과 경고 한 줄(상한 5에 닿으려면 같은 반응이 다섯 번 연속 걸려야 한다). 빈도: check.sh 12회 중 1회, 150 동시 부하 0건(`load_test` 끝의 `failed_once`). MySQL(READ COMMITTED)에도 같은 창이 있었다. (버린 안: 이 위반을 실패로 세지 않기 — 진짜 결함이 반복되면 stuck으로 드러나지 않는다 / 같은 결정의 판정을 잠금으로 줄 세우거나 한 질의로 — 판정 경로가 바뀌고 경합이 는다) [보완 2026-11-02, 사용자] `process_reactions --once`가 실패를 종료 코드로 알리게 된 뒤(버그 탐색 B15) 이 위반만 `failed`와 따로 `contended`로 센다 — 요약에 보이고 `--once`의 종료 코드에만 넣지 않는다. 버린 안과 달리 `attempts`는 그대로 올려 stuck 탐지를 지킨다 |
| D12 | 식별자·코드·키 열의 정렬(ICU 버전 위험을 줄이는 방법) | DB 전체를 builtin `C.UTF-8`로 한다 → D11을 다시 정함. 열마다 정렬을 나누는 안(키·코드 열만 `C`)과 ICU `und` 유지 안은 버렸다. 이름순 정렬만 ICU로 하는 완화(`Collate(..., "und-x-icu")`)는 하지 않는다 |

## 4. P1 — PostgreSQL 전환

### 4.1 단계

| 단계 | 작업 | 위치 | 완료 기준 |
|---|---|---|---|
| P1-1 실행 환경 | `mysql_config` → `postgres_config`: `ENGINE` postgresql, `READ COMMITTED` 고정, `lock_timeout` 5s(D9), `CONN_MAX_AGE=0`·`connect_timeout` 유지, 기본 포트 5432. MySQL 전용 옵션(`STRICT_ALL_TABLES`, `charset`, 시험 collation) 삭제. 드라이버를 `mysqlclient`에서 `psycopg[binary]`(3)로 바꾼다. 같은 함수를 새로 쓰므로 격리 수준 비교 손잡이 삭제(부록 A-3)도 여기서 한다. 로컬 DB는 Docker 컨테이너(PostgreSQL 18·MongoDB 8.0, MySQL 8.4는 비교·이관 때만 `--profile mysql`) | `config/settings/base.py:211-240`, `dev.py:23-30`, `prod.py:39-46`, `test.py:35-41`, `.env.example`, `requirements.txt:7`, `tests/tools/pinned_requirements.py`(extras 의존성도 점검), 새 `tests/docker/compose.yaml` | 빈 PostgreSQL에 `migrate` 성공, `check --deploy` 경고 없음 |
| P1-2 시험 기반 | 시험 DB 관리 도구(`pg_admin.py`)를 만들고, MySQL에 묶인 시험·도구를 바꾼다 | 부록 C | `check.sh`가 PostgreSQL로 끝까지 실행된다(실패는 P1-3~P1-5에서 해소) |
| P1-3 DB 오류 분류 | MySQL 오류 번호를 SQLSTATE로: 유일 위반 `1062` → `23505`, 일시 오류 `{1205, 1213}` → `{55P03, 40P01}`(psycopg 3의 `__cause__.sqlstate`) | `foundation/dberrors.py:8-25`, 주석 `foundation/http.py:188`·`foundation/middleware.py:77,91` | 동시에 들어온 같은 접수가 같은 행을 돌려준다. 잠금 초과는 503 + `Retry-After`. 일시 오류는 반응 실패 횟수를 올리지 않는다(`test_db_errors`) |
| P1-4 outbox 쓰기 | `bulk_create([row], update_conflicts=True, update_fields=["event_name"])` → `bulk_create([row], ignore_conflicts=True)` | `events/outbox.py:21-22` | 같은 이벤트를 두 번 써도 한 줄이고 내용이 바뀌지 않는다(`test_event_outbox`) |
| P1-5 스냅샷 읽기 | `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ`를 트랜잭션 밖이 아니라 `atomic()` 안의 첫 문장으로(`REPEATABLE READ, READ ONLY`). 거절 규칙(`ConsistentReadUnavailable`)·`quiescent`는 유지 | `billing/services.py:322-343` | 점검 중 다른 연결이 커밋한 행이 결과에 섞이지 않는다(`test_ledger_check`). **여기까지가 전환 묶음 — `check.sh` 첫 전체 통과**(pip-audit 제외 — 기준선에도 있던 실패라 P1-12에서) |
| P1-6 우회 구조 단순화 | 생성 열 2개 → 조건부 유일 제약, 2질의 잠금 경로 2곳 → 1질의(선택 1곳 추가). 격리 수준 손잡이(A-3)는 P1-1에서 처리 | 부록 A | 경쟁 시험(`test_campaign_races`, `test_receipt_ordering`, `test_media_lifecycle_ordering`, `test_billing_ledger`, `test_campaigns_review`) 통과. InnoDB 전용 시험 둘은 PostgreSQL 동작을 보는 시험으로 바꾼다: 기간 종료의 '잠금 조건은 기본 키뿐' → 두 기간 종료가 겹쳐도 교착 없이 한 번씩, 없는 결정 접수의 '`FOR UPDATE` SQL 없음' → 잠근 행 0개. 생성 열 삭제 마이그레이션은 임시 `0002`(P1-8에서 `0001`에 합친다) |
| P1-7 색인 정리 | 겹치는 FK 색인 8개, `Decision.media_id`·`Creative.image_name` 단일 색인 삭제. 선택 B-4·B-5·B-6도 함께(`username`·`Media.name`의 `_like`는 Django 인증 규칙에 닿아 남김) | 부록 B | 해당 질의가 남은 색인을 쓰는지 질의 계획(`EXPLAIN`)으로 확인. **주의(실제로 겪음):** FK의 `db_index`를 끄는 `AlterField`는 그 열 하나로 된 색인을 모두 지운다 — 같은 열 하나짜리 부분 유일 색인(`billing_one_signup_grant_per_wallet`)도 지워졌다(`sqlmigrate`에는 안 보임, DB 제약 시험이 잡음). 임시 마이그레이션에서 FK 변경을 제약 추가보다 앞에 두었다 |
| P1-8 마이그레이션 재생성 | 앱마다 PostgreSQL용 `0001_initial`(P1-6·P1-7 반영, D4). 임시 `0002`·`0003`을 지운다. 개발 DB는 다시 만든다 | `*/migrations/0001_initial.py` | 빈 DB에서 `migrate` 뒤 `makemigrations --check` 변화 없음, 두 부분 유일 색인(`billing_one_signup_grant_per_wallet`·`campaigns_one_open_review_per_campaign`)이 있음, 표·색인·제약 목록이 P1-7 상태와 같음 |
| P1-9 명칭·계약 값·정렬 버전 | manifest `scope` 값 변경(D5). "MySQL" 문구·주석 정리. 정렬 버전 관리: 이미지 태그 고정(§4.3 V-1), 점검에 모든 DB의 정렬 버전 검사(V-2) | `records/management/commands/export_integrity.py:12`, `export_hop_manifests.py:15`, `console/management/commands/ops_check.py:6,116,123`, `foundation/http.py:24-26`(주석만), `records/backfill.py:3` | `test_event_hops:299`·`test_event_ndjson:393` 갱신 뒤 통과 |
| P1-10 재측정 | 150 동시 부하(`tests/tools/load_test.py`)·장애 시험(`tests/tools/failure_test.py`). 다른 세션이 DB 서버를 쓰지 않을 때 돌리고 그 사실을 기록한다. `REJECTED`이면서 `attempts > 0`인 반응 수도 센다(판정의 두 조회 사이 창 → `accepted_key` 유일 제약이 막는 경쟁의 빈도, P1-9 검토) | `tests/tools/` | 유실 0 · 이중 과금 0 · 잔액 = 원장 · `RECEIVED` 잔류 0. 503 건수를 기록하고 D6·D9를 확정(P1-9 점검 첫 실행에서 400 동시 결정 중 503 13건 — `too many clients`). **충족(2026-10-31)**: 150 동시 불변식 52개, 장애 시험 5종 모두 통과 — 수치는 D6·D9·D13 |
| P1-11 데이터 이관 (운영 데이터가 있을 때만) | **해당 없음**(사용자 2026-10-31: 옮길 운영 데이터 없음). MySQL → PostgreSQL 데이터 이관. **새 DB에 `0001`을 적용(migrate)한 뒤 앱 표의 데이터만 옮긴다** — `django_migrations`·`django_content_type`·`auth_permission`은 옮기지 않는다(새 DB가 post_migrate로 만든 것을 쓴다) | — | 표마다 건수·ID 집합 일치, `check_ledger` 0건 |
| P1-12 의존성 취약점 (실행: 전환 묶음 커밋 직후) | pip-audit 실패 해소(사용자 결정 2026-10-31). 기준선(MySQL, 체크포인트)에도 있던 실패: `Pillow` 12.3.0(고침 13.0.0), `certifi` 2026.7.22(고침 2026.10.18, 전이 의존성) | `requirements.txt`의 `Pillow`, 설치 환경의 `certifi` | `check.sh`의 pip-audit 통과, 소재 이미지 검증 시험(`tests.ops.test_assets_validation` 등) 통과. **`check.sh` 전체 통과** |

### 4.2 데이터 이관 시 주의 (P1-11)

- **마이그레이션 이력:** P1-8에서 `0001_initial`을 같은 이름으로 다시 만들었으므로 Django는 옛 `0001`과 새 `0001`을 구별하지 못한다. 옛 `0001`만 적용된 DB는 '적용됨'으로 보고 새 `0001`을 돌리지 않아 스키마가 **조용히 어긋난다**(생성 열·FK 색인이 남고 부분 유일 색인이 없음). 그래서 이관은 새 DB에 `0001`을 적용한 뒤 데이터만 옮기고, P1-8 이전 마이그레이션으로 만든 PostgreSQL DB는 재사용하지 않고 다시 만든다(로컬은 `docker compose … down -v`).
- Django는 MySQL에 `UUIDField`를 하이픈 없는 `char(32)`로 저장하고, PostgreSQL에는 `uuid`로 저장한다. 이관할 때 변환한다.
- `DATETIME(6)`(UTC)은 `timestamptz`로, `BIGINT UNSIGNED`는 `bigint`로 옮긴다. 금액은 모두 `MAX_AMOUNT`(2^53 − 1) 이하인지 확인한다.
- 생성 열(`signup_grant_wallet`, `open_campaign`)은 P1-6에서 없어지므로 복사하지 않는다.
- 정렬: D11의 builtin `C.UTF-8`로 운영 DB를 만든다(`initdb --locale-provider=builtin --locale=C.UTF-8` 또는 `CREATE DATABASE … LOCALE_PROVIDER builtin LOCALE 'C.UTF-8' TEMPLATE template0` — `LOCALE`을 주어 서버 template0의 libc 설정을 물려받지 않게). 화면의 이름순이 바뀐다: MySQL(`utf8mb4_0900_as_cs`)은 알파벳순이고 같은 글자 안에서만 소문자→대문자인데, `C.UTF-8`은 코드값 순서라 대문자 전체가 소문자보다 앞에 온다(§4.3).
- 이관 시점에 이미 있는 캠페인·광고그룹에는 변경 로그 `CREATED` 행이 없다. 이력의 시작점은 D8의 별도 백필에서 다룬다.

### 4.3 정렬 버전 관리 (D11)

**확인한 내용 — PostgreSQL 18 공식 문서(2026-10-31 확인)**

"ICU 정렬 규칙이 바뀌면 기존 인덱스가 새 정렬 규칙과 어긋날 수 있다"는 말은 사실이다. 공식 문서는 "인덱스 손상(corrupt indexes)"이라고 더 강하게 쓴다.

- [ALTER COLLATION — Notes](https://www.postgresql.org/docs/18/sql-altercollation.html#SQL-ALTERCOLLATION-NOTES):
  > A change in collation definitions can lead to corrupt indexes and other problems because the database system relies on stored objects having a certain sort order. Generally, this should be avoided, but it can happen in legitimate circumstances, such as when upgrading the operating system to a new major version or when using `pg_upgrade` to upgrade to server binaries linked with a newer version of ICU. When this happens, all objects depending on the collation should be rebuilt, for example, using `REINDEX`. When that is done, the collation version can be refreshed using the command `ALTER COLLATION ... REFRESH VERSION`. … Note that this does not actually check whether all affected objects have been rebuilt correctly.
  - 정렬을 만들 때 제공자의 버전을 카탈로그에 기록하고, 쓸 때 지금 버전과 다르면 경고한다. DB 기본 정렬에는 [`ALTER DATABASE … REFRESH COLLATION VERSION`](https://www.postgresql.org/docs/18/sql-alterdatabase.html)이 있다.
- [amcheck — F.1.3](https://www.postgresql.org/docs/18/amcheck.html): 정렬 규칙이 바뀌면 B-tree의 순서 불변식이 깨져 **색인 검색이 틀린 답을 낼 수 있다**. 이런 불일치는 주 서버와 복제본의 OS 메이저 버전이 다를 때 흔하고, 대개 복제본에서만 생기고 복제본에서만 찾을 수 있다. amcheck는 손상이 있음은 증명해도 없음은 증명하지 못한다.
- 이 위험은 ICU만의 것이 아니다. libc 정렬(이미지 기본값 `en_US.utf8`)도 glibc가 바뀌면 같다.
- builtin 제공자 — [Locale Providers §23.1.4](https://www.postgresql.org/docs/18/locale.html): "The `C.UTF-8` locale … The collation uses the code point values only. … the case mapping is the "simple" variant." [Collation Support §23.2.2.1](https://www.postgresql.org/docs/18/collation.html)의 같은 정렬(`pg_c_utf8`)은 "stable within a PostgreSQL major version"이다 — 메이저 버전에서 바뀔 수 있는 것은 대소문자 변환·정규식 문자 분류다. 순서가 메이저 버전에서도 같다는 것은 문서가 직접 말하지는 않고, "코드값만 쓴다"는 정의에서 나오는 추론이다.

**결정과 그 결과 (D11·D12)**

DB 전체를 builtin `C.UTF-8`로 만든다. 그래서 ICU·glibc가 바뀌어도 문자열 색인 — 과금 1회(`reaction_ref`)·인정 1건(`accepted_key`) 같은 안전망 유일 제약을 포함해 — 의 순서가 어긋나지 않는다. 컨테이너에서 확인: 모든 DB(`ads_server`·`postgres`·`template1`)가 provider `b`, locale·LC_COLLATE·LC_CTYPE `C.UTF-8`, 정렬 버전 `1`(기록 = 실제).

- 바뀌지 않는 것: 같음·유일 제약(대소문자·악센트 구분), 대소문자 무시 검색(관리 화면의 `icontains` — 유니코드 대소문자 변환이라 `upper('é')` = `É`). 표현식 색인은 실제 DB에 0개다.
- 감수하는 것 — 이름순이 코드값 순서가 된다: 예) `11번가 < Naver < _test < kakao < Ébène < 광고A < 광고a < 네이버` (ICU `und`였다면 `_test < 11번가 < Ébène < kakao < Naver < 광고a < 광고A < 네이버`). 한글끼리는 가나다순 그대로다. 영향 받는 곳: 운영자 매체 목록(`masters.list_media`), 구성 목록(`masters.list_slots` — 운영자 화면과 광고주의 광고 만들기·광고 상세 구성 선택), Django 관리 화면에서 이름·제목·아이디 열로 정렬할 때, `events/hops.py:183`의 이벤트 종류 순서(파이썬 `sorted`와 같아진다).
- 덤: 비교가 코드값 비교라 빠르고, 일반 B-tree 색인이 `LIKE 'abc%'`도 처리하므로 Django가 만드는 `_like` 색인은 쓸모가 없다(부록 B-5).

**남는 위험과 대책**

남는 것은 ① PostgreSQL 메이저 업그레이드 때 대소문자 변환·정규식 문자 분류가 바뀔 수 있다는 것(그것을 쓰는 표현식 색인·CHECK가 생기면 다시 만들어야 한다)과 ② 정렬 버전이 기록과 달라지는 경우다. PostgreSQL 15부터 `CREATE DATABASE`는 template의 정렬 버전이 실제와 다르면 DB를 만들지 않으므로, 그때는 시험 DB 생성(`test_<DB_NAME>`)·`pg_admin.create_database`·`measure.ensure_db`가 모두 실패한다.

| # | 할 일 | 내용 | 단계 |
|---|---|---|---|
| V-1 | 이미지 태그 고정 | compose와 `failure_test.py`의 이미지를 같은 태그로 고정한다: `postgres:18.6-trixie`(태그 있음 확인). 정렬 위험이 아니라 재현성 때문이다. 운영·복제본·복원 서버도 같은 PostgreSQL 메이저로 맞춘다 | P1-9 |
| V-2 | 감지 | `tests/tools/pg_admin.py` 점검과 운영 점검(`ops_check`의 PostgreSQL 항목 — `check_collations` 명령, 따로 돌릴 수도 있다)이 **모든 DB**(적어도 현재 DB와 `template1`)의 `datcollversion` = `pg_database_collation_actual_version(oid)`인지 본다(기록이 없는 `template0`은 보지 않는다 — PostgreSQL과 같다). `check_collations`는 운영 DB에서 공식 문서의 `pg_collation` 대조 질의에 걸리는 정렬이 없는지도 본다(`pg_collation`은 DB마다 따로라 `postgres` DB에 붙는 `pg_admin`에서는 의미가 없다). 다르면 실패. `template1`이 builtin `C.UTF-8`인지는 `pg_admin` 점검이 이미 본다(P1-2). 운영 PostgreSQL을 다른 서비스와 같이 쓰게 되면 DB 검사를 `current_database()`·`template1`·`postgres`로 좁힌다 — 남의 DB 불일치(또는 이 서버에 없는 로케일)로 우리 점검이 실패하지 않게(배포 구성·D6 때) | P1-9 |
| V-3 | 표현식 색인 규칙 | `lower()`·`upper()`·정규식을 쓰는 표현식 색인·CHECK를 새로 만들면 이 절에 적어 메이저 업그레이드 때 다시 만들 목록에 올린다. 지금은 0개 | 코드 규칙 |
| V-4 | 검증 | 메이저 업그레이드 뒤와 정기 점검 때 문자열 색인에 amcheck `bt_index_check(index, heapallindexed => true, checkunique => true)`를 돌린다. **복제본에서도** 돌린다. `CREATE EXTENSION amcheck`는 DBA 권한이다 | 운영 절차 |
| V-5 | 복구 절차 | 아래 순서. 로컬 Docker DB는 데이터를 버려도 되면 `docker compose … down -v` 후 다시 만드는 것이 가장 간단하다 | 운영 절차 |

**정렬 버전 불일치를 발견했을 때 (V-5)**

1. 쓰기를 멈출지 정한다 — 유일 색인이 어긋난 동안 같은 키가 들어올 수 있다(특히 `reaction_ref`). 처리 프로세스(`process_reactions`)를 먼저 세운다.
2. 영향 받는 색인을 다시 만든다: `REINDEX DATABASE CONCURRENTLY ads_server`(또는 해당 색인만 `REINDEX INDEX CONCURRENTLY …`). `CONCURRENTLY`가 실패하면 INVALID 색인 `<이름>_ccnew`가 남으므로 `DROP INDEX`로 지운 뒤 다시 한다. 유일 색인을 다시 만들다 실패하면 이미 중복이 들어온 것이다 — 중복 행을 찾아 정리(원장이면 사람 확인)한 뒤 다시 한다.
3. amcheck로 확인한다(V-4).
4. 그 뒤에 버전을 갱신한다: `ALTER DATABASE ads_server REFRESH COLLATION VERSION`, 그리고 `template1`·`postgres`도(사용자 객체가 없으므로 다시 만들 것 없이 갱신한다 — 갱신하지 않으면 시험 DB를 만들 수 없다). 공식 문서 질의에 걸린 정렬은 `ALTER COLLATION … REFRESH VERSION`. **순서가 중요하다** — 갱신은 다시 만들었는지 확인하지 않고 경고만 없앤다.
5. 무엇을 언제 다시 만들었는지 기록한다.

## 5. P2 — Outbox 확장

### 5.1 신규 이벤트

| 이벤트 | `source_id` | `aggregate_id`(메시지 키) | payload |
|---|---|---|---|
| `ad.bid_policy_changed` v1 | `bid_policy_id` | `slot_id` | `slot_id`, `bid_policy_id`, `policy_version`, `floor_amount`, `base_amount`, `cap_amount`, `impression_share`, `click_share`, `conversion_share`, `changed_at`, `change_source`, `insight_id` |
| `ad.bid_added` v1 | `bid_id` | `ad_group_id` | `bid_id`, `ad_group_id`, `bid_version`, `amount`, `created_at` |
| `ad.campaign_changed` v1 | `change_id` | `campaign_id` | `campaign_id`, `change_id`, `change_type`, `previous_value`, `new_value`, `changed_at` |
| `ad.ad_group_changed` v1 | `change_id` | `ad_group_id` | `ad_group_id`, `campaign_id`, `change_id`, `change_type`, `previous_value`, `new_value`, `changed_at` |

- (2026-11-04: Kafka 메시지 키는 `event_id`로 바뀌었다 — 설계 §10 C2. `aggregate_id`는 대상 키로 남는다.)
- `event_id`는 기존 규칙대로 `event_id_of(event_name, source_id)`(UUIDv5)로 만든다.
- `change_source`: `policy_version = 1`이면 `INITIAL`, 나머지는 `MANUAL`이다. 지금은 DB 열 없이 계산한다. `insight_id`는 지금은 항상 `null`이다(§8.2).
- 싣지 않는 것: 정책 변경 사유·`changed_by`(관리자 자유문·사람 연결), 캠페인 중단 사유, 캠페인·광고그룹 이름(표시용). 광고주 계정·잔액·요청 원문도 싣지 않는다.

### 5.2 변경 로그 표 `campaigns_change_log` (신규, D2·D10)

캠페인·광고그룹의 변경 1회 = 행 1개다. 이 행의 ID가 `ad.campaign_changed`·`ad.ad_group_changed`의 원천 ID다. 그래서 같은 엔터티를 두 번 바꿔도 두 이벤트가 충돌하지 않는다(설계안 §3.2).

| 열 | 내용 |
|---|---|
| `change_id` | PK, UUIDv7 |
| `target_type` | `campaign` · `ad_group` (CHECK) |
| `target_id` | 캠페인 ID 또는 광고그룹 ID. FK가 아니다(이력 행은 `Decision`·`AuditLog`처럼 값으로 가리킨다) |
| `campaign_id` | 항상 채운다(광고그룹 행은 소속 캠페인). 광고주 범위로 좁힐 때 쓴다 |
| `change_type` | 캠페인: `CREATED`·`STATUS_CHANGED`·`BUDGET_CHANGED`·`PERIOD_CHANGED` / 광고그룹: `CREATED`·`ENABLED_CHANGED`·`SLOTS_CHANGED`·`CREATIVE_SELECTED`. 대상에 맞는 값만(CHECK `campaigns_change_log_kind`, P2-5) |
| `previous_value`·`new_value` | JSON. 바뀐 업무 필드만 담는다. `CREATED`의 `previous_value`는 NULL, 그 밖에는 NULL이 아니다(CHECK `campaigns_change_log_previous_unless_created`, P2-5). 형식은 `events.change_value` |
| `changed_at` | 변경 시각 |
| 색인 | `(target_type, changed_at)` — 정합성 점검의 원천 스트림이 종류·시간 창마다 읽는다 |

- `AppendOnlyModel`로 만든다(R-5). 값이 **실제로 바뀐 때만** 행을 쓴다. 같은 값으로 저장하면 행이 생기지 않는다.
- 쓰는 함수는 campaigns 안에 하나만 둔다(예: `record_change(...)`). 이 함수가 로그 행과 outbox 이벤트를 같은 트랜잭션에 함께 쓴다. `transitions.py`와 `services.py`는 이 함수만 부른다.

### 5.3 단계

| 단계 | 작업 | 위치 | 완료 기준 |
|---|---|---|---|
| P2-1 층 규칙 | events를 foundation 바로 위 독립 층으로 옮기고, masters 이상 층 번호를 한 칸씩 올린다(D1) | `tests/dev/boundaries/test_layers.py:10-16` | 층 시험 통과 |
| P2-2 events 공통 | 이름 4개 + `SCHEMA_VERSIONS` 각 v1 + payload 함수 4개. `record_*` 4개와 `NAMES`·`__all__`. `aggregate_id` 주석을 "종류별 키"로 고친다(열 추가 없음 — `aggregate_type`은 Debezium 라우팅을 정할 때). **지킬 점(P2-1 검토)**: ① `record_*`·payload 함수는 **값만 받는다**(키워드 인자 — UUID·정수·문자열·시각, §5.1 payload 필드 그대로). ORM 객체를 넘기면 import 없이도 events가 위 모듈의 모델 모양에 묶이고 층 시험이 잡지 못한다 ② `change_source`(`policy_version = 1` → `INITIAL`, 나중에 `insight_id`가 있으면 추천)는 **`events.schemas`의 payload 함수가 값으로 계산**한다 — C2 스키마 변환은 events에서만(설계 §6.1), 규칙을 한 곳에 | `events/schemas.py:9-13`, `events/api.py:11-31`, `events/models.py:15` | payload 모양 시험 |
| P2-3 `ad.bid_policy_changed` | 슬롯 생성 시 v1, 정책 버전 추가 시(단건·일괄 공통) 기록. 정책의 `changed_at`이 이벤트 생성 시각이 되므로 `create_slot`·`add_policy_version`·`add_policy_version_bulk`에 `now=None`을 둔다(캠페인 서비스와 같은 방식 — 기본값이면 동작은 그대로, 시험 세계는 고정 시각) | `masters/services.py:140-168` `create_slot`, `masters/services.py:171-191` `_add_version` | 아래 공통 완료 기준 |
| P2-4 `ad.bid_added` | 광고그룹 생성 시(입찰액이 있을 때 v1), 입찰 추가 시 기록. `add_bid`는 안쪽 저장점 뒤, 바깥 트랜잭션 안에서 쓴다 | `campaigns/services.py:514` `create_ad_group`, `campaigns/services.py:578-583` `add_bid` | 공통 완료 기준 |
| P2-5 변경 로그 | §5.2의 모델과 `record_change`(`campaigns/changes.py` — services가 transitions를 import하므로 순환을 피해 따로 둔다). 로그·이벤트 쓰기는 이 단계에서 넣고, 부르는 곳·정합성 원천·백필 제외는 P2-6·P2-7(이벤트가 실제로 생기는 단계). **값 형식은 events가 정한다(P2-2 검토)**: `events.schemas`에 변경 값 형식기(예: `change_value(**fields)` — UUID → 문자열, 시각 → `_iso`(UTC ISO, 마이크로초), UUID 목록은 정렬한 문자열 목록, 정수·불리언·문자열은 그대로)를 두고 api로 내보낸다. `record_change`는 그 결과를 변경 로그에 저장하고 **같은 값**을 `record_*`에 넘긴다 — 로그 = 이벤트(정합성 원천과 같음)이고 C2 형식은 events 한 곳(설계 §6.1). JSONField에 시각 객체를 그대로 넣으면 `DjangoJSONEncoder`가 밀리초·`Z` 형식으로 바꿔 같은 이벤트 안에서 형식이 갈린다 | `campaigns/models.py`, campaigns 새 함수 | 추가 전용 시험(`test_append_only_all_models`), 모델 규약 시험 통과 |
| P2-6 `ad.campaign_changed` | §5.4 기록 지점 | `campaigns/services.py`, `campaigns/transitions.py` | 공통 완료 기준. `tr.apply`를 트랜잭션 밖에서 부르던 시험(`test_campaigns_transitions.py:39-59`)은 트랜잭션 안으로 고친다 — **확인(P2-6)**: `TestCase`라 이미 트랜잭션 안이어서 고칠 것이 없었다 |
| P2-7 `ad.ad_group_changed` | §5.5 기록 지점 | `campaigns/services.py` | 공통 완료 기준 |
| P2-8 백필 제외 | 신규 4종은 백필 분기를 만들지 않고 제외를 명시한다(D8). **실행은 종류마다 그 이벤트 단계에서**(P2-2 검토): `records/backfill.py`는 `SOURCE_KINDS`에 없는 종류가 한 줄이라도 있으면 manifest 전체를 거부하므로(`InvalidInput`), `records/integrity.py` `_sources`에 신규 원천을 더하는 단계(P2-3·P2-4·P2-6·P2-7)에서 `SOURCE_KINDS`와 제외("백필 대상 아님"으로 세고 건너뜀)를 같은 커밋에 넣는다 — 그러지 않으면 신규 종류 누락이 섞인 `event_gaps`로는 기존 4종의 백필도 돌릴 수 없다. P2-8은 남은 확인(시험·문구)만. **한 것(P2-8)**: 이벤트 종류 전체 = 백필 원천 종류표 = 누락 점검이 훑는 원천 종류, 백필하는 종류는 처음 넷뿐임을 시험이 지킨다(`case_every_event_kind_is_either_backfilled_or_explicitly_excluded` — 새 종류를 더하고 표를 잊으면 실패) | `records/backfill.py:25, 90-91, 101-110` | `test_event_backfill` 통과 |
| P2-9 문서 | §10 | — | **완료(2026-11-01)** — §10 머리의 반영 목록 |

**정합성 점검은 이벤트와 같은 단계에서 넣는다.** outbox 쪽은 이벤트 종류를 동적으로 읽는다(`events/hops.py:179-184`). 그래서 원천 쪽 스트림이 없으면 새 종류의 첫 이벤트부터 `verify_hops`가 불일치(`line_missing_in_a`, `events/hopcheck.py:140`)를 보고한다. 이벤트를 추가하는 단계(P2-3·P2-4·P2-6·P2-7)마다 다음을 함께 넣는다.

- `records/hops.py:35-41` `source_streams`에 원천 스트림 추가: `masters_bid_policy.changed_at`, `campaigns_bid.created_at`, 변경 로그 `changed_at`(`target_type`별). masters·campaigns `api.py`에 읽기 함수를 둔다.
- `records/integrity.py:16-28` `_sources`에 누락 점검 원천을 추가한다. **같은 단계에서** `records/backfill.py`의 `SOURCE_KINDS`에 그 종류를 더하고 백필 제외(D8)로 센다(P2-8 행 참고 — 순서가 바뀌면 백필 전체가 막힌다).
- MongoDB 검증기(`events/store.py:27-33`)는 `event_name`이 자유 문자열이라 바꿀 것이 없다. 중계도 새 종류를 그대로 옮긴다.

**이벤트 단계의 공통 완료 기준**

1. 업무 변경과 이벤트가 함께 커밋되고 함께 되돌려진다. 이벤트 쓰기 실패를 주입하면 업무 변경도 남지 않고, 업무 쓰기가 실패하면 이벤트도 없다.
2. 같은 원천은 같은 `event_id`를 만든다. 같은 엔터티를 두 번 바꾸면 서로 다른 `event_id`가 생긴다.
3. 값이 바뀌지 않은 저장은 변경 로그와 이벤트를 만들지 않는다(변경 로그 쓰는 이벤트만).
4. `export_hop_manifests` → `verify_hops`에서 그 종류가 일치한다.
5. `check.sh` 전체 통과.

### 5.4 `ad.campaign_changed` 기록 지점

| 변경 | 위치 | 값 | 메모 |
|---|---|---|---|
| 생성 → `CREATED` | `campaigns/services.py:191-193` `create_campaign` | `status`, `daily_budget`, `starts_at`, `ends_at` | |
| 상태 → `STATUS_CHANGED` | `campaigns/transitions.py:47-78` `apply` 한 곳 | `status`(조기 종료는 `ends_at`도) | 상태를 쓰는 곳은 이 파일뿐이다. 일시 중지·재개(`services.py:263`), 조기 종료(`:279`), 중단(`:317`), 해제(`:339`)가 모두 여기를 지난다 |
| 기간 종료 → `STATUS_CHANGED` | `campaigns/transitions.py:80-100` `expire_due` → `services.py:301-308` | `status` | 일괄 UPDATE라 지금은 이전 상태를 모른다. **한 것(P2-6)**: 잠근 행의 `status`를 모아 `expire_due` 안에서 바로 기록한다 — 상태를 쓰는 파일(transitions)이 변경 로그도 쓰므로 반환값(바뀐 ID 목록)은 그대로 둔다 |
| 일예산 → `BUDGET_CHANGED` | `campaigns/services.py:253-268` `update_campaign` | `daily_budget` | 같은 요청에서 상태도 바뀌면 행이 2개다(상태는 `apply`가 쓴다) |
| 종료 시각 → `PERIOD_CHANGED` | 같은 곳 | `ends_at` | D7 |

### 5.5 `ad.ad_group_changed` 기록 지점

| 변경 | 위치 | 값 | 메모 |
|---|---|---|---|
| 생성 → `CREATED` | `campaigns/services.py:498-516` `create_ad_group` | `enabled`, `slot_ids`(정렬), `creative_id`(NULL) | 광고 만들기(`create_ad`, `:749-780`)도 여기를 지난다. 같은 트랜잭션에서 소재가 붙으면 뒤이어 `CREATIVE_SELECTED`가 생긴다 |
| 켜기·끄기 → `ENABLED_CHANGED` | `campaigns/services.py:552-555` `update_ad_group` | `enabled` | |
| 게재 위치 → `SLOTS_CHANGED` | `campaigns/services.py:556-560` `update_ad_group` | `slot_ids`(정렬) | 지금은 기존 행을 지운 뒤에 남은 목록을 읽는다. 지우기 **전에** 현재 목록을 읽어 `previous_value`로 쓴다. `AdGroupSlot`은 지금처럼 현재 상태만 두고, 이력은 변경 로그가 갖는다. **한 것(P2-7)**: 지운 뒤 읽던 질의를 지우기 전으로 옮겨 새로 넣을 것도 그 목록으로 가린다 — 질의 수는 그대로 |
| 소재 선택 → `CREATIVE_SELECTED` | `campaigns/services.py:682-684` `_link` | `creative_id` | 선택이 바뀌는 곳은 여기 하나다(`create_creative:671`·`link_creative:697`이 부른다). 소재가 없을 때의 자동 선택도 포함한다 |

## 6. 바꾸지 않는 장치 — PostgreSQL에서도 그대로 성립

| 장치 | 위치 | 근거 |
|---|---|---|
| 조건부 UPDATE(상태 비교 후 교체) | `records/processing.py` `_finish`·`_hold`, `masters/services.py` `set_media_active`·`rotate_media_key` | PostgreSQL READ COMMITTED는 잠금을 기다린 뒤 새 행 버전으로 WHERE를 다시 평가한다. 늦은 쪽은 0행을 바꾼다 — MySQL과 같다 |
| 업무 + outbox 같은 트랜잭션 | `events/outbox.py:15-16`(트랜잭션 밖이면 예외), `records/services.py` `decide` 의 `atomic(savepoint=False)`(2026-11-01 전에는 `_atomic_if_needed`) | DB와 무관 |
| `IntegrityError` 처리 11곳 | `records/services.py:191,247`, `billing/services.py:104,174`, `masters/services.py:93,166,183,303`, `campaigns/services.py:194,360,581,774`, `foundation/accounts.py:50` | 모두 `try` 안에 자기 `atomic()`(저장점)이 있다. PostgreSQL은 실패한 문장 뒤 트랜잭션 전체를 중단하지만, 저장점까지만 되돌려지므로 안전하다. **새 코드도 이 형태를 지킨다** |
| 공유 잠금 원문 SQL | `masters/services.py:126-137` `media_active_for_share`(`FOR SHARE`) | 같은 문법·같은 의미(`FOR SHARE`는 `UPDATE`와 충돌) |
| `select_for_update(skip_locked=True)` | `events/relay.py:88` | 지원 |
| 단일 SELECT 집계 | `records/report.py:62` | READ COMMITTED는 문장 단위 스냅샷 |
| 조건 없는 UNIQUE + NULL 키 | `reaction_ref`, `adjust_key`, `request_key`(매체 키·캠페인·광고그룹) | 기본이 NULL끼리 서로 다른 값 |
| `nulls_first` 정렬, `TruncDate(tzinfo=UTC)`, `JSONField` | `records/processing.py` `process_pending`, `billing/budget_day.py:32-36` | ORM이 PostgreSQL SQL로 만든다 |
| 요청 JSON 숫자 상한 | `foundation/http.py:24-26` `MAX_JSON_NUMBER` | 계기는 MySQL 저장 오류였지만 지금은 계약(금액 상한 = JS 안전 정수)이다. 주석만 고친다 |
| 추가 전용 가드 | `foundation/models.py` `AppendOnly*` | 앱 계층 가드. DB 트리거로 강화하는 것은 범위 밖 |

## 7. DB 단순화 기준과 남기는 데이터

**기준:** 가격 최적화·광고 분석이 나중에 다시 필요로 할 데이터(열·이력·스냅샷)는 줄이지 않는다. 다시 추가해야 할 것을 지금 지우는 것은 단순화가 아니다. 줄이는 것은 쓰이지 않거나 다른 색인이 이미 덮는 색인(부록 B)과 DB 우회 구조(부록 A)뿐이다. 색인은 데이터가 아니라서 필요해지면 다시 만들 수 있고, 분석은 운영 DB가 아니라 Gold 테이블에서 읽는다(설계안 §2.1).

**단순화처럼 보이지만 남기는 것**

| 대상 | 단순화처럼 보이는 이유 | 남기는 이유 |
|---|---|---|
| `BidPolicy.base_amount` | 광고 선택·과금에 쓰이지 않는다(입찰 화면 기본값만) | 가격 모델의 첫 제어 대상(설계안 §5.2·§9) |
| `Decision`의 스냅샷 열(`bid_amount`, `applied_amount`, 과금 비율 3개, `bid_version`, `policy_version`, `owner_id`, `audience`, `snapshot`) | 입찰·정책·슬롯·캠페인과 조인하면 다시 구할 수 있다 | 과금 근거(재계산 금지), C2 `ad.decision` v2 payload, 결정 당시의 가격 피처, 광고주별 접근 통제. 조인으로 바꾸면 과금·백필·정합성 점검 경로가 모두 늘어난다 |
| `Decision.trace`(`picked`, `weight`, `total`, `candidates`), `candidate_count`, `rejected_counts`, `selection_policy` | `selection_policy`는 `trace["policy"]`와 같은 값 | `weight / total`은 그 광고가 뽑힐 확률이다 — 가격 정책을 오프라인으로 평가할 때 필요하다. `trace["candidates"]`(필터 통과 수)와 `candidate_count`(입력 수)는 다른 값이다. `selection_policy`는 실험군을 거르는 열이다 |
| `Decision.context` | 저장만 하고 해석하지 않는다 | 매체·지면별 분석의 원천(설계안 §3.2.1) |
| `Reaction`의 `raw`, `occurred_at`, `subject_id`(보고), `person_id`(판정), `reason_code`, `applied_rule`, `basis` | 판정이 끝나면 읽지 않는 값이 많다 | Bronze 원문, 시각 의미 구분(설계안 §2.3), 거부 품질 분석, 퍼널 단위(결정 × 사람) |
| `LedgerEntry`의 `shortfall`, `charge_policy`, `created_at`(= 접수 시각) | — | 실제 차감액·부족분·예산 일 기준 |
| 추가 전용 이력(`BidPolicy`, `Bid`, `MediaDeactivation`, `AdGroupCreative`, `CreativeImage`, `LedgerEntry`, `Decision`, `AuditLog`) | 화면은 대부분 현재 값만 쓴다 | 정책·입찰 시계열이 가격 학습 데이터다(R-5) |
| 색인 `records_decision_owner_idx`, `records_reaction_decision_idx`, `records_reaction_received_idx`, `records_reaction_processed_idx` | — | 광고주 대시보드(`records/report.py`)와 정합성 점검의 시간 창 읽기. 분석이 Gold로 옮겨 가기 전까지 유지 |
| `Campaign.ended_from` | 변경 로그에 이전 상태가 남는다 | 콘솔 계약의 필수 필드. 화면이 로그를 조회하게 바꾸면 오히려 복잡해진다 |
| `Creative.image_bytes` | 서버 코드는 읽지 않는다(보유량은 `CreativeImage`로 계산) | 화면 데이터(`CreativeView`)에 실려 프론트로 나간다(`foundation/presentation.py:47-49`). 프론트가 쓰는지 이 저장소에서는 확인할 수 없다 |
| `Wallet.balance`, `LedgerEntry.seq`·`balance_after` | 원장 합계로 구할 수 있다 | 잔액 잠금 대상, 결정 조립의 한 번 조회, 원장 목록 커서(계약), 연속성 점검 |
| `Reaction.accepted_key` | 조건부 유일 제약으로 바꿀 수 있다 | D3 |

## 8. 가격 최적화·광고 분석 대비

### 8.1 이미 갖춰져 있는 것

- 정책·입찰이 **추가 전용 버전 행**이다. 모델 추천을 반영하는 일은 새 `BidPolicy` 행 하나를 넣는 것이다. 표 구조는 그대로다.
- 추천을 적용할 때의 버전 확인이 이미 있다. `_add_version(expected_version)`의 `StaleVersion`은 설계안 §5.4의 "`observed_policy_version`이 현재와 다르면 거부"와 같은 규칙이다.
- 추천값 범위를 DB가 이미 막는다: `floor ≤ base ≤ cap` CHECK(`masters_policy_floor_base_cap_order`).
- 결정마다 적용된 정책·입찰 버전 ID와 선택 확률(`trace`)이 남는다. 정책 변경 전후의 성과 비교와 오프라인 평가가 가능하다.

### 8.2 이번에 맞춰 두는 것 — 나중에 계약·표를 다시 바꾸지 않으려고

| 내용 | 이유 |
|---|---|
| `ad.bid_policy_changed` v1에 `change_source`(`INITIAL`·`MANUAL`, 나중에 `RECOMMENDATION`)와 `insight_id`(지금은 `null`) | 추천으로 바뀐 정책과 수동 변경을 구분해야 추천 성과를 잰다. 지금 넣어 두면 추천 기능을 붙일 때 v2로 올리지 않는다(종류별 버전 규칙, `events/schemas.py:4`) |
| 변경 로그 공용 표 + `ad.ad_group_changed` | 광고그룹 켜기·끄기·게재 위치·소재 선택은 후보 공급량을 바꾸는 사실이라 가격 피처와 광고 분석이 모두 쓴다. 지금부터 쌓아야 과거 구간이 생긴다(D10) |
| `ad.campaign_changed`의 `PERIOD_CHANGED` | 송출 기간도 공급량 피처다(D7) |

### 8.3 나중에 붙일 것 — 모두 추가형

PostgreSQL은 기본값 없는 NULL 열 추가가 표를 다시 쓰지 않는다. 그래서 큰 표(`records_decision`)에서도 바로 끝난다.

| 시점 | 추가 | 형태 |
|---|---|---|
| 가격 모델 C4 | Insight 저장(설계안 §5.4: `insight_id`, `kind`, `model_version`, `valid_from`/`valid_until`, `payload`, 승인 상태) | 새 표 |
| 추천 적용 | `BidPolicy.insight_id`(NULL 허용). `change_source` 계산에 `insight_id IS NOT NULL → RECOMMENDATION`을 더한다. 추천이 정책·입찰 버전을 자동으로 쓰기 시작하므로 구간 대조의 원천 스트림 색인 `masters_bid_policy(changed_at)`·`campaigns_bid(created_at)`도 이때 둔다(P2-3·P2-4는 작은 표라 색인 없이 훑는다) | NULL 열, 색인 2개 |
| 가격 실험 | `Decision` 실험군 열(설계안 §3.2.1 `pricing_experiment_id`·`experiment_variant`) + `ad.decision` 다음 판(**v4** — v3는 대상 그룹 타기팅이 먼저 쓴다, 2026-11-08) | NULL 열, 이벤트는 다음 판 |
| 기본 입찰 제안의 효과 측정(설계안 §5.2 (a)) | `Bid`에 입찰 때 보여 준 기본값 | NULL 열 |
| **대상 그룹 타기팅**(사용자 2026-11-08 — 설계서 §10 "대상 그룹 타기팅", 구현 전) | 대상 그룹 `masters_segment`·판 `masters_segment_version`(추가 전용)·광고그룹-대상 그룹 `campaigns_ad_group_segment`, `Decision` 열 넷(`segment_id`·`segment_active`·`target_segment_ids`·`segment_rule`), 변경 로그 종류 `SEGMENTS_CHANGED`(CHECK 목록을 넓힘), `ad.decision` v3·`ad.segment_changed` v1. 가격 피처: 그룹 × 슬롯별 공급·경쟁(그룹 때문에 빠진 후보는 `rejected_counts`의 `segment_not_targeted`) | 새 표 셋, NULL 열 넷, CHECK 하나 넓힘, R1 CHECK 추가(대상 그룹 열 묶음) |

## 9. 위험과 대응

| 위험 | 대응 |
|---|---|
| 잠금·격리 의미 차이로 경쟁 결함이 생긴다 | 우회 로직은 시험 통과 뒤 제거(P1-6). 경쟁 시험 전체와 150 동시 부하·장애 시험 재측정(P1-10) |
| `lock_timeout`을 주지 않으면 잠금 대기가 끝나지 않는다(PostgreSQL 기본 0 = 무한) | P1-1에서 지정, P1-3 시험으로 503 + `Retry-After` 확인 |
| `SET TRANSACTION` 위치를 그대로 두면 정합성 점검이 오류 없이 틀린다 | P1-5 |
| 연결 수 한도(PostgreSQL 기본 100) | P1-10 측정 → D6(운영 100 유지·시험 200, 확장은 PgBouncer) |
| 이름순 목록의 순서가 바뀐다(collation) | D11(builtin `C.UTF-8`)로 감수한다 — 대문자 먼저·악센트 문자 뒤, 한글은 가나다순 그대로. 시험 DB부터 맞췄고 운영 DB도 같은 값으로 만든다(§4.2) |
| 정렬 라이브러리가 바뀌어 문자열 색인이 어긋난다(공식 문서: 인덱스 손상) | builtin `C.UTF-8`은 코드값 순서라 해당하지 않는다. 메이저 업그레이드·버전 불일치에 대비해 이미지 고정·버전 검사·복구 절차(§4.3) |
| 신규 이벤트가 정합성 점검을 깨뜨린다 | 원천 스트림·누락 점검을 이벤트와 같은 단계에 넣는다(§5.3) |
| 새 코드가 `IntegrityError`를 저장점 없이 잡는다 | §6 규칙, 리뷰에서 확인 |
| 마이그레이션이 부분 유일 색인을 조용히 지운다(FK `db_index`를 끄는 `AlterField`가 같은 열 하나짜리 색인을 모두 지움) | P1-7에서 겪음. 마이그레이션 순서로 피하고, P1-8 재생성 뒤 색인 목록을 대조. 이후 스키마 변경 때도 DB 제약 시험(`test_billing_ledger` 등)으로 확인 |

## 10. 문서 반영

**P1 몫은 2026-10-31 반영했다**(P1 마무리): 설계 §6.5-6·7(DB 설정·연결 한도), §10 "PostgreSQL 전환 점검"(옛 "MySQL 전환 점검")·"그룹 복제(InnoDB Cluster) 전제 — MySQL 때 기록"·"운영 서버 실행 환경"(동기 워커)·"시험 구성"(ops_check ③)과 지금 구조를 MySQL로 적던 문장, DRD 0.55, README "DB 시험 환경"(지운 MySQL·클러스터 도구 안내 삭제), 위협 모델 T22·T25·T37·"DB 단일 노드·연결 한도". 역사 기록(Step DB·MySQL 때 측정·구현 계획·load-test.md)은 그대로 두었다. **P2 몫은 2026-11-01 반영했다**(P2-9): 설계 §6.2 층 정의(D1, P2-1), §6.3 outbox를 쓰는 변경·메시지 키 종류별·소비자 순서 `(changed_at, change_id)`·누락 점검 원천과 D8 기존 행 전제, §6.3a 백필 결과 `excluded`, §10 "C2 이벤트 모양"(여덟 종류·메시지 키·싣지 않는 것)과 새 행 "PostgreSQL 전환·Outbox 확장 결정"(D1~D13), DRD 0.56(O10 변경 로그 표·CHECK 둘·R-5·§4.4·§6, P3 원천·메시지 키, §5.6 신규 넷과 정렬 규칙·백필 범위, §5.9), 레이크하우스 설계안 §3.2·§3.3·§3.6(저장소 밖 `temp/`). 위협 모델·계약 README는 인용한 시험 이름이 바뀌지 않아 그대로다.

| 문서 | 반영 |
|---|---|
| 광고 서버 설계(현재 `docs/legacy/ads-server/ads-server-design.md`) | §6.2 층 정의(D1), §10 "MySQL 전환 점검"(:834 — DB-1b 의 계산 열 구현)을 PostgreSQL 기준(조건부 유일 제약 = 부분 유일 색인)으로, 결정 D1~D12 기록 |
| 설계 §6.3a 백필 6번·DRD `backfill_events` 행(P2-3) | 결과 종류에 `excluded`(백필하지 않는 종류 — 정책·입찰·캠페인·광고그룹 변경, D8)를 더한다. 누락 목록에 섞여 있어도 목록 전체를 거부하지 않는다 |
| 설계 §6.3 이벤트와 outbox(P2-1 검토) | "결정·반응·과금이 저장되는 같은 트랜잭션에서 outbox 행을 쓴다" → 정책·입찰·캠페인·광고그룹 변경도, "`aggregate_id`(= `decision_id`)"·"Kafka 토픽(메시지 키 `decision_id`)" → 종류별 키(§5.1) |
| 광고 서버 DRD(현재 `docs/legacy/ads-server/ads-server-drd.md`) | 표 목록에 `campaigns_change_log`(열·색인과 CHECK 두 개 `campaigns_change_log_kind`·`campaigns_change_log_previous_unless_created` — P2-5), C2 이벤트 표에 신규 4종 — 변경 이벤트는 "소비자는 `(changed_at, change_id)`로 정렬한다, 전달 순서에 기대지 않는다"(같은 요청의 여러 줄은 `changed_at`이 같고 `event_id`(UUIDv5)는 시간 순서가 아니라 중계 순서가 임의다, P2-6 검토). 생성 열을 물리 구현으로 적은 곳을 조건부 유일 제약(부분 유일 색인 — `pg_constraint`가 아니라 색인이라 지울 때는 `DROP INDEX`)으로: :395(물리 구현 ② GeneratedField 설명), :592·:598(O6 `open_campaign`), :639·:642(B2 `signup_grant_wallet`), :736·:737(§4.4 무결성 표) |
| 공통 레이크하우스 설계안(`temp/`) | §3.2 광고그룹 이벤트를 권장 범위로·표는 공용 1개, §3.3 트랜잭션 불변식에 "광고그룹 변경 + 변경 로그 + `ad.ad_group_changed`" 추가, §3.6 Topic 표에 `ad.ad_group_changed`(Topic 계약을 정할 때) |
| 위협 모델·계약 README | 인용한 시험 이름이 바뀌면 함께 고친다 |
| 광고 서버 DRD의 저장소 한계 | `docs/legacy/ads-server/ads-server-drd.md:1191` "MySQL 단일 노드 … `max_connections`(기본 151)" → PostgreSQL 100·확장 경로(D6) |
| 설계 §10 "운영 서버 실행 환경" | gunicorn 워커 종류는 동기(스레드 1), 연결 합계(워커 + 상시 처리·중계 + 주기 작업)는 `max_connections`(100)에서 여유를 둔 값 이하 — 스레드·워커·서버를 늘리면 D6을 다시 본다(P1-10 검토: `--threads N`을 켜면 연결이 워커 × N이 되어 계산이 조용히 깨진다) |
| 광고 서버 README·설계의 배포 후 점검 | `ops_check`의 ③ 항목 이름 "MySQL" → "PostgreSQL"(마이그레이션·원장 대조에 정렬 버전 추가, P1-9): `docs/legacy/ads-server/README.md:106`, 설계 §10 "시험 구성"(:901) |

## 11. 범위 밖·후속

- **Kafka 전환 때 바꿀 곳:** `events/relay.py:84-96`(성공 뒤 `sent_at` UPDATE), `events/management/commands/purge_sent_events.py`(보낸 지 N일 뒤 DELETE), `events/relay.py:107-132`(MongoDB 대조). 확장은 Debezium 일반 CDC로 한다(2026-11-02 결정, 설계서 §6.3). publication을 INSERT 전용으로 만들어 이 UPDATE·DELETE가 Kafka로 흐르지 않게 한다. → **[2026-11-03 D8·D17, 사용자] 정해졌다**: `sent_at` UPDATE·MongoDB 대조는 운영에 없다(로컬 병행만). `purge_sent_events`는 창 대사 확정 기준의 창 단위 정리로 바뀐다(설계서 §6.3 게이트 1·§6.3a, 구현 계획 §8.4.26).
- PgBouncer 도입(D6): 요청이 늘어 앱 서버·워커를 늘릴 때. `lock_timeout`·`idle_in_transaction_session_timeout`(60초, 2026-11-02 버그 탐색 K3) 전달 방식을 함께 정한다.
- 반응이 많이 쌓인 DB 에 서비스 중 색인을 더할 때(예: `records/0002_reaction_queue_idx`, 2026-11-02): `AddIndex` 는 `CREATE INDEX`(동시 아님)라 만드는 동안 표 쓰기가 막힌다 — 트래픽이 적은 때 올리거나 `AddIndexConcurrently`(atomic=False 마이그레이션)로 바꾼다. 운영 데이터가 없는 지금은 그대로 둔다.
- PostgreSQL 복제·HA [결정 2026-11-03 D12, 사용자 — 파이프라인 설계 문서]
  - 구성: 3대 + Patroni(etcd 3대) + HAProxy(rw/ro, 상태 검사 2초·3번, 재시작 직후 첫 검사 전까지 DOWN, PostgreSQL 연결 유휴 제한 7일 + keepalive).
  - 동기 복제: `synchronous_mode: quorum`, `synchronous_node_count: 1`, strict 끔.
  - 논리 슬롯: 장애 전환 슬롯(`slot.failover`·`sync_replication_slots`), Patroni `ignore_slots`에 CDC 슬롯(필수 — 장애 전환 표시가 꺼진 슬롯은 Patroni가 지운다).
  - 보호 목록: `synchronized_standby_slots`를 노드별 갱신 스크립트가 2초 주기 + 승격 콜백으로 "지금 붙어 복제받는 대기 서버"로 유지.
  - 운영 배치: 홈서버 노트북 3대에 한 대씩.
  - 장애 시험: 파이프라인 계획 P5(E-P2·E-P2b·E-P3). 광고 서버 쪽 `failure_test.py`의 클러스터 시나리오는 그 결과를 보고 다시 정한다.
- `masters.create_slot`의 `except IntegrityError`(P2-3 검토, 선택): 블록 안의 모든 무결성 오류를 "같은 이름의 구성" 409로 바꾼다 — 같은 블록의 감사·outbox 쓰기 결함(CHECK·NOT NULL)도 이름 충돌로 보인다. `is_unique_violation`이 아니면 다시 올리도록 고친다. 실패 방식이 바뀌므로 따로 한다.
- C4 Insight 저장·`ad.insight_recorded`, `ad.decision` 다음 판(v4 — v3는 대상 그룹 타기팅, 2026-11-08): 가격 모델 단계(§8.3).

---

## 부록 A. 우회 구조 단순화 상세 (P1-6)

| # | 위치 | 지금(MySQL 우회) | 바꾼 뒤 | 우선 | 확인 시험 |
|---|---|---|---|---|---|
| A-1 | `billing/models.py:45-50, 59` | 조건부 유일 제약이 없어(W036) 가입 지급 줄에만 지갑 ID를 갖는 **생성 열** `signup_grant_wallet` + 조건 없는 UNIQUE | `UniqueConstraint(fields=["wallet"], condition=Q(entry_type="SIGNUP_GRANT"), name="billing_one_signup_grant_per_wallet")`, 생성 열 삭제 | 권장 | `test_billing_ledger`, `test_append_only_all_models` |
| A-2 | `campaigns/models.py:156-165` | 같은 이유로 열린 요청에만 캠페인 ID를 갖는 생성 열 `open_campaign` | `UniqueConstraint(fields=["campaign"], condition=Q(status="OPEN"), name="campaigns_one_open_review_per_campaign")`, 생성 열 삭제 | 권장 | `test_campaigns_review`, `test_campaign_races` |
| A-3 | `config/settings/base.py:211-236`, `dev.py:29`, `prod.py:45` | `DB_ISOLATION_LEVEL` 비교 손잡이 — MySQL `REPEATABLE READ`의 간격 잠금 교착을 측정하려고 둔 것 | READ COMMITTED 고정, 손잡이 삭제(PostgreSQL의 REPEATABLE READ는 동시 갱신 시 `40001`로 실패하므로 운영값으로 쓰지 않는다) | 권장 — **P1-1에서 처리** | `test_settings:104-122` |
| A-4 | `records/ordering.py:31-45` `lock_for_receipt` | InnoDB가 `FOR UPDATE`의 조건을 평가하기 전에 행을 잠가서, 잠금 없는 소유 확인 후 PK로만 잠금(2질의) | `Decision.objects.select_for_update().filter(pk=decision_id, media_id=reporting_media_id)` 1질의 — PostgreSQL은 조건에 맞지 않는 행을 잠그지 않는다. 결정은 추가 전용이라 `media_id`가 바뀌지 않는다. 반응 접수 경로 질의 1회 감소 | 권장 | `test_receipt_ordering`, `test_media_lifecycle_ordering` |
| A-5 | `campaigns/transitions.py:80-100` `expire_due` | InnoDB 보조 색인 잠금 교착을 피하려고 잠금 없는 후보 조회 → PK로 잠금 → 재확인(2질의) | `select_for_update().filter(ends_at__lte=now).exclude(status=ENDED).order_by("pk")` 1질의. `is_expired` 재확인은 유지(종료 판정은 engine 한 곳) | 권장 | `test_campaign_races:157-260`, `test_campaigns_ended` |
| A-6 | `records/processing.py:146-163` `_wake_dependents` | 조건 UPDATE가 커밋 전 접수 행을 기다릴까 봐 후보 조회 후 PK로 UPDATE(2질의) | 조건 UPDATE 1질의 — PostgreSQL의 UPDATE는 커밋 전 INSERT를 보지 않는다 | 선택(낮음) — **하지 않음**(P1-6): 이득은 질의 1회이고, 깨우기는 판정 결과에 영향이 없는 최적화라 바꿀 위험만 남는다 | `test_funnel_rules`(보류·깨우기) |

## 부록 B. 색인 정리 상세 (P1-7)

| # | 위치 | 지금 | 필요 없는 이유 | 바꿀 내용 | 우선 |
|---|---|---|---|---|---|
| B-1 | FK 8개: `masters/models.py:43` `MediaDeactivation.media`, `:53` `Slot.media`, `:104` `BidPolicy.slot`, `campaigns/models.py:69` `AdGroupSlot.ad_group`, `:101` `AdGroupCreative.ad_group`, `:118` `CreativeImage.creative`, `:132` `Bid.ad_group`, `billing/models.py:34` `LedgerEntry.wallet` | PostgreSQL에서는 Django가 FK마다 색인을 자동으로 만든다(MySQL InnoDB에서는 만들지 않아 지금은 없다) | 같은 열로 시작하는 복합 UNIQUE·색인이 이미 있다(`masters_media_deact_at_idx`, `masters_slot_name_per_media`, `masters_policy_version_per_slot`, `campaigns_ad_group_slot_unique`, `campaigns_ad_group_creative_unique`, `campaigns_creative_image_unique`, `campaigns_bid_version_per_ad_group`, `billing_entry_seq_per_wallet`) | 각 FK에 `db_index=False` | 권장 — 원장은 과금마다 INSERT |
| B-2 | `records/models.py:16` `Decision.media_id` | `db_index=True` 단일 색인 | `Index(media_id, decided_at)`의 앞 열이라 겹친다 | `db_index=True` 삭제 — 광고 요청마다 INSERT되는 표에서 색인 1개 감소 | 권장 |
| B-3 | `campaigns/models.py:86` `Creative.image_name` | `db_index=True` 단일 색인 | 소재를 이미지 이름으로 찾는 질의가 없다(서빙 판정은 `CreativeImage.image_name`을 본다) | 삭제 | 권장 |
| B-4 | `foundation/models.py:131` `AuditLog.actor_id` | `db_index=True` 단일 색인 | 코드·관리 화면 모두 이 열로 찾지 않는다 | 삭제(사고 조사 때 DB 직접 조회가 느려질 뿐) | 선택 |
| B-5 | `foundation/models.py:133` `AuditLog.action`, `campaigns/models.py:119` `CreativeImage.image_name` | `db_index=True` — PostgreSQL은 문자열 열에 `LIKE` 전용 `_like` 색인을 하나 더 만든다 | 접두 검색 질의가 없다(시험에만). 게다가 D11(builtin `C.UTF-8`)에서는 일반 B-tree 색인이 `LIKE 'abc%'`도 처리하므로 `_like` 색인은 쓸모가 없다. `unique=True`인 `User.username`·`Media.name`에도 `_like` 색인이 생긴다(실제 DB 확인) | `Meta.indexes=[models.Index(...)]`로 옮긴다(같은 색인, `_like` 없음). `unique=True` 두 열은 `UniqueConstraint`로 옮길 수 있는지 함께 본다(`username`은 Django 인증의 `USERNAME_FIELD`) | 선택 |
| B-6 | `foundation/models.py:48` `User.session_epoch` | `db_default=0` — 옛 마이그레이션 상태의 INSERT 호환용 | `0001`을 새로 만들면(D4) 옛 코드가 PostgreSQL에 INSERT할 일이 없다 | `db_default` 삭제 | 선택(낮음) |

## 부록 C. 시험·도구 교체 (P1-2)

| 대상 | 위치 | 바꿀 내용 |
|---|---|---|
| 시험 DB 컨테이너·관리 | `tests/docker/innodb-cluster/`, `tests/tools/mysql_admin.py`, `tests/tools/mysql_cluster.py`, `tests/tools/cluster_check.sh`, `tests/dev/test_mysql_admin.py`, `tests/dev/test_cluster_check_script.py`, `tests/dev/test_docker_cli.py` | PostgreSQL 컨테이너·관리 도구로. 클러스터(HA) 구성은 HA를 정한 뒤 |
| 측정·부하·장애 | `tests/tools/measure.py:45-62, 85, 240, 312`(`CREATE DATABASE … COLLATE`), `tests/tools/load_test.py`, `tests/tools/failure_test.py` | PostgreSQL SQL로 |
| 세션 변수 | `tests/ops/test_settings.py:104-122`(`@@sql_mode`·`@@transaction_isolation`·`innodb_flush_log_at_trx_commit`) | `SHOW`·`current_setting`. 내구성은 `synchronous_commit`·`fsync` |
| 세션 격리 수준 — **P1-5 에서** | `tests/ops/test_ledger_check.py:66-91`(`SET SESSION TRANSACTION ISOLATION LEVEL …` — PostgreSQL 에서는 `SET TRANSACTION` 과 같은 뜻이라 트랜잭션 밖에서는 경고만 내고 바뀌지 않는다) | `consistent_read` 변경과 함께 |
| 잠금 대기 짧게 | `tests/ops/test_operator_advertisers.py:731-738`, `tests/ops/test_receipt_ordering.py:195-196`(P1-2 에서 바꿈), `tests/ops/test_db_errors.py:121-122`(**P1-3 에서**, 같은 파일의 오류 흉내와 함께) | `SET innodb_lock_wait_timeout = 1` → `SET lock_timeout = '1s'` |
| 오류 흉내 — **P1-3 에서** | `tests/ops/test_db_errors.py:23-43`(`MySQLdb`, `OperationalError(1213)`), `tests/ops/test_ops_logging.py:30, 83, 90, 94`, `tests/ops/test_records_processing.py:293` | psycopg 예외 + SQLSTATE(`dberrors` 변경과 함께) |
| 범위 문자열 | `tests/ops/test_event_hops.py:299`, `tests/ops/test_event_ndjson.py:393` | D5 값으로 |
| 이름 | `tests/ops/test_concurrency_mysql.py` → `test_concurrency_db.py`, `tests/support/concurrency.py` | 파일 이름·주석 |
