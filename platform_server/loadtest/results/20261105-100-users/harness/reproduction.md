### 저장된 원본 수치 검산

이 검산에는 DB, 로그인 쿠키, 실행 중인 서버가 필요하지 않습니다.
산출물 폴더와 Python 3만 있으면 됩니다.

```bash
python3 loadtest/results/20261105-100-users/harness/recalculate.py
```

이 명령은 파일 해시, 혼합 부하 지표, 요청별 P95와 HTTP 오류 수를 다시 확인합니다.
`AssertionError` 없이 결과를 출력하면 보관한 원본과 집계가 일치합니다.

### 새 부하 실험의 전제

아래 명령은 이번 macOS 로컬 환경을 재현하는 절차입니다.
다른 장비에서는 하네스의 고정 경로와 가상환경을 먼저 맞춰야 합니다.
일반 PostgreSQL 이미지로 바꾸면 같은 환경의 비교 실험이 아닙니다.

- 소스: `/private/tmp/platform-event-pr-53`, 커밋 `a747e7980848b4c5b677f5fc132b7dd103e8ce40`.
- Python: `/private/tmp/platform-event-postgres-venv/bin/python`. 버전은 `versions.json` 참고.
- 로컬 이미지: `platform-pipeline-patroni:17.7-4.1.0`.
- 비교 서버 설정: `/private/tmp/platform-recipe-server-state.json`. 파일 내용은 산출물에 포함하지 않음.
- 55479·8055·9055 포트가 비어 있어야 함. 실제 8004·9003 서버는 사용하지 않음.
- 매번 새 테스트 DB를 사용함. 기존 테스트 컨테이너와 계정 DB를 재사용하지 않음.

### 새 실행 순서

아래 명령은 서버 프로젝트 루트에서 순서대로 실행합니다.
기능 검사의 비정상 종료는 실패 결과입니다. 기록한 뒤 다음 독립 검사는 계속 실행합니다.
준비 단계가 실패하면 해당 부하는 시작하지 않습니다.

```bash
TASK_PYTHON=/private/tmp/platform-event-postgres-venv/bin/python
TASK_HARNESS="$PWD/loadtest/results/20261105-100-users/harness"
TASK_OUTPUT=/private/tmp/platform-load100-20261105

# 소스 작업본이 없는 새 로컬 환경에서만 실행합니다.
git worktree add --detach /private/tmp/platform-event-pr-53 a747e7980848b4c5b677f5fc132b7dd103e8ce40

docker run --rm -d --name platform-load100-20261105 --user postgres \
  -p 127.0.0.1:55479:5432 --entrypoint /bin/bash \
  platform-pipeline-patroni:17.7-4.1.0 -lc \
  '/usr/lib/postgresql/17/bin/initdb -D /tmp/load100-data -A trust -U event_test > /tmp/initdb.log && printf "host all all 172.17.0.1/32 trust\n" >> /tmp/load100-data/pg_hba.conf && exec /usr/lib/postgresql/17/bin/postgres -D /tmp/load100-data -c listen_addresses=* -c shared_buffers=128MB -c max_connections=100'

"$TASK_PYTHON" "$TASK_HARNESS/setup.py"
"$TASK_PYTHON" "$TASK_HARNESS/fixtures.py" prepare
"$TASK_PYTHON" "$TASK_HARNESS/fixtures.py" fullbank
"$TASK_PYTHON" "$TASK_HARNESS/collect_environment.py"
"$TASK_PYTHON" "$TASK_HARNESS/monitor.py" > "$TASK_OUTPUT/monitor.log" 2>&1 &

"$TASK_PYTHON" "$TASK_HARNESS/fixtures.py" casual
"$TASK_PYTHON" "$TASK_HARNESS/mixed.py" --users 100 --behavior real --client new --procs 2 --ramp 15 --warmup 15 --measure 180 > "$TASK_OUTPUT/stress100-final.log" 2>&1
"$TASK_PYTHON" "$TASK_HARNESS/fixtures.py" casual
"$TASK_PYTHON" "$TASK_HARNESS/mixed.py" --users 100 --behavior walk --moving 0.2 --client new --procs 2 --ramp 15 --warmup 15 --measure 180 > "$TASK_OUTPUT/casual100-final.log" 2>&1

"$TASK_PYTHON" "$TASK_HARNESS/restart.py"
# 이 초기화가 토큰도 다시 발급합니다. 성공 직후 scenarios를 실행합니다.
"$TASK_PYTHON" "$TASK_HARNESS/fixtures.py" reset_scenarios
"$TASK_PYTHON" "$TASK_HARNESS/scenarios.py" > "$TASK_OUTPUT/scenarios.log" 2>&1
"$TASK_PYTHON" "$TASK_HARNESS/restart.py"
"$TASK_PYTHON" "$TASK_HARNESS/npc.py" > "$TASK_OUTPUT/npc.log" 2>&1
"$TASK_PYTHON" "$TASK_HARNESS/restart.py"
"$TASK_PYTHON" "$TASK_HARNESS/private_poke.py" > "$TASK_OUTPUT/private-poke.log" 2>&1
"$TASK_PYTHON" "$TASK_HARNESS/fixtures.py" audit
"$TASK_PYTHON" "$TASK_HARNESS/regression.py"

# 기본 P95와 별도로 진단을 실행합니다.
"$TASK_PYTHON" "$TASK_HARNESS/restart.py" --diagnostic
"$TASK_PYTHON" "$TASK_HARNESS/query_probe.py"
"$TASK_PYTHON" "$TASK_HARNESS/fixtures.py" casual
LOAD_OUTPUT_TAG=diagnostic "$TASK_PYTHON" "$TASK_HARNESS/mixed.py" --users 100 --behavior real --client new --procs 2 --ramp 10 --warmup 10 --measure 60 > "$TASK_OUTPUT/diagnostic-load.log" 2>&1
"$TASK_PYTHON" "$TASK_HARNESS/persistence.py" > "$TASK_OUTPUT/persistence.log" 2>&1

"$TASK_PYTHON" "$TASK_HARNESS/metrics.py"
"$TASK_PYTHON" "$TASK_HARNESS/build_report.py"
```

`setup.py`가 환경·접속 파일을 만들며 외부에 공개하면 안 됩니다.
`collect_environment.py`가 장비·패키지·DB 설정 파일을 생성합니다.
`fixtures.py fullbank`가 문제 개수 파일을 생성합니다.
`monitor.py`가 `resources.jsonl`을 생성합니다.
진단 재시작과 40개 조회·60초 부하가 `diagnostics.jsonl`을 생성합니다.
진단 전에는 이전 `diagnostics.jsonl`이 없는 새 출력 폴더를 사용해야 합니다.
`metrics.py`는 위 결과와 감사·회귀·재시작 파일을 읽습니다.
집계 전 필요한 파일이 없으면 성공 결과로 대체하지 않고 중단합니다.

새 실행의 회귀 결과가 바뀌면 보고서의 회귀 설명도 갱신해야 합니다.
경로 계산 탐색 검사는 최종 재측정에 필수인 단계가 아닙니다.
해당 탐색의 기존 원본은 `exploratory-navigate`에 별도로 보관했습니다.
