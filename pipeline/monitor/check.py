"""파이프라인 점검(설계 01 §10 M1~M8·S7) — **임시**(D18, 운영 배포 전에 정식으로 다시 정한다).

사용: check.py   (주기 실행은 launchd·systemd 타이머가 1분마다 부른다. 결과는 표준 출력에 JSON 한 줄)
- 점검마다 결과(ok·alert·error·unimplemented·unsupported)를 낸다. 상태가 바뀐 것만 알린다(새 경보, 경보 해소) — 상태 파일 MONITOR_STATE.
- 알림: MONITOR_WEBHOOK_URL 또는 MONITOR_WEBHOOK_FILE(주소를 담은 저장소 밖 파일)에 POST {"text","content"}. 없으면 화면에만 쓴다. 주소는 출력하지 않는다.
- 끝: 경보가 하나라도 있으면 rc=1. 점검하지 않으면 알린 뒤 rc=2 — 필수 모드(MONITOR_REQUIRE_ALL=1)에서 운영 변수가 없거나 빔(missing_config),
  설정 형식이 틀림(bad_config — MONITOR_PG_NODES 등), 코드 사본이 MONITOR_CODE_COMMIT 과 다르거나 더러움(code_pin — 주기 실행만),
  상태 파일 자리(MONITOR_STATE, 기본 ~/.local/state/pipeline_monitor/state.json)가 이 사용자 것이 아니거나 링크이거나 그룹·다른 사용자가 쓸 수 있음(unsafe_state).
  rc 2 알림은 처음 한 번 바로, 같은 거부가 이어지면 한 시간에 한 번(P2-4b 결정 F (나) — 표시 파일 <상태 파일>.refused, unsafe_state 는 늘).
- D1 디스크(MONITOR_DISK — 이 장치의 경로마다 파일 시스템 여유), D2 outbox 표 크기(MONITOR_OUTBOX — primary 에서), S7 gap(지난 회차 뒤 공백 — MONITOR_GAP_S).
- 운영 적용(Ops 2026-11-08): DB 계정 MONITOR_PG_USER, MONITOR_BRONZE=none(M5 unsupported·S3 설정 필요 없음), MONITOR_CONNECT_REST 쉼표 목록(앞에서부터 처음 응답한 곳),
  목록의 모든 Connect 주소에 GET /(응답 없으면 connect_rest:<주소> alert — Planner (나)), 웹훅 User-Agent(Discord 가 기본 UA 를 거절).
- PostgreSQL: MONITOR_PG_NODES(host:port 목록)의 노드마다 물어 primary 를 스스로 찾는다. M1·M2 primary 쪽은 그 노드에서, synced 는 대기 서버에서.
  primary 가 정확히 하나가 아니면 M1·M2:run error(지난 값 유지 — 해소로 알리지 않음), S7 clock_skew 도 error.
- R7(FAILED 커넥터·Task 자동 재시작 — 설계 01 R7, 사용자 결정 2026-11-06으로 다시 엶): MONITOR_R7=1 일 때만. 30분에 3번(앞 시도 뒤 최소 0·5·15분),
  다 쓰면 사람(`check.py --r7-clear <커넥터>` 로 기록을 비움). 같은 회차 M1 에서 그 커넥터의 슬롯이 없음·lost·unreserved 이거나 슬롯 상태를 모르면
  재시작하지 않는다(설계와 다른 점 — snapshot.mode=no_data 라 새 슬롯으로 조용히 이어 가는 빠짐을 막음). 주기 실행이 없으면 이 스크립트가 돌 때만 효과가 있다.
비밀값(DB 비밀번호·RustFS 키·웹훅 주소)은 파일·환경 변수로만 받고 출력하지 않는다.
"""
import json
import math
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple
from datetime import UTC, datetime
from pathlib import Path

import psycopg

E = os.environ.get
# PostgreSQL 노드 목록·S7 전원·필수 모드는 main() 이 읽는다(pg_nodes·flag·missing_config) — 형식이 틀려도 불러올 때 죽지 않고 JSON + rc=2 로 끝나게
PG_PASSWORD_FILE = E("MONITOR_PG_PASSWORD_FILE", "/opt/postgresql/config/postgres-password")
PG_USER = E("MONITOR_PG_USER", "postgres")  # 운영은 읽기 전용 감시 역할(pg_monitor 회원 — Ops 2026-11-08). 비밀번호 파일은 그 역할의 것
# 회차가 DB 에서 끝없이 멈추지 않게(MON-3, 2026-11-09). 연결 뒤에는 libpq 에 시간 제한이 없다 — 둘을 건다:
# - statement_timeout: 서버가 살아 있는데 문장이 막힐 때(잠금 대기 — D2 pg_total_relation_size 는 AccessExclusiveLock 뒤에서 기다린다) 10초에서 서버가 끊는다
# - keepalive: 답을 기다리는 사이 상대가 사라지면(전원·네트워크) 약 25초(10 + 5 × 3)에서 연결 오류. tcp_user_timeout(보낸 데이터가 확인되지 않음 30초)은
#   TCP_USER_TIMEOUT 이 있는 Linux(운영 감시 서버)에서만 걸리고 macOS 에서는 libpq 가 건너뛴다
# 덮지 못함: 커널은 답하는데 서버 프로세스가 멈춘 경우(SIGSTOP 등) — 운영은 감시 유닛 TimeoutStartSec=120(Ops)이 회차를 끊는다
PG_LIMITS = {"connect_timeout": 5, "options": "-c statement_timeout=10000",
             "keepalives": 1, "keepalives_idle": 10, "keepalives_interval": 5, "keepalives_count": 3, "tcp_user_timeout": 30000}
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
CONNECT = E("MONITOR_CONNECT_REST", "http://127.0.0.1:8083")  # 쉼표 목록 — 회차마다 앞에서부터 처음 응답한 곳을 쓴다(운영: 노트북 셋, Ops 2026-11-08)
CONNECT_SEEN = {}  # 이번 회차에 응답한 곳(base)과 그 앞의 응답하지 않은 주소(skipped) — R7 POST 도 같은 곳으로
KAFKA = E("MONITOR_KAFKA", "127.0.0.1:9092,127.0.0.1:9192,127.0.0.1:9292")
KAFKA_BIN = Path(E("MONITOR_KAFKA_BIN", "/opt/kafka/current/bin"))
JAVA_HOME = E("MONITOR_JAVA_HOME", "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home")


