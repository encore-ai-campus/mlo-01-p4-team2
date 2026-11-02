#!/usr/bin/env python
"""여러 워커·처리기 측정(버그 탐색 K2·K5, 사용자 2026-11-02). check.sh 에는 넣지 않는다(수 분 걸리는 측정이다).

    python tests/tools/scale_test.py                                  # 워커 1·2·4, 처리기 1·2·4
    python tests/tools/scale_test.py --workers 1 4 --processors 1 4 --requests 2000 --json out.json

① 웹(K2): 운영과 같은 서버(gunicorn 동기 워커 N개)에 결정 → 노출을 동시 C 개로 보낸다(요청 제한은 측정 동안 풀어 둔다). 처리량·지연·상태를 잰다.
② 요청 제한: 작은 한도(초당 5, 버스트 20)로 결정 400개를 재시도 없이 한꺼번에 보낸다. 한도는 모든 워커가 함께 쓰는 DB 버킷이라(설계 §10 "요청 제한 구현")
   워커 수와 무관하게 201 이 버스트 이상, 버스트 + 초당 토큰 × 걸린 시간 이하여야 한다(워커마다 버킷이 따로면 워커 수만큼 늘어난다).
③ 처리기(K5): 접수 직후 처리를 끈 채 결정마다 노출 셋을 동시에 쌓고(같은 유형이 겹친다), 처리기 M개로 비운다.
   비우는 시간, 처리기들이 센 보류(pending)·locked·contended·실패, 가장 많았던 보류 수(held)를 잰다.
끝에 남은 것을 비우고 load_test 의 불변식(유실·이중 과금·잔액 = 원장)을 확인한다. 종료 코드 0 = 불변식·요청 제한 확인 충족.

환경: load_test 와 같다(실행마다 새 `ads_load_*` DB, 끝나면 지운다). MongoDB 는 쓰지 않는다(중계를 띄우지 않으므로 이벤트는 outbox 에 쌓이기만 한다).
한계: 서버·처리기·클라이언트가 한 PC 다(CPU 를 나눠 쓴다). 수치는 이 장비의 값이고, 워커·처리기 수에 따른 **경향**을 보는 측정이다.
"""
import argparse
import ast
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "tests" / "tools"))

import load_test  # noqa: E402 — tests/tools/ 가 경로에 있다

TIGHT = (5, 20)  # ② 의 (초당 토큰, 버스트)
# 서버·처리기 프로세스가 쓰는 설정: dev 에 측정용 두 값만 환경변수로 바꾼다(요청 제한, 접수 직후 처리를 할지)
SETTINGS_SOURCE = f'''import os
from config.settings.dev import *  # noqa: F401,F403
MEDIA_RATE_LIMITS = {{group: (10**6, 10**6) if os.environ["SCALE_RATE"] == "unlimited" else {TIGHT} for group in MEDIA_RATE_LIMITS}}
REACTION_PROCESS_ON_RECEIPT = os.environ.get("SCALE_ON_RECEIPT") == "1"
'''


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def child_env(tmp, unlimited, on_receipt):
    return dict(os.environ, DJANGO_SETTINGS_MODULE="scale_settings", PYTHONPATH=f"{tmp}{os.pathsep}{BASE_DIR}",
                SCALE_RATE="unlimited" if unlimited else "tight", SCALE_ON_RECEIPT="1" if on_receipt else "0")


class Gunicorn:
    def __init__(self, workers, tmp, unlimited, on_receipt):
        self.port, self.log = free_port(), open(tmp / f"gunicorn-{workers}.log", "a")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "gunicorn", "config.wsgi:application", "-w", str(workers), "-b", f"127.0.0.1:{self.port}", "--timeout", "60", "--log-level", "warning"],
            cwd=BASE_DIR, env=child_env(tmp, unlimited, on_receipt), stdout=self.log, stderr=self.log,
        )
        deadline = time.perf_counter() + 60
        while load_test.call(self.port, "GET", "/api/media/v1/health", None, retry=False).status != 200:
            if self.proc.poll() is not None or time.perf_counter() > deadline:
                self.stop()  # 연결을 쥔 채 남으면 끝의 DB 삭제가 실패한다
                raise RuntimeError(f"gunicorn 이 뜨지 않았다(로그 {self.log.name})")
            time.sleep(0.2)

    def stop(self):
        load_test.stop_process(self.proc)
        self.log.close()


