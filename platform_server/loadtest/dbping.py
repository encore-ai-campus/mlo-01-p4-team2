"""DB 왕복 재기: 새 연결(TLS 포함)·읽기·쓰기 커밋. 쓰기는 봇 계정(load000) 한 행의 last_login 만 고친다.

사용(백엔드 환경에서): python /load/dbping.py  — 운영은 game-server 에서 같은 환경으로 돌린다(README).
"""
import os
import sys
import time

sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import connection, transaction  # noqa: E402
from django.utils import timezone  # noqa: E402


def median(xs):
    return sorted(xs)[len(xs) // 2]


connects = []
for _ in range(5):
    connection.close()
    started = time.perf_counter()
    connection.ensure_connection()
    connects.append((time.perf_counter() - started) * 1000)
reads = []
with connection.cursor() as cursor:
    for _ in range(30):
        started = time.perf_counter()
        cursor.execute("SELECT 1")
        cursor.fetchall()
        reads.append((time.perf_counter() - started) * 1000)
users = get_user_model().objects.filter(username="load000")
writes = []
if users.exists():
    for _ in range(20):
        started = time.perf_counter()
        with transaction.atomic():
            users.update(last_login=timezone.now())
        writes.append((time.perf_counter() - started) * 1000)
print(f"새 연결 {median(connects):.1f}ms · 읽기 {median(reads):.2f}ms · 쓰기 커밋 {median(writes):.1f}ms" if writes else "load000 없음")