def parse_bronze(raw):
    """Bronze: "이름|토픽|checkpoint s3a 경로" 를 쉼표로. `none` 이면 None(Bronze 가 아직 없음 — M5 unsupported, S3 설정 필요 없음)."""
    return None if raw.strip() == "none" else [b.split("|") for b in raw.split(",") if b]


BRONZE = parse_bronze(E("MONITOR_BRONZE", "lab|ads.outbox.events|s3a://ads-lake/checkpoints/bronze_ads_outbox,"
                                          "e2e|ads.e2e.outbox.events|s3a://ads-lake/checkpoints/bronze_e2e_ads_outbox"))
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


class ConfigError(ValueError):
    """설정이 없거나 형식이 틀림 — 점검하지 않는다(rc=2). 메시지에는 변수 이름과 까닭만(값은 넣지 않는다)."""


# 필수 모드(MONITOR_REQUIRE_ALL=1)에서 없거나 비면 점검하지 않는 운영 변수 — 기본값이 모두 로컬 시험대를 가리켜, 하나만 빠져도 조용히 lab 을 보고 ok 를 내지 않게
REQUIRED = ("MONITOR_PG_NODES", "MONITOR_PG_PASSWORD_FILE", "MONITOR_CONNECT_REST", "MONITOR_KAFKA", "MONITOR_KAFKA_BIN", "MONITOR_JAVA_HOME",
            "MONITOR_BRONZE", "S3_ENDPOINT", "S3_ACCESS_KEY_FILE", "S3_SECRET_KEY_FILE", "MONITOR_SLOTS", "MONITOR_STATE", "MONITOR_S7_POWER", "MONITOR_R7",
            "MONITOR_DISK", "MONITOR_OUTBOX", "MONITOR_PG_USER")
S3_SETTINGS = ("S3_ENDPOINT", "S3_ACCESS_KEY_FILE", "S3_SECRET_KEY_FILE")  # MONITOR_BRONZE=none 이면 필수가 아니다(M5 를 보지 않음)
# P2-4b 기본값(로컬 시험대). D1 은 이 장치의 경로만 본다 — 디스크가 없는 장치(관리 장치 ④·감시 서버)는 MONITOR_DISK=none(결정 B·리뷰 C2)
DEFAULT_DISK = "pg|/opt/postgresql,reconcile_ads|~/Library/Caches/ads-pipeline/reconcile|du"
DEFAULT_OUTBOX = ("ads_lab|ads_pipeline_lab|public.events_outbox,ads_e2e|ads_pipeline_e2e|public.events_outbox,"
                  "metaverse_lab|metaverse_lab|public.eventing_eventoutbox")
