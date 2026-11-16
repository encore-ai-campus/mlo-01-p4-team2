import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from laya_python.__main__ import main, next_run, schedule
from laya_python.settings import Settings

ROOT = Path(__file__).resolve().parent.parent


class CLITests(unittest.TestCase):
    def test_missing_real_key_stops_before_model_and_ads(self):
        settings = Settings("http://127.0.0.1/api/internal/v1", "test-key", "", Path("model"), Path("jobs"))
        output = io.StringIO()
        with (patch("laya_python.__main__.Settings.read", return_value=settings), patch("laya_python.__main__.LayaModel") as model,
              patch("laya_python.__main__.AdsClient") as client, redirect_stdout(output)):
            code = main(["run"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["reason"], "OPENAI_API_KEY_MISSING")
        self.assertEqual(json.loads(output.getvalue())["source"], "laya")
        model.assert_not_called()
        client.assert_not_called()

    def test_full_flag_and_result_json(self):
        settings = Settings("http://127.0.0.1/api/internal/v1", "test-key", "test-key", Path("model"), Path("jobs"))
        output = io.StringIO()
        with (patch("laya_python.__main__.Settings.read", return_value=settings), patch("laya_python.__main__.LayaModel"),
              patch("laya_python.__main__.Runner") as runner, redirect_stdout(output)):
            runner.return_value.run.return_value = {"source": "laya", "status": "completed_with_failures", "failed": 1}
            code = main(["run", "--full"])
        runner.return_value.run.assert_called_once_with(full=True)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["source"], "laya")

    def test_kst_22_matches_utc_13_and_next_day(self):
        before = datetime(2026, 11, 12, 12, 59, tzinfo=timezone.utc)
        self.assertEqual(next_run(before).astimezone(timezone.utc), before.replace(hour=13, minute=0))
        exact = before.replace(hour=13, minute=0)
        self.assertEqual(next_run(exact).astimezone(timezone.utc), exact)
        self.assertEqual(next_run(exact + timedelta(seconds=1)).astimezone(timezone.utc), exact + timedelta(days=1))

    def test_schedule_waits_then_runs_once_and_stops_after_interrupt(self):
        current = datetime(2026, 11, 12, 12, 59, tzinfo=timezone.utc)
        waits, outputs = [], []

        def sleep(seconds):
            nonlocal current
            waits.append(seconds)
            current += timedelta(seconds=seconds)

        class OneRun:
            def run(self):
                return {"source": "laya", "status": "interrupted"}

        self.assertEqual(schedule(OneRun(), now=lambda: current, sleep=sleep, output=outputs.append), 130)
        self.assertEqual(waits, [30, 30])
        self.assertEqual(outputs[0]["next_run_at"], "2026-11-12T22:00:00+09:00")
        self.assertTrue(all(row["source"] == "laya" for row in outputs))

    @unittest.skipUnless(shutil.which("git"), "git이 필요합니다")
    def test_gitignore_excludes_keys_models_and_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / ".gitignore", root / ".gitignore")
            env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True, env=env)
            for path in (".env", ".env.local", "models/laya/model.safetensors", "var/jobs/a.json", ".venv/bin/python", "laya_python/__pycache__/a.pyc"):
                result = subprocess.run(["git", "-c", "core.excludesFile=/dev/null", "check-ignore", "-q", "--no-index", path], cwd=root, env=env)
                self.assertEqual(result.returncode, 0, path)
            for path in (".env.example", "README.md", "laya_python/runner.py"):
                result = subprocess.run(["git", "-c", "core.excludesFile=/dev/null", "check-ignore", "-q", "--no-index", path], cwd=root, env=env)
                self.assertEqual(result.returncode, 1, path)


if __name__ == "__main__":
    unittest.main()
