"""카테고리와 외부 입력의 최소 계약."""
import math
import re
from urllib.parse import urlsplit

CATEGORIES = (
    "Data Analysis", "Machine Learning", "LLM & RAG", "AI Agents",
    "Data Engineering", "Database & Knowledge Graph", "Cloud & MLOps", "Programming & API",
)
UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


class Failure(Exception):
    def __init__(self, code, *, request_id=None):
        self.code = code
        self.request_id = request_id if isinstance(request_id, str) and UUID_PATTERN.fullmatch(request_id) else None
        super().__init__(code)


def require_uuid(value):
    if not isinstance(value, str) or not UUID_PATTERN.fullmatch(value):
        raise Failure("INVALID_UUID")
    return value


def category_mask(scores):
    if not isinstance(scores, dict) or set(scores) != set(CATEGORIES):
        raise Failure("INVALID_CATEGORY_SCORES")
    mask = 0
    for bit, name in enumerate(CATEGORIES):
        score = scores[name]
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise Failure("INVALID_CATEGORY_SCORES")
        if not 0 <= score <= 1 or not math.isfinite(score):
            raise Failure("INVALID_CATEGORY_SCORES")
        if score >= 0.5:
            mask |= 1 << bit
    return mask


def validate_url(url):
    try:
        if not isinstance(url, str) or re.search(r"[\x00-\x20\x7f]", url):
            raise ValueError
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        _ = parsed.port
    except (ValueError, TypeError):
        raise Failure("INVALID_URL") from None
    return url
