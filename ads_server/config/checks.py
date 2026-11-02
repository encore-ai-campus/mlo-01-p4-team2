"""설정의 기동 시점 검사(버그 탐색 K1, 사용자 2026-11-02 — Django 시스템 검사).

요청 제한 표·선택 정책·추가 필터·반응 규칙·과금 정책·가입 포인트·공개 주소·예산 경계는 전에는 **쓰는 순간에만** 검사해, 잘못 배포하면 매체 요청이
모두 500 이 됐다. 이제 `check`·`migrate`·`runserver`·배포 점검(`check --deploy`)이 오류로 멈춘다. gunicorn 기동만으로는 돌지 않으므로 배포 절차의
migrate·check 단계가 전제다. 쓰는 곳의 검사는 마지막 방어선으로 그대로 둔다. 여러 모듈의 이름표를 함께 보므로 조립 층에 두고, 각 모듈의 api.py 만 쓴다.
"""
import math
from urllib.parse import urlsplit

from django.conf import settings
from django.core import checks

from billing import api as billing
from engine import api as engine
from foundation.api import MAX_AMOUNT, is_int
from records import api as records
from serving_api.auth import GROUPS

BUDGET_DAY_OFFSET_RANGE = range(-12, 15)  # 실제 시간대의 UTC 오프셋 범위(-12 ~ +14). 0 = UTC 자정, 9 = KST 자정


def _rate_limits(limits):
    if not isinstance(limits, dict) or set(limits) != set(GROUPS):
        return [f"MEDIA_RATE_LIMITS 는 경로군 {sorted(GROUPS)} 를 모두, 그것만 가진 dict 여야 합니다: {limits!r}"]
    problems = []
    for group, value in limits.items():
        rate, burst = value if isinstance(value, (tuple, list)) and len(value) == 2 else (None, None)
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not (math.isfinite(rate) and rate > 0) or not (is_int(burst) and burst >= 1):
            problems.append(f"MEDIA_RATE_LIMITS[{group!r}] 는 (초당 토큰 > 0, 버스트 1 이상의 정수) 여야 합니다: {value!r}")
    return problems


def _names():
    problems = []
    for setting, resolve in (
        ("ADS_SELECTION_POLICY", engine.get_policy),
        ("ADS_ENGINE_EXTRA_FILTERS", lambda names: [engine.get_filter(name) for name in names]),
        ("REACTION_RULES", records.resolve_rules),
        ("BILLING_CHARGE_POLICY", billing.get_charge_policy),
    ):
        try:
            resolve(getattr(settings, setting))
        except (engine.UnknownName, records.UnknownRule, billing.UnknownChargePolicy, TypeError) as error:  # TypeError: 사전 키가 될 수 없거나 돌 수 없는 값
            problems.append(f"{setting}: {error}")
    return problems


def _values():
    problems = []
    points = settings.SIGNUP_GRANT_POINTS
    if not (is_int(points) and 0 <= points <= MAX_AMOUNT):
        problems.append(f"SIGNUP_GRANT_POINTS 는 0 이상 {MAX_AMOUNT} 이하의 정수여야 합니다: {points!r}")
    url = settings.ASSET_PUBLIC_BASE_URL
    try:
        parts = urlsplit(url) if isinstance(url, str) else None
    except ValueError:  # 대괄호가 맞지 않는 호스트(`http://[::1`) — 예외가 아니라 이 오류로 알린다
        parts = None
    if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:  # 결정 응답의 image_url·click_url 이 계약의 URI 형식이 되게
        problems.append(f"ASSET_PUBLIC_BASE_URL 은 http(s)://호스트 형식이어야 합니다: {url!r}")
    offset = settings.BUDGET_DAY_OFFSET_HOURS
    if not (is_int(offset) and offset in BUDGET_DAY_OFFSET_RANGE):
        problems.append(f"BUDGET_DAY_OFFSET_HOURS 는 {BUDGET_DAY_OFFSET_RANGE.start} 이상 {BUDGET_DAY_OFFSET_RANGE.stop - 1} 이하의 정수여야 합니다: {offset!r}")
    return problems


@checks.register()
def check_settings(app_configs=None, **kwargs):
    return [checks.Error(text, id="config.E001") for text in _rate_limits(settings.MEDIA_RATE_LIMITS) + _names() + _values()]
