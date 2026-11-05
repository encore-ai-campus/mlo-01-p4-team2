"""RB-2 재개 전 LSN 점검(설계 01 §6 RB-2·P4) — 단독 실행 가능.

사용: rb_lsn_check.py [--connector ads-outbox-cdc]
- 커넥터가 저장한 offset 의 LSN(lsn·lsn_commit·lsn_proc 중 가장 큰 값)과 지금 primary 의 WAL 끝(pg_current_wal_lsn)을 비교한다.
- offset 이 WAL 끝보다 **앞서면 진행을 거부한다(rc=2)**: 복원·비동기 승격 뒤 이 상태로 다시 켜면 PostgreSQL 이 시작 위치보다 앞에서 커밋된
  트랜잭션을 오류 없이 0번 보내고, Debezium 도 저장 위치 이하를 걸러 조용히 빠진다(Worker 보완 §1 실험). offset 을 지우고(RB-2) 다시 켠다.
- rc: 0 offset ≤ WAL 끝, 2 거부(offset 이 앞섬), 3 저장된 offset 없음, 4 조회 실패.
- **rc=0 은 안전하다는 뜻이 아니다.** 크기 비교는 타임라인이 갈린 것을 가리지 못한다: 복원 지점(승격 분기점) R < 저장 offset X 인데 새 primary 가
  X 를 넘게 쓰면 rc=0 이지만, 그대로 켜면 새 타임라인의 (R, X] 트랜잭션을 Debezium 이 저장 위치 이하로 걸러 빠진다. 그래서 복원·승격 뒤에는
  결과와 상관없이 rb.py rb2 로만 다시 켠다(offset 을 늘 지운다 — 설계 01 §6 RB-2). 이 점검 결과는 사건 기록용이다.
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

import psycopg

E = os.environ.get


def lsn_text(n):
    return f"{n >> 32:X}/{n & 0xFFFFFFFF:X}"


def rest(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def pg(port, dbname="postgres"):
    pw = Path(E("PG_PASSWORD_FILE", "/opt/postgresql/config/postgres-password")).read_text().strip()
    return psycopg.connect(host=E("PG_HOST", "127.0.0.1"), port=port, user="postgres", password=pw, dbname=dbname, connect_timeout=5, autocommit=True)


def stored_lsn(base, connector):
    offsets = rest(base, "GET", f"/connectors/{connector}/offsets").get("offsets", [])
    values = [int(o["offset"][k]) for o in offsets for k in ("lsn", "lsn_commit", "lsn_proc") if k in o.get("offset", {})]
    return max(values) if values else None


def current_lsn(port):
    with pg(port) as c:
        if c.execute("select pg_is_in_recovery()").fetchone()[0]:  # 대기 서버에서는 pg_current_wal_lsn() 자체가 오류다 — 먼저 가린다
            raise RuntimeError(f"포트 {port} 는 대기 서버다 — primary(HAProxy rw)로 물어야 한다")
        return int(c.execute("select pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0')::bigint").fetchone()[0])


def check(connector, rest, port):
    """(rc, 설명). 다른 RB 절차도 이 함수를 쓴다."""
    try:
        offset = stored_lsn(rest, connector)
        server = current_lsn(port)
    except Exception as error:  # noqa: BLE001 — 점검 도구: 조회 실패는 진행 거부와 같은 뜻으로 알린다
        return 4, f"조회 실패: {type(error).__name__}: {error}"
    if offset is None:
        return 3, f"{connector}: 저장된 offset 이 없다(새로 시작하면 슬롯 위치부터 읽는다)"
    detail = f"{connector}: 저장된 offset {lsn_text(offset)}, primary WAL 끝 {lsn_text(server)}"
    if offset > server:
        return 2, detail + f" — offset 이 {offset - server}B 앞선다. 이대로 켜면 그 사이 새 트랜잭션을 건너뛴다. offset 을 지우고 다시 켠다(RB-2)"
    return 0, detail + " — offset ≤ WAL 끝(복원·승격 뒤라면 이 결과와 상관없이 RB-2 로 offset 을 지운다)"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--connector", default="ads-outbox-cdc")
    ap.add_argument("--rest", default=E("CONNECT_REST", "http://127.0.0.1:8083"))
    ap.add_argument("--pg-port", type=int, default=int(E("PG_PORT", "5010")))
    a = ap.parse_args()
    rc, detail = check(a.connector, a.rest, a.pg_port)
    print(f"LSN_CHECK rc={rc} {detail}")
    sys.exit(rc)
