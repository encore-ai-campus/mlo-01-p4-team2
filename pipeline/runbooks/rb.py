"""RB-1·RB-2·RB-3 커넥터 쪽 절차(설계 01 §6). **기본은 dry-run** — 읽기 조회만 하고 할 일을 적는다. `--apply` 일 때만 바꾼다.

사용:
  rb.py rb1 --connector ads-outbox-cdc [--apply]                     슬롯·offset 불일치(P5·K3·C4)
  rb.py rb2 --connector ads-outbox-cdc [--apply]                     PostgreSQL 복원·비동기 승격(P4·P2) — 새 원천 세대
  rb.py rb3 --connector ads-outbox-cdc --generation g2 [--apply --bronze-stopped]   Kafka 데이터 상실·꼬리 유실(K3·K4)
- 정지는 REST `PUT /connectors/{name}/stop`(pause 아님 — offsets API 는 STOPPED 에서만 되고, EOS 커넥터는 쓰기 전에 좀비 펜싱을 한다).
- 슬롯 이름·DB·토픽은 커넥터의 지금 설정에서 읽는다. 슬롯은 커넥터 DB 에 붙어 지운다(논리 슬롯은 만든 DB 에서만 지울 수 있다).
- Bronze 정지·재시작, 정본 설정 파일 고침, Silver 구독, RB-0 재발행은 사람이 한다 — 끝에 할 일로 적는다(README 점검표).
- rc: 0 끝(또는 dry-run 끝), 1 중단(조건 불충족·조회 실패).
"""
import argparse
import os
import subprocess
import sys
import time

from rb_lsn_check import E, check, pg, rest

KAFKA = E("KAFKA_BOOTSTRAP", "127.0.0.1:9092,127.0.0.1:9192,127.0.0.1:9292")
KAFKA_BIN = E("KAFKA_BIN", "/opt/kafka/current/bin")
JAVA_HOME = E("JAVA_HOME", "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home")


class Run:
    def __init__(self, a):
        self.a, self.name = a, a.connector
        self.config = rest(a.rest, "GET", f"/connectors/{self.name}/config")
        self.slot, self.db = self.config["slot.name"], self.config["database.dbname"]

    def step(self, text, action=None):
        """바꾸는 단계: dry-run 이면 적기만 한다."""
        print(("APPLY " if self.a.apply else "DRY   ") + text, flush=True)
        if self.a.apply and action:
            action()

    def info(self, text):
        print("INFO  " + text, flush=True)

    def state(self):
        return rest(self.a.rest, "GET", f"/connectors/{self.name}/status")["connector"]["state"]

    def wait(self, want, limit=90):
        for _ in range(limit):
            s = rest(self.a.rest, "GET", f"/connectors/{self.name}/status")
            states = [s["connector"]["state"]] + [t["state"] for t in s.get("tasks", [])]
            if states[0] == want and all(x == want for x in states[1:]) and (want == "STOPPED" or len(states) > 1):
                self.info(f"{self.name} {states}")
                return
            time.sleep(1)
        raise SystemExit(f"{self.name} 가 {want} 가 되지 않음: {states}")

    def stop(self):
        self.info(f"{self.name} 지금 {self.state()}")
        self.step(f"PUT /connectors/{self.name}/stop → STOPPED 까지 기다림",
                  lambda: (rest(self.a.rest, "PUT", f"/connectors/{self.name}/stop"), self.wait("STOPPED")))

    def slot_row(self):
        with pg(self.a.pg_port, self.db) as c:
            return c.execute("select active, wal_status, failover, confirmed_flush_lsn::text from pg_replication_slots where slot_name = %s",
                             (self.slot,)).fetchone()

    def drop_slot(self):
        with pg(self.a.pg_port, self.db) as c:
            c.execute("select pg_drop_replication_slot(%s)", (self.slot,))

    def clean_slot(self, always):
        """RB-1: 무효(lost)면 지운다. RB-2: 있으면 지운다(새 원천 세대 — 새 슬롯은 커넥터가 지금 위치에 failover=true 로 만든다)."""
        row = self.slot_row()
        if row is None:
            self.info(f"슬롯 {self.slot} 없음 — 재개 때 커넥터가 지금 위치에 새로 만든다")
            return
        active, wal_status, failover, confirmed = row
        self.info(f"슬롯 {self.slot}: active={active} wal_status={wal_status} failover={failover} confirmed_flush_lsn={confirmed}")
        for _ in range(30 if self.a.apply else 0):  # STOPPED 직후에는 walsender 가 아직 슬롯을 쥐고 있을 수 있다
            if not active:
                break
            time.sleep(1)
            row = self.slot_row()
            if row is None:
                self.info(f"슬롯 {self.slot} 이 기다리는 동안 없어졌다 — 재개 때 커넥터가 지금 위치에 새로 만든다")
                return
            active = row[0]
        if active and self.a.apply:
            raise SystemExit(f"슬롯 {self.slot} 를 30초 넘게 누가 쓰고 있다 — 커넥터가 STOPPED 인지 확인(커넥터는 STOPPED 로 남는다, 다시 돌리면 이어서 한다)")
        if always or wal_status == "lost":
            self.step(f"pg_drop_replication_slot('{self.slot}') (DB {self.db})", self.drop_slot)
        else:
            self.info("슬롯이 살아 있다 — 그대로 두고 offset 만 지운다(재개하면 슬롯 위치부터 읽는다)")

    def delete_offsets(self):
        self.info(f"저장된 offset: {rest(self.a.rest, 'GET', f'/connectors/{self.name}/offsets')}")
        self.step(f"DELETE /connectors/{self.name}/offsets", lambda: self.info(str(rest(self.a.rest, "DELETE", f"/connectors/{self.name}/offsets"))))

    def resume(self):
        self.step(f"PUT /connectors/{self.name}/resume → RUNNING 까지 기다림",
                  lambda: (rest(self.a.rest, "PUT", f"/connectors/{self.name}/resume"), self.wait("RUNNING")))

    def todo(self, *lines):
        for line in lines:
            print("TODO  " + line, flush=True)