ROOT = Path(__file__).resolve().parents[2]  # 코드 판 확인(code_pin)이 보는 저장소
REFUSE_REPEAT_S = 3600  # 같은 rc 2 거부는 한 시간에 한 번만 다시 알린다(결정 F (나))
_ITEM_NAME = re.compile(r"[A-Za-z0-9_.-]+")
_TABLE = re.compile(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*")
_COMMIT = re.compile(r"[0-9a-f]{40}")
Extra = namedtuple("Extra", "disk outbox min_free max_bytes gap_s commit")
# R7(설계 01 R7·:512, 결정 2026-11-06 Q1 (나)·Q4 (나)): 앞 시도 뒤 최소 대기 — 첫 시도는 바로, 둘째 5분, 셋째 15분(셋째는 첫 시도 뒤 20분, 30분 예산 안).
# 세 번을 다 쓰면 사람 차례다 — 기록은 "FAILED 아님 + 마지막 시도가 30분보다 오래됨" 또는 사람(--r7-clear)만 비운다(깜빡여도 같은 3번 안에 든다)
R7_WAITS_S = (0, 5 * 60, 15 * 60)
R7_WINDOW_S = 30 * 60
R7_BAD_SLOT = ("lost", "unreserved")  # 이 상태나 슬롯 없음이면 재시작하지 않는다(RB-1 몫 — snapshot.mode=no_data 라 새 슬롯으로 조용히 빠질 수 있다)


def flag(env, name, default):
    """0·1 만 받는다. 그 밖(true·off·빈 값 등)은 설정 오류다 — `true` 를 '1 이 아니니 끔'으로 읽어 필수 모드가 조용히 꺼지지 않게."""
    value = env.get(name, default).strip()
    if value not in ("0", "1"):
        raise ConfigError(f"{name} 는 0 또는 1 이어야 한다")
    return value == "1"


def missing_config(env):
    """필수 모드일 때 없거나 빈(공백만 포함) 운영 변수의 이름. 필수 모드가 아니면 빈 목록."""
    if not flag(env, "MONITOR_REQUIRE_ALL", "0"):
        return []
    no_bronze = env.get("MONITOR_BRONZE", "").strip() == "none"
    return [name for name in REQUIRED if not env.get(name, "").strip() and not (no_bronze and name in S3_SETTINGS)]


def pg_nodes(env):
    """감시할 PostgreSQL 노드 [(host, port)]. MONITOR_PG_NODES(host:port 쉼표 목록)가 있으면 그것만 보고(빈 값은 오류),
    없으면 로컬 시험대 기본 MONITOR_PG_HOST(127.0.0.1) × MONITOR_PG_NODE_PORTS(5432,5433,5434). IPv6 대괄호 표기·중복은 받지 않는다."""
    if "MONITOR_PG_NODES" in env:
        if not env["MONITOR_PG_NODES"].strip():  # 있는데 비었으면 기본값(로컬 시험대)으로 빠지지 않는다
            raise ConfigError("MONITOR_PG_NODES 가 비었다")
        name, items = "MONITOR_PG_NODES", env["MONITOR_PG_NODES"].split(",")
    else:
        host = env.get("MONITOR_PG_HOST", "127.0.0.1")
        name, items = "MONITOR_PG_NODE_PORTS", [f"{host}:{p.strip()}" for p in env.get("MONITOR_PG_NODE_PORTS", "5432,5433,5434").split(",") if p.strip()]
    nodes = []
    for number, item in enumerate(items, 1):
        host, sep, port = item.strip().rpartition(":")  # brokers_up 과 같은 나누기(마지막 ':' 뒤가 포트)
        if not sep or not host or "[" in host or "]" in host or not (port.isascii() and port.isdigit()) or not 1 <= int(port) <= 65535:
            raise ConfigError(f"{name} 의 {number}번째 항목이 host:port(포트 1~65535, IPv6 대괄호 불가)가 아니다")
        if (host, int(port)) in nodes:
            raise ConfigError(f"{name} 의 {number}번째 항목이 앞 항목과 같다(primary 를 두 번 세지 않게)")
        nodes.append((host, int(port)))
    if not nodes:
        raise ConfigError(f"{name} 에 노드가 없다")
    return nodes


def _entries(name, raw, sizes):
    """`이름|…` 쉼표 목록 → 칸 목록. 빈 값·빈 칸·칸 수·이름 모양·이름 겹침은 ConfigError(이름과 몇 번째인지만 — 값은 넣지 않는다)."""
    if not raw.strip():
        raise ConfigError(f"{name} 가 비었다")
    out, seen = [], set()
    for number, item in enumerate(raw.split(","), 1):
        parts = [part.strip() for part in item.split("|")]
        if len(parts) not in sizes or not all(parts) or not _ITEM_NAME.fullmatch(parts[0]):
            raise ConfigError(f"{name} 의 {number}번째 항목이 `이름|…` 모양({'·'.join(map(str, sizes))}칸)이 아니다")
        if parts[0] in seen:
            raise ConfigError(f"{name} 의 {number}번째 항목 이름이 앞 항목과 같다")
        seen.add(parts[0])
        out.append(parts)
    return out


def _number(env, name, default, low, high, kind):
    try:
        value = kind(env.get(name, default).strip())
    except ValueError:
        raise ConfigError(f"{name} 는 수여야 한다") from None
    if not (math.isfinite(value) and low <= value <= high):
        raise ConfigError(f"{name} 는 {low}~{high} 범위여야 한다")
    return value


def extra_settings(env):
    """P2-4b 설정. 형식이 틀리면 ConfigError.
    - MONITOR_DISK: `이름|절대 경로[|du]` 목록(`~` 는 홈) 또는 `none`(이 장치에서는 디스크를 보지 않음 → D1 unsupported 한 줄).
    - MONITOR_OUTBOX: `이름|DB|스키마.표` 목록. MONITOR_DISK_MIN_FREE_PCT(0~100, 기본 20), MONITOR_OUTBOX_MAX_BYTES(기본 5 GiB),
      MONITOR_GAP_S(0 = 끔, 기본), MONITOR_CODE_COMMIT(있으면 소문자 40자 — 빈 값·자리 표시 `__COMMIT__` 는 bad_config)."""
    disk = None
    if env.get("MONITOR_DISK", DEFAULT_DISK).strip() != "none":
        disk = []
        for parts in _entries("MONITOR_DISK", env.get("MONITOR_DISK", DEFAULT_DISK), (2, 3)):
            path = os.path.expanduser(parts[1])
            if not os.path.isabs(path) or parts[2:] not in ([], ["du"]):
                raise ConfigError(f"MONITOR_DISK 의 {parts[0]} 항목: 경로는 절대 경로, 셋째 칸은 du 만")
            disk.append((parts[0], path, parts[2:] == ["du"]))
    outbox = []
    for name, db, table in _entries("MONITOR_OUTBOX", env.get("MONITOR_OUTBOX", DEFAULT_OUTBOX), (3,)):
        if not _TABLE.fullmatch(table):
            raise ConfigError(f"MONITOR_OUTBOX 의 {name} 항목: 표는 `스키마.표`(소문자)다")
        outbox.append((name, db, table))
    commit = env.get("MONITOR_CODE_COMMIT")
    if commit is not None and not _COMMIT.fullmatch(commit.strip()):
        raise ConfigError("MONITOR_CODE_COMMIT 는 커밋 40자(소문자 16진)다")
    return Extra(disk, outbox, _number(env, "MONITOR_DISK_MIN_FREE_PCT", "20", 0, 100, float),
                 _number(env, "MONITOR_OUTBOX_MAX_BYTES", str(5 * 1024 ** 3), 1, 2 ** 63 - 1, int),
                 _number(env, "MONITOR_GAP_S", "0", 0, 10 ** 9, int), None if commit is None else commit.strip())


def code_pin_problem(commit, root=ROOT):
    """주기 실행의 코드 판 확인(P2-4b — SL-5 D4 와 같은 원칙): 저장소 HEAD 가 `commit` 과 같고 미커밋·추적 안 된 파일이 없어야 한다.
    문제가 있으면 그 까닭, 없거나 `commit` 이 None(손 실행)이면 None. git status 는 무시 파일(*.pyc)을 보지 않는다 — 래퍼가 python -I 로 부른다."""
    if commit is None:
        return None
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"코드 사본 {root} 에서 git 을 부르지 못함({type(error).__name__})"
    if head.returncode or dirty.returncode:
        return f"코드 사본 {root} 에서 git 이 실패했다(rc {head.returncode}·{dirty.returncode})"
    if head.stdout.strip() != commit:
        return f"코드 사본 {root} 의 HEAD {head.stdout.strip()} 가 MONITOR_CODE_COMMIT {commit} 과 다르다"
    if dirty.stdout.strip():
        return f"코드 사본 {root} 에 미커밋·추적 안 된 파일 {len(dirty.stdout.splitlines())}개 — 사본에서는 아무것도 고치지 않는다"
    return None


def addr(node):
    return f"{node[0]}:{node[1]}"


def pg(host, port, dbname="postgres"):
    return psycopg.connect(host=host, port=port, user=PG_USER, password=Path(PG_PASSWORD_FILE).read_text().strip(), dbname=dbname,
                           autocommit=True, **PG_LIMITS)


def survey(nodes, clock=lambda: datetime.now(UTC)):
    """회차마다 한 번, 노드마다: 대기 서버인지·논리 슬롯 synced·temporary·시계 차이. M1·M2 와 S7 이 같은 결과를 쓴다(따로 찾으면 switchover 도중 다를 수 있다).
    시계 차이는 그 쿼리 앞뒤 시각의 가운데와 DB now() 로 그 자리에서 잰다(뒤 점검의 Kafka 명령 시간이 차이에 들어가지 않게).
    닿지 못한 노드(접속·비밀번호 파일 실패)는 오류 이름만 남긴다."""
    polled = []
    for node in nodes:
        try:
            with pg(*node) as c:
                before = clock()
                db_now, recovery = c.execute("select now(), pg_is_in_recovery()").fetchone()
                after = clock()
                slots = {r[0]: r[1:] for r in c.execute("select slot_name, synced, temporary from pg_replication_slots where slot_type = 'logical'").fetchall()}
        except (psycopg.Error, OSError) as error:
            polled.append({"node": node, "error": type(error).__name__})
            continue
        polled.append({"node": node, "recovery": recovery, "slots": slots, "skew": abs((before + (after - before) / 2 - db_now).total_seconds())})
    return polled


def primary_of(polled):
    """정확히 하나인 primary 의 조회 결과. 0개·둘 이상·조회 자체 실패(`polled` 가 문자열)면 RuntimeError — 주소와 오류 이름만 담는다."""
    if isinstance(polled, str):
        raise RuntimeError(f"primary 를 알 수 없다 — {polled}")
    primaries = [n for n in polled if n.get("recovery") is False]
    if len(primaries) == 1:
        return primaries[0]
    missed = [f"{addr(n['node'])}({n['error']})" for n in polled if "error" in n]
    raise RuntimeError(f"primary {len(primaries)}개 {[addr(n['node']) for n in primaries]}{' — 갈라짐' if len(primaries) > 1 else ''}, "
                       f"닿은 노드 {len(polled) - len(missed)}/{len(polled)}" + (f", 닿지 못함 {missed}" if missed else ""))


def m1_m2(state, polled, slots_seen=None):
    """M1 슬롯(무효·오래 쉼·붙잡은 WAL), M2 대기 서버(복제 지연·동기 후보·보호 목록 양쪽·failover·synced).
    primary 가 정확히 하나가 아니면 예외 — main 이 M1·M2:run error 로 두고 지난 M1·M2 값을 그대로 둔다(못 본 것을 해소로 알리지 않는다).
    `slots_seen` 에 슬롯마다 wal_status(없으면 None)를 적는다 — main 은 이 함수가 예외 없이 끝난 회차에만 R7 에 넘긴다(반쯤 찬 값은 쓰지 않음)."""
    primary = primary_of(polled)
    out = []
    slots_seen = {} if slots_seen is None else slots_seen
    with pg(*primary["node"]) as c:
        limit = c.execute("select pg_size_bytes(current_setting('max_slot_wal_keep_size'))").fetchone()[0]
        for name in SLOTS:
            row = c.execute("select active, inactive_since, extract(epoch from now() - inactive_since)::float, wal_status, failover, "
                            "pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint from pg_replication_slots where slot_name = %s", (name,)).fetchone()
            if row is None:
                slots_seen[name] = None
                out.append(finding("M1", f"slot:{name}", "alert", "슬롯이 없다"))
                continue
            active, inactive_since, idle_s, wal_status, failover, held = row
            slots_seen[name] = wal_status
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
    first_bad = state.setdefault("first_bad", {})
    configured = {f"synced:{addr(n['node'])}:{name}" for n in polled for name in SLOTS}
    for key in [k for k in first_bad if k not in configured]:  # 설정 밖(옛 키 모양·지운 노드·슬롯)만 지운다 — 잠깐 닿지 않은 노드의 유예 시각은 남긴다
        first_bad.pop(key)
    for node in polled:  # 조회 결과로(다시 연결하지 않음), 노드 목록 순서대로
        where = addr(node["node"])
        if "error" in node:  # 키는 노드·슬롯마다 같게 둔다(못 본 것을 '해소'로 알리지 않게)
            out += [finding("M2", f"synced:{where}:{name}", "alert", f"노드 접속 실패 {node['error']}") for name in SLOTS]
            continue
        if not node["recovery"]:
            for name in SLOTS:
                first_bad.pop(f"synced:{where}:{name}", None)
            out += [finding("M2", f"synced:{where}:{name}", "ok", "이 노드는 지금 primary(대기 아님)") for name in SLOTS]
            continue
        rows = node["slots"]
        for name in SLOTS:
            synced, temporary = rows.get(name, (None, None))
            key, detail = f"synced:{where}:{name}", f"synced={synced}, temporary={temporary}"
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


def connect_bases():
    return [base.strip() for base in CONNECT.split(",") if base.strip()]


def rest(method, path):
    """Connect REST 를 목록 앞에서부터 — 처음 응답한 곳을 쓰고 CONNECT_SEEN 에 남긴다(노트북 하나가 꺼져도 M3·R7 이 멈추지 않게).
    연결 실패·HTTP 오류는 다음 곳으로 넘어가고, 모두 실패하면 마지막 예외를 낸다(main 이 M3:run error 로 둔다)."""
    skipped, last = [], None
    for base in connect_bases():
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, method=method, headers={"Content-Type": "application/json"}), timeout=15) as resp:
                raw = resp.read()
        except (urllib.error.URLError, OSError) as error:  # HTTPError 는 URLError — 그곳이 고장이면 다음 곳
            skipped.append(base)
            last = error
            continue
        CONNECT_SEEN.update(base=base, skipped=skipped)
        return json.loads(raw) if raw else None
    raise last if last is not None else ValueError("MONITOR_CONNECT_REST 에 주소가 없다")


