"""감시 check.py 의 R7(MON-2): FAILED 커넥터·Task 자동 재시작 — 30분에 3번(앞 시도 뒤 0·5·15분), 다 쓰면 사람, 슬롯 문제·모름이면 안 함,
시도 기록을 저장한 뒤에만 부름, 설정은 slot.name 만 씀, MONITOR_R7=1 일 때만.

Connect·DB 없이 돈다 — rest(GET)·urlopen(POST)·노드 연결을 가짜로 바꾼다. 실행: 저장소 뿌리에서 `python -m unittest discover pipeline/tests`.
"""
import http.client
import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from pipeline.monitor import check
from pipeline.tests.monitor.test_check_nodes import NODES, cluster

T0 = 1_800_000_000.0  # 가짜 시계의 시작(초)
MIN = 60


class Connect:
    """가짜 Connect: GET /connectors?expand=status&expand=info 와 POST …/restart(urlopen)를 기록한다."""

    def __init__(self):
        self.state = {"a": "FAILED"}
        self.slot = {"a": "s1"}
        self.posts = []
        self.post_error = None

    def get(self, method, path):
        assert (method, path) == ("GET", "/connectors?expand=status&expand=info"), (method, path)
        out = {}
        for name, st in self.state.items():
            config = {"connector.class": "io.debezium.connector.postgresql.PostgresConnector", "database.password": "secret-probe"}
            if self.slot.get(name) is not None:
                config["slot.name"] = self.slot[name]
            out[name] = {"status": {"connector": {"state": "RUNNING"}, "tasks": [{"id": 0, "state": st}]}, "info": {"config": config}}
        return out

    def urlopen(self, request, timeout=None):
        self.posts.append((request.get_method(), request.full_url))
        if self.post_error:
            raise self.post_error
        return mock.MagicMock(**{"__enter__.return_value.status": 204})


