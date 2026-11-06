"""e2e_setup 전체 다시 만들기(계획 E2E-1 2판)의 시험 — Connect·Kafka·PG·S3·Patroni 없이 가짜 세계로 돈다.

가짜 세계는 이 도구가 부르는 읽기·쓰기 함수(rest·kafka·nodes·db_state·db_connections·drop_slot·drop_db·database·s3_client·patroni_slots·processes)를
바꿔 끼우고, 바꾸는 호출을 차례대로 calls 에 적는다. connect-offsets 처럼 커넥터를 지워도 offset 은 남는다.
"""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import e2e_setup as e  # noqa: E402

OLD_OFFSET = [{"partition": {"server": "adse2e"}, "offset": {"lsn": 10706262824}}]


def http_error(code):
    error = urllib.error.HTTPError("u", code, "x", {}, None)
    error.close()
    return error


class World:
    def __init__(self):
        cfg = e.connector_config()
        self.connectors = {e.NAME: {"state": "RUNNING", "config": dict(cfg, **{"database.password": "secret-probe"})},
                           "ads-outbox-cdc": {"state": "RUNNING", "config": {"database.dbname": "ads_pipeline_lab", "slot.name": "ads_outbox_slot"}},
                           "metaverse-outbox-cdc": {"state": "RUNNING", "config": {"database.dbname": "metaverse_lab", "slot.name": "metaverse_outbox_slot"}}}
        self.offsets = {e.NAME: list(OLD_OFFSET)}  # connect-offsets: 커넥터를 지워도 남는다
        self.groups = {e.GROUP, "ads-outbox-cdc-signal", "metaverse-outbox-cdc-signal"}
        self.topics = set(e.TOPICS) | {"ads.outbox.events", "metaverse.outbox.events", "connect-offsets"}
        keep = {"ads_outbox_slot": {"type": "logical", "database": "ads_pipeline_lab", "active": True, "failover": True, "synced": True},
                "metaverse_outbox_slot": {"type": "logical", "database": "metaverse_lab", "active": True, "failover": True, "synced": False}}
        self.primary_slots = dict(keep, **{e.SLOT: {"type": "logical", "database": e.DB, "active": True, "failover": True, "synced": True}})
        self.standby_slots = {p: dict(keep, **{e.SLOT: {"type": "logical", "database": e.DB, "active": False, "failover": True, "synced": True}})
                              for p in (5433, 5434)}
        self.dbs = {e.DB, "ads_pipeline_lab", "metaverse_lab", "appdb", "polaris", "postgres", "template0", "template1"}
        self.extra_conns = []  # Debezium 말고 e2e DB 연결
        self.s3 = {"bronze_e2e/ads_outbox_events/part-0.parquet": 10, "bronze_e2e/ads_outbox_events/_delta_log/0.json": 2,
                   "checkpoints/bronze_e2e_ads_outbox/commits/0": 1, "bronze/ads_outbox_events/part-0.parquet": 5,
                   "bronze/metaverse/outbox_events/part-0.parquet": 5, "checkpoints/bronze_ads_outbox/commits/0": 1}
        self.s3_errors, self.s3_extra_listed = False, []
        self.dcs = {"from": "http://127.0.0.1:8008", "slots": [], "ignore_slots": ["ads_e2e_slot", "ads_outbox_slot", "metaverse_outbox_slot"]}
        self.procs = []
        self.slotsync_stuck = self.offsets_stuck = False
        self.topic_delete_lag, self.deleting = 0, {}  # Kafka 토픽 지움은 비동기 — 몇 번의 --list 동안 남아 보임
        self.fail = None  # (도구, 인자 일부) 가 오면 예외
        self.calls = []
        self.resumed_with = []

    # Connect REST
    def rest(self, method, path, body=None):
        base = path.split("?")[0]
        if (method, path) == ("GET", "/connectors?expand=status&expand=info"):
            return {n: {"status": {"connector": {"state": c["state"]}, "tasks": [] if c["state"] == "STOPPED" else [{"id": 0, "state": c["state"]}]},
                        "info": {"config": c["config"]}} for n, c in self.connectors.items()}
        if (method, path) == ("POST", "/connectors"):
            self.calls.append(("create", body["name"], body.get("initial_state")))
            state = body.get("initial_state") or "RUNNING"
            self.connectors[body["name"]] = {"state": state, "config": body["config"]}
            if state == "RUNNING":
                self._start(body["name"])
            return {"name": body["name"]}
        name, _, rest_ = base[len("/connectors/"):].partition("/")
        here = self.connectors.get(name)
        if here is None and not (method == "PUT" and rest_ == "config"):
            raise http_error(404)
        if (method, rest_) == ("GET", "status"):
            return {"connector": {"state": here["state"]}, "tasks": [] if here["state"] == "STOPPED" else [{"id": 0, "state": here["state"]}]}
        if (method, rest_) == ("GET", "offsets"):
            return {"offsets": list(self.offsets.get(name, []))}
        if (method, rest_) == ("GET", "config"):
            return here["config"]
        if (method, rest_) == ("PUT", "stop"):
            self.calls.append(("stop", name))
            here["state"] = "STOPPED"
            self.primary_slots.get(e.SLOT, {})["active"] = False
            return None
        if (method, rest_) == ("DELETE", "offsets"):
            if here["state"] != "STOPPED":
                raise http_error(400)
            self.calls.append(("delete_offsets", name))
            if not self.offsets_stuck:
                self.offsets[name] = []
            return {"message": "The offsets for this connector have been reset successfully"}
        if (method, rest_) == ("DELETE", ""):
            self.calls.append(("delete_connector", name))
            del self.connectors[name]
            return None
        if (method, rest_) == ("PUT", "resume"):
            self.calls.append(("resume", name))
            here["state"] = "RUNNING"
            self._start(name)
            return None
        if (method, rest_) == ("PUT", "config"):
            self.calls.append(("put_config", name))
            self.connectors[name] = {"state": "RUNNING", "config": body}
            self._start(name)
            return None
        raise AssertionError((method, path))

    def _start(self, name):
        self.resumed_with.append(list(self.offsets.get(name, [])))
        self.offsets[name] = self.offsets.get(name) or [{"partition": {"server": "adse2e"}, "offset": {"lsn": 1}}]
        self.primary_slots[e.SLOT] = {"type": "logical", "database": e.DB, "active": True, "failover": True, "synced": False}
        for slots in self.standby_slots.values():
            slots[e.SLOT] = {"type": "logical", "database": e.DB, "active": False, "failover": True, "synced": True}

    # Kafka
    def kafka(self, tool, *args):
        if self.fail and self.fail[0] == tool and self.fail[1] in args:
            raise e.subprocess.CalledProcessError(1, tool)
        if tool == "kafka-consumer-groups.sh" and args == ("--list",):
            return "\n".join(sorted(self.groups))
        if tool == "kafka-consumer-groups.sh" and args[0] == "--delete":
            self.calls.append(("delete_group", args[2]))
            self.groups.discard(args[2])
            return ""
        if tool == "kafka-topics.sh" and args == ("--list",):
            for name in list(self.deleting):
                self.deleting[name] -= 1
                if self.deleting[name] < 0:
                    del self.deleting[name]
                    self.topics.discard(name)
            return "\n".join(sorted(self.topics))
        if tool == "kafka-topics.sh" and args[0] == "--delete":
            self.calls.append(("delete_topic", args[2]))
            self.deleting[args[2]] = self.topic_delete_lag
            return ""
        if tool == "kafka-topics.sh" and args[0] == "--create":
            self.calls.append(("create_topic", args[2]))
            self.topics.add(args[2])
            return ""
        if tool == "kafka-topics.sh" and args == ("--describe", "--under-min-isr-partitions"):
            return ""
        if tool == "kafka-get-offsets.sh":
            return f"{args[1]}:0:5 {args[1]}:1:6"
        raise AssertionError((tool, args))

    # PG
    def nodes(self):
        return [{"port": 5432, "recovery": False, "slots": {k: dict(v) for k, v in self.primary_slots.items()}, "streaming": 2}] + \
               [{"port": p, "recovery": True, "slots": {k: dict(v) for k, v in s.items()}, "streaming": None} for p, s in self.standby_slots.items()]

    def conns(self):
        debezium = [["ads_cdc", "Debezium Streaming", "walsender"]] if self.connectors.get(e.NAME, {}).get("state") == "RUNNING" else []
        return (debezium + self.extra_conns) if e.DB in self.dbs else []

    def db_state(self):
        return {"exists": e.DB in self.dbs, "bytes": 2_418_000_000 if e.DB in self.dbs else 0, "conns": self.conns(), "names": sorted(self.dbs)}

    def db_connections(self):
        return len(self.conns())

    def drop_slot(self):
        slot = self.primary_slots.get(e.SLOT)
        if slot and not slot["active"] and slot["database"] == e.DB:
            self.calls.append(("drop_slot",))
            del self.primary_slots[e.SLOT]
            if not self.slotsync_stuck:
                for slots in self.standby_slots.values():
                    slots.pop(e.SLOT, None)

    def drop_db(self):
        self.calls.append(("drop_db",))
        self.dbs.discard(e.DB)

    def database(self):
        self.calls.append(("create_db",))
        self.dbs.add(e.DB)

    # S3
    def s3_client(self):
        world = self

        class S3:
            def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
                assert Bucket == e.BUCKET
                keys = sorted(k for k in world.s3 if k.startswith(Prefix)) + world.s3_extra_listed
                return {"Contents": [{"Key": k, "Size": world.s3.get(k, 1)} for k in keys], "IsTruncated": False}

            def delete_objects(self, Bucket, Delete):
                keys = [o["Key"] for o in Delete["Objects"]]
                world.calls.append(("s3_delete", tuple(keys)))
                if world.s3_errors:
                    return {"Errors": [{"Key": keys[0], "Code": "AccessDenied"}]}
                for k in keys:
                    world.s3.pop(k, None)
                return {}
        return S3()

    def patch(self, test):
        for name in ("rest", "kafka", "nodes", "db_state", "db_connections", "drop_slot", "drop_db", "database", "s3_client"):
            p = mock.patch.object(e, name, getattr(self, name))
            p.start()
            test.addCleanup(p.stop)
        for name, value in (("patroni_slots", lambda: self.dcs), ("processes", lambda: list(self.procs)), ("brokers_up", lambda: (3, 3))):
            p = mock.patch.object(e, name, value)
            p.start()
            test.addCleanup(p.stop)
        p = mock.patch.object(e.time, "sleep", lambda s: None)
        p.start()
        test.addCleanup(p.stop)