CONNECT_PROBE_TIMEOUT_S = 5


def m3_reach():
    """M3 Connect Worker 응답(Planner 2026-11-08 (나)): 목록의 모든 주소에 GET / — 응답하지 않는 주소마다 `connect_rest:<주소>` alert, 돌아오면 해소.
    Worker 프로세스만 멈추면 PG·Kafka 는 멀쩡해 M2·M4 가 울리지 않고, 그 Task 는 scheduled.rebalance.max.delay.ms(기본 300초) 뒤 남은 Worker 로 옮겨져
    RUNNING 이라 커넥터 줄도 울리지 않는다 — 이중화가 조용히 줄지 않게. /connectors 조회·R7 은 rest() 가 처음 응답한 곳으로 한다."""
    out = []
    for base in connect_bases():
        try:
            with urllib.request.urlopen(urllib.request.Request(base + "/", method="GET"), timeout=CONNECT_PROBE_TIMEOUT_S) as resp:
                resp.read()
        except (urllib.error.URLError, OSError, ValueError) as error:  # 연결 거절·시간 초과(읽기 포함)·HTTP 오류·주소 형식 — 값·비밀은 적지 않는다
            out.append(finding("M3", f"connect_rest:{base}", "alert", f"GET / 응답 없음({CONNECT_PROBE_TIMEOUT_S}초 안, {type(error).__name__}) — 이 Connect Worker 가 멈췄거나 "
                               "장치가 꺼짐(Task 는 남은 Worker 로 옮겨져 커넥터 줄은 RUNNING 일 수 있다)"))
            continue
        out.append(finding("M3", f"connect_rest:{base}", "ok", "GET / 응답"))
    return out


