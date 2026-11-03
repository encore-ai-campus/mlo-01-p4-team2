"""종단 연속 유입 도구(설계 §11.1·S6): 광고 서버 API 에 결정 → 노출(일부 클릭)을 일정 속도로 계속 넣고, 광고주·관리자 변경을 섞어 이벤트 8종이 모두 나오게 한다.

사용: e2e_load.py --duration 1800 [--rate 20] [--workers 4] [--threads 16] [--click-every 20] [--change-every 60] [--give-up 90] [--report out.json]
- 시험 전용 DB `ads_pipeline_e2e`(먼저 e2e_setup.py). 광고 서버(gunicorn 동기 워커)·상시 `process_reactions` 를 직접 띄우고 끝나면 내린다. 둘 다 HAProxy rw(PG_PORT, 기본 5010)로 붙는다.
- 클라이언트는 계약의 재시도 규칙을 따른다(`load_test.call`: 429·503·연결 실패면 Retry-After 를 지켜 같은 요청 반복). 2xx 를 받은 결정·반응을 장부에 적는다.
- 판정: J0 시험 유효(설계 §11.2 — 이벤트 8종이 모두 나옴, 광고 없는 결정 1% 이하, 실제 유입 속도 ≥ 목표의 95%, 이번 실행 구간의 과금(원장 CHARGE, 금액 < 0) > 0) / J1 장애 중에도 500 = 0, 모든 요청이 결국 2xx, 광고 서버·처리기가 끝까지 살아 있음(종료 코드 0) / J2 장부 = 원천 표·이중 과금 0·잔액 = 원장(`load_test.verify_invariants`) / J3 원천 행 ↔ outbox 일대일(`load_test.verify_events`).
- 시드(관리자·매체·슬롯·광고주)는 처음 한 번 만들고 문맥을 $TMPDIR/<DB 이름>/ctx.json(0600 — 매체 키가 들어 있다)에 둔다. 매체 키는 출력하지 않는다.
- 실제 유입 속도 = 보낸 쌍 / 유입 시작 → 마지막 요청 끝의 벽시계 시간. 요청은 버리지 않고 늦게라도 다 보내므로 이 값은 "끝날 때까지 따라잡았는가"를 본다.
  장애 중에 밀린 정도는 `schedule_lag_max_s`(예정 시각보다 늦게 보낸 최대 시간)로 따로 본다.
- SIGTERM 을 받으면 띄운 광고 서버·처리기를 내리고 rc=143 으로 끝난다(앞에서 돌릴 때는 Ctrl-C 도).
"""
import argparse
import itertools
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CWD = Path.cwd()  # 인자의 상대 경로는 실행한 폴더 기준(아래 chdir 전에 고정)
BASE = ROOT / "ads_server"
DB = os.environ.get("PG_DB", "ads_pipeline_e2e")
LAB = Path(tempfile.gettempdir()) / DB  # 문맥·로그는 DB 별(다른 DB 로 돌리면 그 DB 의 문맥을 쓴다)
CTX = LAB / "ctx.json"
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tests" / "tools"))
DB_ENV = {"DJANGO_SETTINGS_MODULE": "config.settings.dev", "DB_HOST": "127.0.0.1", "DB_PORT": os.environ.get("PG_PORT", "5010"), "DB_USER": "postgres",
          "DB_PASSWORD": Path("/opt/postgresql/config/postgres-password").read_text().strip(), "DB_NAME": DB,
          "MEDIA_ROOT": str(LAB / "media"), "STATIC_ROOT": str(LAB / "static")}
os.environ.update(DB_ENV)
os.chdir(BASE)
import django  # noqa: E402

django.setup()
import load_test  # noqa: E402
from billing import api as billing  # noqa: E402
from campaigns import api as campaigns  # noqa: E402
from django.db import connection  # noqa: E402
from django.db.models import Sum  # noqa: E402
from foundation.api import User  # noqa: E402
from masters import api as masters  # noqa: E402

