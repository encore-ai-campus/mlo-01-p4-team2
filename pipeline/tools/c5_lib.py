"""C5 공용: DB·Kafka·Connect REST·슬롯 관측. 비밀번호는 파일에서 읽어 연결에만 쓴다."""
import json, os, subprocess, sys, time, urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
import psycopg
PY = sys.executable
PG_PORT = int(os.environ.get("PG_PORT", "5010"))  # HAProxy rw — 늘 현재 primary
KAFKA = os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:9092,127.0.0.1:9192,127.0.0.1:9292")
ENV = {"JAVA_HOME": "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home", "PATH": "/usr/bin:/bin"}
NAME, REST, TOPIC = "ads-outbox-cdc", "http://127.0.0.1:8083", "ads.outbox.events"
def now(): return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"
def db(): return psycopg.connect(host="127.0.0.1", port=PG_PORT, user="postgres", password=Path("/opt/postgresql/config/postgres-password").read_text().strip(), dbname="ads_pipeline_lab", autocommit=True)
def kafka(*a): return subprocess.run([f"/opt/kafka/current/bin/{a[0]}", "--bootstrap-server", KAFKA, *a[1:]], env=ENV, capture_output=True, text=True, timeout=180)
def rest(m, p):
    req = urllib.request.Request(REST + p, method=m, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r: d = r.read(); return json.loads(d) if d else None
def state():
    s = rest("GET", f"/connectors/{NAME}/status"); return [s["connector"]["state"]] + [t["state"] for t in s.get("tasks", [])]
def wait_state(want, limit=60):
    for _ in range(limit * 2):
        st = state()
        if all(x == want for x in st): return st
        time.sleep(0.5)
    return state()
SLOT_SQL = """select active, restart_lsn::text, confirmed_flush_lsn::text, pg_current_wal_lsn()::text,
  pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint, pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint, wal_status
  from pg_replication_slots where slot_name = 'ads_outbox_slot'"""
def slot():
    with db() as c: r = c.execute(SLOT_SQL).fetchone()
    return dict(zip(("active", "restart_lsn", "confirmed_flush_lsn", "current_wal_lsn", "retained_bytes", "unconfirmed_bytes", "wal_status"), r))
def offsets():
    return {int(x.split(":")[1]): int(x.split(":")[2]) for x in kafka("kafka-get-offsets.sh", "--topic", TOPIC).stdout.split()}
def outbox_ids():
    with db() as c: return {r[0]: (r[1], r[2]) for r in c.execute("select event_id::text, event_name, created_at from events_outbox")}
def messages():
    """토픽 전체를 read_committed 로 읽는다: (partition, offset, key, value)."""
    out = []
    for p in range(3):
        o = kafka("kafka-console-consumer.sh", "--topic", TOPIC, "--partition", str(p), "--offset", "earliest", "--isolation-level", "read_committed", "--timeout-ms", "8000",
                  "--formatter-property", "print.partition=true", "--formatter-property", "print.offset=true", "--formatter-property", "print.key=true")
        for line in o.stdout.splitlines():
            if line.startswith("Partition:"):
                part, off, key, val = line.split("\t", 3)
                out.append({"partition": int(part.split(":")[1]), "offset": int(off.split(":")[1]), "key": key, "value": val})
    return out
def kafka_event_ids(msgs): return Counter(json.loads(m["value"])["after"]["event_id"] for m in msgs)
