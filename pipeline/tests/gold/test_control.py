"""pipeline/gold/control.py — Gold 드라이버 쪽 판단(Spark 없음, 계획 41 r3d): 미확정 시간, KST 날짜, 기준 시각, 정정 이유, 설정, 경로, gold_release."""
import ast
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pipeline.gold import control

ROOT = Path(__file__).resolve().parents[3]


def epoch_hour(text):
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp()) // 3600


class Unconfirmed(unittest.TestCase):
    def test_window_marks_its_hour_and_the_hour_before(self):  # AG5 (가) — 보류 300초 + 다시 판정 30초가 한 시간 안
        w = epoch_hour("2026-11-12T11:00Z")
        result = control.unconfirmed([("window", w, "mismatch"), ("window", w + 5, "match"), ("window", w + 7, "copies_mismatch"),
                                      ("errors", None, "match"), ("unplaced", None, "match"), ("quarantine", None, "match")])
        self.assertEqual(result, control.Unconfirmed(False, frozenset({w, w - 1}), True))

    def test_unplaced_mismatch_is_everything(self):  # 어느 시간인지 모른다
        self.assertEqual(control.unconfirmed([("unplaced", None, "mismatch"), ("window", 10, "match")]), control.Unconfirmed(True, frozenset(), False))

    def test_rows_that_do_not_touch_gold(self):  # 리뷰 r2 참고 ① — 오류-Bronze·목록-Bronze 는 본 표를 바꾸지 않고, 겹침은 그 창도 mismatch
        self.assertEqual(control.unconfirmed([("errors", None, "mismatch"), ("quarantine", None, "mismatch"), ("unplaced", None, "copies_mismatch")]),
                         control.Unconfirmed(False, frozenset(), False))

    def test_nothing(self):
        self.assertEqual(control.unconfirmed([]), control.Unconfirmed(False, frozenset(), False))


class KstDay(unittest.TestCase):
    def test_boundary_is_utc_15(self):
        day = control.kst_day(epoch_hour("2026-11-12T15:00Z"))  # KST 11-13 00시
        self.assertEqual(control.kst_day(epoch_hour("2026-11-12T14:00Z")), day - 1)  # KST 11-12 23시
        self.assertEqual(control.kst_day(epoch_hour("2026-11-13T14:00Z")), day)
        self.assertEqual(datetime.fromtimestamp(day * 86400, timezone.utc).date().isoformat(), "2026-11-13")


class Cutoff(unittest.TestCase):
    def test_smaller_of_produced_and_reconciled(self):  # 앞선 시각이 찍힌 행 하나가 기준을 밀어내지 않는다
        self.assertEqual(control.cutoff(5, 9, None), 5)
        self.assertEqual(control.cutoff(9, 5, None), 5)

    def test_never_moves_back(self):  # 리뷰 r3d 권고 1
        self.assertEqual(control.cutoff(5, 9, 7), 7)
        self.assertEqual(control.cutoff(8, 9, 7), 8)

    def test_no_events(self):
        self.assertIsNone(control.cutoff(None, 9, None))
        self.assertEqual(control.cutoff(None, 9, 4), 4)
        self.assertIsNone(control.cutoff(5, None, None))


class Reason(unittest.TestCase):
    def test_priority(self):  # AG8 (가) — 세대 > Gold 코드 > 그 밖
        base = {"silver_generation": 1, "gold_release": "gold@a"}
        self.assertEqual(control.reason(base, {"silver_generation": 2, "gold_release": "gold@b"}), "generation_changed")
        self.assertEqual(control.reason(base, {"silver_generation": 1, "gold_release": "gold@b"}), "gold_release_changed")
        self.assertEqual(control.reason(base, dict(base)), "silver_changed")


class Settings(unittest.TestCase):
    GOOD = {"GOLD_LAKE_ROOT": "s3a://lake", "GOLD_CODE_ROOT": "/opt/code"}

    def test_good(self):
        self.assertEqual(control.job_settings(self.GOOD, "ads"), control.JobSettings("ads", "s3a://lake", "/opt/code"))

    def test_missing_all_named(self):
        with self.assertRaisesRegex(ValueError, "^Gold 잡 설정이 없다\\(기본값 없음\\): GOLD_LAKE_ROOT, GOLD_CODE_ROOT$"):
            control.job_settings({"GOLD_LAKE_ROOT": " "}, "ads")

    def test_bad_values(self):
        with self.assertRaisesRegex(ValueError, "domain"):
            control.job_settings(self.GOOD, "Ads")
        with self.assertRaisesRegex(ValueError, "GOLD_LAKE_ROOT"):
            control.job_settings({**self.GOOD, "GOLD_LAKE_ROOT": "s3a://la`ke"}, "ads")

    def test_paths(self):
        self.assertEqual(control.table_path("s3a://lake/", "ads", "course_daily", 1), "s3a://lake/gold/ads/course_daily/v1")
        self.assertEqual(control.corrections_path("s3a://lake", "ads"), "s3a://lake/gold/ads/corrections")


class GoldRelease(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        for part in ("pipeline/gold", "pipeline/silver", "pipeline/common"):
            shutil.copytree(ROOT / part, self.root / part, ignore=shutil.ignore_patterns("__pycache__"))

    def test_inputs(self):
        before = control.gold_release(self.root)
        self.assertRegex(before, r"^gold@[0-9a-f]{12}$")
        self.assertEqual(before, control.gold_release(ROOT))
        for unchanged in ("pipeline/silver/adapters/ads.py", "pipeline/silver/quarantine/ads.json", "pipeline/common/__note__.py", "pipeline/silver/run-silver.sh"):
            path = self.root / unchanged
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((path.read_bytes() if path.exists() else b"") + b"\n# x\n")
        self.assertEqual(control.gold_release(self.root), before)  # 어댑터(Gold 가 부르지 않음)·목록·스크립트·다른 공용 파일은 넣지 않는다
        for changed in ("pipeline/gold/framework.py", "pipeline/silver/pointers.py", "pipeline/common/hop_rules.py"):
            with self.subTest(changed):
                path = self.root / changed
                path.write_bytes(path.read_bytes() + b"\n# x\n")
                self.assertNotEqual(control.gold_release(self.root), before)
                before = control.gold_release(self.root)

    def test_no_gold_code(self):
        shutil.rmtree(self.root / "pipeline/gold")
        with self.assertRaisesRegex(FileNotFoundError, "코드 뿌리"):
            control.gold_release(self.root)

    def test_hashes_everything_gold_imports(self):  # Gold 가 import 하는 pipeline 모듈(전이 포함)이 모두 판 입력에 든다
        hashed = {p.relative_to(ROOT).as_posix() for p in (*(ROOT / "pipeline/gold").rglob("*.py"), *(ROOT / "pipeline/silver").glob("*.py"),
                                                          ROOT / "pipeline/common/hop_rules.py")}
        seen, todo = set(), [p.relative_to(ROOT).as_posix() for p in (ROOT / "pipeline/gold").rglob("*.py")]
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            for node in ast.walk(ast.parse((ROOT / current).read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pipeline"):
                    for alias in node.names:
                        for candidate in (f"{node.module}.{alias.name}", node.module):
                            path = candidate.replace(".", "/") + ".py"
                            if (ROOT / path).is_file():
                                todo.append(path)
                                break
        self.assertIn("pipeline/silver/pointers.py", seen)  # 빈 검사가 아님
        self.assertIn("pipeline/common/hop_rules.py", seen)
        self.assertEqual(sorted(seen - hashed), [])


if __name__ == "__main__":
    unittest.main()