def rb1(r):
    r.stop()
    r.clean_slot(always=False)
    r.delete_offsets()
    r.resume()
    r.todo("불일치가 시작된 창부터 RB-0(대사 보고의 mismatch 창 → outbox 재발행 → 다음 대사에서 match)")


def rb2(r):
    r.stop()
    rc, detail = check(r.name, r.a.rest, r.a.pg_port)
    r.info(f"재개 전 LSN 점검 rc={rc} {detail}")
    if rc == 4:
        raise SystemExit("LSN 점검 조회 실패 — 중단")
    # offset 이 앞서면(rc=2) 반드시 지운다. 앞서지 않아도 지운다: 새 슬롯은 지금 위치에서 시작하므로 옛 offset 이 남으면 R1(trust_offset)이 멈춘다.
    r.clean_slot(always=True)
    r.delete_offsets()
    r.resume()
    r.todo("복원 시점이 든 창부터 RB-0",
           "대사에서 'Bronze에만 있음'(유령) 창 목록을 사건 기록으로 남기고 팀 Silver 표시 규칙(D9·D10)에 넘긴다")


def kafka(*args):
    return subprocess.run([f"{KAFKA_BIN}/{args[0]}", "--bootstrap-server", KAFKA, *args[1:]], env=dict(os.environ, JAVA_HOME=JAVA_HOME),
                          capture_output=True, text=True, timeout=120)


def ungen(name, sep):
    """세대 꼬리(.g2·/g2)를 뗀 이름 — 세대는 한 번만 붙는다."""
    head, _, tail = name.rpartition(sep + "g")
    return head if head and tail.isdigit() else name


