"""tests/tools/pg_admin.py — 시험·부하용 DB 도우미가 **남의 데이터베이스를 지우지 않는다**(검토 DB-1a P1)."""
import sys
import uuid

from django.test import SimpleTestCase, tag

from tests.support.helpers import BASE_DIR

sys.path.insert(0, str(BASE_DIR / "tests" / "tools"))
import pg_admin  # noqa: E402
from tests.support.cases import Cases


def exists(name):
    with pg_admin.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM pg_database WHERE datname = %s", (name,)).fetchone()[0] == 1


def other_database_with_marker(prefix):
    """도우미가 만들지 않은 DB(남의 것)를 만들고 표식 행을 넣는다. 지우는 정리 작업을 함께 돌려준다."""
    name = f"{prefix}_{uuid.uuid4().hex}"
    with pg_admin.connect() as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    with pg_admin.connect(name) as conn:
        conn.execute("CREATE TABLE marker (id INT PRIMARY KEY)")
        conn.execute("INSERT INTO marker VALUES (7)")

    def cleanup():
        with pg_admin.connect() as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    return name, cleanup


@tag("security")
class DatabaseOwnershipTests(Cases, SimpleTestCase):
    def case_created_database_is_random_and_dropped_only_when_owned(self):
        a, b = pg_admin.create_database("ads_t"), pg_admin.create_database("ads_t")
        self.addCleanup(lambda: [pg_admin.drop_database(n) for n in (a, b) if exists(n)])
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), len("ads_t_") + 32)  # 이름이 겹칠 수 없을 만큼 길다
        self.assertTrue(exists(a) and exists(b))
        pg_admin.drop_database(a)
        self.assertFalse(exists(a))
        self.assertTrue(exists(b))

    def case_a_database_this_run_did_not_create_is_never_dropped(self):
        """남이 만든 DB(같은 이름이 이미 있는 경우 포함)는 지우기를 거부하고 데이터가 그대로다."""
        other, cleanup = other_database_with_marker("ads_t_other")
        self.addCleanup(cleanup)
        with self.assertRaises(ValueError):
            pg_admin.drop_database(other)
        with pg_admin.connect(other) as conn:
            self.assertEqual(conn.execute("SELECT id FROM marker").fetchone()[0], 7)

    def case_create_does_not_replace_an_existing_database(self):
        """무작위 이름이 우연히 겹쳐도 CREATE 가 실패할 뿐 기존 DB 를 지우지 않는다."""
        other, cleanup = other_database_with_marker("ads_t_fixed")
        self.addCleanup(cleanup)
        fixed = uuid.UUID(other.rsplit("_", 1)[1])
        original = pg_admin.uuid.uuid4
        pg_admin.uuid.uuid4 = lambda: fixed
        try:
            with self.assertRaises(SystemExit):
                pg_admin.create_database("ads_t_fixed")
        finally:
            pg_admin.uuid.uuid4 = original
        self.assertTrue(exists(other))
        with pg_admin.connect(other) as conn:
            self.assertEqual(conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall(), [("marker",)])

    def test_database_ownership(self):
        self.run_cases()
