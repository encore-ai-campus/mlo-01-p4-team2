import json
import os
import tempfile
import unittest
from pathlib import Path

from laya_python.core import CATEGORIES, Failure, category_mask
from laya_python.model import MODEL_FILES, LayaModel, validate_model

ROOT = Path(__file__).resolve().parent.parent


class ModelValidationTests(unittest.TestCase):
    def skeleton(self, directory):
        root = Path(directory)
        for name in MODEL_FILES | {"manifest.json", "task.json"}:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(b"")
        task = {
            "max_len": 1024, "head_max_len": 256, "title_chars": 240, "body_chars": 2000, "threshold": 0.5,
            "categories": dict.fromkeys(CATEGORIES, "시험 범주"),
            "questions": {name: {"instructions": "시험 질문", "criteria": {"A": "해당하지 않음", "B": "주요 주제에 해당함"}}
                          for name in CATEGORIES},
        }
        return root, task

    def test_missing_model_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(Failure, "LAYA_MODEL_MISSING"):
            validate_model(Path(directory))

    def test_wrong_token_contract_fails_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root, task = self.skeleton(directory)
            task["max_len"] = 2048
            (root / "task.json").write_text(json.dumps(task))
            with self.assertRaisesRegex(Failure, "LAYA_TASK_MISMATCH"):
                validate_model(root)

    def test_wrong_category_and_choices_fail_before_loading(self):
        for change, code in (("category", "LAYA_CATEGORY_MISMATCH"), ("choices", "LAYA_CHOICES_MISMATCH")):
            with tempfile.TemporaryDirectory() as directory:
                root, task = self.skeleton(directory)
                if change == "category":
                    task["categories"].pop(CATEGORIES[0])
                else:
                    task["questions"][CATEGORIES[0]]["criteria"] = {"A": "positive", "B": "negative"}
                (root / "task.json").write_text(json.dumps(task))
                with self.assertRaisesRegex(Failure, code):
                    validate_model(root)

    def test_task_content_hash_is_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            root, task = self.skeleton(directory)
            task["questions"][CATEGORIES[0]]["instructions"] = "변경된 질문"
            (root / "task.json").write_text(json.dumps(task))
            with self.assertRaisesRegex(Failure, "LAYA_HASH_MISMATCH"):
                validate_model(root)


@unittest.skipUnless(os.getenv("LAYA_RUN_MODEL_TEST") == "1", "실제 모델 시험은 LAYA_RUN_MODEL_TEST=1로 실행")
class ActualModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = LayaModel(ROOT / "models/laya")

    def test_actual_checkpoint_reuse_and_eight_bounded_inputs(self):
        classifier_id = id(self.model.classifier)
        scores = self.model.predict("RAG와 에이전트 실습", "LLM과 임베딩으로 검색 증강 생성을 구현합니다. 도구 호출 에이전트와 API를 실습합니다.")
        self.assertEqual(set(scores), set(CATEGORIES))
        self.assertEqual(len(self.model.last_input_lengths), 8)
        self.assertTrue(all(0 < length <= 1024 for length in self.model.last_input_lengths))
        self.assertIn(str(self.model.classifier.device), ("cpu", "cuda"))
        self.assertTrue(0 <= category_mask(scores) <= 255)
        self.model.predict("데이터 파이프라인", "Kafka와 Spark로 데이터를 수집하고 정제합니다.")
        self.assertEqual(id(self.model.classifier), classifier_id)

    def test_long_body_uses_training_tail_cut_and_keeps_question_budget(self):
        title = "파이썬 개발 " * 100
        body = "파이썬으로 API를 만들고 테스트합니다. " * 300
        scores = self.model.predict(title, body)
        lengths = list(self.model.last_input_lengths)
        clipped_scores = self.model.predict(title.strip()[:240], body.strip()[:2000])
        self.assertEqual(lengths, self.model.last_input_lengths)
        self.assertEqual(scores, clipped_scores)
        self.assertEqual(len(lengths), 8)
        self.assertTrue(all(0 < length <= 1024 for length in lengths))

    def test_bad_input_has_safe_error(self):
        for title, body in (("", "본문"), ("제목", ""), (None, "본문"), ("제목", 12)):
            with self.assertRaises(Failure):
                self.model.predict(title, body)


if __name__ == "__main__":
    unittest.main()
