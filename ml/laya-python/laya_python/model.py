"""사용자가 제공한 체크포인트와 분류 코드를 그대로 사용한다."""
import hashlib
import importlib.util
import json
import threading
from pathlib import Path

from .core import CATEGORIES, Failure, category_mask

EXPORT = Path(__file__).resolve().parent.parent / "vendor/laya_export"
MODEL_FILES = {
    "encoder/config.json", "model.safetensors", "rl_agent_config.json",
    "tokenizer/special_tokens_map.json", "tokenizer/tokenizer.json", "tokenizer/tokenizer_config.json",
}


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_model(model_dir):
    model_dir = Path(model_dir).resolve()
    if any(not (model_dir / name).is_file() for name in MODEL_FILES | {"manifest.json", "task.json"}):
        raise Failure("LAYA_MODEL_MISSING")
    try:
        task = json.loads((model_dir / "task.json").read_text(encoding="utf-8"))
        expected = {"max_len": 1024, "head_max_len": 256, "title_chars": 240, "body_chars": 2000, "threshold": 0.5}
        if any(task.get(key) != value for key, value in expected.items()):
            raise Failure("LAYA_TASK_MISMATCH")
        if set(task["categories"]) != set(CATEGORIES) or set(task["questions"]) != set(CATEGORIES):
            raise Failure("LAYA_CATEGORY_MISMATCH")
        for question in task["questions"].values():
            if list(question["criteria"]) != ["A", "B"] or question["criteria"] != {"A": "해당하지 않음", "B": "주요 주제에 해당함"}:
                raise Failure("LAYA_CHOICES_MISMATCH")
        info = json.loads((EXPORT / "MODEL_INFO.json").read_text(encoding="utf-8"))
        if file_hash(model_dir / "task.json") != info["task_sha256"]:
            raise Failure("LAYA_HASH_MISMATCH")
        manifest = json.loads((model_dir / "manifest.json").read_text(encoding="utf-8"))
        normalized = {name.replace("\\", "/"): value for name, value in manifest.items()}
        if len(manifest) != len(MODEL_FILES) or set(normalized) != MODEL_FILES:
            raise Failure("LAYA_MANIFEST_INVALID")
        for name, entry in normalized.items():
            path = (model_dir / name).resolve()
            if not path.is_relative_to(model_dir):
                raise Failure("LAYA_MANIFEST_INVALID")
            if path.stat().st_size != entry["bytes"] or file_hash(path) != entry["sha256"]:
                raise Failure("LAYA_HASH_MISMATCH")
        if normalized["model.safetensors"]["sha256"] != info["weights_sha256"]:
            raise Failure("LAYA_HASH_MISMATCH")
        return task
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise Failure("LAYA_CONFIG_INVALID") from None


class LayaModel:
    def __init__(self, model_dir):
        self.task = validate_model(model_dir)
        self.last_input_lengths = []
        self._lock = threading.Lock()
        try:
            spec = importlib.util.spec_from_file_location("laya_exported_classifier", EXPORT / "classifier.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.classifier = module.LayaClassifier(model_dir=model_dir, device="auto")
        except ImportError:
            raise Failure("LAYA_DEPENDENCY_MISSING") from None
        except Exception:
            raise Failure("LAYA_LOAD_FAILED") from None

    def predict(self, title, body):
        with self._lock:
            return self._predict(title, body)

    def _predict(self, title, body):
        from laya.train import encode_item, items_from_rows

        self.last_input_lengths = []
        if not isinstance(title, str) or not isinstance(body, str):
            raise Failure("INVALID_MODEL_INPUT")
        title = title.strip()[:self.task["title_chars"]]
        body = body.strip()[:self.task["body_chars"]]
        if not title or not body:
            raise Failure("EMPTY_MODEL_INPUT")
        rows = [{"state": f"제목: {title}\n본문 발췌: {body}", "questions": self.task["questions"],
                 "expected": {label: "A" for label in self.task["categories"]}}]
        try:
            items, skipped = items_from_rows(self.classifier.tokenizer, rows, 1024, 256)
            if skipped or len(items) != 8:
                raise Failure("CATEGORY_QUESTION_NOT_ENCODED")
            lengths = [len(encode_item(self.classifier.tokenizer, item, 1024, 256)["ids"]) for item in items]
            if any(not 0 < length <= 1024 for length in lengths):
                raise Failure("MODEL_TOKEN_LIMIT")
            result = self.classifier.predict(title, body, markdown=False)
            scores = result["scores"]
            category_mask(scores)
            self.last_input_lengths = lengths
            return scores
        except Failure:
            raise
        except Exception:
            raise Failure("LAYA_INFERENCE_FAILED") from None
