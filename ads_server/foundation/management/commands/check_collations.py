"""정렬 버전 점검(docs/plans/00 §4.3 V-2): 기록된 정렬 버전이 실제와 같은가. 다르면 문자열 색인의 순서가 어긋났을 수 있다 — 고치는 순서는 V-5.

- 모든 DB 의 기록 버전(`pg_database`). 기록이 없는 DB(template0)는 보지 않는다 — PostgreSQL 과 같다.
- 이 DB 의 객체가 쓰는 정렬(PostgreSQL 문서 ALTER COLLATION 의 대조 질의 — `pg_collation` 은 DB 마다 따로다).
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

STALE = """
SELECT 'DB ' || datname FROM pg_database WHERE datcollversion <> pg_database_collation_actual_version(oid)
UNION ALL
SELECT DISTINCT pg_describe_object(refclassid, refobjid, refobjsubid) FROM pg_depend JOIN pg_collation c ON refclassid = 'pg_collation'::regclass AND refobjid = c.oid
 WHERE c.collversion <> pg_collation_actual_version(c.oid)
"""


class Command(BaseCommand):
    help = "정렬 버전이 기록과 같은지 본다(모든 DB, 이 DB 의 객체가 쓰는 정렬). 다르면 비정상 종료한다. 운영 데이터는 바꾸지 않는다."

    def handle(self, *args, **options):
        with connection.cursor() as cur:
            cur.execute(STALE)
            stale = [row[0] for row in cur.fetchall()]
        if stale:
            raise CommandError(f"정렬 버전이 기록과 다르다: {', '.join(stale)} — 색인을 다시 만든 뒤 버전을 갱신한다(docs/plans/00 §4.3 V-5)")
        self.stdout.write("ok")
