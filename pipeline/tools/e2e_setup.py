"""종단 시험 전용 환경을 만든다(설계 §11.1 — lab 과 섞이지 않는 DB·publication·슬롯·커넥터·토픽). 다시 돌려도 안전하다(있으면 그대로 둠).

사용: e2e_setup.py
- DB `ads_pipeline_e2e`(template1 의 builtin C.UTF-8), 광고 서버 마이그레이션, CDC 계정 권한, INSERT 전용 publication
- Kafka 토픽: 이벤트 3 파티션, signal 1 파티션(채널이 파티션 0만 읽음), heartbeat 1 파티션 — 모두 복제 3·min.insync.replicas=2
- 커넥터 `ads-outbox-e2e`: 정본 `connect/ads-outbox-cdc.json` 에서 이름·DB·슬롯·토픽만 바꿔 PUT(슬롯은 Debezium 이 failover=true 로 만든다)
비밀번호는 파일에서 읽어 연결에만 쓴다.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
DB = "ads_pipeline_e2e"
PG_PORT = int(os.environ.get("PG_PORT", "5010"))
PW = Path("/opt/postgresql/config/postgres-password").read_text().strip()
KAFKA = os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:9092,127.0.0.1:9192,127.0.0.1:9292")
REST = os.environ.get("CONNECT_REST", "http://127.0.0.1:8083")
NAME, PREFIX, SLOT = "ads-outbox-e2e", "adse2e", "ads_e2e_slot"
TOPIC, SIGNAL, HEARTBEAT = "ads.e2e.outbox.events", "ads.e2e.outbox.signal", f"__debezium-heartbeat.{PREFIX}"
LAB = Path(tempfile.gettempdir()) / DB
ENV = dict(os.environ, JAVA_HOME="/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home")


def pg(dbname):
    return psycopg.connect(host="127.0.0.1", port=PG_PORT, user="postgres", password=PW, dbname=dbname, autocommit=True)


def database():
    with pg("postgres") as c:
        if not c.execute("select 1 from pg_database where datname = %s", (DB,)).fetchone():
            c.execute(f'create database "{DB}"')
            print(f"db {DB} 만듦")
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="config.settings.dev", DB_HOST="127.0.0.1", DB_PORT=str(PG_PORT), DB_USER="postgres",
               DB_PASSWORD=PW, DB_NAME=DB, MEDIA_ROOT=str(LAB / "media"), STATIC_ROOT=str(LAB / "static"))
    r = subprocess.run([sys.executable, "manage.py", "migrate", "--no-input"], cwd=ROOT / "ads_server", env=env, capture_output=True, text=True)
    print(f"migrate rc={r.returncode} {(r.stdout.strip().splitlines() or [''])[-1]}")
    if r.returncode:
        raise SystemExit(r.stderr[-2000:])
    with pg(DB) as c:
        c.execute(f'grant connect on database "{DB}" to ads_cdc')
        c.execute("grant usage on schema public to ads_cdc")
        c.execute("grant select on public.events_outbox to ads_cdc")  # 증분 스냅샷(D13)이 outbox 를 읽는다
        if not c.execute("select 1 from pg_publication where pubname = 'ads_outbox_pub'").fetchone():
            c.execute("create publication ads_outbox_pub for table public.events_outbox with (publish = 'insert')")
        print("publication", c.execute("select pubname, pubinsert, pubupdate, pubdelete, pubtruncate from pg_publication").fetchall())


def topics():
    have = set(subprocess.run(["/opt/kafka/current/bin/kafka-topics.sh", "--bootstrap-server", KAFKA, "--list"], env=ENV, capture_output=True, text=True, check=True).stdout.split())
    for name, partitions in ((TOPIC, 3), (SIGNAL, 1), (HEARTBEAT, 1)):
        if name not in have:
            subprocess.run(["/opt/kafka/current/bin/kafka-topics.sh", "--bootstrap-server", KAFKA, "--create", "--topic", name, "--partitions", str(partitions),
                            "--replication-factor", "3", "--config", "min.insync.replicas=2"], env=ENV, check=True, capture_output=True)
            print(f"topic {name} 만듦({partitions} 파티션)")


def rest(method, path, body=None):
    req = urllib.request.Request(REST + path, method=method, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def connector():
    config = json.loads((ROOT / "pipeline" / "connect" / "ads-outbox-cdc.json").read_text())["config"]
    config.update({
        "name": NAME, "database.dbname": DB, "topic.prefix": PREFIX, "slot.name": SLOT,
        "transforms.route.regex": PREFIX + r"\.public\.events_outbox", "transforms.route.replacement": TOPIC,
        "signal.kafka.topic": SIGNAL, "signal.kafka.groupId": f"{NAME}-signal",
    })
    try:
        current = rest("GET", f"/connectors/{NAME}/config")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        current = None
    if current != config:  # 같으면 올리지 않는다(같은 설정을 다시 PUT 하면 커넥터가 재배치된다)
        rest("PUT", f"/connectors/{NAME}/config", config)
        print(f"connector {NAME} 설정 올림")
    states = []
    for _ in range(60):
        try:
            s = rest("GET", f"/connectors/{NAME}/status")
        except urllib.error.HTTPError as error:  # 막 만든 직후에는 상태가 아직 없다
            if error.code != 404:
                raise
            time.sleep(1)
            continue
        states = [s["connector"]["state"]] + [t["state"] for t in s.get("tasks", [])]
        if len(states) > 1 and all(x == "RUNNING" for x in states):
            print(f"connector {NAME} {states}")
            return
        time.sleep(1)
    raise SystemExit(f"connector {NAME} not RUNNING: {states}")


if __name__ == "__main__":
    database()
    topics()
    connector()
    with pg(DB) as c:
        print("slot", c.execute("select slot_name, active, failover, wal_status from pg_replication_slots where slot_name = %s", (SLOT,)).fetchone())