class Base(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.w.patch(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lab = Path(tmp.name) / e.DB
        lab.mkdir()
        for name, value in (("LAB", lab), ("CTX", lab / "ctx.json")):
            p = mock.patch.object(e, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = e.main(list(argv))
        return rc, out.getvalue()

    def checks(self, execute=False):
        return {n: (r, t) for n, r, t in e.judge(e.survey(), execute)}


class DryRun(Base):
    def test_dry_run_changes_nothing_and_shows_every_target(self):
        e.CTX.write_text("{}")
        rc, out = self.run_main("--rebuild")
        self.assertEqual((rc, self.w.calls), (0, []))
        for line in ("상태: 처음부터", "D1 커넥터 offset", "D2 커넥터", "D3 소비 그룹", "D4 슬롯", "D5 DB", "D6 토픽", "D7 S3", "D8 S3", "D9 ", "남기는 대상",
                     "4-1 통과", "4-2 통과", "4-3 통과", "4-4 통과", "4-5 통과", "4-6 통과", "4-7 통과", "4-8 통과", "4-10 통과", "판정: 확인 통과"):
            self.assertIn(line, out)
        self.assertTrue(e.CTX.exists())

    def test_output_has_no_secret_or_full_connector_config(self):
        rc, out = self.run_main("--rebuild")
        self.assertNotIn("secret-probe", out)
        self.assertNotIn("connector.class", out)  # 설정은 여섯 키만

    def test_a_running_bronze_is_a_todo_in_dry_run_and_a_refusal_in_execute(self):
        self.w.procs = [{"pid": 94892, "what": "bronze_ads_outbox.py", "match": ["ads.e2e", "bronze_e2e"]}]
        rc, out = self.run_main("--rebuild")
        self.assertEqual(rc, 0)
        self.assertIn("4-6 할 일", out)
        self.assertIn("실행 전 할 일", out)
        rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual((rc, self.w.calls), (2, []))
        self.assertIn("4-6 실패", out)

    def test_usage(self):
        for argv in (["--execute"], ["--rebuild", "--execute", "x"], ["--execute", "--rebuild"], ["x"]):
            with self.subTest(argv=argv), redirect_stdout(io.StringIO()), mock.patch.object(sys, "stderr", io.StringIO()):
                self.assertEqual(e.main(argv), 2)
        self.assertEqual(self.w.calls, [])


class Gates(Base):
    def assert_refused(self, number):
        checks = self.checks(execute=True)
        self.assertEqual(checks[number][0], "실패", checks[number])
        rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual((rc, self.w.calls), (2, []))
        return checks[number][1]

    def test_name_gate(self):
        for name, value in (("NAME", "ads-outbox-cdc"), ("DB", "ads_pipeline_lab"), ("SLOT", "ads_outbox_slot"), ("GROUP", "ads-outbox-cdc-signal"),
                            ("TOPICS", ("ads.outbox.events",)), ("S3_PREFIXES", ("bronze/ads_outbox_events/", "checkpoints/bronze_e2e_ads_outbox/"))):
            with self.subTest(name=name), mock.patch.object(e, name, value):
                self.assertTrue(e.name_problems(), name)
        for kind, names in (("connector", (e.NAME,)), ("s3", ("bronze_e2e/",))):  # e2e 이름이어도 남기는 대상과 겹치면 거부
            with self.subTest(keep=kind), mock.patch.dict(e.KEEP, {kind: names}):
                self.assertTrue(e.name_problems(), kind)
        self.assertEqual(e.name_problems(), [])

    def test_same_name_with_other_settings_is_refused(self):
        self.w.connectors[e.NAME]["config"]["database.dbname"] = "ads_pipeline_lab"
        self.assertIn("database.dbname", self.assert_refused("4-2"))

    def test_another_connector_using_e2e_is_refused(self):
        self.w.connectors["ads-outbox-cdc"]["config"]["slot.name"] = e.SLOT
        self.assert_refused("4-3")

    def test_slot_of_another_database_or_busy_without_connector_is_refused(self):
        self.w.primary_slots[e.SLOT]["database"] = "ads_pipeline_lab"
        self.assert_refused("4-4")
        self.w.primary_slots[e.SLOT]["database"] = e.DB
        del self.w.connectors[e.NAME]
        self.assertIn("누가 쓰는 중", self.assert_refused("4-4"))

    def test_foreign_connections_are_refused(self):
        self.w.extra_conns = [["postgres", "psql", "client backend"]]
        self.assert_refused("4-5")
        self.w.extra_conns = []
        self.w.connectors[e.NAME]["state"] = "STOPPED"
        self.w.extra_conns = [["ads_cdc", "Debezium Streaming", "walsender"]]  # 멈춘 커넥터인데 Debezium 연결이 남음
        self.assert_refused("4-5")

    def test_unhealthy_cluster_is_refused(self):
        cases = {"lab 커넥터 FAILED": lambda: self.w.connectors["ads-outbox-cdc"].update(state="FAILED"),
                 "lab 슬롯 쉼": lambda: self.w.primary_slots["ads_outbox_slot"].update(active=False),
                 "브로커": lambda: setattr(e, "brokers_up", lambda: (2, 3)),
                 "대기 하나가 streaming 아님": lambda: setattr(e, "nodes", lambda: [dict(n, streaming=1) if n["recovery"] is False else n for n in self.w.nodes()])}
        for label, broke in cases.items():
            with self.subTest(label):
                self.w = World()
                self.w.patch(self)
                broke()
                self.assert_refused("4-7")

    def test_two_primaries_are_refused(self):
        with mock.patch.object(e, "nodes", lambda: [dict(n, recovery=False) for n in self.w.nodes()]):
            self.assert_refused("4-7")

    def test_patroni_permanent_slot_or_unreadable_config_is_refused(self):
        self.w.dcs = dict(self.w.dcs, slots=[e.SLOT])
        self.assertIn("Patroni 가 다시 만듦", self.assert_refused("4-10"))
        self.w.dcs = None
        self.assertIn("읽지 못함", self.assert_refused("4-10"))

    def test_the_run_bronze_pid_is_named_in_the_todo(self):
        self.w.procs = [{"pid": 94892, "what": "bronze_ads_outbox.py", "match": ["ads.e2e"], "runner": 94848}]
        rc, out = self.run_main("--rebuild")
        self.assertIn("run-bronze.sh pid [94848] 에 SIGTERM", out)

    def test_survey_failure_is_rc2_without_changes(self):
        with mock.patch.object(e, "rest", mock.Mock(side_effect=urllib.error.URLError("down"))):
            rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual((rc, self.w.calls), (2, []))
        self.assertIn("점검 실패", out)


class Partial(Base):
    """§4.1: 일부만 지운 상태마다 넘어갈 확인과 늘 할 확인."""

    def test_each_partial_state(self):
        always = ("4-1", "4-3", "4-6", "4-7", "4-8", "4-10")
        cases = {"커넥터 없음": (lambda w: (w.connectors.pop(e.NAME), w.primary_slots[e.SLOT].update(active=False)), {"4-2"}),
                 "슬롯 없음": (lambda w: (w.primary_slots.pop(e.SLOT)), {"4-4"}),
                 "DB 없음": (lambda w: w.dbs.discard(e.DB), {"4-5"}),
                 "토픽·S3·그룹 없음": (lambda w: (w.topics.difference_update(e.TOPICS), w.groups.discard(e.GROUP),
                                            [w.s3.pop(k) for k in list(w.s3) if k.startswith(e.S3_PREFIXES)]), set())}
        for label, (cut, skipped) in cases.items():
            with self.subTest(label):
                self.w = World()
                self.w.patch(self)
                cut(self.w)
                checks = self.checks()
                self.assertEqual({n for n, (r, _t) in checks.items() if r == "넘어감"}, skipped)
                self.assertTrue(all(checks[n][0] == "통과" for n in always), checks)
                rc, out = self.run_main("--rebuild")
                self.assertEqual(rc, 0)
                self.assertIn("일부만 지운 상태", out)

    def test_nothing_left_is_reported_as_such(self):
        self.w.connectors.pop(e.NAME)
        self.w.offsets.clear()
        self.w.primary_slots.pop(e.SLOT)
        for s in self.w.standby_slots.values():
            s.pop(e.SLOT)
        self.w.dbs.discard(e.DB)
        self.w.topics.difference_update(e.TOPICS)
        self.w.groups.discard(e.GROUP)
        for k in [k for k in self.w.s3 if k.startswith(e.S3_PREFIXES)]:
            self.w.s3.pop(k)
        rc, out = self.run_main("--rebuild")
        self.assertIn("상태: 모두 없음", out)


class Execute(Base):
    EXPECTED = [("stop", e.NAME), ("delete_offsets", e.NAME), ("delete_connector", e.NAME), ("delete_group", e.GROUP), ("drop_slot",), ("drop_db",),
                ("delete_topic", e.TOPIC), ("delete_topic", e.SIGNAL), ("delete_topic", e.HEARTBEAT)]

    def test_full_rebuild_order_and_result(self):
        e.CTX.write_text("{}")
        rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.w.calls[:len(self.EXPECTED)], self.EXPECTED)
        rest = self.w.calls[len(self.EXPECTED):]
        self.assertEqual([c[0] for c in rest], ["s3_delete", "s3_delete", "create_db", "create_topic", "create_topic", "create_topic", "create", "resume"])
        self.assertEqual(rest[6], ("create", e.NAME, "STOPPED"))
        self.assertEqual(self.w.resumed_with, [[]])  # 새 커넥터는 빈 offset 으로만 시작
        self.assertFalse(e.CTX.exists())
        self.assertFalse([k for k in self.w.s3 if k.startswith(e.S3_PREFIXES)])
        self.assertEqual(sorted(k for k in self.w.s3), ["bronze/ads_outbox_events/part-0.parquet", "bronze/metaverse/outbox_events/part-0.parquet",
                                                         "checkpoints/bronze_ads_outbox/commits/0"])  # 남기는 접두사는 그대로
        self.assertEqual(self.w.connectors["ads-outbox-cdc"]["state"], "RUNNING")
        self.assertIn("S13 synced 확인: synced=t", out)

    def test_old_offsets_left_without_a_connector_never_start_the_new_one(self):
        del self.w.connectors[e.NAME]  # 누가 손으로 지움 — connect-offsets 에는 옛 offset 이 남음
        self.w.primary_slots[e.SLOT]["active"] = False
        rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 0, out)
        i = self.w.calls.index(("create", e.NAME, "STOPPED"))
        self.assertEqual(self.w.calls[i + 1:i + 3], [("delete_offsets", e.NAME), ("resume", e.NAME)])
        self.assertEqual(self.w.resumed_with, [[]])

    def test_offsets_that_do_not_clear_stop_before_resume(self):
        del self.w.connectors[e.NAME]
        self.w.primary_slots[e.SLOT]["active"] = False
        self.w.offsets_stuck = True
        rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 1)
        self.assertNotIn(("resume", e.NAME), self.w.calls)
        self.assertEqual(self.w.connectors[e.NAME]["state"], "STOPPED")
        self.assertIn("S12 다시 만듦: 실패", out)

    def test_offsets_that_do_not_clear_in_s4_stop_before_the_connector_goes(self):
        self.w.offsets_stuck = True
        rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 1)
        self.assertIn("S4 offset 지움: 실패", out)
        self.assertNotIn(("delete_connector", e.NAME), self.w.calls)
        self.assertEqual(self.w.connectors[e.NAME]["state"], "STOPPED")

    def test_topics_are_recreated_only_after_kafka_has_removed_them(self):
        self.w.topic_delete_lag = 3
        rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(c[1] for c in self.w.calls if c[0] == "create_topic"), sorted(e.TOPICS))
        self.assertTrue(set(e.TOPICS) <= self.w.topics)

    def test_slot_copies_must_disappear_before_the_database_goes(self):
        self.w.slotsync_stuck = True
        rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 1)
        self.assertIn("S7 슬롯 지움: 실패", out)
        self.assertNotIn(("drop_db",), self.w.calls)

    def test_connections_block_the_database_drop(self):
        original = self.w.rest

        def rest(method, path, body=None):  # 멈춘 뒤 누가 붙음
            result = original(method, path, body)
            if (method, path) == ("PUT", f"/connectors/{e.NAME}/stop"):
                self.w.extra_conns = [["postgres", "psql", "client backend"]]
            return result
        with mock.patch.object(e, "rest", rest):
            rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 1)
        self.assertIn("S8 DB 지움: 실패", out)
        self.assertNotIn(("drop_db",), self.w.calls)

    def test_s3_errors_stop_at_once(self):
        self.w.s3_errors = True
        rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 1)
        self.assertIn("AccessDenied", out)
        self.assertEqual([c[0] for c in self.w.calls].count("s3_delete"), 1)
        self.assertNotIn(("create_db",), self.w.calls)

    def test_keys_outside_the_prefix_are_never_deleted(self):
        self.w.s3_extra_listed = ["bronze/ads_outbox_events/part-0.parquet"]  # 서버가 접두사 밖 키를 돌려줌
        rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 2)  # 점검(§4-8)에서 거부
        self.assertFalse([c for c in self.w.calls if c[0] == "s3_delete"])
        self.w.s3_extra_listed = []
        original = self.w.s3_client

        def late():  # 점검 뒤 지우기 직전에야 밖 키가 보임
            client = original()
            listing = client.list_objects_v2

            def list_objects_v2(**kw):
                out = listing(**kw)
                if any(c[0] == "drop_db" for c in self.w.calls):
                    out["Contents"].append({"Key": "bronze/ads_outbox_events/part-0.parquet", "Size": 5})
                return out
            client.list_objects_v2 = list_objects_v2
            return client
        with mock.patch.object(e, "s3_client", late):
            rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 1)
        self.assertIn("밖 키", out)
        self.assertIn("bronze/ads_outbox_events/part-0.parquet", self.w.s3)

    def test_a_connector_that_reappears_before_s12_is_not_overwritten(self):
        original = e.s11_ctx

        def s11():  # S5 뒤 누가 같은 이름을 만듦
            self.w.connectors[e.NAME] = {"state": "RUNNING", "config": {"database.dbname": "x"}}
            return original()
        with mock.patch.object(e, "STEPS", tuple((n, s11 if f is e.s11_ctx else f) for n, f in e.STEPS)):
            rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 1)
        self.assertIn("같은 이름의 커넥터가 생김", out)
        self.assertFalse([c for c in self.w.calls if c[0] in ("create", "resume")])
        self.assertEqual(self.w.connectors[e.NAME]["config"], {"database.dbname": "x"})

    def test_rerun_after_a_failed_step_finishes_the_job(self):
        self.w.fail = ("kafka-topics.sh", "--delete")
        rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 1)
        self.assertIn("S9 토픽 지움: 실패", out)
        self.w.fail = None
        rc, out = self.run_main("--rebuild", "--execute")
        self.assertEqual(rc, 0, out)
        self.assertIn("상태: 일부만 지운 상태", out)
        self.assertIn("4-2 넘어감", out)
        self.assertEqual(self.w.resumed_with, [[]])
        self.assertEqual(self.w.connectors[e.NAME]["state"], "RUNNING")


