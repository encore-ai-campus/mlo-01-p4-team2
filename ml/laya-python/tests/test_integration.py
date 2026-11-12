import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.offline_demo import IDS, run_demo


@unittest.skipUnless(os.getenv("LAYA_RUN_MODEL_TEST") == "1", "실제 모델 통합시험은 LAYA_RUN_MODEL_TEST=1로 실행")
class OfflineIntegrationTests(unittest.TestCase):
    def test_mock_http_mock_openai_and_actual_laya(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "must-never-be-used"}), patch("laya_python.summary.OpenAISummary", side_effect=AssertionError("외부 요약기를 생성하면 안 됩니다")):
            demo = run_demo(jobs_dir=Path(directory))
        result = demo["result"]
        self.assertEqual((demo["source"], demo["summary_api"], demo["classifier"]), ("laya", "mock", "actual_laya"))
        self.assertEqual(demo["advertiser_api"], "mock_loopback")
        self.assertIn(demo["device"], ("cpu", "cuda"))
        self.assertEqual(len(demo["input_lengths"]), 8)
        self.assertTrue(all(0 < value <= 1024 for value in demo["input_lengths"]))
        self.assertEqual((result["total"], result["processed"], result["succeeded"], result["failed"]), (4, 4, 2, 2))
        self.assertEqual(result["status"], "completed_with_failures")
        self.assertTrue(result["counts_final"])
        self.assertEqual({failure["ad_group_id"] for failure in result["failures"]}, {IDS[1], IDS[3]})
        self.assertEqual([call["count"] for call in demo["http_requests"] if call["method"] == "POST"], [1, 2, 1])
        print(f"offline integration: device={demo['device']}, total=4, succeeded=2, failed=2, input_lengths={demo['input_lengths']}")


if __name__ == "__main__":
    unittest.main()
