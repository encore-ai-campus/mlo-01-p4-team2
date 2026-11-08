"""감시 check.py 의 노드 목록(MON-1): 설정 읽기, primary 고르기, 조회 순간의 시계 차이, primary 를 모를 때 main() 이 지난 값을 그대로 두는지.

DB 없이 돈다 — psycopg 연결을 SQL 문으로 답하는 가짜로 바꾼다. 실행: 저장소 뿌리에서 `python -m unittest discover pipeline/tests`.
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import psycopg

from pipeline.monitor import check

T0 = datetime(2020, 1, 1, tzinfo=UTC)  # 실제 시각과 아주 먼 값 — S7 이 그때의 datetime.now() 로 다시 견주면 바로 드러난다
A, B, C = ("10.0.0.1", 5432), ("10.0.0.2", 5432), ("10.0.0.3", 5432)
NODES = "10.0.0.1:5432,10.0.0.2:5432,10.0.0.3:5432"


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    """노드 하나. recovery·논리 슬롯·now() 와, primary 쪽 M1·M2 쿼리(슬롯 행·복제·목록·catalog_xmin)에 답한다."""

    def __init__(self, recovery, db_now=T0, slot_rows=None):
        self.recovery, self.db_now, self.slot_rows = recovery, db_now, slot_rows or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.recovery and "pg_current_wal_lsn" in sql:  # 대기 서버에서는 실제로도 실패한다(lab 확인 — 55000) — M1 을 primary 가 아닌 노드에서 보면 드러난다
            raise psycopg.errors.ObjectNotInPrerequisiteState("recovery is in progress")
        if sql.startswith("select now(), pg_is_in_recovery()"):
            return Result([(self.db_now, self.recovery)])
        if "synced, temporary" in sql:
            return Result([("s1", True, False), ("s2", True, False)])
        if "max_slot_wal_keep_size" in sql:
            return Result([(10 * 1024 ** 3,)])
        if "where slot_name = %s" in sql:
            row = self.slot_rows.get(params[0])
            return Result([row] if row else [])
        if "from pg_stat_replication" in sql:
            return Result([("node2", "streaming", "quorum", 0), ("node3", "streaming", "quorum", 0)])
        if "synchronized_standby_slots" in sql:
            return Result([("node2,node3",)])
        if "s.slot_type = 'physical' and s.active" in sql:
            return Result([("node2", "streaming"), ("node3", "streaming")])
        if "pg_snapshot_xmax" in sql:
            return Result([("100",)])
        if "catalog_xmin::text" in sql:
            return Result([])
        raise AssertionError(f"모르는 쿼리: {sql[:60]}")


SLOT_OK = (True, None, None, "reserved", True, 1024)  # active, inactive_since, idle_s, wal_status, failover, held


def cluster(primary=A, unreachable=(), error=psycopg.OperationalError):
    """노드 → 가짜 연결. primary=None 이면 셋 모두 대기 서버. unreachable 노드는 `error` 를 낸다."""
    def connect(host, port):
        node = (host, port)
        if node in unreachable:
            raise error("닿지 못함")
        return FakeConnection(recovery=node != primary, slot_rows={"s1": SLOT_OK})  # s2 는 없음 → M1 alert
    return connect


class Ticks:
    """가짜 시계: 부를 때마다 1초씩."""

    def __init__(self, start):
        self.now = start

    def __call__(self):
        value, self.now = self.now, self.now + timedelta(seconds=1)
        return value


class NodeListTests(unittest.TestCase):
    def test_default_is_the_local_test_bed(self):
        self.assertEqual(check.pg_nodes({}), [("127.0.0.1", 5432), ("127.0.0.1", 5433), ("127.0.0.1", 5434)])
        self.assertEqual(check.pg_nodes({"MONITOR_PG_HOST": "h", "MONITOR_PG_NODE_PORTS": "1, 2"}), [("h", 1), ("h", 2)])

    def test_explicit_list_wins_and_old_entry_settings_are_ignored(self):
        env = {"MONITOR_PG_NODES": " 10.0.0.1:5432 ,10.0.0.2:5432", "MONITOR_PG_HOST": "x", "MONITOR_PG_NODE_PORTS": "abc", "MONITOR_PG_PORT": "5010"}
        self.assertEqual(check.pg_nodes(env), [("10.0.0.1", 5432), ("10.0.0.2", 5432)])

    def test_bad_lists_fail_without_values(self):
        bad = {"빈 값": "", "공백": "  ", "콜론 없음": "10.0.0.1", "호스트 빔": ":5432", "포트 문자": "10.0.0.1:pg", "포트 0": "10.0.0.1:0",
               "포트 65536": "10.0.0.1:65536", "중복": "10.0.0.1:5432,10.0.0.1:5432", "IPv6 대괄호": "[::1]:5432", "빈 항목": "10.0.0.1:5432,"}
        for label, value in bad.items():
            with self.subTest(label), self.assertRaises(check.ConfigError) as caught:
                check.pg_nodes({"MONITOR_PG_NODES": value})
            self.assertIn("MONITOR_PG_NODES", str(caught.exception))
            if value.strip():
                self.assertNotIn(value.split(",")[0].strip(), str(caught.exception))  # 값은 메시지에 넣지 않는다
        for ports in ("5432,abc", "", " , "):
            with self.subTest(ports=ports), self.assertRaises(check.ConfigError):
                check.pg_nodes({"MONITOR_PG_NODE_PORTS": ports})

    def test_flags_take_only_zero_or_one(self):
        self.assertTrue(check.flag({}, "MONITOR_S7_POWER", "1"))
        self.assertFalse(check.flag({"MONITOR_S7_POWER": " 0 "}, "MONITOR_S7_POWER", "1"))
        for name in ("MONITOR_REQUIRE_ALL", "MONITOR_S7_POWER"):
            for value in ("true", "", "yes", "off", "2"):
                with self.subTest(name=name, value=value), self.assertRaises(check.ConfigError):
                    check.flag({name: value}, name, "0")

    def test_require_all_names_missing_or_blank_variables_only(self):
        full = {name: "v" for name in check.REQUIRED}
        self.assertEqual(check.missing_config({}), [])
        self.assertEqual(check.missing_config({**full, "MONITOR_REQUIRE_ALL": "1"}), [])
        env = {**full, "MONITOR_REQUIRE_ALL": "1", "MONITOR_KAFKA_BIN": "   "}
        del env["MONITOR_PG_NODES"]
        self.assertEqual(check.missing_config(env), ["MONITOR_PG_NODES", "MONITOR_KAFKA_BIN"])


class PrimaryTests(unittest.TestCase):
    def test_exactly_one_primary_is_chosen(self):
        polled = [{"node": A, "recovery": False}, {"node": B, "recovery": True}, {"node": C, "error": "OperationalError"}]
        self.assertEqual(check.primary_of(polled)["node"], A)

    def test_zero_or_two_primaries_or_a_failed_survey_raise_with_addresses_only(self):
        zero = [{"node": A, "recovery": True}, {"node": B, "error": "OperationalError"}, {"node": C, "error": "FileNotFoundError"}]
        with self.assertRaises(RuntimeError) as caught:
            check.primary_of(zero)
        self.assertEqual(str(caught.exception), "primary 0개 [], 닿은 노드 1/3, 닿지 못함 ['10.0.0.2:5432(OperationalError)', '10.0.0.3:5432(FileNotFoundError)']")
        two = [{"node": A, "recovery": False}, {"node": B, "recovery": False}, {"node": C, "recovery": True}]
        with self.assertRaises(RuntimeError) as caught:
            check.primary_of(two)
        self.assertEqual(str(caught.exception), "primary 2개 ['10.0.0.1:5432', '10.0.0.2:5432'] — 갈라짐, 닿은 노드 3/3")
        with self.assertRaises(RuntimeError):
            check.primary_of("노드 조회 실패 RuntimeError")


class SurveyTests(unittest.TestCase):
    def test_clock_skew_is_measured_at_the_query_and_not_later(self):
        clock = Ticks(T0 - timedelta(seconds=3))  # 쿼리 앞뒤 = T0-3, T0-2 → 가운데 T0-2.5, DB now = T0 → 차이 2.5초
        with mock.patch.object(check, "pg", cluster()):
            polled = check.survey([A, B], clock=clock)
        self.assertEqual(polled[0]["skew"], 2.5)
        clock.now += timedelta(seconds=60)  # 조회 뒤 시간이 지나도(뒤 점검의 Kafka 명령 등) S7 은 조회 때 값을 쓴다
        clock_line = check.s7(polled, power=False)[0]
        self.assertEqual((clock_line["key"], clock_line["level"]), ("clock_skew", "alert"))
        self.assertIn("2.500초", clock_line["detail"])

    def test_unreachable_nodes_keep_only_the_error_name(self):
        with mock.patch.object(check, "pg", cluster(unreachable=(B,))):
            polled = check.survey([A, B, C], clock=Ticks(T0))
        self.assertEqual(polled[1], {"node": B, "error": "OperationalError"})
        with mock.patch.object(check, "pg", cluster(unreachable=(A, B, C), error=FileNotFoundError)):  # 비밀번호 파일을 못 읽음 — 모든 노드
            self.assertEqual({n["error"] for n in check.survey([A, B, C], clock=Ticks(T0))}, {"FileNotFoundError"})

    def test_s7_without_a_primary_marks_only_the_clock_and_keeps_power(self):
        lines = check.s7([{"node": A, "recovery": True}], power=False)
        self.assertEqual([(f["key"], f["level"]) for f in lines], [("clock_skew", "error"), ("power", "unsupported")])
        self.assertIn("점검 장치 자신의 전원은 보지 않음", lines[1]["detail"])


class MainTests(unittest.TestCase):
    """회차를 이어 돌려 primary 를 모르는 회차가 지난 M1·M2 값을 해소로 알리지 않는지(Planner 조건)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # 0700
        self.addCleanup(tmp.cleanup)
        self.state_dir = Path(tmp.name) / "pipeline_monitor"  # 아직 없는 폴더 — 설정 오류에서는 만들지 않아야 한다
        self.env = {"MONITOR_PG_NODES": NODES, "MONITOR_S7_POWER": "0"}
        self.sent = []
        for name, value in (("STATE", self.state_dir / "state.json"), ("SLOTS", ["s1", "s2"]), ("m3", lambda *args: []), ("m4", lambda: []), ("m5", lambda: []),
                            ("notify", lambda text: self.sent.append(text) or "웹훅 없음(화면에만)")):
            patcher = mock.patch.object(check, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, env=None):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            check.main(self.env if env is None else env)
        return caught.exception.code, json.loads(out.getvalue())

    def state(self):
        return json.loads(check.STATE.read_text())

    def test_rounds_without_a_primary_keep_the_last_values(self):
        with mock.patch.object(check, "pg", cluster(primary=A)):
            rc, first = self.run_main()
        levels = self.state()["levels"]
        self.assertEqual((rc, levels["slot:s1"], levels["slot:s2"], levels["synced:10.0.0.2:5432:s1"]), (1, "ok", "alert", "ok"))
        m12 = {k: v for k, v in levels.items() if self.state()["checks"][k] in ("M1", "M2")}
        rounds = {"primary 0": mock.patch.object(check, "pg", cluster(primary=None)),
                  "조회 자체 예외": mock.patch.object(check, "survey", mock.Mock(side_effect=RuntimeError("뜻밖"))),
                  "비밀번호 파일 없음": mock.patch.object(check, "pg", cluster(unreachable=(A, B, C), error=FileNotFoundError))}
        for label, patcher in rounds.items():
            with self.subTest(label), patcher:
                rc, out = self.run_main()
                self.assertEqual(rc, 1)
                self.assertFalse([c for c in out["changes"] if c.startswith("[해소]")], out["changes"])
                self.assertEqual({k: v for k, v in self.state()["levels"].items() if k in m12}, m12)
                lines = {f["key"]: f for f in out["findings"]}
                self.assertEqual(lines["M1·M2:run"]["level"], "error")
                self.assertEqual((lines["clock_skew"]["level"], lines["power"]["level"]), ("error", "unsupported"))

    def test_m1_is_read_on_the_found_primary_even_when_it_is_not_first(self):
        with mock.patch.object(check, "pg", cluster(primary=B)):  # 목록 순서 A, B, C — primary 는 둘째
            rc, out = self.run_main()
        lines = {f["key"]: f for f in out["findings"]}
        self.assertNotIn("M1·M2:run", lines)
        self.assertEqual((rc, lines["slot:s1"]["level"], lines["slot:s2"]["level"]), (1, "ok", "alert"))
        self.assertEqual((lines["synced:10.0.0.1:5432:s1"]["level"], lines["synced:10.0.0.2:5432:s1"]["detail"]), ("ok", "이 노드는 지금 primary(대기 아님)"))

    def test_first_bad_drops_only_keys_outside_the_configured_nodes(self):
        self.state_dir.mkdir(mode=0o700)
        check.STATE.write_text(json.dumps({"first_bad": {"synced:5433:s1": 1.0, "synced:10.0.0.3:5432:s1": 2.0}}))
        with mock.patch.object(check, "pg", cluster(primary=A, unreachable=(C,))):
            _, out = self.run_main()
        self.assertEqual(self.state()["first_bad"], {"synced:10.0.0.3:5432:s1": 2.0})  # 옛 키 모양은 지우고, 잠깐 닿지 않은 노드의 유예는 남긴다
        lines = {f["key"]: f for f in out["findings"]}
        for name in ("s1", "s2"):  # 못 본 노드는 해소(ok)가 아니라 경보다
            self.assertEqual((lines[f"synced:10.0.0.3:5432:{name}"]["level"], lines[f"synced:10.0.0.3:5432:{name}"]["detail"]), ("alert", "노드 접속 실패 OperationalError"))

    def test_config_errors_refuse_to_check_and_notify(self):
        full = {name: "v" for name in check.REQUIRED} | {"MONITOR_PG_NODES": NODES, "MONITOR_S7_POWER": "0"}
        cases = {"빠짐": ({**full, "MONITOR_REQUIRE_ALL": "1", "MONITOR_KAFKA": ""}, "missing_config"),
                 "필수 모드 값 true": ({**full, "MONITOR_REQUIRE_ALL": "true"}, "bad_config"),
                 "전원 값 yes": ({**full, "MONITOR_S7_POWER": "yes"}, "bad_config"),
                 "노드 목록 빈 값": ({"MONITOR_PG_NODES": ""}, "bad_config")}
        for label, (env, error) in cases.items():
            with self.subTest(label):
                self.sent.clear()
                rc, out = self.run_main(env)
                self.assertEqual((rc, out["error"]), (2, error))
                self.assertFalse(self.state_dir.exists())  # 상태 자리(폴더)를 만들기 전에 멈춘다 — 확인 순서 필수 모드 → 설정 형식 → 상태 자리
                self.assertEqual(len(self.sent), 1)
        self.assertEqual(out["detail"], "MONITOR_PG_NODES 가 비었다")
        rc, out = self.run_main({**full, "MONITOR_REQUIRE_ALL": "1", "MONITOR_KAFKA": "", "S3_ENDPOINT": " "})
        self.assertEqual(out["detail"], "없거나 빈 운영 변수 MONITOR_KAFKA, S3_ENDPOINT")  # 이름만


if __name__ == "__main__":
    unittest.main()
