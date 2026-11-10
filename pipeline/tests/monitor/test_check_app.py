"""감시 M9(앱 → DB 끝에서 끝)·M10(.204 LAN 닿음) — MON-4 계획 46 §2.5·§5.

가짜 A·B 는 이 장치의 실제 소켓이다(표준 라이브러리 http.server·socket): 정해 둔 상태·몸, 받고 답하지 않음, 머리만 보내고 끊음, 닫힌 포트.
가짜 A 는 http:// 다 — https 로 듣기만 하는 소켓에 붙으면 TLS 핸드셰이크가 연결 단계에서 멈춰 다른 분류가 된다(리뷰 N1, 운영은 nginx 가 TLS 를 끝냄).
실행: 저장소 뿌리에서 `python -m unittest discover pipeline/tests`.
"""
import io
import json
import socket
import ssl
import tempfile
import threading
import unittest
import urllib.error
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from pipeline.monitor import check
from pipeline.tests.monitor.test_check_nodes import NODES, cluster

T0 = 1_800_000_000.0


class Reply(BaseHTTPRequestHandler):
    """경로마다 (상태, 몸). 받은 요청 머리를 남긴다."""

    routes, seen = {}, []

    def do_GET(self):
        Reply.seen.append(dict(self.headers))
        status, body = Reply.routes.get(self.path, (404, b"no"))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(test):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Reply)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    Reply.routes, Reply.seen = {}, []
    return f"http://127.0.0.1:{server.server_port}"


def raw_server(test, answer):
    """연결마다 요청을 읽고 `answer`(바이트, None 이면 아무것도 보내지 않고 닫음)를 보낸 뒤 닫는다."""
    listener = socket.create_server(("127.0.0.1", 0))
    test.addCleanup(listener.close)

    def loop():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                conn.recv(65536)
                if answer is not None:
                    conn.sendall(answer)
    threading.Thread(target=loop, daemon=True).start()
    return f"http://127.0.0.1:{listener.getsockname()[1]}"


def silent_server(test):
    """연결은 받지만(커널 대기열) 읽지도 답하지도 않는다 — 감시 쪽에서는 응답 단계의 시간 초과."""
    listener = socket.create_server(("127.0.0.1", 0))
    test.addCleanup(listener.close)
    return f"http://127.0.0.1:{listener.getsockname()[1]}"


def closed_port():
    with socket.create_server(("127.0.0.1", 0)) as s:
        return f"http://127.0.0.1:{s.getsockname()[1]}"


OK = (200, b'{"status": "ok"}')
DOWN = (503, b'{"status": "unavailable"}')


class Settings(unittest.TestCase):
    def test_none_defaults_urls_and_bad_values(self):
        extra = check.extra_settings({})
        self.assertEqual((extra.app_url, extra.lan), (None, None))
        extra = check.extra_settings({"MONITOR_APP_URL": " https://203.0.113.5/health/db ", "MONITOR_LAN": "ads|http://192.0.2.4:9105/, x|http://h/"})
        self.assertEqual(extra.app_url, "https://203.0.113.5/health/db")
        self.assertEqual(extra.lan, [("ads", "http://192.0.2.4:9105/"), ("x", "http://h/")])
        for env in ({"MONITOR_APP_URL": ""}, {"MONITOR_APP_URL": "ftp://h/"}, {"MONITOR_APP_URL": "https:///x"}, {"MONITOR_APP_URL": "http://h:99999/"},
                    {"MONITOR_APP_URL": "http://[::1/"}, {"MONITOR_APP_URL": "http://h/a b"}, {"MONITOR_LAN": ""}, {"MONITOR_LAN": "ads|tcp://h:1"},
                    {"MONITOR_LAN": "ads|http://h/,ads|http://g/"}, {"MONITOR_LAN": "ads"}, {"MONITOR_LAN": "a b|http://h/"}):
            with self.subTest(env), self.assertRaises(check.ConfigError) as caught:
                check.extra_settings(env)
            self.assertNotIn("h/", str(caught.exception).replace("http:// 또는 https:// 주소", ""))  # 값은 넣지 않는다

    def test_both_names_are_required_in_required_mode(self):
        full = {name: "v" for name in check.REQUIRED} | {"MONITOR_REQUIRE_ALL": "1"}
        self.assertEqual(check.missing_config(full), [])
        self.assertEqual(check.missing_config({k: v for k, v in full.items() if k not in ("MONITOR_APP_URL", "MONITOR_LAN")}),
                         ["MONITOR_APP_URL", "MONITOR_LAN"])

    def test_none_is_one_unsupported_line_each_and_nothing_is_called(self):
        state = {}
        with mock.patch.object(check.urllib.request, "urlopen", side_effect=AssertionError("부르지 않아야 한다")):
            lines = check.m9_m10(state, None, None)
        self.assertEqual([(f["check"], f["key"], f["level"]) for f in lines], [("M10", "lan", "unsupported"), ("M9", "app_db", "unsupported")])
        self.assertEqual(state["streak"], {})


