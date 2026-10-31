#!/usr/bin/env python
"""DB-1d 장애 시험(로컬): 지속 유입 중에 DB 를 재시작·강제 종료하거나 처리 프로세스를 강제 종료하고, **약속이 지켜지는지** 확인한다.

    python tests/tools/failure_test.py                       # 단일 노드 PostgreSQL 의 모든 시나리오
    python tests/tools/failure_test.py restart crash         # 골라서
    python tests/tools/failure_test.py --json out.json

환경: **임시 PostgreSQL 18 컨테이너와 임시 MongoDB 8.0 레플리카셋 컨테이너**를 따로 띄운다(공식 이미지 `postgres:18`·`mongo:8.0`, 포트·비밀번호는 무작위).
공유 시험 DB(`ads_server`)나 다른 사람이 쓰는 컨테이너는 건드리지 않는다. 끝나면 컨테이너를 지운다. Docker 가 없으면 **건너뛰지 않고 실패**한다.
check.sh 에는 넣지 않는다(Docker 가 필요하고 수 분 걸린다).

시나리오(모두 지속 유입 중 — 결정을 받고 노출을 보고하는 클라이언트 여러 개가 계약의 재시도 규칙대로 움직인다):
  restart          `docker restart`(정상 종료 → 기동): 계획된 재시작·전환에 가까운 끊김
  crash            `docker kill`(SIGKILL) → 기동: 강제 종료. WAL 충돌 복구. **수신 확인(2xx)한 것은 전부 살아 있어야 한다**(커밋마다 디스크 동기화 설정 하에서)
  processor-kill   처리 프로세스 하나를 SIGKILL 하고 새로 띄운다(DB 는 그대로): 처리 도중 죽어도 이중 과금·유실이 없다
  mongo-restart    임시 MongoDB(레플리카셋)를 재시작한다: **요청은 전혀 영향이 없고**(503 도 없다) outbox 에 이벤트가 쌓였다가 복구 뒤 상시 중계 프로세스가 비운다
  relay-kill       중계 프로세스 하나를 SIGKILL 하고 새로 띄운다: 보낸 뒤 표시 전에 죽어도 같은 내용의 중복으로 통과해 이벤트가 늘거나 줄지 않는다

클러스터(HA) 시나리오는 PostgreSQL HA 구성을 정한 뒤에 둔다(MySQL InnoDB Cluster 시나리오는 PostgreSQL 전환 때 지웠다 — 계획 docs/plans/00 P1-2).

확인하는 것(종료 코드 0 = 모두 충족):
  1. 끊김 동안 API 는 503(또는 연결 실패)으로 답하고 **500 은 없다**. 시험이 공회전이 아니다(끊김을 실제로 겪은 요청이 있다).
  2. 클라이언트의 재시도가 모두 끝내 수신 확인된다(복구 뒤 정상).
  3. 수신 확인한 결정·반응은 전부 저장돼 있다(유실 0), 이중 과금 0, 잔액 = 원장 합계(`load_test.verify_invariants`).
  5. 이벤트(Step 4): 결정·접수·판정·과금마다 outbox 에 정확히 하나(원본 행과 일대일), 복구 뒤 **상시 중계 프로세스만으로** 미전송이 0 이 되고 MongoDB 의 이벤트가 outbox 와 같다.
  4. 상시 처리 프로세스는 DB 가 끊겼다 돌아와도 죽지 않고 이어서 처리한다. **접수 직후 처리를 끄고**, 복구 뒤 상시 처리 프로세스**만으로** 대기열이 비워지는지
     (별도 `process_reactions --once` 없이) 최종 불변식 전에 판정한다 — 다른 곳이 처리할 수 없으므로 상시 프로세스가 일하지 않으면 실패한다.

한계: 개발 PC 의 Docker 컨테이너다(한 호스트, 네트워크 분리·지연 주입 없음). 인프라 서버의 실제 네트워크·디스크에서의 복구 시간과 유실 범위는 인프라 서버에서 확인할 일이다(계획 7.3.4 #6).
"""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from functools import partial
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "tests" / "tools"))

import docker_cli  # noqa: E402 — tests/tools/ 가 경로에 있다
import mongo_temp_rs  # noqa: E402

