"""부하 시험용 MySQL 엔진: 운영 DB 클러스터의 느린 쓰기 커밋을 흉내 낸다(운영 실측 7.5ms, 로컬 1ms).

쓰기 문장(autocommit)과 트랜잭션 커밋 뒤에 FAKE_COMMIT_MS(기본 5.8 → 로컬과 합쳐 약 7.5ms) 만큼 기다린다. 기다리는 동안 GIL 을 놓는다(네트워크 대기와 같다).
읽기는 그대로다(컨테이너의 네트워크 지연이 맡는다). 운영 설정이 아니다 — settings_prodlike 에서만 쓴다.
"""
import os
import time

from django.db.backends.mysql import base

COMMIT = float(os.environ.get("FAKE_COMMIT_MS", "5.8")) / 1000
WRITES = ("insert", "update", "delete", "replace")


class Cursor(base.CursorWrapper):
    def __init__(self, cursor, db):
        super().__init__(cursor)
        self.db = db

    def _wait(self, query):
        if COMMIT and self.db.get_autocommit() and query.lstrip()[:7].lower().startswith(WRITES):
            time.sleep(COMMIT)

    def execute(self, query, args=None):
        result = super().execute(query, args)
        self._wait(query)
        return result

    def executemany(self, query, args):
        result = super().executemany(query, args)
        self._wait(query)
        return result


class DatabaseWrapper(base.DatabaseWrapper):
    def create_cursor(self, name=None):
        return Cursor(self.connection.cursor(), self)

    def _commit(self):
        result = super()._commit()
        if COMMIT and self.connection is not None:
            time.sleep(COMMIT)
        return result