class R7Steps(unittest.TestCase):
    """R7 판단 — rest·urlopen·상태 파일만 가짜로."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.connect, self.state = Connect(), {}
        self.state_path = Path(tmp.name) / "pipeline_monitor" / "state.json"
        self.state_path.parent.mkdir(mode=0o700)
        for target, name, value in ((check, "rest", self.connect.get), (check.urllib.request, "urlopen", self.connect.urlopen),
                                    (check, "STATE", self.state_path), (check, "CONNECT", "http://fake")):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def m3(self, minutes, slots_seen="ok", r7=True):
        slots = {"s1": "reserved"} if slots_seen == "ok" else slots_seen
        return {f["key"]: f for f in check.m3(self.state, slots, r7, clock=lambda: T0 + minutes * MIN)}

    def test_three_tries_at_least_0_5_15_minutes_apart_then_a_person(self):
        expect = [(0, 1, "ok", "자동 재시작 1/3 시도(HTTP 204)"), (4.9, 1, "ok", "1/3 썼음, 다음 가능"), (5, 2, "ok", "2/3 시도"),
                  (19.9, 2, "ok", "2/3 썼음"), (20, 3, "ok", "3/3 시도"), (20.5, 3, "alert", "3/3 다 씀"),
                  (60, 3, "alert", "다 씀"), (60 * 24, 3, "alert", "다 씀")]  # 다 쓴 뒤에는 30분·하루가 지나도 다시 부르지 않는다(Q4 (나))
        for minutes, posts, level, text in expect:
            with self.subTest(minutes=minutes):
                lines = self.m3(minutes)
                self.assertEqual(len(self.connect.posts), posts)
                self.assertEqual(lines["connector:a"]["level"], "alert")  # M3 경보는 늘
                self.assertEqual(lines["r7:a"]["level"], level)
                self.assertIn(text, lines["r7:a"]["detail"])
        self.assertEqual(self.connect.posts[0], ("POST", "http://fake/connectors/a/restart?includeTasks=true&onlyFailed=true"))
        self.assertIn("--r7-clear a", lines["r7:a"]["detail"])

    def test_flapping_stays_within_the_same_three_tries(self):
        for minutes in range(0, 30):  # 1분 주기: 재시작 → RUNNING 한 회차 → 다시 FAILED 를 되풀이
            self.connect.state["a"] = "RUNNING" if minutes % 2 else "FAILED"
            self.m3(minutes)
        self.assertEqual(len(self.connect.posts), 3)
        self.connect.state["a"] = "FAILED"
        self.assertEqual(self.m3(31)["r7:a"]["level"], "alert")
        self.assertEqual(len(self.connect.posts), 3)

    def test_the_record_clears_only_when_not_failed_for_30_minutes_after_the_last_try(self):
        self.m3(0)
        self.connect.state["a"] = "RUNNING"
        lines = self.m3(29.9)  # 30분 직전까지는 둔다
        self.assertEqual((lines["r7:a"]["level"], self.state["r7"]["a"]), ("ok", [T0]))
        self.assertIn("FAILED 아님 — 지난 자동 재시작 1번", lines["r7:a"]["detail"])
        self.assertNotIn("r7:a", self.m3(30.1))  # 30분 지남 → 비움, 키 없음
        self.assertNotIn("a", self.state["r7"])
        self.connect.state["a"] = "FAILED"
        self.assertIn("1/3 시도", self.m3(31)["r7:a"]["detail"])  # 새 FAILED 는 다시 처음부터

    def test_a_failed_post_still_counts_as_a_try(self):
        http_error = urllib.error.HTTPError("u", 500, "x", {}, None)
        self.addCleanup(http_error.close)
        for error, text in ((http_error, "HTTP 500"), (urllib.error.URLError("down"), "URLError"), (http.client.BadStatusLine("x"), "BadStatusLine")):
            with self.subTest(text):
                self.state, self.connect.posts = {}, []
                self.connect.post_error = error
                lines = self.m3(0)
                self.assertEqual(len(self.state["r7"]["a"]), 1)
                self.assertIn(f"1/3 시도({text})", lines["r7:a"]["detail"])

    def test_no_post_unless_the_try_is_saved_first(self):
        with mock.patch.object(check, "save_state", side_effect=PermissionError("ro")):
            lines = self.m3(0)
        self.assertEqual(self.connect.posts, [])
        self.assertEqual(lines["r7:a"]["level"], "error")
        self.assertNotIn("a", self.state["r7"])  # 빈 목록도 남기지 않는다
        self.assertIn("저장하지 못해 재시작하지 않음(PermissionError)", lines["r7:a"]["detail"])
        saved = []
        with mock.patch.object(check, "save_state", side_effect=lambda s: saved.append(json.loads(json.dumps(s)))):
            self.m3(0)
        self.assertEqual((saved[0]["r7"]["a"], len(self.connect.posts)), ([T0], 1))  # 기록이 든 상태를 먼저 저장한 뒤 부른다

    def test_the_name_is_quoted_in_the_url(self):
        self.connect.state, self.connect.slot = {"x/y z": "FAILED"}, {"x/y z": "s1"}
        self.m3(0)
        self.assertEqual(self.connect.posts, [("POST", "http://fake/connectors/x%2Fy%20z/restart?includeTasks=true&onlyFailed=true")])

    def test_slot_problems_or_unknown_slots_never_restart(self):
        cases = {"슬롯 없음": ({"s1": None}, "s1", "error", "슬롯 s1 없음 — 자동 재시작 안 함, RB-1"),
                 "lost": ({"s1": "lost"}, "s1", "error", "슬롯 s1 lost — 자동 재시작 안 함, RB-1"),
                 "unreserved": ({"s1": "unreserved"}, "s1", "error", "RB-1"),
                 "M1 이 끝까지 돌지 않음": (None, "s1", "error", "슬롯 상태를 모름(이번 회차 M1 이 끝까지 돌지 않음)"),
                 "slot.name 없음": ({"s1": "reserved"}, None, "error", "slot.name 없음"),
                 "감시 목록 밖": ({"s1": "reserved"}, "other", "error", "감시 목록(MONITOR_SLOTS) 밖")}
        for label, (slots, slot, level, text) in cases.items():
            with self.subTest(label):
                self.state, self.connect.posts = {}, []
                self.connect.slot["a"] = slot
                lines = self.m3(0, slots_seen=slots)
                self.assertEqual(self.connect.posts, [])
                self.assertEqual(lines["r7:a"]["level"], level)
                self.assertIn(text, lines["r7:a"]["detail"])

    def test_the_connector_config_is_never_output_or_stored(self):
        lines = self.m3(0)
        self.assertNotIn("secret-probe", json.dumps(lines, ensure_ascii=False))
        self.assertNotIn("secret-probe", self.state_path.read_text())

    def test_r7_off_only_alerts(self):
        lines = self.m3(0, r7=False)
        self.assertEqual((lines["connector:a"]["level"], "r7:a" in lines, self.connect.posts, self.state), ("alert", False, [], {}))


class R7Main(unittest.TestCase):
    """main() 수준: 설정, 끝까지 돈 M1 만, M3 실패 때 기록 유지, 웹훅 실패 회차에도 기록, --r7-clear."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.connect = Connect()
        self.env = {"MONITOR_PG_NODES": NODES, "MONITOR_S7_POWER": "0", "MONITOR_R7": "1"}
        for target, name, value in ((check, "rest", self.connect.get), (check.urllib.request, "urlopen", self.connect.urlopen),
                                    (check, "STATE", Path(tmp.name) / "pipeline_monitor" / "state.json"), (check, "CONNECT", "http://fake"),
                                    (check, "SLOTS", ["s1", "s2"]), (check, "pg", cluster()), (check, "m4", lambda: []), (check, "m5", lambda: []),
                                    (check, "notify", lambda text: "웹훅 없음(화면에만)")):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, env=None):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            check.main(self.env if env is None else env)
        return caught.exception.code, json.loads(out.getvalue())

    def state(self):
        return json.loads(check.STATE.read_text())

    def test_settings(self):
        for value, error in (("true", "bad_config"), ("", "bad_config")):
            with self.subTest(value=value):
                rc, out = self.run_main({**self.env, "MONITOR_R7": value})
                self.assertEqual((rc, out["error"]), (2, error))
        self.assertIn("MONITOR_R7", check.REQUIRED)
        full = {name: "v" for name in check.REQUIRED} | {"MONITOR_REQUIRE_ALL": "1", "MONITOR_R7": ""}
        rc, out = self.run_main(full)
        self.assertEqual((rc, out["error"], out["detail"]), (2, "missing_config", "없거나 빈 운영 변수 MONITOR_R7"))
        for env in ({**self.env, "MONITOR_R7": "0"}, {k: v for k, v in self.env.items() if k != "MONITOR_R7"}):  # 0 과 없음(기본 끔)
            with self.subTest(r7=env.get("MONITOR_R7")):
                rc, out = self.run_main(env)
                self.assertEqual(self.connect.posts, [])
                self.assertNotIn("r7:a", {f["key"] for f in out["findings"]})
                self.assertEqual({f["key"]: f["level"] for f in out["findings"]}["connector:a"], "alert")

    def test_restart_uses_slots_only_from_a_completed_m1(self):
        rc, out = self.run_main()
        self.assertEqual(len(self.connect.posts), 1)  # s1 은 cluster() 에서 reserved
        state = self.state()
        self.assertEqual(len(state["r7"]["a"]), 1)
        failing = cluster()

        def half(host, port):  # 둘째 슬롯 쿼리에서 예외 — 첫 슬롯은 이미 slots_seen 에 적힌 뒤
            conn = failing(host, port)
            original = conn.execute

            def execute(sql, params=None):
                if params == ("s2",):
                    raise RuntimeError("둘째 슬롯에서 끊김")
                return original(sql, params)
            conn.execute = execute
            return conn
        self.connect.posts.clear()
        with mock.patch.object(check, "pg", half):
            rc, out = self.run_main()
        lines = {f["key"]: f for f in out["findings"]}
        self.assertEqual(lines["M1·M2:run"]["level"], "error")
        self.assertEqual((self.connect.posts, lines["r7:a"]["level"]), ([], "error"))
        self.assertIn("이번 회차 M1 이 끝까지 돌지 않음", lines["r7:a"]["detail"])

    def test_m3_failure_keeps_the_record_and_last_level(self):
        self.run_main()
        before = self.state()
        with mock.patch.object(check, "rest", mock.Mock(side_effect=urllib.error.URLError("connect down"))):
            rc, out = self.run_main()
        after = self.state()
        self.assertEqual((after["r7"], after["levels"]["r7:a"]), (before["r7"], before["levels"]["r7:a"]))
        self.assertFalse([c for c in out["changes"] if c.startswith("[해소]")])

    def test_the_record_is_saved_even_when_the_webhook_fails(self):
        with mock.patch.object(check, "notify", lambda text: "웹훅 실패 URLError"):
            self.run_main()
        self.assertEqual(len(self.state()["r7"]["a"]), 1)
        self.assertNotIn("levels", self.state())  # 못 보낸 변화(levels)는 미루고, 시도 기록은 남긴다

    def test_save_failures_never_post(self):
        real, calls = check.save_state, []

        def first_fails(state):  # 회차 중간 저장만 실패, 끝의 저장은 됨
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError("ro")
            real(state)
        with mock.patch.object(check, "save_state", first_fails):
            rc, out = self.run_main()
        line = {f["key"]: f for f in out["findings"]}["r7:a"]
        self.assertEqual((self.connect.posts, line["level"]), ([], "error"))
        self.assertNotIn("a", self.state()["r7"])  # 시도 0번 — 빈 목록도 남기지 않는다
        clear = io.StringIO()
        with redirect_stdout(clear), self.assertRaises(SystemExit) as caught:
            check.r7_clear("a")
        self.assertEqual((caught.exception.code, json.loads(clear.getvalue())["found"]), (1, False))  # 지울 기록이 없다고 답한다
        self.assertIn("저장하지 못해 재시작하지 않음(PermissionError)", line["detail"])
        out = io.StringIO()
        with mock.patch.object(check, "save_state", side_effect=PermissionError("ro")), redirect_stdout(out), self.assertRaises(PermissionError):
            check.main(self.env)  # 늘 실패: 끝의 저장도 실패해 JSON 없이 트레이스백(runbook ④ "점검 자체 실패")
        self.assertEqual((self.connect.posts, out.getvalue()), ([], ""))

    def test_r7_clear_removes_only_that_record_and_reports_the_state_file(self):
        check.STATE.parent.mkdir(mode=0o700)
        check.STATE.write_text(json.dumps({"r7": {"a": [T0, T0, T0], "b": [T0]}, "levels": {"r7:a": "alert"}}))
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            check.r7_clear("a")
        result = json.loads(out.getvalue())
        self.assertEqual((caught.exception.code, result["found"], result["state"]), (0, True, str(check.STATE)))
        self.assertEqual(self.state()["r7"], {"b": [T0]})
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            check.r7_clear("a")  # 이미 없음(또는 엉뚱한 MONITOR_STATE) — rc 1 로 드러낸다
        self.assertEqual((caught.exception.code, json.loads(out.getvalue())["found"]), (1, False))


if __name__ == "__main__":
    unittest.main()
