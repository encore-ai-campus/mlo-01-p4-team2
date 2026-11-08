"""종단 시험 전용 환경을 만든다(설계 §11.1 — lab 과 섞이지 않는 DB·publication·슬롯·커넥터·토픽). 다시 돌려도 안전하다(있으면 그대로 둠).

사용: e2e_setup.py                      만든다(있으면 그대로 둠)
      e2e_setup.py --rebuild            전체 다시 만들기 — 점검만(읽기만). 지울 대상·남기는 대상·확인 결과를 출력. rc 0 = 확인 통과, 2 = 확인 실패
      e2e_setup.py --rebuild --execute  전체 다시 만들기 — 실행(되돌릴 수 없음, 사용자 허락 뒤). rc 0 = 끝, 1 = 단계 실패(그 자리에서 멈춤), 2 = 확인 실패
- 만들기: DB `ads_pipeline_e2e`(template1 의 builtin C.UTF-8), 광고 서버 마이그레이션, CDC 계정 권한, INSERT 전용 publication
- Kafka 토픽: 이벤트 3 파티션, signal 1 파티션(채널이 파티션 0만 읽음), heartbeat 1 파티션 — 모두 복제 3·min.insync.replicas=2
- 커넥터 `ads-outbox-e2e`: 정본 `connect/ads-outbox-cdc.json` 에서 이름·DB·슬롯·토픽만 바꿈(connector_config — 두 길이 같은 설정을 씀). 슬롯은 Debezium 이 failover=true 로 만든다
비밀번호는 파일에서 읽어 연결에만 쓴다. 출력에는 비밀번호·S3 키·프로세스 환경·커넥터 설정 전체를 넣지 않는다(설정은 CHECK_KEYS 여섯 키만).

전체 다시 만들기(설계 01 §11.2 — Phase 시작 때 DB·슬롯·커넥터 offset·시험 토픽·Bronze 경로·checkpoint 를 함께 새로; 계획 temp/review/E2E-1-plan.md 2판)
- 지움(이것뿐, 모두 `e2e` 이름): 커넥터 ads-outbox-e2e 의 offset 과 커넥터, 소비 그룹 ads-outbox-e2e-signal, 슬롯 ads_e2e_slot, DB ads_pipeline_e2e,
  토픽 ads.e2e.outbox.events·ads.e2e.outbox.signal·__debezium-heartbeat.adse2e, S3 ads-lake/bronze_e2e/ads_outbox_events/·checkpoints/bronze_e2e_ads_outbox/,
  $TMPDIR/ads_pipeline_e2e/ctx.json
- 남김: lab·메타버스 DB·슬롯·커넥터·토픽·그룹·Bronze·checkpoint, 그 밖의 DB(appdb·polaris …)·버킷, Patroni·standby-slots-sync 설정(같은 이름으로 다시 만듦)
- 순서: 멈춤 → offset → 커넥터 → 그룹 → 슬롯(대기 사본이 사라질 때까지) → DB → 토픽 → S3 → ctx → 만들기(커넥터는 STOPPED 로 만들어 offset 이 빈 것을 본 뒤 resume)
  → 대기 서버 synced=t. 단계마다 이미 없으면 넘어간다 — 멈춘 뒤 같은 명령으로 이어 간다.
- 도구 밖(사람): --execute 전에 e2e Bronze 정상 종료(run-bronze.sh 에 SIGTERM), 끝난 뒤 e2e Bronze 다시 켬과 확인 유입(e2e_load.py·c4_reconcile.py).
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
DB = "ads_pipeline_e2e"
PG_PORT = int(os.environ.get("PG_PORT", "5010"))
NODE_PORTS = [int(p) for p in os.environ.get("PG_NODE_PORTS", "5432,5433,5434").split(",") if p]
PW_FILE = Path("/opt/postgresql/config/postgres-password")
KAFKA = os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:9092,127.0.0.1:9192,127.0.0.1:9292")
KAFKA_BIN = Path("/opt/kafka/current/bin")
REST = os.environ.get("CONNECT_REST", "http://127.0.0.1:8083")
PATRONI = os.environ.get("PATRONI_REST", "http://127.0.0.1:8008,http://127.0.0.1:8009,http://127.0.0.1:8010").split(",")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://127.0.0.1:9000")
S3_KEY_FILES = (os.environ.get("S3_ACCESS_KEY_FILE", "/opt/rustfs/config/access-key"), os.environ.get("S3_SECRET_KEY_FILE", "/opt/rustfs/config/secret-key"))
BUCKET = "ads-lake"
NAME, PREFIX, SLOT = "ads-outbox-e2e", "adse2e", "ads_e2e_slot"
TOPIC, SIGNAL, HEARTBEAT = "ads.e2e.outbox.events", "ads.e2e.outbox.signal", f"__debezium-heartbeat.{PREFIX}"
TOPICS = (TOPIC, SIGNAL, HEARTBEAT)
GROUP = f"{NAME}-signal"
S3_PREFIXES = ("bronze_e2e/ads_outbox_events/", "checkpoints/bronze_e2e_ads_outbox/")
LAB = Path(tempfile.gettempdir()) / DB
CTX = LAB / "ctx.json"
ENV = dict(os.environ, JAVA_HOME="/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home")
CHECK_KEYS = ("database.dbname", "slot.name", "topic.prefix", "transforms.route.replacement", "signal.kafka.topic", "signal.kafka.groupId")
# 남기는 대상(계획 §2) — 지울 이름이 이것과 겹치면 거부한다. 출력의 "남기는 대상" 줄도 이것을 본다
KEEP = {
    "db": ("ads_pipeline_lab", "metaverse_lab", "appdb", "polaris", "postgres", "template0", "template1"),
    "slot": ("ads_outbox_slot", "metaverse_outbox_slot", "node1", "node2", "node3"),
    "connector": ("ads-outbox-cdc", "metaverse-outbox-cdc"),
    "topic": ("ads.outbox.events", "ads.outbox.signal", "__debezium-heartbeat.ads", "metaverse.outbox.events", "metaverse.outbox.signal",
              "__debezium-heartbeat.metaverse", "connect-configs", "connect-offsets", "connect-status", "__consumer_offsets", "__transaction_state"),
    "group": ("ads-outbox-cdc-signal", "metaverse-outbox-cdc-signal"),
    "s3": ("bronze/", "checkpoints/bronze_ads_outbox/", "checkpoints/bronze/", "manifests/", "_verify/"),
}
HEALTHY_SLOTS = ("ads_outbox_slot", "metaverse_outbox_slot")  # §4-7: primary 에서 active 여야 함
LIVE_PATTERNS = ("ads.e2e", "bronze_e2e", DB, "e2e_load.py", "er1.py")  # §4-6: 이 글자를 명령·환경에 가진 프로세스


class StepError(Exception):
    pass


def password():
    return PW_FILE.read_text().strip()


def pg(dbname, port=PG_PORT):
    return psycopg.connect(host="127.0.0.1", port=port, user="postgres", password=password(), dbname=dbname, autocommit=True)


def kafka(tool, *args):
    return subprocess.run([str(KAFKA_BIN / tool), "--bootstrap-server", KAFKA, *args], env=ENV, capture_output=True, text=True, check=True, timeout=120).stdout


def database():
    with pg("postgres") as c:
        if not c.execute("select 1 from pg_database where datname = %s", (DB,)).fetchone():
            c.execute(f'create database "{DB}"')
            print(f"db {DB} 만듦")
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="config.settings.dev", DB_HOST="127.0.0.1", DB_PORT=str(PG_PORT), DB_USER="postgres",
               DB_PASSWORD=password(), DB_NAME=DB, MEDIA_ROOT=str(LAB / "media"), STATIC_ROOT=str(LAB / "static"))
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


def topic_list():
    return set(kafka("kafka-topics.sh", "--list").split())


def topics():
    have = topic_list()
    for name, partitions in ((TOPIC, 3), (SIGNAL, 1), (HEARTBEAT, 1)):
        if name not in have:
            kafka("kafka-topics.sh", "--create", "--topic", name, "--partitions", str(partitions), "--replication-factor", "3", "--config", "min.insync.replicas=2")
            print(f"topic {name} 만듦({partitions} 파티션)")


def rest(method, path, body=None):
    req = urllib.request.Request(REST + path, method=method, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def connector_config():
    """두 길(인자 없음 PUT config, --rebuild --execute 의 POST initial_state=STOPPED)과 §4-2 확인이 모두 이 dict 를 쓴다."""
    config = json.loads((ROOT / "pipeline" / "connect" / "ads-outbox-cdc.json").read_text())["config"]
    config.update({
        "name": NAME, "database.dbname": DB, "topic.prefix": PREFIX, "slot.name": SLOT,
        "transforms.route.regex": PREFIX + r"\.public\.events_outbox", "transforms.route.replacement": TOPIC,
        "signal.kafka.topic": SIGNAL, "signal.kafka.groupId": GROUP,
    })
    return config


def status_or_none(name):
    try:
        return rest("GET", f"/connectors/{name}/status")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        return None


def wait_running():
    states = []
    for _ in range(60):
        s = status_or_none(NAME)  # 막 만든 직후에는 상태가 아직 없다
        if s is None:
            time.sleep(1)
            continue
        states = [s["connector"]["state"]] + [t["state"] for t in s.get("tasks", [])]
        if len(states) > 1 and all(x == "RUNNING" for x in states):
            print(f"connector {NAME} {states}")
            return
        time.sleep(1)
    raise SystemExit(f"connector {NAME} not RUNNING: {states}")


def connector():
    config = connector_config()
    try:
        current = rest("GET", f"/connectors/{NAME}/config")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        current = None
    if current != config:  # 같으면 올리지 않는다(같은 설정을 다시 PUT 하면 커넥터가 재배치된다)
        rest("PUT", f"/connectors/{NAME}/config", config)
        print(f"connector {NAME} 설정 올림")
    wait_running()


# ---- 전체 다시 만들기: 읽기 ----

def nodes():
    out = []
    for port in NODE_PORTS:
        try:
            with pg("postgres", port) as c:
                recovery = c.execute("select pg_is_in_recovery()").fetchone()[0]
                slots = {r[0]: {"type": r[1], "database": r[2], "active": r[3], "failover": r[4], "synced": r[5]} for r in c.execute(
                    "select slot_name, slot_type, database, active, failover, synced from pg_replication_slots").fetchall()}
                streaming = None if recovery else c.execute(  # 물리 복제만(논리 walsender 인 Debezium 은 빼고)
                    "select count(*) from pg_stat_replication r join pg_replication_slots s on s.active_pid = r.pid "
                    "where s.slot_type = 'physical' and r.state = 'streaming'").fetchone()[0]
            out.append({"port": port, "recovery": recovery, "slots": slots, "streaming": streaming})
        except psycopg.Error as error:
            out.append({"port": port, "error": type(error).__name__})
    return out


def primary_of(ns):
    found = [n for n in ns if n.get("recovery") is False]
    return found[0] if len(found) == 1 else None


def db_state():
    """postgres DB 에서만 본다(e2e DB 에 붙지 않음 — S8 이 도구 자신의 연결로 막히지 않게)."""
    with pg("postgres") as c:
        names = [r[0] for r in c.execute("select datname from pg_database order by 1").fetchall()]
        size = c.execute("select pg_database_size(datname) from pg_database where datname = %s", (DB,)).fetchone()
        conns = c.execute("select usename, coalesce(application_name, ''), backend_type from pg_stat_activity where datname = %s and pid <> pg_backend_pid()",
                          (DB,)).fetchall()
    return {"exists": DB in names, "bytes": size[0] if size else 0, "conns": [list(r) for r in conns], "names": names}


def topic_messages(topic):
    out = kafka("kafka-get-offsets.sh", "--topic", topic)
    return sum(int(line.rsplit(":", 1)[1]) for line in out.split())


def brokers_up():
    up = 0
    for host, port in (h.rsplit(":", 1) for h in KAFKA.split(",") if h):
        try:
            with socket.create_connection((host, int(port)), timeout=1):
                up += 1
        except OSError:
            pass
    return up, len(KAFKA.split(","))


def s3_client():
    import boto3
    keys = [Path(p).read_text().strip() for p in S3_KEY_FILES]
    return boto3.client("s3", endpoint_url=S3_ENDPOINT, aws_access_key_id=keys[0], aws_secret_access_key=keys[1], region_name=os.environ.get("S3_REGION", "ap-northeast-2"))


def s3_objects(s3, prefix):
    out, token = [], None
    while True:
        resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix, **({"ContinuationToken": token} if token else {}))
        out += [(o["Key"], o["Size"]) for o in resp.get("Contents", [])]
        if not resp.get("IsTruncated"):
            return out
        token = resp["NextContinuationToken"]


def patroni_slots():
    """DCS 설정의 영구 슬롯 이름(§4-10). 세 노드 모두 응답이 없으면 None."""
    for base in PATRONI:
        try:
            with urllib.request.urlopen(base + "/config", timeout=5) as resp:
                config = json.loads(resp.read())
            return {"from": base, "slots": sorted((config.get("slots") or {}).keys()),
                    "ignore_slots": sorted(s.get("name", "") for s in config.get("ignore_slots") or [])}
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return None


def processes():
    """e2e 이름을 명령·환경에 가진 프로세스(§4-6). 이 도구와 그 조상은 뺀다. 돌려주는 값에 환경은 넣지 않는다(비밀값 자리)."""
    rows = {}
    # macOS ps: -E 가 환경을 붙인다(-e 는 -A 와 같아 환경이 없다 — e2e Bronze 는 명령줄이 아니라 환경 BRONZE_TOPIC 에만 e2e 가 있음)
    for line in subprocess.run(["ps", "-axEww", "-o", "pid=,ppid=,command="], capture_output=True, text=True, check=True).stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit():
            rows[int(parts[0])] = (int(parts[1]), parts[2])
    mine, pid = set(), os.getpid()
    while pid in rows and pid not in mine:
        mine.add(pid)
        pid = rows[pid][0]
    hits = []
    for pid, (ppid, command) in sorted(rows.items()):
        found = [p for p in LIVE_PATTERNS if p in command]
        if command.startswith("postgres:"):  # PG 백엔드의 프로세스 제목(DB 이름이 들어감) — DB 연결은 §4-5 가 본다
            continue
        if found and pid not in mine and ppid != os.getpid():
            script = next((w.rsplit("/", 1)[-1] for w in command.split()[:4] if w.endswith((".py", ".sh"))), command.split()[0].rsplit("/", 1)[-1])
            hit = {"pid": pid, "what": script, "match": found}
            seen, up = set(), ppid  # S1 에서 SIGTERM 을 보낼 곳: 조상 가운데 run-bronze.sh
            while up in rows and up not in seen and up > 1:
                seen.add(up)
                if "run-bronze.sh" in rows[up][1]:
                    hit["runner"] = up
                    break
                up = rows[up][0]
            hits.append(hit)
    return hits


def survey():
    conns = rest("GET", "/connectors?expand=status&expand=info")
    connectors = {n: {"state": [v["status"]["connector"]["state"]] + [t["state"] for t in v["status"].get("tasks", [])],
                      "config": {k: ((v.get("info") or {}).get("config") or {}).get(k) for k in CHECK_KEYS}} for n, v in conns.items()}
    have_topics, groups, s3 = topic_list(), set(kafka("kafka-consumer-groups.sh", "--list").split()), s3_client()
    return {
        "connectors": connectors,
        "offsets": rest("GET", f"/connectors/{NAME}/offsets")["offsets"] if NAME in connectors else None,
        "group": GROUP in groups,
        "groups": sorted(groups & set(KEEP["group"])),
        "nodes": nodes(),
        "db": db_state(),
        "topics": {t: topic_messages(t) for t in TOPICS if t in have_topics},
        "keep_topics": sorted(have_topics & set(KEEP["topic"])),
        "brokers": brokers_up(),
        "under_min_isr": kafka("kafka-topics.sh", "--describe", "--under-min-isr-partitions").strip(),
        "s3": {p: s3_objects(s3, p) for p in S3_PREFIXES},
        "keep_s3": {p: len(s3_objects(s3, p)) for p in KEEP["s3"]},
        "dcs": patroni_slots(),
        "procs": processes(),
        "ctx": CTX.exists(),
    }


# ---- 전체 다시 만들기: 확인(계획 §4·§4.1) ----

def name_problems():
    """§4-1 이름 문: 지울 이름이 모두 e2e 이고 남기는 이름과 겹치지 않는다."""
    targets = {"db": [DB], "slot": [SLOT], "connector": [NAME], "topic": list(TOPICS), "group": [GROUP]}
    bad = [f"{kind} {n} 에 e2e 가 없음" for kind, names in targets.items() for n in names if "e2e" not in n]
    bad += [f"{kind} {n} 은 남기는 대상" for kind, names in targets.items() for n in names if n in KEEP[kind]]
    bad += [f"s3 {p} 에 e2e 가 없음" for p in S3_PREFIXES if "e2e" not in p]
    bad += [f"s3 {p} 가 남기는 {k} 와 겹침" for p in S3_PREFIXES for k in KEEP["s3"] if p.startswith(k) or k.startswith(p)]
    if CTX.name != "ctx.json" or CTX.parent != LAB or "e2e" not in str(LAB):
        bad.append(f"파일 {CTX} 가 e2e 작업 폴더의 ctx.json 이 아님")
    return bad


def judge(inv, execute):
    """(번호, 결과, 설명) 목록. 결과: 통과·실패·넘어감·할 일. 하나라도 실패면 아무것도 바꾸지 않는다."""
    out = []
    names = name_problems()
    out.append(("4-1", "실패" if names else "통과", "; ".join(names) or "지울 이름이 모두 e2e 이고 남기는 이름과 겹치지 않음"))
    e2e = inv["connectors"].get(NAME)
    want = {k: connector_config()[k] for k in CHECK_KEYS}
    if e2e is None:
        out.append(("4-2", "넘어감", "커넥터 없음 → 설정 확인 넘어감, S2~S5 넘어감, 옛 offset 은 S12 ①(STOPPED 로 만든 뒤)에서 보고 지움"))
    else:
        diff = [k for k in CHECK_KEYS if e2e["config"].get(k) != want[k]]
        out.append(("4-2", "실패" if diff else "통과", f"같은 이름인데 설정이 e2e 값과 다름: {diff}" if diff else f"설정 여섯 키가 e2e 값, 상태 {e2e['state']}"))
    others = [f"{n}.{k}={v}" for n, c in inv["connectors"].items() if n != NAME for k, v in c["config"].items() if v and "e2e" in str(v)]
    out.append(("4-3", "실패" if others else "통과", f"다른 커넥터가 e2e 를 씀: {others}" if others else "다른 커넥터는 e2e 를 쓰지 않음"))
    primary = primary_of(inv["nodes"])
    slot = (primary or {}).get("slots", {}).get(SLOT)
    if primary is None:
        out.append(("4-4", "실패", "primary 를 하나로 정할 수 없음"))
    elif slot is None:
        out.append(("4-4", "넘어감", f"primary {primary['port']} 에 슬롯 없음 → S7 은 대기 사본이 사라지는지만 봄"))
    else:
        bad = slot["type"] != "logical" or slot["database"] != DB
        busy = slot["active"] and e2e is None
        text = (f"슬롯이 e2e DB 의 논리 슬롯이 아님({slot['type']}, {slot['database']})" if bad else
                "커넥터가 없는데 슬롯이 active — 누가 쓰는 중" if busy else
                f"logical·{DB}, active={slot['active']}" + (" → S2·S3 에서 커넥터를 멈춰 쉬게 함" if slot["active"] else ""))
        out.append(("4-4", "실패" if bad or busy else "통과", text))
    db = inv["db"]
    if not db["exists"]:
        out.append(("4-5", "넘어감", "DB 없음 → S8 넘어감"))
    else:
        running = e2e is not None and e2e["state"][0] == "RUNNING"
        foreign = [c for c in db["conns"] if not (running and c[0] == "ads_cdc" and c[1].startswith("Debezium"))]
        out.append(("4-5", "실패" if foreign else "통과",
                    f"Debezium 말고 연결이 있음(또는 커넥터가 RUNNING 이 아닌데 연결이 있음): {foreign}" if foreign else f"연결 {len(db['conns'])}개, 모두 Debezium"))
    procs = inv["procs"]
    if procs:
        runners = sorted({p["runner"] for p in procs if "runner" in p})
        out.append(("4-6", "실패" if execute else "할 일", f"e2e 를 쓰는 프로세스 {procs} — S1(e2e Bronze 는 run-bronze.sh"
                    + (f" pid {runners}" if runners else "") + " 에 SIGTERM)·유입 도구를 멈춘 뒤 --execute"))
    else:
        out.append(("4-6", "통과", "e2e 를 쓰는 프로세스 없음"))
    sick = []
    if primary is None:
        sick.append("primary 가 하나가 아님")
    else:
        if primary["streaming"] != len(NODE_PORTS) - 1:
            sick.append(f"streaming 대기 {primary['streaming']}대(기대 {len(NODE_PORTS) - 1})")
        sick += [f"슬롯 {s} active 아님" for s in HEALTHY_SLOTS if not primary["slots"].get(s, {}).get("active")]
    sick += [f"노드 {n['port']} 접속 실패 {n['error']}" for n in inv["nodes"] if "error" in n]
    for n in KEEP["connector"]:
        st = inv["connectors"].get(n, {}).get("state")
        if not st or len(st) < 2 or any(x != "RUNNING" for x in st):  # 커넥터와 task 모두 RUNNING
            sick.append(f"커넥터 {n} {st or '없음'}")
    up, total = inv["brokers"]
    if up != total:
        sick.append(f"Kafka 브로커 {total}대 중 {up}대 응답")
    if inv["under_min_isr"]:
        sick.append("min ISR 미달 파티션 있음")
    out.append(("4-7", "실패" if sick else "통과", "; ".join(sick) or "primary 하나·대기 streaming·lab/메타버스 커넥터 RUNNING·슬롯 active·브로커 모두 응답"))
    foreign_keys = [k for p, objs in inv["s3"].items() for k, _ in objs if not k.startswith(p)]
    out.append(("4-8", "실패" if foreign_keys else "통과", f"접두사 밖 키: {foreign_keys[:5]}" if foreign_keys else "지울 객체 키가 모두 두 접두사 안(지우기 직전에 다시 확인)"))
    dcs = inv["dcs"]
    if dcs is None:
        out.append(("4-10", "실패", "Patroni 설정을 읽지 못함(세 노드 모두 응답 없음)"))
    else:
        out.append(("4-10", "실패" if SLOT in dcs["slots"] else "통과",
                    f"DCS 영구 슬롯에 {SLOT} 가 있음({dcs['from']}) — 지우면 Patroni 가 다시 만듦" if SLOT in dcs["slots"] else f"DCS 영구 슬롯에 {SLOT} 없음({dcs['from']}, slots={dcs['slots']})"))
    return out


def mb(n):
    return f"{n / 1e6:.1f} MB"


def report(inv, checks, execute):
    print(f"e2e 전체 다시 만들기 — {'실행 전 확인' if execute else '점검(읽기만)'} {datetime.now(UTC).isoformat(timespec='seconds')}")
    e2e, primary, db = inv["connectors"].get(NAME), primary_of(inv["nodes"]), inv["db"]
    present = {"D1": bool(inv["offsets"]), "D2": e2e is not None, "D3": inv["group"], "D4": SLOT in (primary or {}).get("slots", {}),  # primary 기준 — 대기 사본만 남았으면 일부만 지운 상태
               "D5": db["exists"], "D6": bool(inv["topics"]), "D7": bool(inv["s3"][S3_PREFIXES[0]]), "D8": bool(inv["s3"][S3_PREFIXES[1]]), "D9": inv["ctx"]}
    core = [present[k] for k in ("D2", "D4", "D5", "D6", "D7", "D8")]
    state = "처음부터(지울 대상이 모두 있음)" if all(core) else "모두 없음(만들기만 함)" if not any(present.values()) else \
        "일부만 지운 상태(없는 것: " + ", ".join(k for k, v in present.items() if not v) + ")"
    print(f"상태: {state}")
    print("지울 대상(계획 §2):")
    print(f"  D1 커넥터 offset {NAME}: " + ("커넥터 없음 — REST 로 볼 수 없음, S12 ① 에서 봄" if inv["offsets"] is None else f"{len(inv['offsets'])}줄 {inv['offsets']}"))
    print(f"  D2 커넥터 {NAME}: " + (f"있음 {e2e['state']} {e2e['config']}" if e2e else "없음"))
    print(f"  D3 소비 그룹 {GROUP}: {'있음' if inv['group'] else '없음'}")
    print(f"  D4 슬롯 {SLOT}: " + ", ".join(f"{n['port']}{'(primary)' if n.get('recovery') is False else ''} " +
                                          (f"접속 실패 {n['error']}" if "error" in n else str(n["slots"].get(SLOT)) if SLOT in n["slots"] else "없음") for n in inv["nodes"]))
    print(f"  D5 DB {DB}: " + (f"있음 {mb(db['bytes'])}, 연결 {len(db['conns'])}개 {db['conns']}" if db["exists"] else "없음"))
    print("  D6 토픽: " + ", ".join(f"{t} " + (f"있음(끝 offset 합 {inv['topics'][t]})" if t in inv["topics"] else "없음") for t in TOPICS))
    for d, p in zip(("D7", "D8"), S3_PREFIXES):
        print(f"  {d} S3 {BUCKET}/{p}: {len(inv['s3'][p])} 객체 {mb(sum(s for _, s in inv['s3'][p]))}")
    print(f"  D9 {CTX}: {'있음' if inv['ctx'] else '없음'}")
    print("남기는 대상(지금 상태 — 실행 뒤 같은지 본다):")
    for n in KEEP["connector"]:
        print(f"  커넥터 {n}: {inv['connectors'].get(n, {}).get('state', '없음')}")
    if primary:
        print(f"  슬롯(primary {primary['port']}): " + ", ".join(f"{s} active={primary['slots'][s]['active']}" if s in primary["slots"] else f"{s} 없음" for s in HEALTHY_SLOTS))
    print(f"  DB: {[n for n in db['names'] if n != DB]}")
    print(f"  토픽: {inv['keep_topics']}")
    print(f"  소비 그룹: {inv['groups']}")
    print("  S3: " + ", ".join(f"{BUCKET}/{p} {n} 객체" for p, n in inv["keep_s3"].items()))
    dcs = inv["dcs"]
    print("  Patroni DCS: " + (f"{dcs['from']} slots={dcs['slots']} ignore_slots={dcs['ignore_slots']}" if dcs else "읽지 못함"))
    print("확인(계획 §4·§4.1):")
    for number, result, text in checks:
        print(f"  {number} {result}: {text}")
    todo = [c for c in checks if c[1] == "할 일"]
    failed = [c for c in checks if c[1] == "실패"]
    if todo:
        print("실행 전 할 일: " + "; ".join(c[2] for c in todo))
    verdict = "확인 실패 — 바꾸지 않음(rc 2)" if failed else ("확인 통과 — 실행함" if execute else "확인 통과 — 사용자 허락 뒤 --rebuild --execute (rc 0)")
    print(f"판정: {verdict}")
    return 2 if failed else 0


# ---- 전체 다시 만들기: 단계(계획 §3) ----

def wait(check, seconds, what):
    for _ in range(seconds):
        if check():
            return
        time.sleep(1)
    if not check():
        raise StepError(f"{seconds}초 안에 {what} 가 되지 않음")


def e2e_connector_exists():
    return status_or_none(NAME) is not None


def primary_slot():
    primary = primary_of(nodes())
    if primary is None:
        raise StepError("primary 를 하나로 정할 수 없음")
    return primary["slots"].get(SLOT)


def offsets_now():
    return rest("GET", f"/connectors/{NAME}/offsets")["offsets"]


def s2_stop():
    if not e2e_connector_exists():
        return "커넥터 없음 — 넘어감"
    rest("PUT", f"/connectors/{NAME}/stop")
    wait(lambda: (status_or_none(NAME) or {}).get("connector", {}).get("state") == "STOPPED", 60, "STOPPED")
    return "STOPPED"


def s3_idle():
    wait(lambda: not (primary_slot() or {}).get("active"), 60, f"{SLOT} active=f")
    return "슬롯 쉼(또는 없음)"


def s4_offsets():
    if not e2e_connector_exists():
        return "커넥터 없음 — 넘어감(옛 offset 은 S12 ① 에서 봄)"
    rest("DELETE", f"/connectors/{NAME}/offsets")  # 응답 문구로 판정하지 않는다(Debezium 은 alterOffsets 를 덮지 않음) — GET 이 빈 목록인지 본다
    left = offsets_now()
    if left:
        raise StepError(f"offset 이 남음 {left}")
    return "offset 빈 목록"


def s5_delete():
    if not e2e_connector_exists():
        return "커넥터 없음 — 넘어감"
    rest("DELETE", f"/connectors/{NAME}")
    wait(lambda: not e2e_connector_exists(), 30, "커넥터 404")
    return "지움"


def s6_group():
    if GROUP not in kafka("kafka-consumer-groups.sh", "--list").split():
        return "그룹 없음 — 넘어감"
    kafka("kafka-consumer-groups.sh", "--delete", "--group", GROUP)
    if GROUP in kafka("kafka-consumer-groups.sh", "--list").split():
        raise StepError("그룹이 남음")
    return "지움"


def drop_slot():
    with pg("postgres") as c:
        c.execute("select pg_drop_replication_slot(slot_name) from pg_replication_slots where slot_name = %s and database = %s and slot_type = 'logical' and not active",
                  (SLOT, DB))


def s7_slot():
    """대기 서버 사본이 모두 사라져야 끝난다. 접속 실패 노드는 "아직 있음"으로 본다(fail closed).
    대기 서버에 synced=f 사본(옛 primary 의 잔여)이 남았으면 slotsync 가 지우지 않아 120초 뒤 멈춘다 — 01 §20 standby-slots-sync 정리나 사람 몫."""
    if primary_slot() is not None:
        drop_slot()
        if primary_slot() is not None:
            raise StepError("primary 에서 슬롯이 지워지지 않음(active 이거나 다른 DB 의 슬롯)")
    wait(lambda: all(SLOT not in n.get("slots", {SLOT: 1}) for n in nodes() if n.get("recovery") is not False), 120, "대기 서버 사본이 사라짐")
    return "primary·대기 모두 없음"


def db_connections():
    with pg("postgres") as c:
        return c.execute("select count(*) from pg_stat_activity where datname = %s and pid <> pg_backend_pid()", (DB,)).fetchone()[0]


def drop_db():
    with pg("postgres") as c:
        c.execute(f'drop database "{DB}"')  # FORCE 없이 — 연결이 있으면 실패하게


def s8_db():
    if not db_state()["exists"]:
        return "DB 없음 — 넘어감"
    n = db_connections()
    if n:
        raise StepError(f"{DB} 연결 {n}개 — 지우지 않음")
    drop_db()
    if db_state()["exists"]:
        raise StepError("DB 가 남음")
    return "지움"


def s9_topics():
    present = [t for t in TOPICS if t in topic_list()]
    for t in present:
        kafka("kafka-topics.sh", "--delete", "--topic", t)
    wait(lambda: not set(TOPICS) & topic_list(), 120, "토픽이 목록에서 사라짐")
    return f"지움 {present}" if present else "토픽 없음 — 넘어감"


def s10_s3():
    s3, done = s3_client(), []
    for prefix in S3_PREFIXES:
        keys = [k for k, _ in s3_objects(s3, prefix)]
        outside = [k for k in keys if not k.startswith(prefix)]
        if outside:
            raise StepError(f"접두사 {prefix} 밖 키 {outside[:5]} — 지우지 않음")
        for i in range(0, len(keys), 1000):
            resp = s3.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})
            if resp.get("Errors"):
                raise StepError(f"S3 삭제 실패 {[(e.get('Key'), e.get('Code')) for e in resp['Errors'][:5]]}")
        if s3_objects(s3, prefix):
            raise StepError(f"{prefix} 에 객체가 남음")
        done.append(f"{prefix} {len(keys)}")
    return f"지움 {done}"


def s11_ctx():
    if not CTX.exists():
        return "없음 — 넘어감"
    CTX.unlink()
    return "지움"


def s12_create():
    database()
    topics()
    if e2e_connector_exists():  # S5 에서 지웠는데 다시 생김 — 누가 만든 것이니 멈춘다
        raise StepError("S12 전에 같은 이름의 커넥터가 생김 — 만들지 않음")
    rest("POST", "/connectors", {"name": NAME, "config": connector_config(), "initial_state": "STOPPED"})
    wait(lambda: (status_or_none(NAME) or {}).get("connector", {}).get("state") == "STOPPED", 60, "새 커넥터 STOPPED")
    if offsets_now():  # ① 어떤 경로로 지워졌든 옛 offset 으로 시작하지 않는다
        rest("DELETE", f"/connectors/{NAME}/offsets")
        left = offsets_now()
        if left:
            raise StepError(f"새 커넥터에 옛 offset 이 남음 {left} — resume 하지 않음(STOPPED 로 둠)")
    rest("PUT", f"/connectors/{NAME}/resume")  # ② 빈 목록일 때만
    wait_running()  # ③
    return "만듦(STOPPED → offset 빈 목록 → resume → RUNNING)"


def s13_synced():
    def ok():
        ns = nodes()
        primary = primary_of(ns)
        slot = (primary or {}).get("slots", {}).get(SLOT)
        standbys = [n for n in ns if n is not primary]
        return bool(slot and slot["active"] and slot["failover"]) and all(n.get("slots", {}).get(SLOT, {}).get("synced") for n in standbys)
    wait(ok, 120, "primary 슬롯 active·failover 와 대기 서버 synced=t")
    return "synced=t"


STEPS = (("S2 커넥터 멈춤", s2_stop), ("S3 슬롯 쉼", s3_idle), ("S4 offset 지움", s4_offsets), ("S5 커넥터 지움", s5_delete), ("S6 그룹 지움", s6_group),
         ("S7 슬롯 지움", s7_slot), ("S8 DB 지움", s8_db), ("S9 토픽 지움", s9_topics), ("S10 S3 지움", s10_s3), ("S11 ctx 지움", s11_ctx),
         ("S12 다시 만듦", s12_create), ("S13 synced 확인", s13_synced))


def rebuild(execute):
    try:
        inv = survey()
    except Exception as error:  # noqa: BLE001 — 읽기만 하는 점검이 어디서 실패하든 바꾸지 않고 rc 2(이름만 출력)
        print(f"점검 실패: 상태를 읽지 못함 {type(error).__name__} — 바꾸지 않음(rc 2)")
        return 2
    rc = report(inv, judge(inv, execute), execute)
    if rc or not execute:
        return rc
    for name, step in STEPS:
        try:
            result = step()
        except (Exception, SystemExit) as error:  # noqa: BLE001 — 어느 단계든 실패하면 그 자리에서 멈춘다(SystemExit: migrate·wait_running)
            print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {name}: 실패 {type(error).__name__}: {str(error)[:300]} — 여기서 멈춤(원인을 고친 뒤 같은 명령으로 이어 감)")
            return 1
        print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {name}: {result}")
    print("끝 — 사람이 할 것: e2e Bronze 다시 켬(새 checkpoint, earliest), 확인 유입(e2e_load.py --duration 300), 남기는 대상 전후 비교")
    return 0


def main(argv):
    if argv in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    if argv == ["--rebuild"]:
        return rebuild(execute=False)
    if argv == ["--rebuild", "--execute"]:
        return rebuild(execute=True)
    if argv:
        print("사용: e2e_setup.py [--rebuild [--execute]] (--help)", file=sys.stderr)
        return 2
    database()
    topics()
    connector()
    with pg(DB) as c:
        print("slot", c.execute("select slot_name, active, failover, wal_status from pg_replication_slots where slot_name = %s", (SLOT,)).fetchone())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