def web_throughput(harness, requests, concurrency):
    """결정 → 노출을 `requests` 쌍. 반환: 결정·노출 각각의 요약."""
    per = max(1, requests // concurrency)

    def work(_i):
        decisions, reactions = [], []
        for _ in range(per):
            d = harness.post_decision()
            decisions.append(d)
            if d.status == 201:
                harness.acked_decisions.add(d.body["decision_id"])
                reactions.append(harness.put_reaction(d.body["decision_id"]))
        return decisions, reactions

    results, wall = load_test.run_concurrent(concurrency, work)
    return (load_test.summarize("결정", [o for d, _ in results for o in d], wall),
            load_test.summarize("노출", [o for _, r in results for o in r], wall))


def rate_limit_burst(harness, count=400):
    rate, burst = TIGHT
    results, wall = load_test.run_concurrent(count, lambda _i: harness.post_decision(retry=False))
    for o in results:
        if o.status == 201:
            harness.acked_decisions.add(o.body["decision_id"])
    statuses = dict(Counter(o.status for o in results))
    allowed = burst + rate * wall
    return {"statuses": statuses, "wall_s": round(wall, 2), "allowed_max": round(allowed), "ok": burst <= statuses.get(201, 0) <= allowed and set(statuses) <= {201, 429}}


def processor_counts(log_path):
    """처리기 로그의 요약 줄(`… {'accepted': …} stuck=…`)을 더한다."""
    total = Counter()
    for line in log_path.read_text().splitlines():
        if "{'accepted'" in line:
            total.update(ast.literal_eval(line[line.index("{"):line.index("}") + 1]))
    return total


def drain_with_processors(harness, processors, decisions_count, tmp):
    from records.api import inbox_status
    from records.models import Reaction

    server = Gunicorn(4, tmp, unlimited=True, on_receipt=False)  # 대기열을 쌓는 동안은 처리하지 않는다
    harness.port = server.port
    try:
        pool = [harness.post_decision().body["decision_id"] for _ in range(decisions_count)]
        harness.acked_decisions |= set(pool)
        items = [d for d in pool for _ in range(3)]  # 결정마다 노출 셋이 이웃한 스레드로 나뉘어 거의 동시에 들어간다
        load_test.run_concurrent(30, lambda k: [harness.put_reaction(d) for d in items[k::30]])
    finally:
        server.stop()
    backlog = Reaction.objects.filter(status="RECEIVED").count()
    logs = [tmp / f"processor-{processors}-{i}.log" for i in range(processors)]
    procs = [subprocess.Popen([sys.executable, "manage.py", "process_reactions", "--batch", "100", "--interval", "0.2"], cwd=BASE_DIR,
                              env=child_env(tmp, True, False), stdout=open(path, "w"), stderr=subprocess.STDOUT) for path in logs]
    peak_held, started = 0, time.perf_counter()
    try:
        while Reaction.objects.filter(status="RECEIVED").exists() and time.perf_counter() - started < 300:
            peak_held = max(peak_held, inbox_status()["held"])
            time.sleep(0.2)
        seconds = time.perf_counter() - started
    finally:
        load_test.stop_processes(procs)
    counts = sum((processor_counts(path) for path in logs), Counter())
    return {"processors": processors, "backlog": backlog, "drain_s": round(seconds, 2), "per_s": round(backlog / seconds, 1), "peak_held": peak_held,
            "left": Reaction.objects.filter(status="RECEIVED").count(), **{k: counts[k] for k in ("accepted", "rejected", "pending", "locked", "contended", "failed", "transient")}}


def run(args, tmp, db_name):
    (tmp / "scale_settings.py").write_text(SETTINGS_SOURCE)
    os.environ.update(DJANGO_SETTINGS_MODULE="config.settings.dev", DB_NAME=db_name, MEDIA_ROOT=str(tmp / "media"), STATIC_ROOT=str(tmp / "static"))
    os.environ.pop("DJANGO_DEBUG", None)
    (tmp / "static").mkdir(exist_ok=True)

    import django

    django.setup()
    from django.core.management import call_command

    call_command("migrate", verbosity=0)
    ctx = load_test.seed()
    harness = load_test.Harness(0, ctx, args.concurrency, tmp, print)
    result = {"web": [], "rate_limit": [], "processors": []}
    for workers in args.workers:
        server = Gunicorn(workers, tmp, unlimited=True, on_receipt=True)
        harness.port = server.port
        try:
            decisions, reactions = web_throughput(harness, args.requests, args.concurrency)
        finally:
            server.stop()
        result["web"].append({"workers": workers, "decisions": decisions, "reactions": reactions})
        print(f"① 워커 {workers}: 결정 {decisions['throughput_per_s']}/s p50/p95 {decisions['last_attempt_ms']['p50']}/{decisions['last_attempt_ms']['p95']} ms {decisions['final_status']}, "
              f"노출 {reactions['final_status']}")
        server = Gunicorn(workers, tmp, unlimited=False, on_receipt=True)
        harness.port = server.port
        try:
            burst = rate_limit_burst(harness)
        finally:
            server.stop()
        result["rate_limit"].append({"workers": workers, **burst})
        print(f"② 워커 {workers}: 결정 400개(재시도 없음, 한도 {TIGHT}) {burst['statuses']} — 201 은 {TIGHT[1]} ~ {burst['allowed_max']}({burst['wall_s']}초) {'OK' if burst['ok'] else 'FAIL'}")
    for processors in args.processors:
        row = drain_with_processors(harness, processors, args.backlog, tmp)
        result["processors"].append(row)
        print(f"③ 처리기 {processors}: 대기열 {row['backlog']}건 {row['drain_s']}초({row['per_s']}/s), 보류 최대 {row['peak_held']}, "
              f"pending {row['pending']} · locked {row['locked']} · contended {row['contended']} · failed {row['failed']} · transient {row['transient']}, 남음 {row['left']}")
    out = subprocess.run([sys.executable, "manage.py", "process_reactions", "--once"], cwd=BASE_DIR, env=child_env(tmp, True, False), capture_output=True, text=True)
    problems = load_test.verify_invariants(harness.acked_decisions, harness.acked_reactions, expect_drained=True)
    result["invariants"] = problems
    ok = not problems and out.returncode == 0 and all(r["ok"] for r in result["rate_limit"])
    print("불변식: " + ("모두 충족" if not problems else "; ".join(problems)) + f" / 마지막 --once 종료 코드 {out.returncode}")
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4], help="gunicorn 동기 워커 수(기본 1 2 4)")
    parser.add_argument("--processors", type=int, nargs="+", default=[1, 2, 4], help="process_reactions 수(기본 1 2 4)")
    parser.add_argument("--requests", type=int, default=1000, help="① 결정 → 노출 쌍의 수(기본 1000)")
    parser.add_argument("--concurrency", type=int, default=50, help="① 동시 클라이언트 수(기본 50)")
    parser.add_argument("--backlog", type=int, default=200, help="③ 결정 수 — 결정마다 노출 셋(기본 200 → 600건)")
    parser.add_argument("--json", help="결과를 JSON 으로 저장할 경로")
    args = parser.parse_args(argv)
    os.chdir(BASE_DIR)
    import pg_admin

    tmp, db_name = Path(tempfile.mkdtemp(prefix="ads-scale-")), None
    try:
        db_name = pg_admin.create_database("ads_load")
        return run(args, tmp, db_name)
    finally:
        if "django.db" in sys.modules:
            sys.modules["django.db"].connections.close_all()
        if db_name:
            pg_admin.drop_database(db_name)
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
