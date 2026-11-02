"""URL 접두사 → 역할 표. **접근 통제의 단일 출처**다.

새 URL 은 아래 접두사 밑에 추가하면 자동으로 보호된다. 역할 접두사 밖의 URL 은
공개(@login_not_required)이거나 "로그인만 필요"로 명시해야 하며, tests/ops/test_access.py 의 URL 전수 점검이 이를 강제한다.
"""
from django.core.exceptions import ImproperlyConfigured

ADVERTISER_PREFIX = "/advertiser/"
# 다른 화면·API 가 쓰는 첫 경로 조각(config/urls.py — 시험이 URL 설정 전체와 대조한다). 관리자 경로가 이 밑에 오면
# CSRF 예외(`CSRF_EXEMPT_PREFIXES` = /api/media/)에 들어가거나, 다른 경로를 가리거나, 역할 접두사(/advertiser/)와 겹친다.
# health: 프론트의 `/health/`(ads_frontend/config/urls.py — 관리자 경로보다 먼저 맞춘다). 두 서버는 같은 ADMIN_URL_PATH 를 쓰므로 여기서 함께 막는다.
RESERVED_FIRST_SEGMENTS = frozenset({"api", "c", "assets", "advertiser", "accounts", "static", "health"})


def validate_admin_path(path, allow_default=False):
    """ADMIN_URL_PATH: 앞 슬래시 없이 `xxxx/` 형태. 기본값 `admin/` 은 prod 에서 금지. 첫 조각은 다른 경로의 것과 겹칠 수 없다(버그 탐색 B13)."""
    if not path or path.startswith("/") or not path.endswith("/") or "//" in path:
        raise ImproperlyConfigured("ADMIN_URL_PATH 는 앞 슬래시 없이 `이름/` 형태여야 합니다.")
    if path == "admin/" and not allow_default:
        raise ImproperlyConfigured("ADMIN_URL_PATH 에 기본값 `admin/` 을 쓸 수 없습니다.")
    if path.split("/", 1)[0] in RESERVED_FIRST_SEGMENTS:
        raise ImproperlyConfigured(f"ADMIN_URL_PATH 의 첫 조각은 다른 경로와 겹칠 수 없습니다({', '.join(sorted(RESERVED_FIRST_SEGMENTS))}): {path}")
    return path


def build_access_map(admin_path):
    """각 항목: prefix, role, hide.

    hide=True  → 그 역할이 아니면 **익명 포함 누구에게나 404**(존재를 숨긴다). 관리자 화면.
    hide=False → 익명은 통과시켜 로그인으로 보내고, 로그인했지만 역할이 다르면 403. 광고주 화면.
    """
    return [
        {"prefix": "/" + admin_path, "role": "admin", "hide": True},
        {"prefix": ADVERTISER_PREFIX, "role": "advertiser", "hide": False},
    ]