def restart(name):
    """R7 재시작 요청: 커넥터와 FAILED task 만(includeTasks·onlyFailed — 설계 01 R7). 이번 회차에 응답한 곳으로. 돌려주는 값은 HTTP 상태."""
    path = f"/connectors/{urllib.parse.quote(name, safe='')}/restart?includeTasks=true&onlyFailed=true"
    base = CONNECT_SEEN.get("base") or connect_bases()[0]
    with urllib.request.urlopen(urllib.request.Request(base + path, method="POST"), timeout=15) as resp:
        return resp.status


def stamp(ts):
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def r7_step(state, name, failed, slot, slots_seen, now):
    """커넥터 하나의 R7: 시도하거나·기다리거나·멈추고, `r7:<이름>` 줄 하나(또는 None)를 돌려준다. 기록은 state["r7"][이름] = 시도 시각 목록."""
    records, key = state.setdefault("r7", {}), f"r7:{name}"
    tries = records.get(name, [])
    if not failed:  # 바로 비우지 않는다 — 깜빡임(재시작 → 잠깐 RUNNING → FAILED)도 같은 3번 안에 들게
        if not tries:
            return None
        if now - tries[-1] > R7_WINDOW_S:
            records.pop(name)
            return None
        return finding("M3", key, "ok", f"FAILED 아님 — 지난 자동 재시작 {len(tries)}번, 마지막 {stamp(tries[-1])}(30분 뒤 기록을 비움)")
    if slots_seen is None or slot is None or slot not in slots_seen:  # 슬롯을 모르면 재시작하지 않는다(fail closed)
        why = "이번 회차 M1 이 끝까지 돌지 않음" if slots_seen is None else ("커넥터 설정에 slot.name 없음" if slot is None else "감시 목록(MONITOR_SLOTS) 밖")
        return finding("M3", key, "error", f"슬롯 상태를 모름({why}) — 자동 재시작 안 함")
    if slots_seen[slot] is None or slots_seen[slot] in R7_BAD_SLOT:
        return finding("M3", key, "error", f"슬롯 {slot} {'없음' if slots_seen[slot] is None else slots_seen[slot]} — 자동 재시작 안 함, RB-1")
    if len(tries) >= len(R7_WAITS_S):
        return finding("M3", key, "alert", f"자동 재시작 {len(tries)}/{len(R7_WAITS_S)} 다 씀 — 사람 차례(runbook M3, 원인을 고친 뒤 check.py --r7-clear {name})")
    ready = (tries[-1] if tries else now) + R7_WAITS_S[len(tries)]  # 첫 시도는 now + 0
    if now < ready:
        return finding("M3", key, "ok", f"자동 재시작 {len(tries)}/{len(R7_WAITS_S)} 썼음, 다음 가능 {stamp(ready)}")
    records[name] = tries + [now]
    try:  # 시도 기록을 먼저 저장하고, 저장이 된 뒤에만 부른다 — 기록 없이 회차마다 다시 부르는 일이 없게.
        save_state(state)  # 그 순간의 상태 전체가 저장된다: levels 는 아직 지난 회차 값이라 웹훅 실패 규칙(levels 를 미룸)과 어긋나지 않는다
    except OSError as error:
        if tries:
            records[name] = tries
        else:  # 빈 목록을 남기지 않는다 — --r7-clear 가 없는 기록을 found 로 답하지 않게
            records.pop(name)
        return finding("M3", key, "error", f"시도 기록을 저장하지 못해 재시작하지 않음({type(error).__name__})")
    try:
        result = f"HTTP {restart(name)}"
    except urllib.error.HTTPError as error:
        result = f"HTTP {error.code}"
    except Exception as error:  # noqa: BLE001 — 실패해도 시도로 센다(URLError·OSError·ValueError·http.client.HTTPException 등, 이름만)
        result = type(error).__name__
    return finding("M3", key, "ok", f"자동 재시작 {len(tries) + 1}/{len(R7_WAITS_S)} 시도({result})")


