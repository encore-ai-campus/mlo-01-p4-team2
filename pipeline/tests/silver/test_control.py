"""pipeline/silver/control.py — 드라이버 쪽 설정·정체성 계산(Spark 없음): 넘김 목록, 멱등 appId·checkpoint 정체성, silver_release."""
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.silver import control

TOPIC = "metaverse.outbox.events"


def ack_file(*lines, domain="metaverse"):
    return json.dumps({"domain": domain, "lines": list(lines)}, ensure_ascii=False)


POINT = {"topic": TOPIC, "partition": 0, "offset": 12, "error_code": "foreign_source", "reason": "커넥터 표 목록 오류", "incident": "INC-7",
         "acknowledged_by": "운영자", "acknowledged_at": "2026-11-04T06:00:00Z"}
RANGE = {"topic": TOPIC, "partition": 1, "offset_start": 10, "offset_end": 20, "row_count": 7, "error_code": "not_insert", "reason": "TRUNCATE",
         "incident": "INC-8", "acknowledged_by": "운영자", "acknowledged_at": "2026-11-04T06:00:00Z"}


class LoadAcknowledged(unittest.TestCase):
    def assert_rejected(self, data):
        with self.assertRaises(ValueError):
            control.load_acknowledged(data, "metaverse")

    def test_point_and_range_lines(self):
        point, span = control.load_acknowledged(ack_file(POINT, RANGE).encode("utf-8"), "metaverse")
        self.assertEqual((point["start"], point["end"], point["row_count"]), (12, 12, None))
        self.assertEqual((span["start"], span["end"], span["row_count"]), (10, 20, 7))
        self.assertEqual((point["incident"], span["reason"]), ("INC-7", "TRUNCATE"))
        self.assertEqual(control.load_acknowledged(ack_file(), "metaverse"), [])

    def test_file_shape(self):
        self.assert_rejected(ack_file(POINT, domain="ads"))
        self.assert_rejected(json.dumps({"domain": "metaverse", "lines": [], "x": 1}))
        self.assert_rejected(json.dumps({"domain": "metaverse", "lines": {}}))
        self.assert_rejected(json.dumps([POINT]))
        self.assert_rejected('{"domain": "metaverse", "domain": "metaverse", "lines": []}')
        self.assert_rejected(b'{"domain": "metaverse", "lines": [], "x": "\xff"}')

    def test_line_keys_and_values(self):
        bad = [POINT | {"offset_start": 1}, {k: v for k, v in POINT.items() if k != "incident"}, POINT | {"error_code": "decode"},
               POINT | {"reason": " "}, POINT | {"acknowledged_by": 1}, POINT | {"acknowledged_at": "2026-1-4T6:0:0Z"},
               POINT | {"acknowledged_at": "2026-11-04 06:00:00"}, POINT | {"acknowledged_at": None},
               POINT | {"partition": -1}, POINT | {"offset": True}, POINT | {"offset": 1.0}, RANGE | {"offset_start": 21}, RANGE | {"row_count": 0}, "줄"]
        for line in bad:
            with self.subTest(line=line):
                self.assert_rejected(ack_file(line))

    def test_overlapping_lines(self):
        self.assert_rejected(ack_file(RANGE, RANGE | {"offset_start": 20, "offset_end": 30}))
        self.assert_rejected(ack_file(RANGE, POINT | {"partition": 1, "offset": 15}))
        lines = control.load_acknowledged(ack_file(RANGE, RANGE | {"partition": 2}, RANGE | {"offset_start": 21, "offset_end": 30},
                                                   RANGE | {"topic": "metaverse.outbox.events.g2"}), "metaverse")
        self.assertEqual(len(lines), 4)


class TxnIds(unittest.TestCase):
    CK = "3f0e6a52-1b7c-4d1e-9a3b-5c6d7e8f9a0b"

    def test_app_id(self):
        self.assertEqual(control.txn_app_id("metaverse", 1, "events", self.CK), f"silver:metaverse:g1:events:{self.CK}")
        self.assertEqual(control.txn_app_id("ads", 12, "stops", self.CK), f"silver:ads:g12:stops:{self.CK}")
        self.assertEqual(control.TXN_TABLES, ("events", "errors", "stops"))
        for args in (("meta:verse", 1, "events", self.CK), ("metaverse", 0, "events", self.CK), ("metaverse", True, "events", self.CK),
                     ("metaverse", "1", "events", self.CK), ("metaverse", 1, "gold", self.CK), ("metaverse", 1, "events", "a:b"),
                     ("Metaverse", 1, "events", self.CK), ("metaverse", 1, "events", "")):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    control.txn_app_id(*args)

    def test_foreign_checkpoints(self):
        mine = [control.txn_app_id("metaverse", 1, table, self.CK) for table in control.TXN_TABLES]
        others = ["silver:metaverse:g10:events:old", "silver:metaverse:g2:events:old", "silver:ads:g1:events:old", "sl2:e12", "silver:meta:g1:events:old"]
        self.assertEqual(control.foreign_checkpoints(mine + others, "metaverse", 1, self.CK), [])
        stale = ["silver:metaverse:g1:events:ck2", "silver:metaverse:g1:stops:ck1", "silver:metaverse:g1:errors:ck1"]
        self.assertEqual(control.foreign_checkpoints(mine + others + stale, "metaverse", 1, self.CK), sorted(stale))


