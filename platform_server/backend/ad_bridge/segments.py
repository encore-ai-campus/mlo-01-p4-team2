"""결정 요청에 실을 사용자 과정 코드(segment)를 고른다.

설정이 틀려도 예외를 내지 않는다(틀린 항목은 버림). 과정을 읽는 DB 조회 오류는 그대로 올라가 뷰가 503으로 받는다.
"""

from functools import lru_cache
import logging

from django.conf import settings

from .api.client import SEGMENT_FORMAT

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def registered_codes(raw: str) -> frozenset:
    """ADS_SEGMENT_CODES 원문 → 형식에 맞는 코드 집합. 틀린 항목은 버리고 원문마다 경고를 한 번만 남긴다."""
    items = [item.strip() for item in str(raw).split(",")]
    valid = frozenset(item for item in items if item and SEGMENT_FORMAT.fullmatch(item))
    invalid = [item for item in items if item and not SEGMENT_FORMAT.fullmatch(item)]
    if invalid:
        # 과정 코드는 비밀이 아니다. 고칠 값을 바로 찾을 수 있게 그대로 남긴다.
        logger.warning("ADS_SEGMENT_CODES의 형식이 틀린 항목 %d개를 버렸습니다: %s",
                       len(invalid), ", ".join(invalid))
    return valid


def segment_for(user) -> str | None:
    """로그인 사용자의 과정이 등록된 코드 목록에 있을 때만 그 코드를 준다. 없으면 None(키를 싣지 않음)."""
    profile = getattr(user, "campus_profile", None)
    course = getattr(profile, "course", "") if profile is not None else ""
    codes = registered_codes(getattr(settings, "ADS_SEGMENT_CODES", ""))
    return course if isinstance(course, str) and course in codes else None
