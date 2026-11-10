"""감시 check.py 의 P2-4b: D1 디스크·D2 outbox 크기·S7 공백·코드 판(code_pin)·rc 2 알림 반복(결정 F (나))·주기 실행 견본(plist·래퍼).

DB·Kafka 없이 돈다 — 노드 연결·디스크 크기·웹훅을 가짜로 바꾼다(코드 판 시험은 임시 git 저장소, 래퍼 시험은 bash 를 쓴다).
실행: 저장소 뿌리에서 `python -m unittest discover pipeline/tests`.
"""
import io
import json
import os
import plistlib
import stat
import subprocess
import tempfile
import unittest
from collections import namedtuple
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import psycopg

from pipeline.monitor import check
from pipeline.tests.monitor.test_check_nodes import A, B, NODES, cluster

MONITOR = Path(check.__file__).resolve().parent
T0 = 1_800_000_000.0
Usage = namedtuple("Usage", "total used free")


class Settings(unittest.TestCase):
    def test_defaults_and_none(self):
        extra = check.extra_settings({})
        self.assertEqual([(name, du) for name, _path, du in extra.disk], [("pg", False), ("reconcile_ads", True)])
        self.assertTrue(os.path.isabs(extra.disk[1][1]))  # ~ 를 홈으로 편다
        self.assertEqual([name for name, _db, _table in extra.outbox], ["ads_lab", "ads_e2e", "metaverse_lab"])
        self.assertEqual((extra.min_free, extra.max_bytes, extra.gap_s, extra.commit), (20.0, 5 * 1024 ** 3, 0, None))
        self.assertIsNone(check.extra_settings({"MONITOR_DISK": " none "}).disk)
        self.assertEqual(check.extra_settings({"MONITOR_CODE_COMMIT": "a" * 40}).commit, "a" * 40)

    def test_bad_values_are_config_errors_without_paths(self):
        bad = {"MONITOR_DISK": ["", "pg", "pg|/a|du|x", "pg|/a|size", "pg|rel/a", "pg|/a,pg|/b", "p g|/a", "pg|/a,"],
               "MONITOR_OUTBOX": ["", "a|db", "a|db|events_outbox", "a|db|Public.T", "a|db|public.t,a|db|public.u"],
               "MONITOR_DISK_MIN_FREE_PCT": ["101", "-1", "nan", "x"], "MONITOR_OUTBOX_MAX_BYTES": ["0", "1.5", "x"], "MONITOR_GAP_S": ["-1", "x"],
               "MONITOR_CODE_COMMIT": ["", "__COMMIT__", "A" * 40, "a" * 39]}
        for name, values in bad.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(check.ConfigError) as caught:
                    check.extra_settings({name: value})
                self.assertIn(name, str(caught.exception))
                self.assertNotIn("/a", str(caught.exception))  # 경로 값은 메시지에 넣지 않는다

    def test_required_mode_needs_disk_and_outbox_but_takes_none(self):
        full = {name: "v" for name in check.REQUIRED} | {"MONITOR_REQUIRE_ALL": "1"}
        self.assertEqual(check.missing_config({**full, "MONITOR_DISK": "none"}), [])
        self.assertEqual(check.missing_config({**full, "MONITOR_DISK": " ", "MONITOR_OUTBOX": ""}), ["MONITOR_DISK", "MONITOR_OUTBOX"])


