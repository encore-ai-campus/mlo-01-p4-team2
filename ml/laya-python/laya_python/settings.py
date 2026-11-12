"""환경변수가 .env보다 우선한다. 비밀값은 표시하지 않는다."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .core import Failure, validate_url

ROOT = Path(__file__).resolve().parent.parent


def load_env(path):
    if not path.is_file():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise Failure(f"INVALID_ENV_LINE_{number}")
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise Failure(f"INVALID_ENV_LINE_{number}")
            value = value[1:-1]
        os.environ.setdefault(key, value)


def validate_ads_base(url):
    validate_url(url)
    parsed = urlsplit(url)
    if (parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.path.rstrip("/") != "/api/internal/v1"
            or parsed.query or parsed.fragment):
        raise Failure("LOCAL_ADS_MOCK_REQUIRED")
    return url.rstrip("/")


@dataclass(frozen=True)
class Settings:
    ads_base_url: str
    internal_key: str = field(repr=False)
    openai_key: str = field(repr=False)
    model_dir: Path
    jobs_dir: Path

    def __post_init__(self):
        validate_ads_base(self.ads_base_url)

    def preflight(self, *, require_openai=True):
        if not self.internal_key.strip():
            raise Failure("ADS_INTERNAL_KEY_MISSING")
        if require_openai and not self.openai_key.strip():
            raise Failure("OPENAI_API_KEY_MISSING")
        keys = (self.internal_key, self.openai_key) if require_openai else (self.internal_key,)
        if any(re.search(r"[\r\n\x00]", value) for value in keys):
            raise Failure("INVALID_API_KEY")

    @classmethod
    def read(cls, root=ROOT):
        load_env(root / ".env")
        model_dir = Path(os.getenv("LAYA_MODEL_DIR", "models/laya")).expanduser()
        if not model_dir.is_absolute():
            model_dir = root / model_dir
        return cls(
            validate_ads_base(os.getenv("ADS_API_BASE_URL", "http://127.0.0.1:8765/api/internal/v1")),
            os.getenv("ADS_INTERNAL_KEY", ""), os.getenv("OPENAI_API_KEY", ""),
            model_dir.resolve(), root / "var/jobs",
        )