def m3(state, slots_seen=None, r7=False, clock=time.time):
    """M3 커넥터·Task FAILED 경보(늘). r7 이면 FAILED 를 자동 재시작한다(r7_step). 상태와 설정을 한 번에 읽고(expand=status·info),
    설정에서는 slot.name 만 쓴다 — 설정 전체는 출력·상태에 넣지 않는다."""
    out, now = [], clock()
    CONNECT_SEEN.clear()
    connectors = rest("GET", "/connectors?expand=status&expand=info")
    if CONNECT_SEEN.get("skipped"):  # 주소만 적는다(Planner 2026-11-08). 응답하지 않는 주소의 경보는 m3_reach 가 낸다(주소마다, 순서와 상관없이)
        out.append(finding("M3", "connect_rest", "ok", f"응답한 Connect REST {CONNECT_SEEN['base']} — 응답하지 않은 앞 주소 {CONNECT_SEEN['skipped']}"))
    for name, info in connectors.items():
        s = info["status"]
        states = [s["connector"]["state"]] + [t["state"] for t in s.get("tasks", [])]
        out.append(finding("M3", f"connector:{name}", "alert" if "FAILED" in states else "ok", f"{states}"))
        if r7:
            slot = ((info.get("info") or {}).get("config") or {}).get("slot.name")
            line = r7_step(state, name, "FAILED" in states, slot, slots_seen, now)
            if line:
                out.append(line)
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
    """M5 Bronze 신선도: checkpoint 의 마지막 확정 배치(commits/N 의 시각, offsets/N 의 위치)와 Kafka 끝 offset. MONITOR_BRONZE=none 이면 unsupported 한 줄."""
    if BRONZE is None:  # S3 키를 읽지도, boto3 를 부르지도 않는다
        return [finding("M5", "bronze", "unsupported", "끔(MONITOR_BRONZE=none) — 이 장치가 볼 Bronze 가 아직 없다")]
    import boto3
    from botocore.config import Config

    keys = [Path(f).read_text().strip() for f in S3_KEY_FILES]
    # MON-3: botocore 기본(연결·읽기 60초, legacy 재시도 5번)이면 S3 가 멈출 때 한 호출이 약 5분 — 감시 유닛 TimeoutStartSec=120 을 혼자 넘긴다.
    # 첫 호출이 실패하면 그 예외로 M5 가 error 가 되므로 멈춤은 회차마다 약 20초(10초 × 2번 + 대기)
    s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT, aws_access_key_id=keys[0], aws_secret_access_key=keys[1], region_name=E("S3_REGION", "ap-northeast-2"),
                      config=Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 2, "mode": "standard"}))
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


def s7(polled, power=True):
    """S7 은 점검을 돌리는 장치 자신을 본다: 시계(조회 때 잰 primary 와의 차이), 전원(macOS 배터리·잠듦 — `power=False` 면 끔).
    primary 를 모르면 예외를 내지 않고 clock_skew 만 error 로 둔다(예외면 S7:run 하나가 되어 전원 줄까지 출력에서 빠진다)."""
    out = []
    try:
        skew = primary_of(polled)["skew"]
        out.append(finding("S7", "clock_skew", "alert" if skew > 1 else "ok", f"이 장치와 DB primary 시계 차이 {skew:.3f}초"))
    except RuntimeError as error:
        out.append(finding("S7", "clock_skew", "error", f"시계를 견줄 primary 가 없다: {error}"))
    if not power:
        out.append(finding("S7", "power", "unsupported", "끔(MONITOR_S7_POWER=0) — 점검 장치 자신의 전원은 보지 않음"))
        return out
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


def folder_bytes(path):
    """폴더 아래 보통 파일 크기 합(추세용). 링크는 따라가지도 세지도 않는다. 도는 사이 사라진 파일은 건너뛴다."""
    total = 0
    for top, _dirs, files in os.walk(path):  # followlinks=False(기본)
        for name in files:
            try:
                st = os.lstat(os.path.join(top, name))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total


def d1(items, min_free):
    """D1 디스크(deploy.md ①·③): 경로마다 그 파일 시스템의 여유(free / total). `items` 가 None 이면(MONITOR_DISK=none) unsupported 한 줄.
    경로를 볼 수 없으면 alert(설정이 틀렸거나 폴더가 사라짐). df 의 Capacity(used/(used+avail))와는 기준이 다르다."""
    if items is None:
        return [finding("D1", "disk", "unsupported", "끔(MONITOR_DISK=none) — 디스크는 그 디스크가 있는 장치에서 본다(P2-4b 결정 B)")]
    out = []
    for name, path, du in items:
        try:
            usage = shutil.disk_usage(path)
            size = f", 폴더 {folder_bytes(path)}B" if du else ""
        except OSError as error:
            out.append(finding("D1", f"disk:{name}", "alert", f"{path} 를 볼 수 없다({type(error).__name__}) — 설정이 틀렸거나 폴더가 사라짐"))
            continue
        free = usage.free * 100 / usage.total
        low = free < min_free
        out.append(finding("D1", f"disk:{name}", "alert" if low else "ok", f"{path} 여유 {free:.1f}%({usage.free / 1024 ** 3:.1f} GiB / "
                                                                              f"{usage.total / 1024 ** 3:.1f} GiB){f' < {min_free:g}%' if low else ''}{size}"))
    return out


def d2(items, max_bytes, polled):
    """D2 outbox 표 크기(deploy.md ②): primary 의 그 DB 에서 pg_total_relation_size(색인·TOAST 포함)와 추정 행 수(reltuples — ANALYZE 전이면 -1).
    기준을 넘으면 "P1-6 정리를 할 때"(결정 C). 표가 없으면 alert(리뷰 C6), primary 를 모르거나 DB 에 못 닿으면 같은 키에 error(해소로 보지 않음)."""
    try:
        node = primary_of(polled)["node"]
    except RuntimeError:
        return [finding("D2", f"outbox:{name}", "error", "primary 를 알 수 없어 보지 못함") for name, _db, _table in items]
    out = []
    for name, db, table in items:
        try:
            with pg(*node, dbname=db) as c:
                size, rows = c.execute("select pg_total_relation_size(to_regclass(%s::text)), "
                                       "(select reltuples::bigint from pg_class where oid = to_regclass(%s::text))", (table, table)).fetchone()
        except (psycopg.Error, OSError) as error:
            out.append(finding("D2", f"outbox:{name}", "error", f"{db} 에 닿지 못함({type(error).__name__})"))
            continue
        if size is None:
            out.append(finding("D2", f"outbox:{name}", "alert", f"{db}.{table} 표가 없다 — 설정이 틀렸거나 표가 사라짐"))
            continue
        over = size > max_bytes
        out.append(finding("D2", f"outbox:{name}", "alert" if over else "ok", f"{db}.{table} {size}B({size / 1024 ** 3:.2f} GiB, 추정 행 {rows})"
                           + (f" > {max_bytes}B — P1-6 정리를 할 때" if over else "")))
    return out