class Disk(unittest.TestCase):
    def test_free_below_the_threshold_alerts(self):
        with mock.patch.object(check.shutil, "disk_usage", return_value=Usage(1000, 850, 150)):
            low, = check.d1([("pg", "/x", False)], 20)
            ok, = check.d1([("pg", "/x", False)], 15)
        with mock.patch.object(check.shutil, "disk_usage", return_value=Usage(1000, 800, 200)):
            edge, = check.d1([("pg", "/x", False)], 20)
        self.assertEqual((low["check"], low["key"], low["level"], ok["level"], edge["level"]), ("D1", "disk:pg", "alert", "ok", "ok"))
        self.assertIn("여유 15.0%", low["detail"])
        self.assertIn("< 20%", low["detail"])
        self.assertNotIn("<", ok["detail"] + edge["detail"])

    def test_missing_path_alerts_and_none_is_one_unsupported_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            line, = check.d1([("gone", str(Path(tmp) / "no"), True)], 20)
        self.assertEqual((line["key"], line["level"]), ("disk:gone", "alert"))
        self.assertIn("볼 수 없다(FileNotFoundError)", line["detail"])
        with mock.patch.object(check.shutil, "disk_usage") as usage:
            lines = check.d1(None, 20)
        usage.assert_not_called()
        self.assertEqual([(f["key"], f["level"]) for f in lines], [("disk", "unsupported")])

    def test_du_counts_regular_files_and_not_links(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            (root / "a").write_bytes(b"abc")
            (root / "sub").mkdir()
            (root / "sub" / "b").write_bytes(b"12345")
            (root / "link_file").symlink_to(root / "a")
            (Path(outside) / "big").write_bytes(b"x" * 100)
            (root / "link_dir").symlink_to(outside, target_is_directory=True)
            self.assertEqual(check.folder_bytes(tmp), 8)
            with_du, = check.d1([("w", tmp, True)], 0)
            without, = check.d1([("w", tmp, False)], 0)
        self.assertTrue(with_du["detail"].endswith(", 폴더 8B"))
        self.assertNotIn("폴더", without["detail"])


class Outbox(unittest.TestCase):
    POLLED = [{"node": A, "recovery": True}, {"node": B, "recovery": False}]  # primary 는 둘째
    ITEMS = [("big", "d1", "public.t"), ("small", "d2", "public.t"), ("gone", "d3", "public.no_such")]

    def connect(self, answers, fail=()):
        calls = []

        def pg(host, port, dbname="postgres"):
            if dbname in fail:
                raise psycopg.OperationalError("닿지 못함")
            conn = mock.MagicMock()
            conn.__enter__.return_value = conn
            conn.execute.side_effect = lambda sql, params: calls.append((host, port, dbname, sql, params)) or mock.Mock(fetchone=lambda: answers[dbname])
            return conn
        return pg, calls

    def test_sizes_are_read_on_the_primary(self):
        pg, calls = self.connect({"d1": (101, 7), "d2": (100, -1), "d3": (None, None)})
        with mock.patch.object(check, "pg", pg):
            lines = {f["key"]: f for f in check.d2(self.ITEMS, 100, self.POLLED)}
        self.assertEqual([(c[0], c[1], c[2]) for c in calls], [(*B, "d1"), (*B, "d2"), (*B, "d3")])
        self.assertTrue(all("to_regclass" in c[3] and c[4] == (c[4][0], c[4][0]) for c in calls))
        self.assertEqual({k: v["level"] for k, v in lines.items()}, {"outbox:big": "alert", "outbox:small": "ok", "outbox:gone": "alert"})
        self.assertIn("101B", lines["outbox:big"]["detail"])
        self.assertIn("> 100B — P1-6 정리를 할 때", lines["outbox:big"]["detail"])
        self.assertIn("추정 행 -1", lines["outbox:small"]["detail"])  # ANALYZE 전 값 그대로(리뷰 C6)
        self.assertIn("d3.public.no_such 표가 없다", lines["outbox:gone"]["detail"])

    def test_unknown_primary_or_unreachable_db_is_an_error_on_the_same_key(self):
        pg, calls = self.connect({"d1": (1, 1)}, fail=("d2",))
        with mock.patch.object(check, "pg", pg):
            nolead = check.d2(self.ITEMS[:2], 100, [{"node": A, "recovery": True}])
            down = {f["key"]: f for f in check.d2(self.ITEMS[:2], 100, self.POLLED)}
        self.assertEqual([(f["key"], f["level"]) for f in nolead], [("outbox:big", "error"), ("outbox:small", "error")])
        self.assertEqual(len(calls), 1)  # primary 를 모르면 연결하지 않는다
        self.assertEqual((down["outbox:big"]["level"], down["outbox:small"]["level"]), ("ok", "error"))
        self.assertEqual(down["outbox:small"]["detail"], "d2 에 닿지 못함(OperationalError)")


class Gap(unittest.TestCase):
    def test_lines(self):
        state = {}
        self.assertEqual(check.s7_gap(state, 0, T0), [])
        self.assertEqual(state, {"last_run": T0})  # 꺼져 있어도 시각은 적는다(켰을 때 옛 값으로 울리지 않게)
        self.assertEqual(check.s7_gap({}, 300, T0)[0]["detail"], "첫 회차")
        self.assertEqual(check.s7_gap({"last_run": T0}, 300, T0 + 300)[0]["level"], "ok")
        state = {"last_run": T0}
        line = check.s7_gap(state, 300, T0 + 301)[0]
        self.assertEqual((line["check"], line["key"], line["level"]), ("S7", "gap", "alert"))
        self.assertIn("감시 공백 301초", line["detail"])
        self.assertIn("점검을 거부(rc 2)", line["detail"])  # 리뷰 R4
        self.assertEqual(check.s7_gap(state, 300, T0 + 361)[0], line)  # main 이 닫기 전에는 같은 내용으로 이어진다
        again = check.s7_gap(state, 300, T0 + 1000)[0]["detail"]
        self.assertTrue(again.startswith("감시 공백 301초("), again)  # 시작~끝은 첫 공백 그대로(사이 60초 감시를 공백으로 세지 않음 — 리뷰 R-a)
        self.assertIn("그 뒤 공백 1번 더(가장 긴 639초)", again)
        self.assertEqual(check.s7_gap(state, 0, T0 + 1100), [])
        self.assertNotIn("gap_open", state)
        broken = {"last_run": T0, "gap_open": {"start": "x"}}
        self.assertEqual(check.s7_gap(broken, 300, T0 + 60)[0]["level"], "ok")


class CodePin(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        def git(*args):
            return subprocess.run(["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                                  check=True, capture_output=True, text=True).stdout.strip()
        git("init", "-q")
        (self.root / ".gitignore").write_text("*.pyc\n")
        git("add", ".gitignore")
        git("commit", "-q", "-m", "x")
        self.head = git("rev-parse", "HEAD")

    def test_no_commit_does_not_call_git(self):
        with mock.patch.object(check.subprocess, "run") as run:
            self.assertIsNone(check.code_pin_problem(None, self.root))
        run.assert_not_called()

    def test_clean_head_passes_and_ignored_files_do_not_count(self):
        (self.root / "x.pyc").write_bytes(b"")
        self.assertIsNone(check.code_pin_problem(self.head, self.root))

    def test_git_that_cannot_run_refuses(self):
        with mock.patch.object(check.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 30)):
            self.assertIn("git 을 부르지 못함(TimeoutExpired)", check.code_pin_problem(self.head, self.root))

    def test_other_commit_dirty_tree_or_no_repo_refuse(self):
        self.assertIn(f"HEAD {self.head} 가 MONITOR_CODE_COMMIT {'0' * 40} 과 다르다", check.code_pin_problem("0" * 40, self.root))
        (self.root / "new.py").write_text("")
        self.assertIn("미커밋·추적 안 된 파일 1개", check.code_pin_problem(self.head, self.root))
        with tempfile.TemporaryDirectory() as plain:
            self.assertIn("git 이 실패했다", check.code_pin_problem(self.head, Path(plain)))


class MainRounds(unittest.TestCase):
    """main() 회차: 공백 경보가 웹훅 실패를 넘어 살아남는지(C1), rc 2 알림 반복(결정 F (나)·C4), 재개 줄(C5), code_pin 거부."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # 0700
        self.addCleanup(tmp.cleanup)
        self.state_dir = Path(tmp.name) / "pipeline_monitor"
        self.env = {"MONITOR_PG_NODES": NODES, "MONITOR_S7_POWER": "0"}
        self.bad = {**self.env, "MONITOR_R7": "yes"}  # bad_config
        self.sent, self.result = [], "웹훅 200"
        self.reach = []
        for name, value in (("STATE", self.state_dir / "periodic.json"), ("SLOTS", ["s1", "s2"]), ("pg", cluster()), ("m3", lambda *args: []),
                            ("m3_reach", lambda: self.reach),
                            ("m4", lambda: []), ("m5", lambda: []), ("d1", lambda *args: []), ("d2", lambda *args: []), ("notify", self.notify)):
            patcher = mock.patch.object(check, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.mark = check.STATE.with_name(check.STATE.name + ".refused")

    def notify(self, text):
        self.sent.append(text)
        return self.result

    def run_main(self, env, at):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            check.main(env, clock=lambda: at)
        return caught.exception.code, json.loads(out.getvalue())

    def state(self):
        return json.loads(check.STATE.read_text())

    def test_gap_alert_survives_a_failed_webhook(self):
        env = {**self.env, "MONITOR_GAP_S": "300"}
        self.run_main(env, T0)
        self.result = "웹훅 실패 URLError"  # 깨어난 첫 회차 — Wi-Fi 가 아직 없음
        _, out = self.run_main(env, T0 + 600)
        self.assertEqual({f["key"]: f["level"] for f in out["findings"]}["gap"], "alert")
        self.assertIn("gap_open", self.state())
        self.result = "웹훅 200"
        _, out = self.run_main(env, T0 + 660)  # 공백은 60초뿐이지만 열린 공백을 다시 알린다
        self.assertTrue([c for c in out["changes"] if c.startswith("[경보] S7 gap: 감시 공백 600초")], out["changes"])
        self.assertNotIn("gap_open", self.state())
        _, out = self.run_main(env, T0 + 720)
        self.assertTrue([c for c in out["changes"] if c.startswith("[해소] S7 gap: 지난 점검 뒤 60초")], out["changes"])

    def test_a_silent_connect_worker_alerts_then_resolves(self):  # Planner 2026-11-08 (나) — main 이 m3_reach 를 부르고, m3 가 실패해도 그 줄이 남는다
        key = "connect_rest:http://10.0.0.2:8088"
        self.reach = [check.finding("M3", key, "alert", "GET / 응답 없음")]
        with mock.patch.object(check, "m3", mock.Mock(side_effect=TimeoutError("모든 주소"))):
            _, out = self.run_main(self.env, T0)
        self.assertIn(f"[경보] M3 {key}: GET / 응답 없음", out["changes"])
        self.assertEqual(self.state()["levels"][key], "alert")
        self.reach = [check.finding("M3", key, "ok", "GET / 응답")]
        _, out = self.run_main(self.env, T0 + 60)
        self.assertIn(f"[해소] M3 {key}: GET / 응답", out["changes"])

    def test_refusal_is_sent_once_then_hourly(self):
        self.state_dir.mkdir(mode=0o700)
        rc, out = self.run_main(self.bad, T0)
        self.assertEqual((rc, out["error"], len(self.sent)), (2, "bad_config", 1))
        self.assertEqual(stat.S_IMODE(self.mark.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.mark.read_text()), {"error": "bad_config", "detail": out["detail"], "at": T0})
        rc, out = self.run_main(self.bad, T0 + 3599)
        self.assertEqual((rc, len(self.sent)), (2, 1))
        self.assertTrue(out["notify"].startswith("생략(같은 거부가 이어짐"))
        self.run_main(self.bad, T0 + 3600)
        self.assertEqual(len(self.sent), 2)
        self.run_main({**self.env, "MONITOR_S7_POWER": "yes"}, T0 + 3601)  # 다른 거부는 바로
        self.assertEqual(len(self.sent), 3)

    def test_failed_webhook_writes_no_mark(self):
        self.state_dir.mkdir(mode=0o700)
        self.result = "웹훅 실패 URLError"
        self.run_main(self.bad, T0)
        self.assertFalse(self.mark.exists())
        self.run_main(self.bad, T0 + 60)
        self.assertEqual(len(self.sent), 2)

    def test_a_mark_that_cannot_be_written_still_refuses(self):  # 쓸 수 없는 폴더(0500): rc 2·JSON 그대로, 다음 회차도 알림
        self.state_dir.mkdir(mode=0o700)
        self.state_dir.chmod(0o500)
        self.addCleanup(self.state_dir.chmod, 0o700)
        for at in (T0, T0 + 60):
            rc, out = self.run_main(self.bad, at)
            self.assertEqual((rc, out["error"]), (2, "bad_config"))
        self.assertEqual(len(self.sent), 2)
        self.assertFalse(self.mark.exists())

    def test_untrusted_marks_mean_every_run(self):
        recent = json.dumps({"error": "bad_config", "detail": "MONITOR_R7 는 0 또는 1 이어야 한다", "at": T0})
        cases = {"폴더 없음": None, "폴더 0777": "dir777", "표시 파일 링크": "link", "표시 파일 그룹 쓰기": "0620", "깨짐": "broken", "미래 시각": "future"}
        for label, kind in cases.items():
            with self.subTest(label):
                if self.state_dir.exists():
                    for path in self.state_dir.iterdir():
                        path.unlink()
                    self.state_dir.rmdir()
                self.sent.clear()
                if kind is not None:
                    self.state_dir.mkdir(mode=0o700)
                if kind == "dir777":
                    self.state_dir.chmod(0o777)
                elif kind == "link":
                    target = self.state_dir / "elsewhere"
                    target.write_text(recent)
                    self.mark.symlink_to(target)
                elif kind == "0620":
                    self.mark.write_text(recent)
                    self.mark.chmod(0o620)
                elif kind == "broken":
                    self.mark.write_text("{")
                elif kind == "future":
                    self.mark.write_text(json.dumps({**json.loads(recent), "at": T0 + 10}))
                self.run_main(self.bad, T0 + 1)
                self.run_main(self.bad, T0 + 2)
                self.assertEqual(len(self.sent), 2 if kind in (None, "dir777", "link", "0620") else 1)  # 깨짐·미래는 없음으로 보고 새로 씀
                if kind is None:
                    self.assertFalse(self.state_dir.exists())  # 설정 오류에서는 폴더를 만들지 않는다(MON-1)
                if kind == "dir777":
                    self.assertFalse(self.mark.exists())

    def test_unsafe_state_ignores_the_mark(self):  # 리뷰 C4: 폴더는 안전하고 상태 파일만 그룹 쓰기
        self.state_dir.mkdir(mode=0o700)
        check.STATE.write_text("{}")
        check.STATE.chmod(0o620)
        rc, out = self.run_main(self.env, T0)
        self.assertEqual((rc, out["error"]), (2, "unsafe_state"))
        self.assertFalse(self.mark.exists())
        self.mark.write_text(json.dumps({"error": "unsafe_state", "detail": out["detail"], "at": T0}))
        self.run_main(self.env, T0 + 60)
        self.assertEqual(len(self.sent), 2)

    def test_code_pin_refusal(self):
        with mock.patch.object(check, "code_pin_problem", return_value="코드 사본 /r 의 HEAD x 가 MONITOR_CODE_COMMIT y 과 다르다") as pin:
            rc, out = self.run_main({**self.env, "MONITOR_CODE_COMMIT": "a" * 40}, T0)
        pin.assert_called_once_with("a" * 40)
        self.assertEqual((rc, out["error"], len(self.sent)), (2, "code_pin", 1))
        self.assertIn("코드 사본이 고정한 커밋과 달라", self.sent[0])
        self.assertFalse(self.state_dir.exists())  # 상태 자리보다 먼저 거부한다(폴더를 만들지 않음)

    def test_resume_line_after_a_refusal(self):
        self.state_dir.mkdir(mode=0o700)
        self.run_main(self.bad, T0)
        self.result = "웹훅 실패 URLError"  # 리뷰 C5: 재개 줄을 못 보냈으면 표시 파일을 남겨 다음 회차가 다시 보낸다
        _, out = self.run_main(self.env, T0 + 120)
        self.assertTrue(out["changes"][0].startswith("[해소] 점검을 다시 한다 — 앞선 거부 bad_config(마지막 알림 "), out["changes"])
        self.assertTrue(self.mark.exists())
        self.result = "웹훅 200"
        _, out = self.run_main(self.env, T0 + 180)
        self.assertTrue(out["changes"][0].startswith("[해소] 점검을 다시 한다"))
        self.assertFalse(self.mark.exists())
        _, out = self.run_main(self.env, T0 + 240)
        self.assertFalse([c for c in out["changes"] if "점검을 다시 한다" in c])


class Sample(unittest.TestCase):
    def test_plist_has_every_setting_and_the_values_parse(self):
        data = plistlib.loads((MONITOR / "local.pipeline.monitor.plist").read_bytes())
        env = data["EnvironmentVariables"]
        need = set(check.REQUIRED) | {"MONITOR_REQUIRE_ALL", "MONITOR_CODE_COMMIT", "MONITOR_GAP_S", "MONITOR_LOG"}  # 리뷰 R7
        self.assertEqual(need - set(env), set())
        self.assertEqual((env["MONITOR_REQUIRE_ALL"], env["MONITOR_R7"], env["MONITOR_GAP_S"], data["StartInterval"]), ("1", "1", "300", 60))
        self.assertIs(data["RunAtLoad"], True)
        self.assertEqual(data["ProgramArguments"], ["__REPO__/pipeline/monitor/run-monitor.sh", "__PYTHON__"])
        self.assertNotIn("StandardOutPath", data)  # 로그는 래퍼가 연다(리뷰 R3)
        self.assertNotIn("MONITOR_WEBHOOK_URL", env)
        self.assertNotIn("MONITOR_WEBHOOK_FILE", env)  # 로컬은 웹훅 없음(사용자 결정 2026-11-08) — 키가 있고 파일이 없으면 회차마다 "웹훅 실패"
        filled = {k: v.replace("__HOME__", "/Users/x").replace("__COMMIT__", "0" * 40) for k, v in env.items()}
        self.assertEqual(check.missing_config(filled), [])
        check.pg_nodes(filled)
        check.flag(filled, "MONITOR_R7", "0")
        self.assertEqual(check.extra_settings(filled).gap_s, 300)


class Wrapper(unittest.TestCase):
    def run_wrapper(self, env):
        return subprocess.run(["bash", str(MONITOR / "run-monitor.sh"), "/bin/echo"], env=env, capture_output=True, text=True, timeout=30)

    def test_appends_then_moves_a_big_log_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "monitor.log"
            env = {**os.environ, "MONITOR_LOG": str(log)}
            self.assertEqual([self.run_wrapper(env).returncode for _ in range(2)], [0, 0])
            line = f"-I {MONITOR}/check.py\n"
            self.assertEqual(log.read_text(), line * 2)  # python -I 로 그 폴더의 check.py(리뷰 R2)
            with open(log, "ab") as f:
                f.truncate(10 * 1024 * 1024 + 1)
            self.run_wrapper(env)
            self.assertEqual(((Path(tmp) / "monitor.log.1").stat().st_size, log.read_text()), (10 * 1024 * 1024 + 1, line))
            self.assertNotEqual(self.run_wrapper({k: v for k, v in env.items() if k != "MONITOR_LOG"}).returncode, 0)

    def test_stderr_reaches_the_log(self):  # 리뷰 T1: plist 에 StandardErrorPath 가 없어 트레이스백이 남는 길은 래퍼의 2>&1 하나다
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "python"
            fake.write_text("#!/bin/sh\necho boom >&2\nexit 1\n")
            fake.chmod(0o700)
            log = Path(tmp) / "monitor.log"
            run = subprocess.run(["bash", str(MONITOR / "run-monitor.sh"), str(fake)], env={**os.environ, "MONITOR_LOG": str(log)},
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual((run.returncode, run.stderr, log.read_text()), (1, "", "boom\n"))

    def test_no_caffeinate(self):  # 리뷰 R1: 감시가 잠듦 방지를 걸면 S7 sleep 이 늘 ok
        body = [ln for ln in (MONITOR / "run-monitor.sh").read_text().splitlines() if not ln.startswith("#")]
        self.assertFalse([ln for ln in body if "caffeinate" in ln])


if __name__ == "__main__":
    unittest.main()
