"""Load the exported 1024-token checkpoint and classify a document into eight labels."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

os.environ.update(USE_TF="0", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                  TOKENIZERS_PARALLELISM="false")

import numpy as np
import torch
from laya.train import calibration_records, items_from_rows, load_checkpoint


class LayaClassifier:
    def __init__(self, model_dir=None, device="auto"):
        self.model_dir = Path(model_dir or Path(__file__).parent / "model").resolve()
        if device not in ("auto", "cpu", "cuda"):
            raise ValueError("device must be auto, cpu or cuda")
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable. Use device='cpu'.")
        self.device = torch.device(device)
        torch.set_num_threads(4)
        torch.set_float32_matmul_precision("high")
        manifest = json.loads((self.model_dir / "manifest.json").read_text(encoding="utf-8"))
        for name, info in manifest.items():
            path = self.model_dir / name.replace("\\", "/")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != info["sha256"]:
                raise ValueError(f"Model file checksum mismatch: {name}")
        self.task = json.loads((self.model_dir / "task.json").read_text(encoding="utf-8"))
        # Laya can patch tokenizer metadata. Stage that metadata to preserve the exported files.
        with tempfile.TemporaryDirectory(prefix="laya-inference-") as temporary:
            staged = Path(temporary)
            for name in ("encoder", "tokenizer"):
                shutil.copytree(self.model_dir / name, staged / name)
            shutil.copy2(self.model_dir / "rl_agent_config.json", staged / "rl_agent_config.json")
            tokenizer_config = staged / "tokenizer/tokenizer_config.json"
            config = json.loads(tokenizer_config.read_text(encoding="utf-8"))
            tokenizer_config.write_text(json.dumps(config, ensure_ascii=True), encoding="ascii")
            try:
                os.link(self.model_dir / "model.safetensors", staged / "model.safetensors")
            except OSError:
                shutil.copy2(self.model_dir / "model.safetensors", staged / "model.safetensors")
            self.model, self.tokenizer, _ = load_checkpoint(str(staged))
        self.model.to(self.device)
        self.model.eval()

    def predict(self, title, body, markdown=False):
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a nonempty string")
        if not isinstance(body, str):
            raise ValueError("body must be a string")
        if markdown:
            from preprocess import clean_markdown
            body = clean_markdown(body)
        title = title.strip()[:self.task["title_chars"]]
        body = body.strip()[:self.task["body_chars"]]
        state = f"제목: {title}\n본문 발췌: {body}"
        rows = [{"state": state, "questions": self.task["questions"],
                 "expected": {label: "A" for label in self.task["categories"]}}]
        items, skipped = items_from_rows(self.tokenizer, rows, self.task["max_len"],
                                        self.task["head_max_len"])
        if skipped or len(items) != len(self.task["categories"]):
            raise ValueError("A category question could not be encoded")
        records = calibration_records(self.model, self.tokenizer, items, self.device,
                                      self.task["max_len"], self.task["head_max_len"], batch_size=8)
        scores = {}
        for label, record in zip(self.task["categories"], records):
            logits = np.asarray(record[1], dtype=float)
            probabilities = np.exp(logits - logits.max())
            scores[label] = float(probabilities[1] / probabilities.sum())
        return {"title": title, "categories": [label for label, score in scores.items()
                                               if score >= self.task["threshold"]],
                "scores": scores, "threshold": self.task["threshold"],
                "max_len": self.task["max_len"], "title_chars_used": len(title),
                "body_chars_used": len(body), "device": str(self.device), "epoch": 3}