OK = {200, 201}
EVENT_NAMES = {"ad.decision", "ad.reaction_received", "ad.reaction", "ad.charge", "ad.bid_policy_changed", "ad.bid_added", "ad.campaign_changed", "ad.ad_group_changed"}
ACK = {"decision": OK, "reaction": OK, "click": {302}}  # 클릭 주소는 랜딩으로 302 가 정상


def context():
    """시드 문맥. 처음이면 `load_test.seed` 로 만들고 파일에 둔다(매체 키 포함 — 0600)."""
    if CTX.exists():
        return json.loads(CTX.read_text())
    LAB.mkdir(parents=True, exist_ok=True)
    seeded = load_test.seed(advertisers=3)
    ctx = {k: str(v) for k, v in seeded.items() if k != "advertisers"} | {"advertisers": [a.username for a in seeded["advertisers"]], "admin": "load_admin"}
    fd = os.open(CTX, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # 처음부터 0600(매체 키)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(ctx))
    return ctx


def fund(ctx, rate, duration):
    """오래 도는 시험에 맞춰 광고주 지갑을 채운다(관리자 조정 — 원장에 남아 잔액 = 원장 불변식을 지킨다). 한 광고주가 노출을 거의 다 가져갈 수 있어
    광고주마다 `속도 × 시간 × 상한가(500) × 1.2` 를 채운다. 잔액이 바닥나면 광고가 붙지 않아 노출·과금 이벤트가 사라진다(2026-11-03 세 번째 30분 실행)."""
    admin = User.objects.get(username=ctx["admin"])
    users = [User.objects.get(username=n) for n in ctx["advertisers"]]
    need = int(rate * duration * 500 * 1.2) + 10_000
    have = billing.balances([u.pk for u in users])
    for u in users:
        if have[u.pk] < need:
            billing.adjust(admin, u.pk, need - have[u.pk], "e2e 시험 충전")
    after = billing.balances([u.pk for u in users])
    connection.close()
    return {u.username: after[u.pk] for u in users}


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Servers:
    """광고 서버(gunicorn)와 상시 처리기. 로그는 LAB 아래."""

    def __init__(self, workers):
        env = dict(os.environ, **DB_ENV)
        self.port = free_port()
        self.logs = [open(LAB / "gunicorn.log", "a"), open(LAB / "process_reactions.log", "a")]
        self.web = subprocess.Popen([sys.executable, "-m", "gunicorn", "config.wsgi:application", "-w", str(workers), "-b", f"127.0.0.1:{self.port}", "--timeout", "60",
                                     "--log-level", "warning"], cwd=BASE, env=env, stdout=self.logs[0], stderr=self.logs[0])
        self.proc = subprocess.Popen([sys.executable, "manage.py", "process_reactions", "--interval", "0.2", "--batch", "100"], cwd=BASE, env=env,
                                     stdout=self.logs[1], stderr=self.logs[1])
        deadline = time.monotonic() + 60
        try:  # 뜨기를 기다리는 동안 TERM·오류가 와도 띄운 둘을 남기지 않는다
            while load_test.call(self.port, "GET", "/api/media/v1/health", None, retry=False).status != 200:
                if self.web.poll() is not None or time.monotonic() > deadline:
                    raise SystemExit(f"gunicorn 이 뜨지 않았다(로그 {LAB / 'gunicorn.log'})")
                time.sleep(0.2)
        except BaseException:
            self.stop()
            raise

    def stop(self):
        codes = [load_test.stop_process(p) for p in (self.web, self.proc)]
        [log.close() for log in self.logs]
        return codes


class Traffic:
    def __init__(self, port, ctx, rate, click_every, give_up):
        self.port, self.ctx, self.rate, self.click_every, self.give_up = port, ctx, rate, click_every, give_up
        self.lag_max = 0.0  # 예정 시각보다 늦게 보낸 최대 시간(초) — 재시도로 스레드가 묶이면 커진다
        self.elapsed = 0.0  # 유입 시작 → 마지막 요청이 끝날 때까지 실제 벽시계 시간
        self.lock, self.counter = threading.Lock(), itertools.count()
        self.acked_decisions, self.acked_reactions = set(), {}
        self.status = Counter()        # (요청 종류, 최종 상태)
        self.first_try = Counter()     # (요청 종류, 첫 시도 상태) — 장애 중 503·연결 실패를 겪은 요청 수를 본다
        self.latency = {"decision": [], "reaction": [], "click": []}
        self.no_ad = 0  # 광고가 붙지 않은 결정(잔액·예산·필터) — 많으면 시험이 무효(J0)

    def _note(self, kind, outcome):
        with self.lock:
            self.status[(kind, outcome.status)] += 1
            self.first_try[(kind, outcome.attempts[0])] += 1
            self.latency[kind].append(outcome.total)

    def pair(self, i):
        body = {"slot_id": self.ctx["slot_id"], "subject_id": str(uuid.uuid4()), "context": {"scene": "e2e"}}
        decision = load_test.call(self.port, "POST", "/api/media/v1/decisions", self.ctx["key"], body, give_up_after=self.give_up)
        self._note("decision", decision)
        if decision.status not in OK:
            return
        did = decision.body["decision_id"]
        with self.lock:
            self.acked_decisions.add(did)
        if not decision.body.get("ad"):
            with self.lock:
                self.no_ad += 1
            return
        rid = str(uuid.uuid4())
        reaction = load_test.call(self.port, "PUT", f"/api/media/v1/decisions/{did}/reactions/{rid}", self.ctx["key"],
                                  {"type": "IMPRESSION", "occurred_at": load_test.now_utc()}, give_up_after=self.give_up)
        self._note("reaction", reaction)
        if reaction.status in OK:
            with self.lock:
                self.acked_reactions[(self.ctx["media_id"], rid)] = (did, "IMPRESSION")
        if self.click_every and i % self.click_every == 0:
            url = decision.body["ad"]["click_url"]
            self._note("click", load_test.call(self.port, "GET", "/" + url.split("://", 1)[1].split("/", 1)[1], None, give_up_after=self.give_up))

    def run(self, until, threads):
        start = time.monotonic()

        def worker():
            while True:
                i = next(self.counter)
                due = start + i / self.rate
                if due >= until:
                    return
                now = time.monotonic()
                if due > now:
                    time.sleep(due - now)
                else:
                    with self.lock:
                        self.lag_max = max(self.lag_max, now - due)
                self.pair(i)

        pool = [threading.Thread(target=worker, daemon=True) for _ in range(threads)]
        [t.start() for t in pool]
        [t.join() for t in pool]
        self.elapsed = time.monotonic() - start


def changes(ctx, every, until, done):
    """광고주·관리자 변경 넷을 돌아가며: 입찰 정책(ad.bid_policy_changed), 입찰(ad.bid_added), 캠페인(ad.campaign_changed), 광고그룹(ad.ad_group_changed)."""
    admin = User.objects.get(username=ctx["admin"])
    adv = User.objects.get(username=ctx["advertisers"][0])
    campaign = campaigns.list_campaigns(adv)[-1]  # 가장 먼저 만든 캠페인(시드)
    groups = campaigns.list_ad_groups(adv, campaign.campaign_id)
    group, shared = groups[0], groups[1]  # 개인 슬롯 그룹, 일괄 슬롯 그룹(시드 — 광고주 0만 있음)
    n = 0
    while time.monotonic() < until:
        kind = n % 4
        try:
            if kind == 0:
                p = masters.list_policy_versions(admin, ctx["slot_id"])[0]
                masters.add_policy_version(admin, ctx["slot_id"], p.floor, p.base, p.cap, f"e2e 변경 {n}", p.policy_version, **load_test.LOAD_SHARES)
            elif kind == 1:
                bid = campaigns.list_bids(adv, group.ad_group_id)[0]
                campaigns.add_bid(adv, group.ad_group_id, 300 if bid.amount != 300 else 310, bid.bid_version)
            elif kind == 2:
                budget = campaigns.get_campaign(adv, campaign.campaign_id).daily_budget  # 이벤트는 예산·기간 변경만 싣는다(이름은 표시용)
                campaigns.update_campaign(adv, campaign.campaign_id, {"daily_budget": 10**9 if budget != 10**9 else 10**9 - 1})
            else:
                # 이벤트는 켜기/끄기·게재 위치만 싣는다. 일괄 슬롯 그룹을 켜고 끈다 — 유입(개인 슬롯)의 결정에는 영향이 없다
                campaigns.update_ad_group(adv, shared.ad_group_id, {"enabled": not campaigns.get_ad_group(adv, shared.ad_group_id).enabled})
            done[kind] += 1
        except Exception as error:  # noqa: BLE001 — 시험 도구: 실패를 세고 계속 간다(판정에 보고)
            done[f"error_{kind}"] += 1
            print(f"change {kind} 실패: {error!r}"[:300], flush=True)
        n += 1
        connection.close()  # 다음 변경까지 쉬는 동안 연결을 쥐지 않는다(HAProxy 유휴 끊김 — 설계 D20). 다음 질의가 새로 연다
        end = time.monotonic() + every
        while time.monotonic() < min(end, until):
            time.sleep(0.5)
    connection.close()


def pct(values, p):
    return round(load_test.percentile(values, p) * 1000, 1) if values else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duration", type=int, required=True, help="유입 시간(초)")
    ap.add_argument("--rate", type=float, default=20.0, help="초당 결정·노출 쌍(기본 20 — 설계 S6)")
    ap.add_argument("--workers", type=int, default=4, help="gunicorn 동기 워커 수(기본 4)")
    ap.add_argument("--threads", type=int, default=16, help="클라이언트 스레드 수(기본 16)")
    ap.add_argument("--click-every", type=int, default=20, help="N쌍마다 한 번 클릭 주소를 연다(기본 20, 0이면 끔)")
    ap.add_argument("--change-every", type=float, default=60.0, help="광고주·관리자 변경 간격(초, 기본 60)")
    ap.add_argument("--give-up", type=float, default=90.0, help="요청 하나의 재시도를 포기하는 시간(초, 기본 90 — 이보다 긴 정지를 시험할 때 늘린다)")
    ap.add_argument("--drain-timeout", type=int, default=300, help="유입이 끝난 뒤 처리 대기 상한(초)")
    ap.add_argument("--report", help="결과 JSON 경로(기본 $TMPDIR/<PG_DB>/report-<시각>.json)")
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # finally 가 돌아 띄운 광고 서버·처리기를 내린다

    from billing.models import LedgerEntry
    from events.models import EventOutbox
    from records.models import Reaction

    ctx = context()
    funded = fund(ctx, a.rate, a.duration)
    started_at = datetime.now(UTC)
    outbox_before = EventOutbox.objects.count()
    connection.close()  # 유입 동안(최대 수십 분) 쓰지 않는 연결을 쥐지 않는다 — HAProxy 가 30분 유휴 연결을 끊어 끝의 판정이 실패했다(2026-11-03, 설계 D20)
    servers = Servers(a.workers)
    print(f"start {started_at.isoformat()} port={servers.port} rate={a.rate}/s duration={a.duration}s", flush=True)
    traffic, done = Traffic(servers.port, ctx, a.rate, a.click_every, a.give_up), Counter()
    until = time.monotonic() + a.duration
    changer = threading.Thread(target=changes, args=(ctx, a.change_every, until, done), daemon=True)
    changer.start()
    try:
        traffic.run(until, a.threads)
        changer.join()
        drained_by = time.monotonic() + a.drain_timeout
        while Reaction.objects.filter(status="RECEIVED").exists() and time.monotonic() < drained_by:
            time.sleep(1)
    finally:
        exit_codes = servers.stop()
    ended_at = datetime.now(UTC)
    connection.close()
    j1_bad = {f"{k}:{s}": c for (k, s), c in traffic.status.items() if s not in ACK[k]}
    j2 = load_test.verify_invariants(traffic.acked_decisions, traffic.acked_reactions)
    j3 = load_test.verify_events()
    by_name = Counter(EventOutbox.objects.filter(created_at__gte=started_at, created_at__lte=ended_at).values_list("event_name", flat=True))
    pairs = sum(c for (k, s), c in traffic.status.items() if k == "decision")
    missing = sorted(EVENT_NAMES - set(by_name))
    rate = pairs / traffic.elapsed if traffic.elapsed else None
    rate_ok = rate is not None and rate >= 0.95 * a.rate
    charges = LedgerEntry.objects.filter(entry_type=billing.EntryType.CHARGE, created_at__gte=started_at, amount__lt=0)  # 이번 실행 구간·실제로 받은 금액만(앞 실행의 과금은 세지 않는다)
    charged = {"entries": charges.count(), "amount": -(charges.aggregate(s=Sum("amount"))["s"] or 0)}
    j0 = {"ok": not missing and traffic.no_ad <= 0.01 * max(pairs, 1) and rate_ok and charged["entries"] > 0,
          "missing_event_names": missing, "decisions_without_ad": traffic.no_ad, "rate_ok": rate_ok, "charges_in_run": charged}
    report = {
        "started_at": started_at.isoformat(), "ended_at": ended_at.isoformat(), "duration_s": a.duration, "rate_target": a.rate,
        "rate_achieved": round(rate, 2) if rate is not None else None, "traffic_elapsed_s": round(traffic.elapsed, 1),
        "schedule_lag_max_s": round(traffic.lag_max, 3), "workers": a.workers, "threads": a.threads, "give_up_s": a.give_up,
        "final_status": {f"{k}:{s}": c for (k, s), c in sorted(traffic.status.items())},
        "first_try_status": {f"{k}:{s}": c for (k, s), c in sorted(traffic.first_try.items())},
        "latency_total_ms": {k: {"p50": pct(v, 50), "p95": pct(v, 95), "p99": pct(v, 99), "max": pct(v, 100)} for k, v in traffic.latency.items()},
        "acked": {"decisions": len(traffic.acked_decisions), "reactions": len(traffic.acked_reactions)},
        "changes": {str(k): v for k, v in done.items()}, "outbox_rows_added": EventOutbox.objects.count() - outbox_before,
        "outbox_by_name_in_run": dict(sorted(by_name.items())), "server_exit_codes": exit_codes,
        "funded_balances": funded, "J0": j0,
        "J1": {"ok": not j1_bad and exit_codes == [0, 0], "not_2xx_final": j1_bad, "servers_alive_to_end": exit_codes == [0, 0], "http_500": sum(c for (k, s), c in traffic.status.items() if s == 500)},
        "J2": {"ok": not j2, "problems": j2}, "J3": {"ok": not j3, "problems": j3},
    }
    out = CWD / a.report if a.report else LAB / f"report-{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print("E2E_REPORT " + json.dumps({k: report[k] for k in ("rate_achieved", "schedule_lag_max_s", "acked", "outbox_rows_added", "outbox_by_name_in_run", "changes", "J0", "J1", "J2", "J3")}, ensure_ascii=False), flush=True)
    print(f"report {out}", flush=True)
    sys.exit(0 if all(report[j]["ok"] for j in ("J0", "J1", "J2", "J3")) else 1)


if __name__ == "__main__":
    main()
