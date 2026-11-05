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
