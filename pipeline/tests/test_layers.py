"""pipeline/ 의 import 층 규칙(계획 15 §1.1, SL-3a 결정 D5, SL-3 결정 3). 광고 서버 tests/dev/boundaries/test_layers.py 처럼 AST 로 import 를 읽는다.

1. 잡 코드(pipeline/silver·bronze·monitor·common·reconcile·gold)는 광고 서버(django, ads_server 의 앱 패키지)를 import 하지 않는다. 대사 규칙은 사본(pipeline/common)을 쓴다.
   시험대 도구(pipeline/tools)는 광고 서버를 import 하므로 범위 밖이다.
2. pipeline/common 은 표준 라이브러리만 쓴다(광고 서버 원본의 바이트 사본 — 원본과 같은 조건).
3. Silver 의 드라이버·UDF 쪽 모듈(strict·control, 어댑터의 UDF 쪽 규칙 `adapters/<원천>_rules.py`)은 표준 라이브러리와 pipeline.* 만 쓴다
   — Spark 없이 시험하고, executor 가 UDF 에서 import 한다.
   변환 모듈(contracts·framework·adapters 등)은 pyspark 를 쓴다(사용자 결정: Silver 는 Spark).
4. pipeline/silver 는 상대 import 를 쓰지 않는다(D5: `from pipeline.silver import contracts`).
5. (계획 41 코드 Step ①) Gold 의 드라이버 쪽 판단(pipeline/gold/control.py)은 표준 라이브러리와 pipeline.* 만 쓴다 — Spark 없이 시험한다.
   pipeline/gold 도 상대 import 를 쓰지 않는다.
6. (DEP3-VAC 계획 45 §3.6) 정리 잡의 드라이버 쪽 판단(pipeline/vacuum/control.py)은 표준 라이브러리와 pipeline.* 만 쓴다. pipeline/vacuum 은 상대 import 를
   쓰지 않고, Silver·Gold 는 pipeline.vacuum 을 import 하지 않는다(정리 코드가 silver@·gold@ 판 값과 잡에 닿지 않게).

실행: 저장소 뿌리에서 `python -m unittest discover pipeline/tests`(Spark 시험 spark_*.py 는 pipeline/tests/run_spark_tests.py 로).
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "pipeline"
JOB_DIRS = ("silver", "bronze", "monitor", "common", "reconcile", "gold", "vacuum")
SILVER_STDLIB = ("strict.py", "control.py", "__init__.py")
ADS_ROOTS = frozenset({"django", "ads_server"} | {p.name for p in (ROOT / "ads_server").iterdir() if (p / "__init__.py").is_file()})


def imports(source):
    """소스의 import 를 (뿌리 이름, 상대 단계) 목록으로. `from . import x` 는 ("", 1)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [(alias.name.split(".")[0], 0) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(((node.module or "").split(".")[0], node.level))
    return found


def py_files(*parts):
    base = PIPELINE.joinpath(*parts)
    return sorted(base.rglob("*.py")) if base.is_dir() else ([base] if base.is_file() else [])


def violations(files, allowed):
    """`allowed(뿌리, 상대 단계)` 가 거짓인 import 를 "파일: 뿌리" 로."""
    return [f"{path.relative_to(ROOT)}: {'.' * level}{name}" for path in files
            for name, level in imports(path.read_text(encoding="utf-8")) if not allowed(name, level)]


class LayerRules(unittest.TestCase):
    def test_job_code_does_not_import_ads_server(self):
        files = [path for part in JOB_DIRS for path in py_files(part)]
        self.assertIn(PIPELINE / "bronze" / "bronze_ads_outbox.py", files)  # 빈 검사가 아님
        self.assertIn(PIPELINE / "silver" / "framework.py", files)
        self.assertIn(PIPELINE / "gold" / "framework.py", files)
        self.assertIn(PIPELINE / "vacuum" / "vacuum_job.py", files)
        self.assertEqual(violations(files, lambda name, level: name not in ADS_ROOTS), [])

    def test_common_is_stdlib_only(self):
        files = py_files("common")
        self.assertEqual(violations(files, lambda name, level: level == 0 and name in sys.stdlib_module_names), [])

    def test_silver_driver_and_udf_modules_are_stdlib_and_pipeline_only(self):
        files = [path for name in SILVER_STDLIB for path in py_files("silver", name)]
        self.assertEqual(len(files), len(SILVER_STDLIB))
        files += sorted((PIPELINE / "silver" / "adapters").glob("*_rules.py"))  # 원천별 UDF 쪽 규칙(SL-4 metaverse_rules·SL-8) — 없으면 볼 것이 없다
        self.assertEqual(violations(files, lambda name, level: level == 0 and (name in sys.stdlib_module_names or name == "pipeline")), [])

    def test_silver_has_no_relative_imports(self):
        self.assertEqual(violations(py_files("silver"), lambda name, level: level == 0), [])

    def test_gold_control_is_stdlib_and_pipeline_only(self):
        files = py_files("gold", "control.py")
        self.assertEqual(len(files), 1)
        self.assertEqual(violations(files, lambda name, level: level == 0 and (name in sys.stdlib_module_names or name == "pipeline")), [])

    def test_gold_has_no_relative_imports(self):
        files = py_files("gold")
        self.assertIn(PIPELINE / "gold" / "gold_job.py", files)
        self.assertEqual(violations(files, lambda name, level: level == 0), [])

    def test_vacuum_control_is_stdlib_and_pipeline_only(self):
        files = py_files("vacuum", "control.py")
        self.assertEqual(len(files), 1)
        self.assertEqual(violations(files, lambda name, level: level == 0 and (name in sys.stdlib_module_names or name == "pipeline")), [])

    def test_vacuum_has_no_relative_imports_and_nobody_imports_it(self):
        files = py_files("vacuum")
        self.assertIn(PIPELINE / "vacuum" / "vacuum_job.py", files)
        self.assertEqual(violations(files, lambda name, level: level == 0), [])
        found = []
        for path in py_files("silver") + py_files("gold"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [getattr(node, "module", None) or ""] + [alias.name for alias in node.names]
                    found += [f"{path.relative_to(ROOT)}: {name}" for name in names if "vacuum" in name]
        self.assertEqual(found, [])

    def test_checker_sees_every_import_form(self):
        found = imports("import os.path\nfrom events import api\nfrom . import x\nfrom ..silver import y\ndef f():\n    import pyspark\n")
        self.assertEqual(found, [("os", 0), ("events", 0), ("", 1), ("silver", 2), ("pyspark", 0)])
        self.assertIn("events", ADS_ROOTS)


if __name__ == "__main__":
    unittest.main()