def s7_gap(state, gap_s, now):
    """S7 감시 공백(결정 E): 지난 회차(`last_run`) 뒤 `gap_s` 넘게 지났으면 상태에 열린 공백을 두고, 웹훅 실패가 아닌 회차를 지난 뒤에만 main 이 지운다
    (리뷰 C1 — 한 번 생기는 사건이라, 깨어난 첫 회차에 웹훅이 실패해도 다음 회차에 다시 알린다). 그동안 같은 내용으로 alert. gap_s 가 0 이면 줄 없음."""
    last, state["last_run"] = state.get("last_run"), now
    gap = state.get("gap_open")
    if not (isinstance(gap, dict) and all(type(gap.get(k)) in (int, float) for k in ("start", "end", "count", "longest"))):
        state.pop("gap_open", None)  # 없거나 깨짐
        gap = None
    if not gap_s:
        state.pop("gap_open", None)
        return []
    if type(last) in (int, float) and now - last > gap_s:
        if gap is None:
            gap = state["gap_open"] = {"start": last, "end": now, "count": 1, "longest": now - last}
        else:
            gap.update(count=gap["count"] + 1, longest=max(gap["longest"], now - last))  # start~end 는 첫 공백 그대로(사이에 돈 감시를 공백으로 세지 않게 — 리뷰 R-a)
    if gap is None:
        return [finding("S7", "gap", "ok", f"지난 점검 뒤 {int(now - last)}초" if type(last) in (int, float) else "첫 회차")]
    more = f", 그 뒤 공백 {int(gap['count']) - 1}번 더(가장 긴 {int(gap['longest'])}초)" if gap["count"] > 1 else ""
    return [finding("S7", "gap", "alert", f"감시 공백 {int(gap['end'] - gap['start'])}초({stamp(gap['start'])}~{stamp(gap['end'])}){more} — "
                                          "이 장치가 잠들었거나, 주기 실행이 멈췄거나, 점검을 거부(rc 2)한 사이 감시가 없었다")]


def unimplemented():
    return [finding("M6", "hop_reconcile", "unimplemented", "창 대사 결과 — P1·P2-2 뒤"),
            finding("M7", "kafka_bronze_positions", "unimplemented", "Kafka ↔ Bronze 위치 대사 — P2 뒤"),
            finding("M8", "outbox_unconfirmed_age", "unimplemented", "outbox 미확정 창 나이 — P1-4·P1-6 뒤")]


# Discord 웹훅은 urllib 기본 UA(Python-urllib/3.x)를 403 으로 거절한다 — UA 를 더하면 204(Ops alarnt 실측 2026-11-08). 없으면 웹훅이 늘 실패해 경보가 하나도 가지 않는다
WEBHOOK_USER_AGENT = "pipeline-monitor/1"


