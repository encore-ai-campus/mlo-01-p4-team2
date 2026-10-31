"""시험·부하용 PostgreSQL 관리 도우미. 접속 값은 환경변수(DB_*)와 `.env` 에서 읽는다.

    python tests/tools/pg_admin.py              접속 점검: 서버 18 에 닿는지, 정렬이 builtin C.UTF-8 이고 모든 DB 의 정렬 버전이 기록과 같은지, 시험 DB 를 만들 권한이 있는지(없으면 이유를 말하고 비정상 종료)

    python tests/tools/pg_admin.py --print-env  `.env` 를 읽어 DB_* 를 셸 `export` 문으로 출력한다(check.sh 가 eval)

`check.sh` 가 맨 앞에서 부른다 — DB 가 없으면 시험을 건너뛰지 않고 여기서 멈춘다. 로컬 서버는 `tests/docker/compose.yaml` 로 띄운다.
"""
import os
import sys
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))

from config.env import load_env  # noqa: E402

MAINTENANCE_DB = "postgres"  # 데이터베이스를 만들고 지울 때 붙는 곳(지울 DB 에 붙어 있으면 지울 수 없다)
LOCALE = ("b", "C.UTF-8", "C.UTF-8", "C.UTF-8")  # 결정 D11: builtin 제공자의 C.UTF-8, libc 쪽(LC_COLLATE·LC_CTYPE)도 C.UTF-8. 시험 DB 는 template1 에서 물려받는다


def connect(dbname=MAINTENANCE_DB):
    """autocommit 연결(CREATE/DROP DATABASE 는 트랜잭션 안에서 돌 수 없다)."""
    import psycopg

    load_env()
    missing = [n for n in ("DB_USER", "DB_PASSWORD") if not os.environ.get(n)]
    if missing:
        raise SystemExit(f"환경변수 {', '.join(missing)} 가 필요합니다(.env.example).")
    try:
        return psycopg.connect(host=os.environ.get("DB_HOST", "127.0.0.1"), port=int(os.environ.get("DB_PORT", 5432)), dbname=dbname,
                               user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], connect_timeout=5, autocommit=True)
    except psycopg.Error as exc:
        raise SystemExit(f"PostgreSQL 에 연결하지 못했습니다: {exc}\n시험용 컨테이너가 떠 있는지 확인하세요(tests/docker/compose.yaml).")


_OWNED = set()  # 이 프로세스가 CREATE 에 성공한 데이터베이스. 이것만 지운다(이름이 겹친 남의 DB 는 건드리지 않는다)


def create_database(prefix):
    """무작위 이름의 새 데이터베이스를 만들어 이름을 돌려준다. 이미 있는 이름이면 지우지 않고 실패한다(CREATE 는 `IF NOT EXISTS` 가 없다)."""
    import psycopg
    from psycopg import sql

    name = f"{prefix}_{uuid.uuid4().hex}"
    with connect() as conn:
        try:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        except psycopg.Error as exc:
            raise SystemExit(f"데이터베이스 {name} 을 만들지 못했습니다: {exc}")
    _OWNED.add(name)
    return name


def drop_database(name):
    """이 프로세스가 만든 데이터베이스만 지운다. 만들지 않은 이름이면 거부한다. 남은 연결(끝난 시험 서버 등)이 있어도 지운다(`FORCE`)."""
    from psycopg import sql

    if name not in _OWNED:
        raise ValueError(f"{name} 은 이 실행이 만든 데이터베이스가 아니라 지우지 않는다")
    with connect() as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    _OWNED.discard(name)


def preflight():
    with connect() as conn:
        version = conn.execute("SHOW server_version").fetchone()[0]
        if not version.startswith("18."):  # 아래 datlocale 열은 17 부터 있다 — 먼저 묻는다
            raise SystemExit(f"PostgreSQL 18 이 필요합니다(지금 {version}).")
        locale = conn.execute("SELECT datlocprovider, datlocale, datcollate, datctype FROM pg_database WHERE datname = 'template1'").fetchone()
        # 정렬 버전(docs/plans/00 §4.3 V-2): 기록이 없는 DB(template0)는 보지 않는다 — PostgreSQL 과 같다. 객체별 정렬 대조는 운영 DB 를 보는 ops_check 가 한다
        stale = [r[0] for r in conn.execute("SELECT datname FROM pg_database WHERE datcollversion <> pg_database_collation_actual_version(oid)")]
    if tuple(locale) != LOCALE:
        raise SystemExit(f"template1 의 로케일이 builtin C.UTF-8 이 아닙니다(지금 provider·locale·collate·ctype = {tuple(locale)}). "
                         "tests/docker/compose.yaml 의 POSTGRES_INITDB_ARGS 로 볼륨을 다시 만드세요(`down -v`).")
    if stale:  # template1 이면 CREATE DATABASE 부터 실패한다
        raise SystemExit(f"정렬 버전이 기록과 다른 DB 가 있습니다: {', '.join(stale)}. 문자열 색인이 어긋났을 수 있습니다 — docs/plans/00 §4.3 V-5 순서로 고치세요"
                         "(로컬 컨테이너는 데이터를 버려도 되면 `down -v` 로 다시 만든다).")
    drop_database(create_database("ads_preflight"))  # 생성·삭제 권한 확인: 무작위 이름이라 남의 DB 와 겹치지 않는다
    print(f"PostgreSQL {version} 연결 확인, 정렬 builtin C.UTF-8·버전 기록과 같음, 데이터베이스 생성·삭제 권한 확인")


def print_env():
    import shlex

    load_env()
    for name in ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT"):
        if os.environ.get(name):
            print(f"export {name}={shlex.quote(os.environ[name])}")


if __name__ == "__main__":
    print_env() if sys.argv[1:] == ["--print-env"] else preflight()