class Unreachable(Base):
    """접속 실패 노드는 S7 에서 "아직 있음", S13 에서 "synced 아님"(fail closed)."""

    def standby_down(self):
        nodes = self.w.nodes()
        nodes[1] = {"port": nodes[1]["port"], "error": "OperationalError"}
        return nodes

    def test_s7_does_not_pass_while_a_standby_is_unreachable(self):
        self.w.primary_slots.pop(e.SLOT)
        for s in self.w.standby_slots.values():
            s.pop(e.SLOT)
        with mock.patch.object(e, "nodes", self.standby_down), self.assertRaises(e.StepError):
            e.s7_slot()
        e.s7_slot()  # 노드가 닿으면 지나간다

    def test_s13_does_not_call_an_unreachable_standby_synced(self):
        self.w._start(e.NAME)
        with mock.patch.object(e, "nodes", self.standby_down), self.assertRaises(e.StepError):
            e.s13_synced()
        self.assertEqual(e.s13_synced(), "synced=t")


class Pieces(unittest.TestCase):
    def test_both_paths_use_the_same_connector_config(self):
        bodies = []

        def rest(method, path, body=None):
            if method == "GET" and path.endswith("/config"):
                raise http_error(404)
            if method in ("PUT", "POST") and body:
                bodies.append(body.get("config", body))
            if path.endswith("/status"):
                return {"connector": {"state": "STOPPED" if method == "GET" and len(bodies) > 1 else "RUNNING"}, "tasks": [{"state": "RUNNING"}]}
            if path.endswith("/offsets"):
                return {"offsets": []}
            return None
        with mock.patch.object(e, "rest", rest), mock.patch.object(e, "status_or_none", lambda n: {"connector": {"state": "RUNNING"}, "tasks": [{"state": "RUNNING"}]}), \
                redirect_stdout(io.StringIO()):
            e.connector()
        with mock.patch.object(e, "rest", rest), mock.patch.object(e, "e2e_connector_exists", lambda: False), mock.patch.object(e, "database", lambda: None), \
                mock.patch.object(e, "topics", lambda: None), mock.patch.object(e, "wait", lambda *a: None), mock.patch.object(e, "wait_running", lambda: None):
            e.s12_create()
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual({k: bodies[0][k] for k in e.CHECK_KEYS}, {"database.dbname": e.DB, "slot.name": e.SLOT, "topic.prefix": e.PREFIX,
                                                                   "transforms.route.replacement": e.TOPIC, "signal.kafka.topic": e.SIGNAL,
                                                                   "signal.kafka.groupId": e.GROUP})

    def test_drop_sql(self):
        seen = []

        class Conn:
            def __init__(self, dbname):
                self.dbname = dbname

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                seen.append((self.dbname, sql, params))
        with mock.patch.object(e, "pg", lambda dbname, port=e.PG_PORT: Conn(dbname)):
            e.drop_db()
            e.drop_slot()
        self.assertEqual(seen[0], ("postgres", f'drop database "{e.DB}"', None))  # postgres DB 에서, FORCE 없이
        self.assertEqual(seen[1][0], "postgres")
        self.assertIn("not active", seen[1][1])
        self.assertEqual(seen[1][2], (e.SLOT, e.DB))

    def test_processes_skip_self_and_postgres_backends(self):
        import os
        me = os.getpid()
        ps = "\n".join([f"{me} 1 python e2e_setup.py --rebuild", "200 1 postgres: node1: ads_cdc ads_pipeline_e2e 127.0.0.1(5) idle",
                        "50 1 bash pipeline/bronze/run-bronze.sh", "60 50 /opt/java -cp x", "61 60 caffeinate -is spark-submit-ads.sh",
                        "300 61 /opt/python bronze_ads_outbox.py BRONZE_TOPIC=ads.e2e.outbox.events SECRET=x",
                        "400 1 /opt/python bronze_ads_outbox.py BRONZE_TOPIC=metaverse.outbox.events",
                        "500 1 /opt/python e2e_load.py --duration 300"])
        argv = []
        with mock.patch.object(e.subprocess, "run", lambda cmd, **k: argv.append(cmd) or mock.Mock(stdout=ps)):
            hits = e.processes()
        self.assertIn("-axEww", argv[0])  # 환경까지(macOS -E) — e2e Bronze 는 환경에만 e2e 가 있다
        self.assertEqual(hits, [{"pid": 300, "what": "bronze_ads_outbox.py", "match": ["ads.e2e"], "runner": 50},  # 조상의 run-bronze.sh(S1 대상)
                                {"pid": 500, "what": "e2e_load.py", "match": ["e2e_load.py"]}])
        self.assertNotIn("SECRET", json.dumps(hits))


if __name__ == "__main__":
    unittest.main()
