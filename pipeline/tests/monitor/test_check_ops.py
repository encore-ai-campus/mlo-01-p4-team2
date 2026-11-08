"""감시 check.py 의 운영 적용(P2-4b 뒤, Ops 2026-11-08): DB 계정 MONITOR_PG_USER, MONITOR_BRONZE=none, MONITOR_CONNECT_REST 쉼표 목록,
모든 Connect 주소 응답(m3_reach — Planner (나)), 웹훅 User-Agent.

DB·Connect·S3 없이 돈다 — psycopg.connect·urlopen 을 가짜로 바꾼다. 실행: 저장소 뿌리에서 `python -m unittest discover pipeline/tests`.
"""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from pipeline.monitor import check


class PgUser(unittest.TestCase):
    def test_connections_use_the_configured_account(self):
        self.assertIn("MONITOR_PG_USER", check.REQUIRED)
        with mock.patch.object(check, "PG_USER", "pipeline_monitor"), mock.patch.object(check.Path, "read_text", return_value="pw\n"), \
                mock.patch.object(check.psycopg, "connect") as connect:
            check.pg("10.0.0.1", 5437, dbname="ads")
        self.assertEqual({k: connect.call_args.kwargs[k] for k in ("host", "port", "user", "dbname", "password")},
                         {"host": "10.0.0.1", "port": 5437, "user": "pipeline_monitor", "dbname": "ads", "password": "pw"})


class NoBronze(unittest.TestCase):
    def test_none_is_parsed_and_m5_is_one_unsupported_line_without_s3(self):
        self.assertIsNone(check.parse_bronze(" none "))
        self.assertEqual(check.parse_bronze("a|t|s3a://b/p,c|u|s3a://b/q"), [["a", "t", "s3a://b/p"], ["c", "u", "s3a://b/q"]])
        with mock.patch.object(check, "BRONZE", None), mock.patch.dict(sys.modules, {"boto3": None}), \
                mock.patch.object(check, "brokers_up", side_effect=AssertionError("Kafka 를 보지 않아야 한다")):
            lines = check.m5()  # boto3 를 import 하면 ImportError — 부르지 않는다
        self.assertEqual([(f["check"], f["key"], f["level"]) for f in lines], [("M5", "bronze", "unsupported")])

    def test_s3_settings_are_required_only_with_a_bronze(self):
        full = {name: "v" for name in check.REQUIRED} | {"MONITOR_REQUIRE_ALL": "1"}
        no_s3 = {k: v for k, v in full.items() if k not in check.S3_SETTINGS}
        self.assertEqual(check.missing_config({**no_s3, "MONITOR_BRONZE": "none"}), [])
        self.assertEqual(check.missing_config(no_s3), list(check.S3_SETTINGS))
        self.assertEqual(check.missing_config({**no_s3, "MONITOR_BRONZE": "none", "MONITOR_PG_USER": " "}), ["MONITOR_PG_USER"])