class StartCheck(unittest.TestCase):  # SL-3c 결정 S2 (가) — 시작 검사 ①~④
    CK, OLD = "3f0e6a52-1b7c-4d1e-9a3b-5c6d7e8f9a0b", "0aabe9c8-6b77-4b20-9e52-3360a8f0b86c"

    def check(self, silver_ids=(), error_ids=(), *, checkpoint=None, finished=False):
        control.start_check(silver_ids, error_ids, domain="metaverse", generation=1, checkpoint=checkpoint, finished=finished)

    def ids(self, table, checkpoint=None):
        return [control.txn_app_id("metaverse", 1, table, checkpoint or self.CK)]

    def test_new_start_and_normal_restart(self):
        self.check()  # ① 처음
        self.check(["silver:metaverse:g2:events:x", "silver:ads:g1:events:x"], checkpoint=None)  # ① 다른 세대·원천 기록은 상관없음
        self.check(self.ids("events"), self.ids("errors") + self.ids("stops"), checkpoint=self.CK, finished=True)  # 다시 시작
        self.check((), self.ids("stops"), checkpoint=self.CK, finished=False)  # 첫 배치가 멈춘 뒤(끝난 배치 없음)
        self.check((), (), checkpoint=self.CK, finished=False)  # checkpoint 만 만들어지고 배치 전에 죽음

    def test_stops(self):
        cases = [
            ("checkpoint 가 없는데", dict(silver_ids=self.ids("events"))),  # ② checkpoint 를 지움
            ("checkpoint 가 없는데", dict(error_ids=self.ids("stops"))),  # ② 멈춘 기록만 있어도
            ("checkpoint 가 없는데", dict(finished=True)),  # metadata 없이 commits 만 있음(깨진 checkpoint)
            ("다른 checkpoint", dict(silver_ids=self.ids("events", self.OLD) + self.ids("events"), checkpoint=self.CK, finished=True)),  # ③
            ("다른 checkpoint", dict(error_ids=self.ids("errors", self.OLD), checkpoint=self.CK)),  # ③ 오류 표에만
            ("Silver 표에 그 기록이 없다", dict(error_ids=self.ids("errors"), checkpoint=self.CK, finished=True)),  # ④ Silver 표를 지움
            ("오류 표에 그 기록이 없다", dict(silver_ids=self.ids("events"), checkpoint=self.CK, finished=True)),  # ④ 오류 표를 지움(SL-3c 리뷰 R1)
            ("Silver·오류 표에 그 기록이 없다", dict(silver_ids=self.ids("stops"), error_ids=self.ids("stops"), checkpoint=self.CK, finished=True)),  # ④ events·errors appId 만 센다
        ]
        for needle, kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(control.IdentityError, needle):
                self.check(**kwargs)

    def test_checkpoint_id(self):
        self.assertEqual(control.checkpoint_id('{"id":"%s"}' % self.CK), self.CK)
        self.assertEqual(control.checkpoint_id('{"id":"%s"}\n' % self.CK), self.CK)
        for text in ("", "{}", '{"id": 1}', '{"id":"%s"}' % self.CK.upper(), '{"id":"%s\\n"}' % self.CK, '["%s"]' % self.CK, None, '{"id":"x:y"}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                control.checkpoint_id(text)


ENV = {"SILVER_LAKE_ROOT": "s3a://ads-lake/", "SILVER_BRONZE_TABLE": "s3a://ads-lake/bronze/metaverse/outbox_events", "SILVER_SOURCE_DB": "metaverse_lab",
       "SILVER_SOURCE_SCHEMA": "public", "SILVER_SOURCE_TABLE": "eventing_eventoutbox", "SILVER_ACKNOWLEDGED": "/code/pipeline/silver/acknowledged/metaverse.json",
       "SILVER_CODE_ROOT": "/code", "SILVER_MAX_FILES_PER_TRIGGER": "200"}


class Settings(unittest.TestCase):  # SL-3c 결정 S1·정한 것 1
    def test_source_settings(self):
        source = {"db": "metaverse_lab", "schema": "public", "table": "eventing_eventoutbox"}
        self.assertEqual(control.source_settings(dict(source)), source)
        for bad in (None, [], {"db": "a", "schema": "b"}, {**source, "extra": "x"}, {**source, "db": ""}, {**source, "db": 1}, {**source, "table": " t"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                control.source_settings(bad)

    def test_job_settings(self):
        settings = control.job_settings(ENV, "metaverse", 1)
        self.assertEqual((settings.silver, settings.errors, settings.checkpoint),
                         ("s3a://ads-lake/silver/metaverse/events/g1", "s3a://ads-lake/silver/metaverse/errors/g1",
                          "s3a://ads-lake/checkpoints/silver/metaverse/events/g1"))
        self.assertEqual(settings.source, {"db": "metaverse_lab", "schema": "public", "table": "eventing_eventoutbox"})
        self.assertEqual((settings.bronze, settings.max_files, settings.code_root), (ENV["SILVER_BRONZE_TABLE"], 200, "/code"))

    def test_no_defaults(self):
        with self.assertRaisesRegex(ValueError, "SILVER_LAKE_ROOT, SILVER_BRONZE_TABLE"):
            control.job_settings({k: v for k, v in ENV.items() if k not in ("SILVER_LAKE_ROOT", "SILVER_BRONZE_TABLE")}, "metaverse", 1)
        for name in control._ENV:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                control.job_settings({**ENV, name: " "}, "metaverse", 1)

    def test_bad_values(self):
        for env, domain, generation in (({**ENV, "SILVER_MAX_FILES_PER_TRIGGER": "0"}, "metaverse", 1), ({**ENV, "SILVER_MAX_FILES_PER_TRIGGER": "-1"}, "metaverse", 1),
                                        ({**ENV, "SILVER_LAKE_ROOT": "s3a://a`b"}, "metaverse", 1), ({**ENV, "SILVER_BRONZE_TABLE": "x`"}, "metaverse", 1),
                                        ({**ENV, "SILVER_LAKE_ROOT": "///"}, "metaverse", 1), ({**ENV, "SILVER_BRONZE_TABLE": "s3a://b "}, "metaverse", 1), ({**ENV, "SILVER_SOURCE_DB": "a "}, "metaverse", 1),
                                        (ENV, "Meta", 1), (ENV, "metaverse", 0), (ENV, "metaverse", "1")):
            with self.subTest(env=env, domain=domain, generation=generation), self.assertRaises(ValueError):
                control.job_settings(env, domain, generation)


class SilverRelease(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.write("pipeline/silver/framework.py", "x = 1\n")
        self.write("pipeline/silver/adapters/metaverse.py", "y = 2\n")
        self.write("pipeline/common/hop_rules.py", "z = 3\n")

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_format_and_inputs(self):
        before = control.silver_release(self.root)
        self.assertRegex(before, r"^silver@[0-9a-f]{12}$")
        self.write("pipeline/silver/acknowledged/metaverse.json", "{}")
        self.write("pipeline/silver/__pycache__/framework.cpython-314.pyc", "junk")
        self.write("pipeline/silver/spark-submit-silver.sh", "echo")
        self.write("pipeline/common/ndjson.py", "w = 4\n")
        self.assertEqual(control.silver_release(self.root), before)  # 넘김 목록·바이트코드·제출 스크립트·다른 공용 파일은 넣지 않는다
        for rel, text in (("pipeline/silver/adapters/metaverse.py", "y = 3\n"), ("pipeline/common/hop_rules.py", "z = 4\n"), ("pipeline/silver/pointers.py", "")):
            with self.subTest(rel=rel):
                self.write(rel, text)
                self.assertNotEqual(control.silver_release(self.root), before)
                before = control.silver_release(self.root)

    def test_moving_content_or_renaming_changes_release(self):
        self.write("pipeline/silver/a.py", "x\n")
        self.write("pipeline/silver/b.py", "")
        one = control.silver_release(self.root)
        (self.root / "pipeline/silver/b.py").rename(self.root / "pipeline/silver/c.py")  # 이름만 바뀜 — 내용·정렬 위치(adapters 뒤, framework 앞)는 그대로
        self.assertNotEqual(control.silver_release(self.root), one)
        (self.root / "pipeline/silver/c.py").rename(self.root / "pipeline/silver/b.py")
        self.assertEqual(control.silver_release(self.root), one)
        self.write("pipeline/silver/a.py", "")
        self.write("pipeline/silver/b.py", "x\n")  # 같은 바이트를 다른 파일로 옮김
        self.assertNotEqual(control.silver_release(self.root), one)

    def test_missing_hop_rules_copy_fails(self):
        (self.root / "pipeline/common/hop_rules.py").unlink()
        with self.assertRaises(FileNotFoundError):
            control.silver_release(self.root)

    def test_no_silver_code_under_the_root_fails(self):  # 뿌리 설정을 잘못 두면 hop_rules 하나만 해시한 값이 나오지 않게
        for rel in ("pipeline/silver/framework.py", "pipeline/silver/adapters/metaverse.py"):
            (self.root / rel).unlink()
        self.write("pipeline/silver/acknowledged/metaverse.json", "{}")
        with self.assertRaisesRegex(FileNotFoundError, "pipeline/silver"):
            control.silver_release(self.root)

    def test_default_root_is_this_repository(self):
        repo = Path(control.__file__).resolve().parents[2]
        self.assertTrue((repo / "pipeline" / "silver" / "control.py").is_file())
        if (repo / "pipeline/common/hop_rules.py").is_file():  # P2-1 사본이 있을 때
            self.assertEqual(control.silver_release(), control.silver_release(repo))
        else:
            with self.assertRaisesRegex(FileNotFoundError, "hop_rules.py"):
                control.silver_release()


if __name__ == "__main__":
    unittest.main()