def notify(text):
    try:  # 주소 파일이 없거나 못 읽어도 점검은 끝까지 간다(전송 실패로 다룬다 — 다음 점검이 다시 보낸다)
        url = E("MONITOR_WEBHOOK_URL") or (Path(E("MONITOR_WEBHOOK_FILE")).read_text().strip() if E("MONITOR_WEBHOOK_FILE") else None)
    except OSError as error:
        return f"웹훅 실패 {type(error).__name__}(주소 파일)"
    if not url:
        return "웹훅 없음(화면에만)"
    try:
        req = urllib.request.Request(url, method="POST", data=json.dumps({"text": text, "content": text}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": WEBHOOK_USER_AGENT})
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


def write_json(path, data):
    """같은 폴더에 새 임시 파일(O_EXCL, 0600 — 이름을 미리 알 수 없다)을 만들어 쓰고 바꿔 끼운다(쓰는 도중 꺼져도 옛 파일이나 새 파일 하나만 남는다)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_state(state):
    write_json(STATE, state)


def trusted_mark():
    """rc 2 표시 파일(`<상태 파일>.refused`) 경로 — 믿을 수 있을 때만. 상태 폴더가 없거나(만들지 않는다 — 설정 오류에서 폴더를 만들지 않는 규칙),
    폴더·표시 파일이 안전하지 않으면 None(그때는 분마다 알린다 — 그 폴더에 쓸 수 있는 다른 사용자가 거부 알림을 막지 못하게)."""
    mark = STATE.with_name(STATE.name + ".refused")
    try:
        if unsafe(STATE.parent, directory=True) or (os.path.lexists(mark) and unsafe(mark, directory=False)):
            return None
    except OSError:
        return None
    return mark


def read_mark(mark):
    """표시 파일 내용 {error, detail, at} 또는 None(없음·못 읽음·깨짐·시각이 수가 아니거나 범위 밖)."""
    try:
        data = json.loads(mark.read_text()) if mark is not None else None
    except (OSError, ValueError):
        return None
    ok = (isinstance(data, dict) and isinstance(data.get("error"), str) and isinstance(data.get("detail"), str)
          and type(data.get("at")) in (int, float) and 0 <= data["at"] < 32503680000)  # 3000년 앞 — stamp 가 넘치지 않게
    return data if ok else None


def refuse(error, detail, why, clock=time.time):
    """점검하지 않고 끝낸다(rc=2): JSON 한 줄. 알림은 처음 한 번 바로, 같은 거부(error·detail)가 이어지면 한 시간에 한 번(결정 F (나)).
    unsafe_state 는 표시 파일을 읽지도 쓰지도 않고 늘 알린다(변조 신호 — 리뷰 C4). 웹훅 실패면 표시 파일을 쓰지 않아 다음 회차가 다시 보낸다."""
    mark = None if error == "unsafe_state" else trusted_mark()
    last, now = read_mark(mark), clock()
    if last and (last["error"], last["detail"]) == (error, detail) and 0 <= now - last["at"] < REFUSE_REPEAT_S:
        sent = f"생략(같은 거부가 이어짐 — 마지막 알림 {stamp(last['at'])}, 한 시간마다 다시)"
    else:
        sent = notify(f"파이프라인 점검(임시 D18)\n[오류] {why}: {detail}")
        if mark is not None and not sent.startswith("웹훅 실패"):
            try:
                write_json(mark, {"error": error, "detail": detail, "at": now})
            except OSError:
                pass  # 못 쓰면 다음 회차가 다시 알린다(조용해지는 쪽으로 실패하지 않는다)
    print(json.dumps({"t": datetime.now(UTC).isoformat(), "error": error, "detail": detail, "notify": sent}, ensure_ascii=False))
    sys.exit(2)


def main(env=os.environ, clock=time.time):
    try:  # 순서: 필수 모드 → 설정 형식 → 코드 판 → 상태 자리
        missing = missing_config(env)
        if missing:
            refuse("missing_config", "없거나 빈 운영 변수 " + ", ".join(missing), "필수 설정이 없어 점검하지 않았다", clock)
        nodes, power, r7 = pg_nodes(env), flag(env, "MONITOR_S7_POWER", "1"), flag(env, "MONITOR_R7", "0")
        extra = extra_settings(env)
    except ConfigError as error:
        refuse("bad_config", str(error), "설정 형식이 틀려 점검하지 않았다", clock)
    pinned = code_pin_problem(extra.commit)
    if pinned:  # 리뷰 전 코드가 주기 감시로 돌지 않게(SL-5 D4 와 같은 원칙). 래퍼가 아니라 여기서 거부해야 알림이 간다
        refuse("code_pin", pinned, "코드 사본이 고정한 커밋과 달라 점검하지 않았다", clock)
    problem = state_problem()
    if problem:  # 경보를 막을 수 있는 상태 파일은 믿지 않는다 — 점검하지 않고 크게 실패한다(알림을 보내고 rc=2)
        refuse("unsafe_state", problem, "상태 파일 자리가 안전하지 않아 점검하지 않았다", clock)
    state, state_reset = load_state()
    mark, started = trusted_mark(), clock()
    refused = read_mark(mark)  # 앞 회차들이 거부(rc 2)했다 — 이번에 다시 돌았음을 알린다(Planner 2026-11-08)
    try:
        polled = survey(nodes)
    except Exception as error:  # noqa: BLE001 — 조회가 통째로 실패해도 끝까지 간다: primary 를 알 수 없음(M1·M2 는 지난 값 그대로, S7 시계는 error)
        polled = f"노드 조회 실패 {type(error).__name__}"
    findings, failed_checks, slots_seen = [], set(), {}
    for name, fn in (("M1·M2", lambda: m1_m2(state, polled, slots_seen)),
                     ("M3", lambda: m3(state, None if "M1" in failed_checks else slots_seen, r7)),  # 끝까지 돈 M1 의 슬롯 상태만(아니면 모두 모름)
                     ("M3", m3_reach),  # 따로 — 모든 Connect 주소가 응답하지 않아 m3 가 실패해도 주소마다 줄이 남는다
                     ("M4", m4), ("M5", m5), ("S7", lambda: s7(polled, power)), ("S7", lambda: s7_gap(state, extra.gap_s, started)),  # 공백은 따로(전원 줄이 실패해도 봄)
                     ("D1", lambda: d1(extra.disk, extra.min_free)), ("D2", lambda: d2(extra.outbox, extra.max_bytes, polled))):
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
    if refused:
        changes.insert(0, f"[해소] 점검을 다시 한다 — 앞선 거부 {refused['error']}(마지막 알림 {stamp(refused['at'])})")
    sent = notify("파이프라인 점검(임시 D18)\n" + "\n".join(changes)) if changes else None
    if not (sent or "").startswith("웹훅 실패"):  # 못 보낸 변화는 버리지 않는다 — 지난 값을 두어 다음 점검이 다시 보낸다
        state["levels"] = {**carried, **{f["key"]: f["level"] for f in findings}}
        state["checks"] = {**{k: before_check[k] for k in carried}, **{f["key"]: f["check"] for f in findings}}
        state.pop("gap_open", None)  # 공백 경보를 보낸 뒤에만 닫는다(리뷰 C1)
        if refused:  # 재개 줄을 보낸 뒤에만 지운다(리뷰 C5). 못 지우면 다음 회차가 재개 줄을 다시 보낸다
            try:
                mark.unlink()
            except OSError:
                pass
    save_state(state)
    alerts = [f for f in findings if f["level"] in ("alert", "error")]
    print(json.dumps({"t": datetime.now(UTC).isoformat(), "alerts": len(alerts), "changes": changes, "notify": sent, "state_reset": state_reset,
                      "findings": findings}, ensure_ascii=False, default=str))
    sys.exit(1 if alerts else 0)


def r7_clear(name):
    """사람이 R7 기록 하나를 비운다(원인을 고친 뒤 — runbook M3). 상태 파일 경로와 found 를 JSON 으로. 없었으면 rc=1(엉뚱한 MONITOR_STATE 를 드러냄)."""
    problem = state_problem()
    if problem:
        refuse("unsafe_state", problem, "상태 파일 자리가 안전하지 않아 R7 기록을 비우지 않았다")
    state, _reset = load_state()
    found = state.get("r7", {}).pop(name, None) is not None
    if found:
        save_state(state)
    print(json.dumps({"t": datetime.now(UTC).isoformat(), "r7_cleared": name, "found": found, "state": str(STATE)}, ensure_ascii=False))
    sys.exit(0 if found else 1)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--r7-clear"]:
        if len(sys.argv) != 3:
            print("사용: check.py --r7-clear <커넥터 이름>", file=sys.stderr)
            sys.exit(2)
        r7_clear(sys.argv[2])
    main()