class ConnectList(unittest.TestCase):
    A, B, C = "http://10.0.0.1:8088", "http://10.0.0.2:8088", "http://10.0.0.3:8088"
    BODY = {"x": {"status": {"connector": {"state": "RUNNING"}, "tasks": [{"id": 0, "state": "FAILED"}]}, "info": {"config": {"slot.name": "s1"}}}}

    def setUp(self):
        self.addCleanup(check.CONNECT_SEEN.clear)  # 이 시험의 가짜 주소가 뒤 시험(R7 재시작 주소)에 남지 않게

    def urlopen(self, down):
        calls = []

        def fake(request, timeout=None):
            calls.append((request.get_method(), request.full_url))
            base = request.full_url.split("/connectors", 1)[0]
            if base in down:
                raise down[base]
            body = json.dumps(self.BODY).encode() if request.get_method() == "GET" else b""
            return mock.MagicMock(**{"__enter__.return_value.read.return_value": body, "__enter__.return_value.status": 204})
        return fake, calls

    def test_first_answering_address_serves_get_and_r7_post(self):
        broken = urllib.error.HTTPError(self.B, 500, "고장", {}, io.BytesIO())
        self.addCleanup(broken.close)  # 닫지 않으면 ResourceWarning
        down = {self.A: urllib.error.URLError("꺼짐"), self.B: broken}
        fake, calls = self.urlopen(down)
        tmp = tempfile.TemporaryDirectory()  # R7 은 시도 기록을 상태 파일에 먼저 저장한다 — 이 장치의 상태 파일을 건드리지 않게
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(check, "CONNECT", f"{self.A}, {self.B} ,{self.C}"), mock.patch.object(check.urllib.request, "urlopen", fake), \
                mock.patch.object(check, "STATE", Path(tmp.name) / "state.json"):
            lines = {f["key"]: f for f in check.m3({}, {"s1": "reserved"}, r7=True, clock=lambda: 1_800_000_000.0)}
        self.assertEqual([c[1].split("/connectors")[0] for c in calls if c[0] == "GET"], [self.A, self.B, self.C])
        self.assertEqual([c[1] for c in calls if c[0] == "POST"], [f"{self.C}/connectors/x/restart?includeTasks=true&onlyFailed=true"])
        self.assertEqual(lines["connect_rest"]["level"], "ok")
        self.assertEqual(lines["connect_rest"]["detail"], f"응답한 Connect REST {self.C} — 응답하지 않은 앞 주소 {[self.A, self.B]}")  # 주소만
        self.assertEqual((lines["connector:x"]["level"], lines["r7:x"]["detail"]), ("alert", "자동 재시작 1/3 시도(HTTP 204)"))

    def test_first_address_answering_adds_no_line_and_all_down_raises(self):
        fake, calls = self.urlopen({})
        with mock.patch.object(check, "CONNECT", f"{self.A},{self.B}"), mock.patch.object(check.urllib.request, "urlopen", fake):
            lines = {f["key"] for f in check.m3({})}
        self.assertNotIn("connect_rest", lines)
        self.assertEqual(len(calls), 1)
        fake, calls = self.urlopen({self.A: urllib.error.URLError("꺼짐"), self.B: TimeoutError("시간 초과")})
        with mock.patch.object(check, "CONNECT", f"{self.A},{self.B}"), mock.patch.object(check.urllib.request, "urlopen", fake), \
                self.assertRaises(TimeoutError):
            check.m3({})
        self.assertEqual(check.CONNECT_SEEN, {})  # 앞 회차의 응답한 곳을 남기지 않는다

    def test_read_timeout_on_the_first_address_moves_on(self):  # 리뷰 T1: urllib 은 응답 대기 시간 초과를 URLError 로 감싸지 않는다(맨 TimeoutError)
        fake, calls = self.urlopen({self.A: TimeoutError("응답 없음")})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(check, "CONNECT", f"{self.A},,{self.B}"), mock.patch.object(check.urllib.request, "urlopen", fake), \
                mock.patch.object(check, "STATE", Path(tmp.name) / "state.json"):  # 빈 항목은 버린다(남기면 ValueError 로 다음 주소에 못 감)
            lines = {f["key"]: f for f in check.m3({}, {"s1": "reserved"}, r7=True, clock=lambda: 1_800_000_000.0)}
        self.assertEqual([c[1].split("/connectors")[0] for c in calls if c[0] == "GET"], [self.A, self.B])
        self.assertEqual([c[1] for c in calls if c[0] == "POST"], [f"{self.B}/connectors/x/restart?includeTasks=true&onlyFailed=true"])
        self.assertEqual(lines["connect_rest"]["detail"], f"응답한 Connect REST {self.B} — 응답하지 않은 앞 주소 {[self.A]}")


class ConnectReach(unittest.TestCase):
    """m3_reach: 목록의 모든 주소에 GET /(5초) — 응답하지 않는 주소마다 alert(Worker 프로세스만 멈춘 것도), main 이 부른다."""
    A, B, C = ConnectList.A, ConnectList.B, ConnectList.C

    def test_every_address_is_probed_and_silent_ones_alert(self):
        calls = []

        def fake(request, timeout=None):
            calls.append((request.get_method(), request.full_url, timeout))
            if request.full_url.startswith(self.B):
                raise urllib.error.URLError("꺼짐")
            if request.full_url.startswith(self.C):
                raise TimeoutError("응답 없음")
            return mock.MagicMock(**{"__enter__.return_value.read.return_value": b"{}"})
        with mock.patch.object(check, "CONNECT", f"{self.A}, {self.B},{self.C}"), mock.patch.object(check.urllib.request, "urlopen", fake):
            lines = check.m3_reach()
        self.assertEqual(calls, [("GET", f"{base}/", 5) for base in (self.A, self.B, self.C)])  # 첫 주소가 응답해도 끝까지 묻는다
        self.assertEqual([(f["check"], f["key"], f["level"]) for f in lines],
                         [("M3", f"connect_rest:{self.A}", "ok"), ("M3", f"connect_rest:{self.B}", "alert"), ("M3", f"connect_rest:{self.C}", "alert")])
        self.assertIn("URLError", lines[1]["detail"])
        self.assertIn("TimeoutError", lines[2]["detail"])  # 응답 대기 시간 초과(맨 TimeoutError)도 경보


class Webhook(unittest.TestCase):
    def test_the_request_carries_a_user_agent(self):  # Discord 는 urllib 기본 UA 를 403 으로 거절한다(Ops alarnt 2026-11-08)
        seen = []

        def fake(request, timeout=None):
            seen.append(request)
            return mock.MagicMock(**{"__enter__.return_value.status": 204})
        with mock.patch.dict(os.environ, {"MONITOR_WEBHOOK_URL": "https://hook.invalid/x"}), mock.patch.object(check.urllib.request, "urlopen", fake):
            self.assertEqual(check.notify("시험"), "웹훅 204")
        self.assertEqual(seen[0].get_header("User-agent"), "pipeline-monitor/1")
        self.assertEqual(seen[0].get_header("Content-type"), "application/json")


if __name__ == "__main__":
    unittest.main()