IMAGE = "postgres:18"
INITDB_ARGS = "--locale-provider=builtin --locale=C.UTF-8"  # 시험 DB 와 같은 정렬(결정 D11, tests/docker/compose.yaml)
SCENARIOS = ("restart", "crash", "processor-kill", "mongo-restart", "relay-kill")


docker = partial(docker_cli.docker, timeout=180)  # 이 스크립트의 시간 제한(R5)


class DbContainer:
    """임시 PostgreSQL 컨테이너(사용자 postgres). 비밀번호는 환경변수로만 넘기고 출력하지 않는다. 내구성 설정(fsync·synchronous_commit)은 이미지 기본값(on)이다."""

    def __init__(self):
        self.port = docker_cli.free_port()
        self.name = f"ads-failure-{uuid.uuid4().hex[:10]}"
        self.password = secrets.token_urlsafe(24)

    def start(self):
        docker("run", "-d", "--name", self.name, "-p", f"127.0.0.1:{self.port}:5432", "-e", f"POSTGRES_PASSWORD={self.password}",
               "-e", f"POSTGRES_INITDB_ARGS={INITDB_ARGS}", IMAGE)
        self.wait_ready()

    def wait_ready(self, timeout=180):
        deadline = time.time() + timeout
        while time.time() < deadline:
            # TCP 로 묻는다 — 첫 기동의 초기화 단계에서는 서버가 유닉스 소켓만 열어 두므로 그때는 아직 준비 전이다
            probe = docker("exec", self.name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-q",
                           check=False)  # 다른 docker 호출과 같은 시간 제한(R5) — 걸린 exec 가 대기를 끝없이 늘이지 않게(6-13g)
            if probe.returncode == 0:
                return
            time.sleep(1)
        raise SystemExit(f"컨테이너 {self.name} 의 PostgreSQL 이 {timeout}초 안에 준비되지 않았다")

    def restart(self):
        docker("restart", "-t", "20", self.name)
        self.wait_ready()

    def kill_and_start(self):
        docker("kill", self.name)
        time.sleep(0.5)
        docker("start", self.name)
        self.wait_ready()

    def remove(self):
        docker("rm", "-f", "-v", self.name, check=False)  # 공식 이미지는 VOLUME 을 선언한다 — 익명 볼륨도 함께 지운다


def start_standing(command, env, tmp, name):
    """상시 명령(`process_reactions`·`relay_events`)을 띄우고 시작 줄이 나올 때까지(최대 60초) 기다린다."""
    log = open(tmp / f"{name}.log", "w")
    proc = subprocess.Popen([sys.executable, "manage.py", command, "--batch", "50", "--interval", "0.3"], cwd=BASE_DIR, env=env, stdout=log, stderr=log)
    proc.log_handle = log
    deadline = time.time() + 60
    while time.time() < deadline and "started" not in (tmp / f"{name}.log").read_text():
        time.sleep(0.1)
    return proc


