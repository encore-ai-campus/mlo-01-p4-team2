"""C8 내부 입력 API(설계 §3 "C8 Internal API", 계약 `contracts/internal.openapi.yaml`). 뷰는 얇다: 키 인증(`internal_endpoint`) → 요청 모양 검사 →
`campaigns` 호출 → 계약 모양으로 변환. 규칙(범위·검사·쓰기·감사)은 모두 `campaigns` 서비스에 있다.

| 상황 | 응답 |
|---|---|
| 키 없음·형식 오류·모르는 키·폐기 | 401 + `WWW-Authenticate: InternalKey realm="ads-internal-api"`(이유를 구분하지 않는다) |
| 키의 주인이 활성 운영자가 아님 | 403(서비스의 `require_admin`) |
| 저장소가 잠기거나 끊김·읽기 전용 — 키 조회·뷰 안의 저장 모두 | 503 + `Retry-After`(`OperationalError` 와 읽기 전용 `InternalError` 만 변환한다) |

CSRF 예외 접두사(`/api/internal/`)와 함께 쓰이며 세션을 쓰지 않아 쿠키를 만들지 않는다. 키는 로그에 남기지 않는다(공개 식별자만).
"""
import logging
import re
import uuid
from datetime import timezone as dt_timezone
from functools import wraps

from django.contrib.auth.decorators import login_not_required
from django.db import InternalError, OperationalError
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from assets import api as assets
from campaigns import api as campaigns
from events import api as events
from foundation.api import UUID_TEXT, errors, is_transient_lock_error, parse_json
from masters import api as masters

logger = logging.getLogger(__name__)

CHALLENGE = 'InternalKey realm="ads-internal-api"'
BODY_MAX = 64 * 1024  # 묶음 본문 상한(계약 C8): 항목 200개를 넉넉히 담는다
_KEY_ID = re.compile(r"[0-9a-f]{32}")
_LIMIT = re.compile(r"[0-9]{1,9}")  # ASCII 숫자만(범위는 서비스가 본다). 자릿수를 묶어 거대한 수를 int 로 바꾸지 않는다


def internal_endpoint(view):
    """C8 엔드포인트 데코레이터. 성공하면 `request.internal_client`(`InternalIdentity`)를 채운다. 인증은 본문 해석보다 먼저다."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            raw = request.headers.get("X-Internal-Key", "").strip()
            client = masters.authenticate_internal_key(raw) if raw else None
            if client is None:
                prefix = raw.split(".", 1)[0]  # 공개 식별자만 기록한다(비밀 부분은 절대 기록하지 않는다)
                logger.warning("내부 키 거절 key_id=%s", prefix if _KEY_ID.fullmatch(prefix) else "-")
                raise errors.Unauthorized("유효한 X-Internal-Key 가 필요합니다.", headers={"WWW-Authenticate": CHALLENGE})
            request.internal_client = client
            return view(request, *args, **kwargs)
        except (OperationalError, InternalError) as error:  # 서비스의 트랜잭션은 되돌려졌다(커밋 응답이 끊겼다면 이미 커밋됐을 수 있다 — 같은 묶음을 다시 보내면 unchanged)
            if isinstance(error, InternalError) and not is_transient_lock_error(error):
                raise  # 읽기 전용(25006) 밖의 내부 오류는 가리지 않는다(500)
            logger.exception("저장소 일시 장애 internal_api")
            raise errors.Unavailable("일시적으로 처리할 수 없습니다. 잠시 뒤에 다시 시도하세요.")

    return login_not_required(wrapper)  # 공개 뷰 표시(세션 로그인이 아니라 키로 인증한다)


def _utc(moment):
    """계약 `DateTimeUtc`: UTC `…Z`, 소수 초 6자리."""
    return moment.astimezone(dt_timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _profile(p):
    """지금 판 = 이벤트 페이로드와 같은 일곱 필드. 시각만 계약 형식으로 새로 만든다(빌더의 문자열을 고치지 않는다)."""
    body = events.ad_group_profile_changed_payload(ad_group_id=p.ad_group_id, version=p.version, category=p.category, weight_mlo=p.weight_mlo,
                                                   weight_aio=p.weight_aio, weight_skn=p.weight_skn, changed_at=p.changed_at)
    return {**body, "changed_at": _utc(p.changed_at)}


def _source(s):
    return {
        "ad_group_id": str(s.ad_group_id), "name": s.name, "updated_at": _utc(s.updated_at),
        "creatives": [{"creative_id": str(c.creative_id), "title": c.title, "redirect_url": c.redirect_url, "image_url": assets.asset_url(c.image_name),
                       "updated_at": _utc(c.updated_at)} for c in s.creatives],
        "profile": _profile(s.profile) if s.profile is not None else None,
    }


@require_http_methods(["GET"])
@internal_endpoint
def list_ad_groups(request):
    """① `?state=unclassified` · ③ 인자 없음. 그 밖의 질의 인자는 무시한다."""
    state, after, limit = (request.GET.get(name) for name in ("state", "after", "limit"))
    problems = []
    if state is not None and state != "unclassified":
        problems.append({"pointer": "/state", "message": "`unclassified` 만 받습니다."})
    if after is not None and not UUID_TEXT.fullmatch(after):
        problems.append({"pointer": "/after", "message": "UUID 형식이어야 합니다."})
    if limit is not None and not _LIMIT.fullmatch(limit):
        problems.append({"pointer": "/limit", "message": "1 이상 200 이하의 정수여야 합니다."})
    if problems:
        raise errors.InvalidRequest(errors=problems)
    page = campaigns.profile_sources(request.internal_client, unclassified=state is not None, after=uuid.UUID(after) if after is not None else None,
                                     limit=int(limit) if limit is not None else 100)
    return JsonResponse({"items": [_source(s) for s in page.items], "next_after": str(page.next_after) if page.next_after is not None else None})


def _batch(request, write, entry):
    """②·④ 공통: 본문 `{"items": [...]}`(64 KiB 까지) → 서비스(검사·한 트랜잭션) → 항목마다 결과. 커밋된 뒤에만 200 이 나간다.
    거절된 묶음(본문 모양·항목 검사·없는 광고의 400)은 되돌려져 감사가 없으므로 경고 한 줄을 남긴다(계획 W28) — "돌았지만 거절됨"을 "돌지 않음"과 가른다.
    입구·항목 수·오류 수만 싣고 값·광고 ID·키는 싣지 않는다. JSON 해석 실패·413·415 는 묶음까지 가지 않은 요청이라 여기 오지 않는다."""
    body = parse_json(request, max_bytes=BODY_MAX)
    try:
        problems = [] if "items" in body else [{"pointer": "/items", "message": "필수입니다."}]
        problems += [{"pointer": f"/{key}", "message": "알 수 없는 필드입니다."} for key in body if key != "items"]
        if problems:
            raise errors.InvalidRequest(errors=problems)
        results = write(request.internal_client, body["items"])
    except errors.InvalidRequest as rejected:
        items = body.get("items")
        logger.warning("묶음 거절 entry=%s items=%s errors=%d", entry, len(items) if isinstance(items, list) else "-", len(rejected.errors or ()) or 1)
        raise
    return JsonResponse({"items": [{"ad_group_id": str(r.ad_group_id), "result": r.result, "profile": _profile(r.profile)} for r in results]})


@require_http_methods(["POST"])
@internal_endpoint
def write_categories(request):
    return _batch(request, campaigns.write_categories, "categories")


@require_http_methods(["POST"])
@internal_endpoint
def write_weights(request):
    return _batch(request, campaigns.write_weights, "weights")
