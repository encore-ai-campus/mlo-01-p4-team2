"""파이프라인 점검(설계 01 §10 M1~M8·S7) — **임시**(D18, 운영 배포 전에 정식으로 다시 정한다).

사용: check.py   (주기 실행은 launchd·systemd 타이머가 1분마다 부른다. 결과는 표준 출력에 JSON 한 줄)
- 점검마다 결과(ok·alert·error·unimplemented·unsupported)를 낸다. 상태가 바뀐 것만 알린다(새 경보, 경보 해소) — 상태 파일 MONITOR_STATE.
- 알림: MONITOR_WEBHOOK_URL 또는 MONITOR_WEBHOOK_FILE(주소를 담은 저장소 밖 파일)에 POST {"text","content"}. 없으면 화면에만 쓴다. 주소는 출력하지 않는다.
- 끝: 경보가 하나라도 있으면 rc=1. 상태 파일 자리(MONITOR_STATE, 기본 ~/.local/state/pipeline_monitor/state.json)가 이 사용자 것이 아니거나
  링크이거나 그룹·다른 사용자가 쓸 수 있으면 점검하지 않고 알린 뒤 rc=2.
- R7(FAILED Task 자동 재시작)은 넣지 않았다. M3 는 FAILED 경보만 낸다. E-K2(계획 02 P0-7) 결과로 R7 (나)가 정해지면 그때 넣는다.
비밀값(DB 비밀번호·RustFS 키·웹훅 주소)은 파일·환경 변수로만 받고 출력하지 않는다.
"""
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import psycopg

E = os.environ.get
PG_HOST, PG_PORT = E("MONITOR_PG_HOST", "127.0.0.1"), int(E("MONITOR_PG_PORT", "5010"))   # HAProxy rw = 지금 primary
NODE_PORTS = [int(p) for p in E("MONITOR_PG_NODE_PORTS", "5432,5433,5434").split(",") if p]  # 노드마다 직접(대기 서버면 synced 슬롯 확인) — 승격 뒤에도 그대로
PG_PASSWORD_FILE = E("MONITOR_PG_PASSWORD_FILE", "/opt/postgresql/config/postgres-password")
SLOTS = E("MONITOR_SLOTS", "ads_outbox_slot,ads_e2e_slot").split(",")
REPLICA_LAG_BYTES = int(E("MONITOR_REPLICA_LAG_BYTES", str(64 * 1024 * 1024)))  # 임시 기준
SLOT_INACTIVE_S = int(E("MONITOR_SLOT_INACTIVE_S", "600"))  # 설계 M1: 10분 넘게 쉼(R2 재시도 약 3분 + R3 재배치 60초보다 넉넉히)
UNLISTED_S, UNLISTED_EVERY_S = 10, 2  # 설계 M2: 복제 중인데 synchronized_standby_slots 에 없음이 10초 넘게(갱신 주기 2초 × 5)
# 설계 M2(§20): standby 허용 목록 슬롯이 synced=f(또는 없음·temporary)로 2분 넘게 — slotsync 가 새 사본을 만드는 동안(약 1분)은 경보가 아니다
SYNCED_GRACE_S = int(E("MONITOR_SYNCED_GRACE_S", "120"))
# 설계 M2(§20): primary physical slot 의 catalog_xmin 이 10분 넘게 그대로인데 그 사이 primary xid 가 50개 넘게 늘었음(옛 슬롯이 카탈로그 VACUUM 을 막음).
# 실측(2026-11-04): 정상이면 xmax 보다 2~4 뒤에서 약 20초마다 따라 움직인다. 트랜잭션 heartbeat 하나만으로 10분에 약 60개 는다
CXMIN_STALL_S = int(E("MONITOR_CATALOG_XMIN_STALL_S", "600"))
CXMIN_STALL_XIDS = int(E("MONITOR_CATALOG_XMIN_STALL_XIDS", "50"))
CONNECT = E("MONITOR_CONNECT_REST", "http://127.0.0.1:8083")
KAFKA = E("MONITOR_KAFKA", "127.0.0.1:9092,127.0.0.1:9192,127.0.0.1:9292")
KAFKA_BIN = Path(E("MONITOR_KAFKA_BIN", "/opt/kafka/current/bin"))
JAVA_HOME = E("MONITOR_JAVA_HOME", "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home")
# Bronze: "이름|토픽|checkpoint s3a 경로" 를 쉼표로
BRONZE = [b.split("|") for b in E("MONITOR_BRONZE", "lab|ads.outbox.events|s3a://ads-lake/checkpoints/bronze_ads_outbox,"
                                                     "e2e|ads.e2e.outbox.events|s3a://ads-lake/checkpoints/bronze_e2e_ads_outbox").split(",") if b]
