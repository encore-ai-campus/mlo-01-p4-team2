"""감시 check.py 의 회차 상한(MON-3, 2026-11-09): DB 연결의 statement_timeout·keepalive, M5 S3 클라이언트의 시간 제한·재시도.

DB·S3 없이 돈다 — psycopg.connect·boto3 를 가짜로 바꾼다. 실행: 저장소 뿌리에서 `python -m unittest discover pipeline/tests`.
"""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import psycopg

from pipeline.monitor import check


class PgLimits(unittest.TestCase):
    def test_every_connection_carries_the_limits_and_libpq_accepts_them(self):
        with mock.patch.object(check.Path, "read_text", return_value="pw\n"), mock.patch.object(check.psycopg, "connect") as connect:
            check.pg("10.0.0.1", 5437, dbname="ads")
        kwargs = connect.call_args.kwargs
        self.assertEqual({k: kwargs.get(k) for k in check.PG_LIMITS} | {"autocommit": kwargs.get("autocommit")},
                         {"connect_timeout": 5, "options": "-c statement_timeout=10000", "keepalives": 1, "keepalives_idle": 10,
                          "keepalives_interval": 5, "keepalives_count": 3, "tcp_user_timeout": 30000, "autocommit": True})
        # 이름이 틀리면(예: keepalive_idle) libpq 가 conninfo 를 거절한다 — 연결 없이 libpq 의 해석기로 확인
        parsed = psycopg.conninfo.conninfo_to_dict(psycopg.conninfo.make_conninfo(**{k: v for k, v in kwargs.items() if k != "autocommit"}))
        self.assertEqual({k: parsed[k] for k in check.PG_LIMITS}, {k: str(v) for k, v in check.PG_LIMITS.items()})


class S3Limits(unittest.TestCase):
    def test_m5_client_has_short_timeouts_and_two_attempts(self):
        made = []
        fake_boto3 = types.SimpleNamespace(client=lambda *a, **kw: made.append(kw) or object())
        with tempfile.TemporaryDirectory() as tmp:
            keys = (Path(tmp) / "a", Path(tmp) / "s")
            for key in keys:
                key.write_text("k\n")
            with mock.patch.object(check, "BRONZE", [["lab", "t", "s3a://b/p"]]), mock.patch.object(check, "S3_KEY_FILES", keys), \
                    mock.patch.dict(sys.modules, {"boto3": fake_boto3}), mock.patch.object(check, "brokers_up", return_value=(0, 3)):
                lines = check.m5()  # 브로커 0대 → S3 를 부르기 전에 error 한 줄(클라이언트는 이미 만들어짐)
        self.assertIn(("bronze:lab", "error"), [(f["key"], f["level"]) for f in lines])
        config = made[0]["config"]
        self.assertEqual((config.connect_timeout, config.read_timeout, config.retries),
                         (5, 10, {"total_max_attempts": 2, "mode": "standard"}))


if __name__ == "__main__":
    unittest.main()