class Classification(unittest.TestCase):
    """A 한 번의 분류(앞머리) — 끊긴 곳을 가른다(계획 46 §2.5 표, 리뷰 C1·K2·K3)."""

    def setUp(self):
        self.base = serve(self)

    def problem(self, url):
        with mock.patch.object(check, "APP_TIMEOUT_S", 0.5):
            return check.app_problem(url)

    def test_http_answers(self):
        cases = {"/ok": (OK, None), "/down": (DOWN, ("HTTP 503 — 백엔드까지 닿음", True)),
                 "/other503": ((503, b"<html>busy</html>"), ("HTTP 503(몸이 다름) — 경로·앱 응답 이상", False)),
                 "/502": ((502, b"<html>bad gateway</html>"), ("HTTP 502 — EC2 nginx → 프론트, 또는 프론트 → wg-ad → 백엔드 실패", False)),
                 "/403": ((403, b"<html>forbidden</html>"), ("HTTP 403 — EC2 nginx allow 줄과", False)),
                 "/404": ((404, b"{}"), ("HTTP 404 — 경로·앱 응답 이상", False)),
                 "/login": ((200, b"<html>login</html>"), ("HTTP 200(몸이 다름) — 경로·앱 응답 이상", False))}
        Reply.routes = {path: reply for path, (reply, _) in cases.items()}
        for path, (_, want) in cases.items():
            with self.subTest(path):
                got = self.problem(self.base + path)
                self.assertEqual(got if want is None else (got[0][:len(want[0])], got[1]), want)

    def test_no_answer_after_connecting_differs_from_not_connecting(self):
        silent = self.problem(silent_server(self))
        self.assertTrue(silent[0].startswith("EC2 nginx 까지 닿음 — 0.5초 안에 응답 없음·응답 끊김(TimeoutError)"), silent)
        refused = self.problem(closed_port())
        self.assertTrue(refused[0].startswith("EC2 에 닿지 못함(ConnectionRefusedError)"), refused)

    def test_a_broken_response_is_a_failure_not_a_crash(self):  # 리뷰 K2 — HTTPException(BadStatusLine 등)은 OSError 가 아니다
        garbage = self.problem(raw_server(self, b"garbage\r\n\r\n"))
        self.assertTrue(garbage[0].startswith("EC2 nginx 까지 닿음 — 0.5초 안에 응답 없음·응답 끊김(BadStatusLine)"), garbage)
        cut = self.problem(raw_server(self, b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{\"st"))  # read(amt) 는 모자란 몸을 예외 없이 돌려준다
        self.assertEqual(cut, ("HTTP 200(몸이 다름) — 경로·앱 응답 이상(배포 판·nginx location 확인)", False))
        closed = self.problem(raw_server(self, None))
        self.assertTrue(closed[0].startswith("EC2 nginx 까지 닿음 — 0.5초 안에 응답 없음·응답 끊김(RemoteDisconnected)"), closed)

    def test_a_certificate_failure_is_tls(self):
        error = urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate has expired"))
        with mock.patch.object(check.urllib.request, "urlopen", side_effect=error):
            self.assertEqual(check.app_problem("https://203.0.113.5/health/db"), ("TLS 실패(SSLCertVerificationError) — EC2 인증서(갱신 확인)", False))

    def test_the_request_carries_the_user_agent_and_no_cookie(self):
        Reply.routes = {"/ok": OK}
        self.assertIsNone(self.problem(self.base + "/ok"))
        self.assertEqual(Reply.seen[0]["User-Agent"], "pipeline-monitor/1")
        self.assertNotIn("Cookie", Reply.seen[0])

    def test_lan_counts_any_http_answer_as_reached(self):
        Reply.routes = {"/": (404, b"x")}
        with mock.patch.object(check, "LAN_TIMEOUT_S", 0.5):
            self.assertIsNone(check.lan_problem(self.base + "/"))  # 상태 무관
            self.assertEqual(check.lan_problem(closed_port()), "ConnectionRefusedError")
            self.assertEqual(check.lan_problem(raw_server(self, None)), "RemoteDisconnected")  # portproxy 가 받고 WSL 쪽이 없음
            self.assertEqual(check.lan_problem(silent_server(self)), "TimeoutError")


class Rounds(unittest.TestCase):
    """m9_m10 회차 — 연속 수·watch·조합 문구. 전달된 수준(levels)은 main 이 웹훅 뒤에 쓰는 것을 흉내 낸다."""

    def setUp(self):
        self.base = serve(self)
        Reply.routes = {"/a": OK, "/b": OK}
        self.app, self.lan = self.base + "/a", [("ads", self.base + "/b")]
        self.state = {}

    def round(self, deliver=True):
        lines = {f["key"]: f for f in check.m9_m10(self.state, self.app, self.lan)}
        if deliver:
            self.state["levels"] = {k: f["level"] for k, f in lines.items()}
        return lines

    def test_two_failures_alert_one_is_watch_and_success_resets(self):
        self.assertEqual(self.round()["app_db"]["level"], "ok")
        Reply.routes["/a"] = DOWN
        first = self.round()["app_db"]
        self.assertEqual(first["level"], "watch")
        self.assertTrue(first["detail"].startswith("1회 실패 — 다음 회차도 실패하면 경보: HTTP 503"), first["detail"])
        second = self.round()["app_db"]
        self.assertEqual((second["level"], second["detail"][:10]), ("alert", "연속 2회 실패: "))
        self.assertEqual(self.state["streak"], {"app_db": 2, "lan:ads": 0})
        Reply.routes["/a"] = OK
        self.assertEqual(self.round()["app_db"]["level"], "ok")
        Reply.routes["/a"] = DOWN
        self.assertEqual(self.round()["app_db"]["level"], "watch")  # 연속 수는 성공에서 0 으로

    def test_an_undelivered_resolution_keeps_the_alert(self):  # 리뷰 C2
        Reply.routes["/a"] = DOWN
        self.round(), self.round()
        self.assertEqual(self.state["levels"]["app_db"], "alert")
        Reply.routes["/a"] = OK
        self.assertEqual(self.round(deliver=False)["app_db"]["level"], "ok")  # 웹훅 실패 — levels 는 alert 그대로
        Reply.routes["/a"] = DOWN
        held = self.round()["app_db"]
        self.assertEqual((held["level"], self.state["streak"]["app_db"]), ("alert", 1))  # watch 로 덮지 않는다

    def test_combinations(self):
        Reply.routes["/a"] = DOWN
        self.assertIn(" — B 정상: DB 노드는 M1·M2 를 볼 것", self.round()["app_db"]["detail"])
        Reply.routes["/a"] = (502, b"x")
        self.assertIn(" — B 정상: 외부 길·EC2·앱 쪽", self.round()["app_db"]["detail"])
        Reply.routes["/a"] = DOWN
        self.lan = [("ads", closed_port())]
        lines = self.round()
        self.assertIn(" — B 도 실패: .204 LAN 끊김", lines["app_db"]["detail"])
        self.assertIn("portproxy ", lines["lan:ads"]["detail"])
        self.assertNotIn("A 정상", lines["lan:ads"]["detail"])
        Reply.routes["/a"] = OK
        self.assertIn(" — A 정상: 앱 길은 살아 있음", self.round()["lan:ads"]["detail"])
        self.lan, Reply.routes["/a"] = None, DOWN
        self.assertIn(" — B 없음", self.round()["app_db"]["detail"])
        self.assertEqual(set(self.state["streak"]), {"app_db"})  # 사라진 B 의 수는 남기지 않는다

    def test_lan_uses_the_same_rule(self):
        self.lan = [("ads", closed_port())]
        self.assertEqual(self.round()["lan:ads"]["level"], "watch")
        self.assertEqual(self.round()["lan:ads"]["level"], "alert")


class MainRounds(unittest.TestCase):
    """main() 끝에서 끝: 웹훅 본문·rc·해소, 보내지 못한 해소(C2), 점검 안의 코드 결함은 M9·M10:run(지난 값 유지)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = serve(self)
        Reply.routes = {"/a": DOWN, "/b": OK}
        self.env = {"MONITOR_PG_NODES": NODES, "MONITOR_S7_POWER": "0", "MONITOR_APP_URL": self.base + "/a", "MONITOR_LAN": f"ads|{self.base}/b"}
        self.sent, self.result = [], "웹훅 200"
        for name, value in (("STATE", Path(tmp.name) / "pipeline_monitor" / "state.json"), ("SLOTS", ["s1", "s2"]), ("pg", cluster()),
                            ("m1_m2", lambda *args: []), ("s7", lambda *args: []),  # rc 가 M9·M10 만 따르게(가짜 클러스터의 슬롯·시계 줄을 뺌)
                            ("m3", lambda *args: []), ("m3_reach", lambda: []), ("m4", lambda: []), ("m5", lambda: []), ("d1", lambda *args: []),
                            ("d2", lambda *args: []), ("notify", self.notify)):
            patcher = mock.patch.object(check, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def notify(self, text):
        self.sent.append(text)
        return self.result

    def run_main(self, at):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            check.main(self.env, clock=lambda: at)
        return caught.exception.code, json.loads(out.getvalue())

    def m9(self, out):
        return [c for c in out["changes"] if "app_db" in c]

    def test_alert_on_the_second_round_then_resolution(self):
        rc, out = self.run_main(T0)
        self.assertEqual((rc, self.m9(out), {f["key"]: f["level"] for f in out["findings"]}["app_db"]), (0, [], "watch"))  # watch 는 rc 에 들지 않는다
        rc, out = self.run_main(T0 + 60)
        self.assertEqual(rc, 1)
        (line,) = self.m9(out)
        self.assertTrue(line.startswith("[경보] M9 app_db: 연속 2회 실패: HTTP 503 — 백엔드까지 닿음"), line)
        self.assertIn("[경보] M9 app_db", self.sent[-1])
        self.assertEqual(json.loads(check.STATE.read_text())["streak"], {"app_db": 2, "lan:ads": 0})
        Reply.routes["/a"] = OK
        rc, out = self.run_main(T0 + 120)
        self.assertEqual((rc, self.m9(out)), (0, ["[해소] M9 app_db: 200 — 앱 → DB 닿음"]))

    def test_a_resolution_lost_to_the_webhook_is_sent_later(self):  # 리뷰 C2 — alert → 성공(웹훅 실패) → 실패 → 성공
        self.run_main(T0), self.run_main(T0 + 60)
        Reply.routes["/a"] = OK
        self.result = "웹훅 실패 URLError"
        _, out = self.run_main(T0 + 120)
        self.assertEqual(json.loads(check.STATE.read_text())["levels"]["app_db"], "alert")
        self.result = "웹훅 200"
        Reply.routes["/a"] = DOWN
        _, out = self.run_main(T0 + 180)
        self.assertEqual(({f["key"]: f["level"] for f in out["findings"]}["app_db"], self.m9(out)), ("alert", []))
        Reply.routes["/a"] = OK
        _, out = self.run_main(T0 + 240)
        self.assertEqual(self.m9(out), ["[해소] M9 app_db: 200 — 앱 → DB 닿음"])

    def test_a_code_fault_inside_the_check_is_run_error_and_keeps_levels(self):
        self.run_main(T0), self.run_main(T0 + 60)
        with mock.patch.object(check, "app_problem", side_effect=KeyError("결함")):
            _, out = self.run_main(T0 + 120)
        levels = {f["key"]: f["level"] for f in out["findings"]}
        self.assertEqual(levels["M9·M10:run"], "error")
        self.assertNotIn("[해소] app_db", " ".join(out["changes"]))
        self.assertEqual(json.loads(check.STATE.read_text())["levels"]["app_db"], "alert")  # 보지 못한 것은 해소가 아니다


if __name__ == "__main__":
    unittest.main()