# 설계 M5: 아직 읽지 않은 첫 데이터 레코드가 5분 넘게 기다림. 시각은 그 레코드의 Kafka 타임스탬프 — 두 토픽 모두 message.timestamp.type=CreateTime(기본),
# Connect 생산자가 보낸 시각이다(재발행 op=r 도 다시 보낸 시각, 원래 created_at 아님 — 2026-11-04 확인). read_committed 로 읽어 EOS 표식·중단된 레코드는 빠진다
BRONZE_STALE_S = int(E("MONITOR_BRONZE_STALE_S", "300"))
S3_ENDPOINT = E("S3_ENDPOINT", "http://127.0.0.1:9000")
S3_KEY_FILES = (E("S3_ACCESS_KEY_FILE", "/opt/rustfs/config/access-key"), E("S3_SECRET_KEY_FILE", "/opt/rustfs/config/secret-key"))
# 상태 파일은 이 사용자만 쓰는 폴더에(공용 /tmp 이면 다른 사용자가 미리 만든 폴더·파일·링크로 경보를 막을 수 있다)
STATE = Path(E("MONITOR_STATE", str(Path(E("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "pipeline_monitor" / "state.json")))
KAFKA_TIMEOUT_S = 30  # Kafka 가 멈추면 명령이 오래 걸린다 — 명령마다 30초에서 끊는다. M4 2번, M5 는 Bronze 마다 get-offsets 1번(차이가 있으면 --time -2 1번 + 파티션마다 consumer 1번 더). 시간 초과가 나면 그 Bronze 는 거기서 멈춘다(브로커 0대면 명령을 아예 돌리지 않는다)


def finding(check, key, level, detail):
    return {"check": check, "key": key, "level": level, "detail": detail}


def pg(port):
    return psycopg.connect(host=PG_HOST, port=port, user="postgres", password=Path(PG_PASSWORD_FILE).read_text().strip(), dbname="postgres",
                           connect_timeout=5, autocommit=True)


def m1_m2(state):
    """M1 슬롯(무효·오래 쉼·붙잡은 WAL), M2 대기 서버(복제 지연·동기 후보·보호 목록 양쪽·failover·synced)."""
    out = []
    with pg(PG_PORT) as c:
        limit = c.execute("select pg_size_bytes(current_setting('max_slot_wal_keep_size'))").fetchone()[0]
        for name in SLOTS:
            row = c.execute("select active, inactive_since, extract(epoch from now() - inactive_since)::float, wal_status, failover, "
                            "pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint from pg_replication_slots where slot_name = %s", (name,)).fetchone()
            if row is None:
                out.append(finding("M1", f"slot:{name}", "alert", "슬롯이 없다"))
                continue
            active, inactive_since, idle_s, wal_status, failover, held = row
            # 설계 M1: unreserved·lost, 10분 넘게 쉼, 붙잡은 WAL ≥ 한도 50%. 무효(lost)가 되면 restart_lsn 이 NULL 이라 held 도 None 이다
            bad = [w for w, cond in (("wal_status=" + str(wal_status), wal_status in ("unreserved", "lost")),
                                     (f"{int(idle_s or 0)}초 쉼 > {SLOT_INACTIVE_S}초", not active and idle_s is not None and idle_s > SLOT_INACTIVE_S),
                                     (f"붙잡은 WAL {held}B ≥ 한도 50%", held is not None and limit > 0 and held >= limit // 2)) if cond]
            seen = "active" if active else f"쉼(inactive_since {inactive_since})"
            out.append(finding("M1", f"slot:{name}", "alert" if bad else "ok", f"{', '.join(bad) + ' — ' if bad else ''}{seen}, {wal_status}, 붙잡은 WAL {'held=None' if held is None else f'{held}B'}"))
            out.append(finding("M2", f"failover:{name}", "ok" if failover else "alert", f"failover={failover}"))
        # 대기 서버만: 논리 슬롯을 쥔 walsender(Debezium — 커넥터마다 같은 이름 'Debezium Streaming')는 뺀다
        reps = c.execute("select application_name, state, sync_state, pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint from pg_stat_replication "
                         "where pid not in (select active_pid from pg_replication_slots where slot_type = 'logical' and active_pid is not null)").fetchall()
        candidates = [r for r in reps if r[1] == "streaming" and r[2] in ("sync", "quorum", "potential")]
        out.append(finding("M2", "sync_candidates", "ok" if candidates else "alert", f"동기 후보 {len(candidates)}개 {[(r[0], r[2]) for r in reps]}"))
        for name, rep_state, _sync, lag in reps:  # rep_state: 이 함수의 state(상태 파일)와 이름을 나눈다
            out.append(finding("M2", f"replica_lag:{name}", "alert" if rep_state != "streaming" or (lag or 0) > REPLICA_LAG_BYTES else "ok", f"{rep_state}, 재생 지연 {lag}B"))
        listed, attached, streaming = standby_slots(c)
        missing = [s for s in listed if s not in attached]
        out.append(finding("M2", "synchronized_standby_slots", "alert" if missing else "ok",
                           f"목록 {listed}, 붙어 있는 물리 슬롯 {sorted(attached)}" + (f" — 목록에 있는데 안 붙음 {missing}(CDC 가 기다린다)" if missing else "")))
        # 반대쪽: 복제 중인데 목록에 없음 = 갱신 스크립트(D12-2)가 멈춤. 갱신 한 번 사이의 틈은 경보가 아니다 — 10초 넘게 이어질 때만
        unlisted, started = streaming - set(listed), time.monotonic()
        while unlisted and time.monotonic() - started <= UNLISTED_S:
            time.sleep(UNLISTED_EVERY_S)
            now_listed, _attached, now_streaming = standby_slots(c)
            unlisted &= now_streaming - set(now_listed)
        out.append(finding("M2", "synchronized_standby_slots_unlisted", "alert" if unlisted else "ok",
                           f"복제 중인데 목록에 없는 물리 슬롯 {sorted(unlisted)}" + (f" — {UNLISTED_S}초 넘게 이어짐(갱신 스크립트가 멈춤, 승격되면 그 대기 서버의 보호가 빠진다)" if unlisted else "")))
        out += catalog_xmin_stall(c, state)
    for port in NODE_PORTS:
        try:
            with pg(port) as c:
                rows = {r[0]: r[1:] for r in c.execute("select slot_name, synced, temporary from pg_replication_slots where slot_type = 'logical'").fetchall()}
                recovery = c.execute("select pg_is_in_recovery()").fetchone()[0]
        except psycopg.Error as error:  # 키는 노드·슬롯마다 같게 둔다(못 본 것을 '해소'로 알리지 않게)
            out += [finding("M2", f"synced:{port}:{name}", "alert", f"노드 접속 실패 {type(error).__name__}") for name in SLOTS]
            continue
        if not recovery:
            for name in SLOTS:
                state.setdefault("first_bad", {}).pop(f"synced:{port}:{name}", None)
            out += [finding("M2", f"synced:{port}:{name}", "ok", "이 노드는 지금 primary(대기 아님)") for name in SLOTS]
            continue
        for name in SLOTS:
            synced, temporary = rows.get(name, (None, None))
            key, detail = f"synced:{port}:{name}", f"synced={synced}, temporary={temporary}"
            if synced is True and temporary is False:
                state.setdefault("first_bad", {}).pop(key, None)
                out.append(finding("M2", key, "ok", detail))
                continue
            since = state.setdefault("first_bad", {}).setdefault(key, time.time())
            age = int(time.time() - since)
            late = age > SYNCED_GRACE_S
            out.append(finding("M2", key, "alert" if late else "ok", detail + (f" — {age}초째(강등된 노드의 옛 슬롯이면 자동 정리가 멈춘 것)" if late
                                                                               else f" — {age}초째, {SYNCED_GRACE_S}초 넘으면 경보")))
    return out


def catalog_xmin_stall(c, state):
    """primary physical slot 마다 catalog_xmin 이 바뀐 때와 그때의 xid 끝을 상태에 두고, 오래 그대로인데 xid 는 늘었으면 경보.
    대기 서버가 primary 의 어느 논리 슬롯보다 **오래된** 값을 쥘 때만 센다(강등된 노드의 옛 슬롯, 또는 그 대기 서버의 slotsync 멈춤). physical 값은 평소
    가장 오래된 논리 슬롯을 그대로 따르므로, 커넥터를 멈춘 정상 작업(RB-1·RB-2 등)에서는 논리 슬롯과 같은 값에 함께 멈춘다 — 그것은 M1(10분 넘게 쉼)이 잡는다.
    primary 에 논리 슬롯이 하나도 없으면(슬롯을 지운 사이) coalesce 로 0 과 견주므로 NULL 이 아닌 physical 값은 모두 "오래됨"으로 센다.
    정상 지연: synced 사본은 primary 보다 조금 늦게 따라가 잠깐 "오래됨"이 된다(실측 최대 12초 연속, 값은 약 20초마다 바뀜) — 600초 조건이 거른다."""
    xmax = int(c.execute("select pg_snapshot_xmax(pg_current_snapshot())::text").fetchone()[0])  # xid 를 쓰지 않고 읽는다
    rows = c.execute("select slot_name, catalog_xmin::text, age(catalog_xmin) > coalesce((select max(age(catalog_xmin)) from pg_replication_slots "
                     "where slot_type = 'logical'), 0) from pg_replication_slots where slot_type = 'physical'").fetchall()  # age(): xid 감김에도 안전
    seen, out, now = state.setdefault("catalog_xmin", {}), [], time.time()
    for name, cx, older in rows:
        last = seen.get(name)
        if last is None or last["value"] != cx:
            seen[name] = last = {"value": cx, "since": now, "xmax": xmax}
        still, grew = int(now - last["since"]), xmax - last["xmax"]
        stalled = cx is not None and bool(older) and still > CXMIN_STALL_S and grew > CXMIN_STALL_XIDS
        out.append(finding("M2", f"catalog_xmin:{name}", "alert" if stalled else "ok",
                           f"catalog_xmin={cx}{'(primary 논리 슬롯보다 오래됨)' if older else ''}, {still}초째 그대로, 그사이 xid +{grew}"
                           + (" — primary 카탈로그 VACUUM 을 막는다(강등된 노드의 옛 슬롯 또는 그 대기 서버의 슬롯 동기화 멈춤 확인)" if stalled else "")))
    for gone in set(seen) - {r[0] for r in rows}:
        seen.pop(gone)
    return out


def standby_slots(c):
    """(synchronized_standby_slots 목록, 붙어 있는 물리 슬롯, 그중 복제 중(streaming)인 것)."""
    listed = [s.strip() for s in c.execute("select current_setting('synchronized_standby_slots')").fetchone()[0].split(",") if s.strip()]
    rows = c.execute("select s.slot_name, r.state from pg_replication_slots s left join pg_stat_replication r on r.pid = s.active_pid "
                     "where s.slot_type = 'physical' and s.active").fetchall()
    return listed, {r[0] for r in rows}, {r[0] for r in rows if r[1] == "streaming"}


def rest(method, path):
    req = urllib.request.Request(CONNECT + path, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def m3():
    """M3 커넥터·Task FAILED 경보(재시작은 하지 않는다 — R7 은 E-K2 뒤)."""
    out = []
    for name, info in rest("GET", "/connectors?expand=status").items():
        s = info["status"]
        states = [s["connector"]["state"]] + [t["state"] for t in s.get("tasks", [])]
        out.append(finding("M3", f"connector:{name}", "alert" if "FAILED" in states else "ok", f"{states}"))
    return out


class KafkaTimeout(Exception):
    pass


def kafka(*args, timeout=None):
    try:
        return subprocess.run([str(KAFKA_BIN / args[0]), "--bootstrap-server", KAFKA, *args[1:]], env=dict(os.environ, JAVA_HOME=JAVA_HOME),
                              capture_output=True, text=True, timeout=timeout or KAFKA_TIMEOUT_S)
    except subprocess.TimeoutExpired as error:
        raise KafkaTimeout(f"Kafka 명령 시간 초과({args[0]}, {int(error.timeout)}초)") from None


def brokers_up():
    """(응답한 브로커 수, 전체 수) — 포트만 본다(명령보다 빨리, 시간 초과 없이)."""
    hosts = [h.rsplit(":", 1) for h in KAFKA.split(",") if h]
    up = 0
    for host, port in hosts:
        try:
            with socket.create_connection((host, int(port)), timeout=1):
                up += 1
        except OSError:
            pass
    return up, len(hosts)


def m4():
    up, total = brokers_up()
    out = [finding("M4", "kafka_unreachable", "alert" if up == 0 else "ok", f"Kafka 응답 없음(브로커 {total}대 중 0대 응답)" if up == 0 else f"브로커 {total}대 중 {up}대 응답")]
    for flag in ("--under-min-isr-partitions", "--unavailable-partitions"):
        if up == 0:  # 키를 그대로 두고 error 로 — 못 본 것을 해소로 알리지 않는다
            out.append(finding("M4", flag, "error", f"Kafka 응답 없음(브로커 {total}대 중 0대 응답) — 볼 수 없음"))
            continue
        try:
            r = kafka("kafka-topics.sh", "--describe", flag)
        except KafkaTimeout as error:
            out.append(finding("M4", flag, "error", f"{error}(브로커 {total}대 중 {up}대 응답)"))
            continue
        if r.returncode:
            out.append(finding("M4", flag, "error", r.stderr.strip()[-200:]))
            continue
        lines = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("Topic:") and "Partition:" in ln]
        out.append(finding("M4", flag, "alert" if lines else "ok", f"{len(lines)}개 파티션" + (f": {lines[:3]}" if lines else "")))
    return out


def kafka_offsets(topic, time_spec=None):
    """파티션 → offset. time_spec None 은 끝(latest = LEO), -2 는 가장 이른 offset."""
    r = kafka("kafka-get-offsets.sh", "--topic", topic, *(["--time", str(time_spec)] if time_spec is not None else []))
    return {p: int(o) for _t, p, o in (ln.split(":") for ln in r.stdout.split())} if r.returncode == 0 else None


def first_unread(topic, partition, offset):
    """그 파티션의 offset 부터 read_committed 로 첫 데이터 레코드 하나: (offset, Kafka 타임스탬프 ms) 또는 None(표식·중단된 레코드뿐)."""
    r = kafka("kafka-console-consumer.sh", "--topic", topic, "--partition", str(partition), "--offset", str(offset), "--max-messages", "1",
              "--isolation-level", "read_committed", "--timeout-ms", "3000", "--formatter-property", "print.timestamp=true",
              "--formatter-property", "print.offset=true", "--formatter-property", "print.value=false")
    for line in r.stdout.splitlines():
        m = re.match(r"(?:CreateTime|LogAppendTime):(\d+)\s+Offset:(\d+)", line)
        if m:
            return int(m.group(2)), int(m.group(1))
    return None


def m5():
    """M5 Bronze 신선도: checkpoint 의 마지막 확정 배치(commits/N 의 시각, offsets/N 의 위치)와 Kafka 끝 offset."""
    import boto3

    keys = [Path(f).read_text().strip() for f in S3_KEY_FILES]
    s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT, aws_access_key_id=keys[0], aws_secret_access_key=keys[1], region_name=E("S3_REGION", "ap-northeast-2"))
    out, (up, total) = [], brokers_up()
    for name, topic, uri in BRONZE:
        if up == 0:  # 같은 키에 error — 못 본 것을 해소로 알리지 않는다
            out.append(finding("M5", f"bronze:{name}", "error", f"Kafka 응답 없음(브로커 {total}대 중 0대 응답) — 지연을 볼 수 없음"))
            continue
        bucket, prefix = uri.removeprefix("s3a://").split("/", 1)
        commits = s3.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/commits/").get("Contents", [])
        batches = [(int(o["Key"].rsplit("/", 1)[1]), o["LastModified"]) for o in commits if o["Key"].rsplit("/", 1)[1].isdigit()]
        if not batches:
            out.append(finding("M5", f"bronze:{name}", "alert", "확정된 배치가 없다(checkpoint 없음)"))
            continue
        batch, at = max(batches)
        body = s3.get_object(Bucket=bucket, Key=f"{prefix}/offsets/{batch}")["Body"].read().decode().splitlines()
        done = json.loads(body[2]).get(topic, {})
        try:
            end = kafka_offsets(topic)
            behind = {p: int(done.get(p, 0)) for p in (end or {}) if end[p] > int(done.get(p, 0))}
            earliest = kafka_offsets(topic, -2) if behind else {}
            if end is None or earliest is None:
                out.append(finding("M5", f"bronze:{name}", "error", "Kafka offset 을 못 읽음"))
                continue
            expired = {p: (o, earliest[p]) for p, o in behind.items() if earliest.get(p, 0) > o}
            waits = [first_unread(topic, p, o) for p, o in behind.items() if p not in expired]
        except KafkaTimeout as error:
            out.append(finding("M5", f"bronze:{name}", "error", f"{error}(브로커 {total}대 중 {up}대 응답)"))
            continue
        now_ms = time.time() * 1000
        oldest = max((now_ms - ts for _o, ts in filter(None, waits)), default=0) / 1000  # 가장 오래 기다린 데이터 레코드(초)
        since = int((datetime.now(UTC) - at).total_seconds())
        lag = sum(end[p] - o for p, o in behind.items())
        if expired:  # 읽어야 할 offset 이 Kafka 에서 이미 지워짐(보존 만료 K5) — 첫 레코드 시각으로는 볼 수 없다
            out.append(finding("M5", f"bronze:{name}", "alert", f"마지막 확정 배치 {batch}({since}초 전), 읽을 offset 이 Kafka 에 없음 {expired}(보존 만료 — RB-5)"))
            continue
        stale = oldest > BRONZE_STALE_S
        out.append(finding("M5", f"bronze:{name}", "alert" if stale else "ok",
                           f"마지막 확정 배치 {batch}({since}초 전), 읽지 않은 offset {lag}"
                           + ((f", 가장 오래 기다린 데이터 레코드 {int(oldest)}초(CreateTime)" if any(waits) else ", 3초 안에 읽은 데이터 레코드 없음(EOS 표식·중단된 레코드뿐이거나 Kafka 응답이 느림)") if lag else "")
                           + (f" > {BRONZE_STALE_S}초" if stale else "")))
    out.append(finding("M5", "bronze_app_failed", "unimplemented", "운영은 Spark Operator 앱 상태(FAILED·재시도 소진). 로컬은 run-bronze.sh 로그의 'retries exhausted'"))
    return out


def s7():
    out = []
    with pg(PG_PORT) as c:
        db_now = c.execute("select now()").fetchone()[0]
    skew = abs((datetime.now(UTC) - db_now).total_seconds())
    out.append(finding("S7", "clock_skew", "alert" if skew > 1 else "ok", f"이 장치와 DB primary 시계 차이 {skew:.3f}초"))
    if sys.platform != "darwin":
        out.append(finding("S7", "power", "unsupported", "macOS 밖은 아직 없다(운영 노트북: upower·systemd-logind 설정으로)"))
        return out
    batt = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    pct = re.search(r"(\d+)%", batt)
    on_ac = "AC Power" in batt
    low = pct is not None and int(pct.group(1)) < 20 and not on_ac
    out.append(finding("S7", "battery", "alert" if low else "ok", f"{'AC' if on_ac else '배터리'} {pct.group(1) + '%' if pct else '?'}"))
    sleep = next((ln for ln in subprocess.run(["pmset", "-g"], capture_output=True, text=True).stdout.splitlines() if ln.strip().startswith("sleep")), "")
    out.append(finding("S7", "sleep", "ok" if "prevented" in sleep or re.search(r"sleep\s+0\b", sleep) else "alert", sleep.strip()))
    return out


def unimplemented():
    return [finding("M6", "hop_reconcile", "unimplemented", "창 대사 결과 — P1·P2-2 뒤"),
            finding("M7", "kafka_bronze_positions", "unimplemented", "Kafka ↔ Bronze 위치 대사 — P2 뒤"),
            finding("M8", "outbox_unconfirmed_age", "unimplemented", "outbox 미확정 창 나이 — P1-4·P1-6 뒤")]


def notify(text):
    try:  # 주소 파일이 없거나 못 읽어도 점검은 끝까지 간다(전송 실패로 다룬다 — 다음 점검이 다시 보낸다)
        url = E("MONITOR_WEBHOOK_URL") or (Path(E("MONITOR_WEBHOOK_FILE")).read_text().strip() if E("MONITOR_WEBHOOK_FILE") else None)
    except OSError as error:
        return f"웹훅 실패 {type(error).__name__}(주소 파일)"
    if not url:
        return "웹훅 없음(화면에만)"
    try:
        req = urllib.request.Request(url, method="POST", data=json.dumps({"text": text, "content": text}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return f"웹훅 {resp.status}"
    except (urllib.error.URLError, OSError, ValueError) as error:  # ValueError: 주소 형식이 틀림
        return f"웹훅 실패 {type(error).__name__}"


def unsafe(path, *, directory):
    """이 사용자 것이 아니거나, 심볼릭 링크이거나, 그룹·다른 사용자가 쓸 수 있으면 그 까닭. 괜찮으면 None."""
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        return f"{path} 가 심볼릭 링크다"
    if not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)):  # 파일 자리의 FIFO·소켓도 거른다(읽다가 멈추지 않게)
        return f"{path} 가 {'폴더가' if directory else '보통 파일이'} 아니다"
    if st.st_uid != os.getuid():
        return f"{path} 의 소유자가 이 사용자가 아니다(uid {st.st_uid})"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return f"{path} 를 그룹·다른 사용자가 쓸 수 있다(mode {stat.S_IMODE(st.st_mode):o})"
    return None


def state_problem():
    """상태 폴더를 0700 으로 만들고(이미 있으면 그대로), 폴더·파일이 안전한지 본다. 문제가 있으면 그 까닭."""
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return unsafe(STATE.parent, directory=True) or (unsafe(STATE, directory=False) if os.path.lexists(STATE) else None)


def load_state():
    """(상태, 다시 시작했는지). 상태 파일이 깨졌거나 못 읽으면 빈 상태로 이어 간다 — 지금 경보를 다시 알리게 되며, 그쪽이 맞다(감시가 멈추는 것보다)."""
    if not STATE.exists():
        return {}, False
    try:
        state = json.loads(STATE.read_text())
    except (ValueError, OSError):
        return {}, True
    return (state, False) if isinstance(state, dict) else ({}, True)


def save_state(state):
    """같은 폴더에 새 임시 파일(O_EXCL, 0600 — 이름을 미리 알 수 없다)을 만들어 쓰고 바꿔 끼운다(쓰는 도중 꺼져도 옛 파일이나 새 파일 하나만 남는다)."""
    fd, tmp = tempfile.mkstemp(dir=STATE.parent, prefix=f".{STATE.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, ensure_ascii=False))
        os.replace(tmp, STATE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main():
    problem = state_problem()
    if problem:  # 경보를 막을 수 있는 상태 파일은 믿지 않는다 — 점검하지 않고 크게 실패한다(알림을 보내고 rc=2)
        sent = notify(f"파이프라인 점검(임시 D18)\n[오류] 상태 파일 자리가 안전하지 않아 점검하지 않았다: {problem}")
        print(json.dumps({"t": datetime.now(UTC).isoformat(), "error": "unsafe_state", "detail": problem, "notify": sent}, ensure_ascii=False))
        sys.exit(2)
    state, state_reset = load_state()
    findings, failed_checks = [], set()
    for name, fn in (("M1·M2", lambda: m1_m2(state)), ("M3", m3), ("M4", m4), ("M5", m5), ("S7", s7)):
        try:
            findings += fn()
        except Exception as error:  # noqa: BLE001 — 점검 하나가 실패해도 나머지는 본다(실패 자체를 결과로)
            findings.append(finding(name, f"{name}:run", "error", f"{type(error).__name__}: {str(error)[:200]}"))
            failed_checks |= set(name.split("·"))
    findings += unimplemented()
    before, before_check = state.get("levels", {}), state.get("checks", {})
    now_keys = {f["key"] for f in findings}
    carried = {k: v for k, v in before.items() if k not in now_keys and before_check.get(k) in failed_checks}  # 보지 못한 것은 해소가 아니다 — 지난 값을 그대로 둔다
    changes = []
    for f in findings:
        old = before.get(f["key"])
        if f["level"] in ("alert", "error") and old != f["level"]:
            changes.append(f"[경보] {f['check']} {f['key']}: {f['detail']}")
        elif f["level"] == "ok" and old in ("alert", "error"):
            changes.append(f"[해소] {f['check']} {f['key']}: {f['detail']}")
    for key, old in before.items():  # 점검이 정상으로 돌았는데 없어진 키(예: 살아난 M3:run, 지워진 커넥터) — 해소로 알린다
        if old in ("alert", "error") and key not in now_keys and key not in carried:
            changes.append(f"[해소] {key}: 이번 점검 결과에 없음")
    if state_reset:
        changes.insert(0, f"[알림] 상태 파일 {STATE.name} 을 읽지 못해 새로 시작했다 — 지금 경보를 다시 알린다")
    sent = notify("파이프라인 점검(임시 D18)\n" + "\n".join(changes)) if changes else None
    if not (sent or "").startswith("웹훅 실패"):  # 못 보낸 변화는 버리지 않는다 — 지난 값을 두어 다음 점검이 다시 보낸다
        state["levels"] = {**carried, **{f["key"]: f["level"] for f in findings}}
        state["checks"] = {**{k: before_check[k] for k in carried}, **{f["key"]: f["check"] for f in findings}}
    save_state(state)
    alerts = [f for f in findings if f["level"] in ("alert", "error")]
    print(json.dumps({"t": datetime.now(UTC).isoformat(), "alerts": len(alerts), "changes": changes, "notify": sent, "state_reset": state_reset,
                      "findings": findings}, ensure_ascii=False, default=str))
    sys.exit(1 if alerts else 0)


if __name__ == "__main__":
    main()
