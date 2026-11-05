"""시험 스크립트의 `docker()` 는 공용 도우미(`tests/tools/docker_cli.py`) 하나를 쓰고, 스크립트마다 지금의 시간 제한을 넘긴다(계획 4-4k, R5)."""
import importlib.util
import re
import sys
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase
from tests.support.cases import Cases

SCRIPTS = Path(__file__).resolve().parents[1] / "tools"


class DockerTimeoutTests(Cases, SimpleTestCase):
    def load(self, name):
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def case_each_script_keeps_its_timeout_and_a_call_can_override_it(self):
        with mock.patch.object(sys, "path", [str(SCRIPTS), *sys.path]), mock.patch.dict(sys.modules):
            modules = {name: self.load(name) for name in ("mongo_temp_rs", "failure_test")}
            import docker_cli

            with mock.patch.object(docker_cli.subprocess, "run") as run:
                for name, timeout in (("mongo_temp_rs", 120), ("failure_test", 180)):
                    modules[name].docker("ps", check=False)
                    self.assertEqual(run.call_args.args[0], ["docker", "ps"], name)
                    self.assertEqual((run.call_args.kwargs["timeout"], run.call_args.kwargs["check"]), (timeout, False), name)
                modules["failure_test"].docker("pull", "x", timeout=1800)
                self.assertEqual(run.call_args.kwargs["timeout"], 1800)
                modules["failure_test"].docker("exec", "-i", "n", "cat", input="script")
                self.assertEqual(run.call_args.kwargs["input"], "script")

    def case_only_the_shared_helper_defines_docker(self):
        defined = {p.name for p in SCRIPTS.glob("*.py") if "def docker(" in p.read_text(encoding="utf-8")}
        self.assertEqual(defined, {"docker_cli.py"})

    def case_failure_test_uses_the_same_postgres_image_as_compose(self):
        """이미지 태그는 두 곳에 고정한다(docs/plans/00 §4.3 V-1) — 한쪽만 올리면 장애 시험이 다른 서버를 본다."""
        with mock.patch.object(sys, "path", [str(SCRIPTS), *sys.path]), mock.patch.dict(sys.modules):
            image = self.load("failure_test").IMAGE
        self.assertRegex((SCRIPTS.parent / "docker" / "compose.yaml").read_text(encoding="utf-8"), rf"image:\s*{re.escape(image)}(\s|$)")

    def test_docker_timeout(self):
        self.run_cases()