def rb3(r):
    a = r.a
    old = r.config["transforms.route.replacement"]
    new = f"{ungen(old, '.')}.{a.generation}"
    resuming = new == old  # 지난 --apply 가 설정을 바꾼 뒤(재개 전) 끊겼다 — STOPPED 면 이어서 한다
    if resuming and r.state() != "STOPPED":
        raise SystemExit(f"새 토픽 {new} 가 지금 토픽과 같은데 커넥터가 STOPPED 가 아니다 — 이미 끝난 세대다. 다시 하려면 --generation 을 올린다")
    if a.apply and not a.bronze_stopped:
        raise SystemExit("Bronze 를 먼저 멈춘다(run-bronze.sh 에 TERM). 멈췄으면 --bronze-stopped")
    r.info(f"토픽 {ungen(old, '.') if resuming else old} → {new}" + (" — 지난 실행이 설정까지 바꾸고 멈춤(STOPPED): 이어서 한다" if resuming else ""))
    r.stop()
    have = kafka("kafka-topics.sh", "--list")
    if have.returncode:
        raise SystemExit(f"Kafka 조회 실패: {have.stderr.strip()[-300:]}")
    if new in have.stdout.split():
        r.info(f"토픽 {new} 이미 있음 — 그대로 쓴다")
    else:
        create = ["kafka-topics.sh", "--create", "--topic", new, "--partitions", str(a.partitions), "--replication-factor", str(a.replication_factor)]
        for c in a.topic_config:
            create += ["--config", c]
        r.step(" ".join(create), lambda: kafka(*create).check_returncode())
    if resuming:
        r.info(f"설정은 이미 transforms.route.replacement={new} — PUT 하지 않는다")
    else:
        config = dict(r.config, **{"transforms.route.replacement": new})
        r.step(f"PUT /connectors/{r.name}/config (transforms.route.replacement={new}, STOPPED 유지)",
               lambda: rest(a.rest, "PUT", f"/connectors/{r.name}/config", config))
    r.clean_slot(always=False)
    r.delete_offsets()
    r.resume()
    checkpoint = ungen(a.bronze_checkpoint.rstrip("/"), "/") + "/" + a.generation
    r.todo(f"정본 설정의 transforms.route.replacement 를 {new} 로 고쳐 저장소에 남긴다(운영 pipeline/connect/ads-outbox-cdc.json, 시험 커넥터는 tools/e2e_setup.py)",
           f"Bronze 재시작: BRONZE_TOPIC={new} BRONZE_CHECKPOINT={checkpoint} BRONZE_TABLE=<지금 표 그대로> bash pipeline/bronze/run-bronze.sh (새 checkpoint 라 처음부터 = earliest)",
           f"Silver 구독에 {new} 추가(옛 토픽 행은 Bronze 의 topic 열로 갈린다)",
           "상실 시점이 든 창부터 RB-0")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rb", choices=["rb1", "rb2", "rb3"])
    ap.add_argument("--connector", required=True)
    ap.add_argument("--apply", action="store_true", help="실제로 바꾼다(없으면 dry-run)")
    ap.add_argument("--rest", default=E("CONNECT_REST", "http://127.0.0.1:8083"))
    ap.add_argument("--pg-port", type=int, default=int(E("PG_PORT", "5010")))
    ap.add_argument("--generation", help="rb3: 새 세대 이름(예: g2). 이름 규칙은 팀 통합 때 확정")
    ap.add_argument("--bronze-stopped", action="store_true", help="rb3 --apply: Bronze 를 멈췄음을 확인")
    ap.add_argument("--bronze-checkpoint", default=E("BRONZE_CHECKPOINT"), help="rb3: 지금 Bronze checkpoint(세대 꼬리 없이). 기본 없음 — 커넥터마다 다르다")
    ap.add_argument("--partitions", type=int, default=3)
    ap.add_argument("--replication-factor", type=int, default=3)
    ap.add_argument("--topic-config", action="append", default=["min.insync.replicas=2"], help="rb3: 옛 토픽과 같은 설정(보존 R5 등)을 여러 번")
    a = ap.parse_args()
    if a.rb == "rb3" and not (a.generation and a.bronze_checkpoint):
        ap.error("rb3 는 --generation 과 --bronze-checkpoint(또는 BRONZE_CHECKPOINT)가 필요하다")
    print(f"{a.rb} {a.connector} {'APPLY' if a.apply else 'DRY-RUN'}", flush=True)
    {"rb1": rb1, "rb2": rb2, "rb3": rb3}[a.rb](Run(a))
    print("끝" + ("" if a.apply else " — dry-run: 아무것도 바꾸지 않았다. 바꾸려면 --apply"), flush=True)
    sys.exit(0)
