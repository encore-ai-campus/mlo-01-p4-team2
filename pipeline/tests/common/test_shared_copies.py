"""광고 서버 원본의 파이프라인 쪽 사본(`pipeline/common/`)이 원본과 같은 판인지(설계 01 D14·S5, 파이프라인 계획 P2-1).

- `hop_rules.py`: 공용 요약 규칙. 파이프라인 잡이 manifest 에 `rule_sha256` 을 싣고, 광고 서버는 자기 원본의 해시와 다르면 그 쌍을 대조하지 않는다.
- `ndjson.py`: manifest·데이터 파일의 바이트 규칙. 광고 서버의 받기(`fetch_hop_manifest`)가 같은 규칙(`check`·`check_chain`)으로 확인한다.
두 사본은 원본과 바이트가 같아야 하고, 원본을 바꾸는 커밋에서 사본도 함께 바꾼다. 줄 끝은 `.gitattributes` 로 늘 LF 다(Windows 체크아웃에서도 해시가 같게).
표준 라이브러리만 쓴다(Django·Spark 없이 돈다).
"""
import importlib.util
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COPIES = {name: (ROOT / "ads_server" / "events" / name, ROOT / "pipeline" / "common" / name) for name in ("hop_rules.py", "ndjson.py")}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SharedCopiesTests(unittest.TestCase):
    def test_each_copy_is_byte_identical_to_its_original(self):
        for name, (original, copy) in COPIES.items():
            with self.subTest(name=name):
                self.assertEqual(copy.read_bytes(), original.read_bytes(), f"원본을 바꿨으면 같은 커밋에서 사본도 바꾼다: cp {original.relative_to(ROOT)} {copy.relative_to(ROOT)}")

    def test_the_rule_copy_reports_the_same_rule_sha256(self):
        original, copy = COPIES["hop_rules.py"]
        self.assertEqual(load(copy, "hop_rules_copy").rule_sha256(), load(original, "hop_rules_original").rule_sha256())
        self.assertRegex(load(copy, "hop_rules_copy").rule_sha256(), r"^[0-9a-f]{64}$")

    def test_the_ndjson_copy_writes_the_same_bytes(self):
        """같은 입력이면 사본과 원본이 같은 데이터 파일·manifest·세대 사본을 쓴다(파일 비교에 더해 동작으로도)."""
        import tempfile
        from datetime import datetime, timezone

        now = datetime(2026, 10, 7, 13, 0, tzinfo=timezone.utc)
        outputs = []
        for label, path in (("original", COPIES["ndjson.py"][0]), ("copy", COPIES["ndjson.py"][1])):
            module = load(path, f"ndjson_{label}")
            with tempfile.TemporaryDirectory() as directory:
                for rows in ([{"b": 1, "a": "가"}], [{"a": 2}, {"c": None}]):
                    module.write(directory, "hop_pipeline_hour", rows, "hop_pipeline", 1, checkpoint={"since": "2026-10-07T12:00:00Z"}, now=now, chain=True)
                outputs.append({p.name: p.read_bytes() for p in Path(directory).iterdir() if not p.name.startswith(".")})
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(len(outputs[0]), 5)  # 데이터 둘 + 세대 사본 둘 + 포인터

    def test_every_copy_and_original_is_checked_out_with_lf(self):
        paths = [str(p.relative_to(ROOT)) for pair in COPIES.values() for p in pair]
        out = subprocess.run(["git", "-C", str(ROOT), "check-attr", "eol", "--", *paths], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.splitlines(), [f"{p}: eol: lf" for p in paths])
        for _, copy in COPIES.values():
            self.assertNotIn(b"\r\n", copy.read_bytes())


if __name__ == "__main__":
    unittest.main()