def run_scenario(name, db, mongo_container, log):
    import load_test
    import pg_admin

    tmp = Path(tempfile.mkdtemp(prefix="ads-failure-"))
    (tmp / "static").mkdir()  # 빈 정적 파일 폴더(WhiteNoise 가 시작할 때 읽는다, 6-5e)
    database = pg_admin.create_database("ads_failure")
    os.environ.update(DJANGO_SETTINGS_MODULE="config.settings.dev", DB_NAME=database, MEDIA_ROOT=str(tmp / "media"), STATIC_ROOT=str(tmp / "static"))
    os.environ.pop("DJANGO_DEBUG", None)
    import django

    django.setup()
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from django.conf import settings
    from django.db import connections

    # 시나리오마다 새 데이터베이스와 임시 폴더다. 이 프로세스의 Django 설정은 첫 시나리오 때 굳어 있으므로 직접 바꾼다(처리 프로세스는 새 환경변수를 읽는다).
    # 폴더를 그대로 두면 둘째 시나리오부터 지운 첫 폴더를 가리킨다 — 정적 폴더 경고가 다시 나고, 소재 이미지가 첫 폴더를 다시 만들어 남는다(6-7b).
    connections.close_all()
    settings.DATABASES["default"]["NAME"] = database
    connections["default"].settings_dict["NAME"] = database
    settings.MEDIA_ROOT, settings.STATIC_ROOT = os.environ["MEDIA_ROOT"], os.environ["STATIC_ROOT"]
    from events import mongo, store

    mongo_db = f"ads_events_failure_{uuid.uuid4().hex}"
    os.environ["MONGO_DB"] = mongo_db  # 상시 중계 프로세스가 읽는다
    settings.EVENT_STORE = {**settings.EVENT_STORE, "NAME": mongo_db}  # 이 프로세스(검증)도 같은 데이터베이스를 본다
    mongo.close()
    result = {"scenario": name, "checks": []}

    def check(label, ok, detail=""):
        result["checks"].append({"check": label, "ok": bool(ok), "detail": detail})
        log(f"  {'OK  ' if ok else 'FAIL'} {label} {detail}")

    processors, relays, server, lag = [], [], None, None
    try:
        call_command("migrate", verbosity=0)
        store.ensure()
        ctx = load_test.seed()
        server = load_test.ThreadedServer(("127.0.0.1", 0), load_test.QuietHandler)
        server.set_app(get_wsgi_application())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        settings.REACTION_PROCESS_ON_RECEIPT = False  # 접수 직후 처리를 꺼서, 반응을 처리할 수 있는 것은 **상시 처리 프로세스뿐**이 되게 한다(복구를 가리지 않는다)
        harness = load_test.Harness(server.server_address[1], ctx, 8, tmp, log, rest_scale=0.1)
        env = dict(os.environ)
        processors = [start_standing("process_reactions", env, tmp, "p1"), start_standing("process_reactions", env, tmp, "p2")]
        relays = [start_standing("relay_events", env, tmp, "r1"), start_standing("relay_events", env, tmp, "r2")]
        backlog = {"max": 0, "stop": threading.Event()}

        def sample_backlog():  # 미전송이 쌓이는지 지켜본다(MongoDB 가 끊긴 동안 쌓여야 시험이 공회전이 아니다)
            from django.db import connection

            from events.models import EventOutbox

            while not backlog["stop"].is_set():
                try:
                    backlog["max"] = max(backlog["max"], EventOutbox.objects.filter(sent_at__isnull=True).count())
                except Exception:  # noqa: BLE001 — DB 가 끊긴 순간은 건너뛴다
                    connection.close()
                time.sleep(0.2)
            connection.close()

        threading.Thread(target=sample_backlog, daemon=True).start()

        lag = load_test.LagSampler()
        lag.start()  # 이벤트가 생성 시각 뒤 얼마 만에 보이는지(장애 중 포함)를 잰다
        stop, outcomes, lock, timeline = threading.Event(), [], threading.Lock(), {}

        def client():
            while not stop.is_set():
                decision = harness.post_decision()
                with lock:
                    outcomes.append(decision)
                if not decision.acked:
                    continue
                decision_id = decision.body["decision_id"]
                harness.acked_decisions.add(decision_id)
                reaction = harness.put_reaction(decision_id, "IMPRESSION")
                with lock:
                    outcomes.append(reaction)

        clients = [threading.Thread(target=client, daemon=True) for _ in range(8)]
        [t.start() for t in clients]
        time.sleep(4)
        timeline["injected_at"] = time.time()
        if name == "restart":
            db.restart()
        elif name == "crash":
            db.kill_and_start()
        elif name == "mongo-restart":
            mongo_temp_rs.restart(*mongo_container)
        elif name == "relay-kill":
            relays[0].kill()
            relays[0].wait(timeout=10)
            relays[0].log_handle.close()  # 갈아 끼우는 프로세스의 로그 파일도 닫는다(R25)
            relays[0] = start_standing("relay_events", env, tmp, "r1b")
        else:
            processors[0].kill()
            processors[0].wait(timeout=10)
            processors[0].log_handle.close()
            processors[0] = start_standing("process_reactions", env, tmp, "p1b")
        timeline["recovered_at"] = time.time()
        time.sleep(8)
        stop.set()
        [t.join(timeout=120) for t in clients]

        statuses = [s for o in outcomes for s in o.attempts]
        unavailable = sum(1 for s in statuses if s in (0, 503))
        server_errors = sorted({s for s in statuses if s >= 500 and s != 503})
        unacked = sum(1 for o in outcomes if not o.acked)
        log(f"  요청 {len(outcomes)}건, 시도 {len(statuses)}회: 끊김(503·연결 실패) {unavailable}회, 최종 수신 확인 못 한 것 {unacked}건, "
            f"주입 {timeline['recovered_at'] - timeline['injected_at']:.1f}초")
        if name in ("restart", "crash"):
            check("끊김을 실제로 겪은 요청이 있다(시험이 공회전이 아니다)", unavailable > 0, f"{unavailable}회")
        if name == "mongo-restart":
            check("MongoDB 가 끊겨도 요청은 영향이 없다(503·연결 실패 0)", unavailable == 0, f"{unavailable}회")
        check("끊김 동안 500 이 없다(503·연결 실패만)", not server_errors, f"500대: {server_errors}")
        check("모든 요청이 끝내 수신 확인됐다(재시도로 복구)", unacked == 0, f"{unacked}건 실패")
        alive = [p.poll() is None for p in processors]
        check("상시 처리 프로세스가 죽지 않았다", all(alive), f"살아 있음 {alive}")
        relay_alive = [p.poll() is None for p in relays]
        check("상시 중계 프로세스가 죽지 않았다", all(relay_alive), f"살아 있음 {relay_alive}")

        # 복구 판정: 별도 처리 명령을 쓰기 **전에**, 상시 처리 프로세스만으로 대기열이 비워지는지 본다(접수 직후 처리는 꺼져 있다)
        connections.close_all()  # 주입 전에 열었던 연결은 끊겼을 수 있다
        from datetime import datetime, timezone

        from records.models import Reaction

        recovered_dt = datetime.fromtimestamp(timeline["recovered_at"], timezone.utc)
        deadline = time.time() + 90
        while time.time() < deadline and Reaction.objects.filter(status="RECEIVED").exists():
            time.sleep(0.5)
        left = Reaction.objects.filter(status="RECEIVED").count()
        by_standing = Reaction.objects.filter(processed_at__gte=recovered_dt).count()  # 접수 직후 처리를 껐으므로 처리한 것은 상시 처리 프로세스뿐이다
        check("복구 뒤 상시 처리 프로세스만으로 대기열이 비워진다(별도 처리 명령 없이)", left == 0, f"남은 RECEIVED {left}건")
        check("복구 뒤에 상시 처리 프로세스가 반응을 처리했다(공회전이 아니다)", by_standing > 0, f"{by_standing}건")

        from events.models import EventOutbox

        deadline = time.time() + 90
        while time.time() < deadline and EventOutbox.objects.filter(sent_at__isnull=True).exists():
            time.sleep(0.5)
        unsent = EventOutbox.objects.filter(sent_at__isnull=True).count()
        relayed_since = EventOutbox.objects.filter(sent_at__gte=recovered_dt).count()
        backlog["stop"].set()
        check("복구 뒤 상시 중계 프로세스만으로 outbox 가 비워진다(별도 명령 없이)", unsent == 0, f"남은 미전송 {unsent}건")
        check("복구 뒤에 상시 중계 프로세스가 이벤트를 보냈다(공회전이 아니다)", relayed_since > 0, f"{relayed_since}건")
        if name == "mongo-restart":
            check("MongoDB 가 끊긴 동안 outbox 에 미전송이 쌓였다(시험이 공회전이 아니다)", backlog["max"] >= 20, f"최대 {backlog['max']}건")
        load_test.stop_processes(processors + relays)  # SIGTERM, 30초 안에 끝나지 않으면 SIGKILL — 걸린 프로세스가 남은 점검을 건너뛰게 하지 않는다(6-13g)
        for proc in processors + relays:
            proc.log_handle.close()
        connections.close_all()
        lag.stop()
        report = lag.report(settings.EVENTS_HOP_GRACE_SECONDS)
        result["event_lag"] = report
        check(f"이벤트가 생성 시각 뒤 창 닫힘 여유({settings.EVENTS_HOP_GRACE_SECONDS}초) 안에 모두 보인다(장애 중 포함)", report["late"] == 0 and report["unobserved"] == 0,
              f"이벤트 {report['events']}건, 관찰된 최대 지연 {report['max_lag_s']}초, 여유 초과 {report['late']}건, 관찰 못 한 것 {report['unobserved']}건")
        harness.hop_check(name)  # 복구 뒤 구간별 정합 manifest(원본 기대 = outbox = MongoDB)가 일치해야 한다 — 복구 중의 어긋남은 중계 지연 후보로 분류되지만 여기서는 모두 끝난 뒤다
        result["checks"].extend(harness.checks[-1:])  # (harness.check 는 harness 쪽 목록에 쌓는다 — 이 시나리오의 결과에도 넣는다)
        problems = load_test.verify_invariants(harness.acked_decisions, harness.acked_reactions, expect_drained=True) + load_test.verify_events(relayed=True)
        check("불변식: 유실 0·이중 과금 0·잔액=원장·남은 대기열 없음·이벤트=원본 행(일대일)·MongoDB=outbox", not problems, "; ".join(problems[:3]))
        result.update(requests=len(outcomes), attempts=len(statuses), unavailable=unavailable, acked_decisions=len(harness.acked_decisions), acked_reactions=len(harness.acked_reactions))
    finally:
        if lag is not None:
            lag.stop()
        for proc in processors + relays:
            if proc.poll() is None:
                proc.kill()
        if server is not None:
            server.shutdown()
            server.server_close()
        connections.close_all()
        try:
            if mongo_db.startswith("ads_events_failure_"):
                mongo.client().drop_database(mongo_db)  # 이 시나리오가 만든 이름만 지운다
            mongo.close()
        except BaseException as error:  # noqa: BLE001
            print(f"정리 실패(임시 MongoDB 데이터베이스): {error!r}", file=sys.stderr)
        try:
            pg_admin.drop_database(database)
        except BaseException as error:  # noqa: BLE001
            print(f"정리 실패(임시 DB): {error!r}", file=sys.stderr)
        shutil.rmtree(tmp, ignore_errors=True)
    result["ok"] = all(c["ok"] for c in result["checks"])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenarios", nargs="*", help=f"기본은 모두: {', '.join(SCENARIOS)}")
    parser.add_argument("--json", help="결과를 JSON 으로 저장할 경로")
    args = parser.parse_args(argv)
    chosen = args.scenarios or list(SCENARIOS)
    unknown = [name for name in chosen if name not in SCENARIOS]
    if unknown:
        raise SystemExit(f"알 수 없는 시나리오 {unknown}. 가능한 것: {', '.join(SCENARIOS)}")
    if shutil.which("docker") is None:
        raise SystemExit("docker 가 없다. 이 시험은 임시 PostgreSQL·MongoDB 컨테이너가 필요하다.")

    results, mongo_name = [], None
    db = DbContainer()
    try:
        print(f"임시 PostgreSQL 컨테이너 {db.name} (포트 {db.port}) 기동…")
        db.start()
        os.environ.update(DB_HOST="127.0.0.1", DB_PORT=str(db.port), DB_USER="postgres", DB_PASSWORD=db.password)
        mongo_name, mongo_port, mongo_password = mongo_temp_rs.start()  # 임시 단일 노드 레플리카셋(인증 켜짐)
        print(f"임시 MongoDB 컨테이너 {mongo_name} (포트 {mongo_port}) 기동")
        os.environ.update(MONGO_HOSTS=f"127.0.0.1:{mongo_port}", MONGO_USER="root", MONGO_PASSWORD=mongo_password, MONGO_AUTH_SOURCE="admin", MONGO_REPLICA_SET=mongo_temp_rs.REPLICA_SET)
        for name in chosen:
            print(f"\n== 시나리오: {name}")
            results.append(run_scenario(name, db, (mongo_name, mongo_port), print))
    finally:
        db.remove()
        if mongo_name:
            mongo_temp_rs.stop(mongo_name)
    ok = all(r["ok"] for r in results)
    print("\n결과: " + ("모든 시나리오 통과" if ok else "실패 있음 — " + ", ".join(r["scenario"] for r in results if not r["ok"])))
    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
