"""feat;#17 게임의 로그인 프로필과 기존 광고 요청 모듈을 연결한다."""

import hashlib
import hmac
from datetime import timezone as utc_timezone
from uuid import UUID

from django.apps import apps
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from ..api.config import AdsConfig
from ..decisions import store as decision_store
from ..impressions import store as impression_store
from ..impressions.service import queue_impression
from ..clicks import click_target


@transaction.non_atomic_requests
@never_cache
@login_required
@require_POST
def report_click(request, decision_id):
    """로그인 계정에 발급한 광고를 확인한 뒤 새 탭을 광고 서버로 이동한다."""
    from ..models import IssuedDecision

    try:
        owner_id = _owner_id(request.user)
        if owner_id is None:
            raise ValueError('로그인 프로필을 확인해 주세요.')
        target = click_target(request.POST.get('token', ''), decision_id, owner_id)
        known = IssuedDecision.objects.filter(pk=decision_id, has_ad=True).filter(
            Q(audience='shared') | Q(audience='individual', owner_id=owner_id),
        ).exists()
        if not known:
            return HttpResponse('광고를 확인할 수 없습니다. 게임 화면에서 광고를 다시 눌러 주세요.',
                                status=404, content_type='text/plain; charset=utf-8')
    except (signing.BadSignature, ValueError):
        return HttpResponse('광고 링크를 확인할 수 없습니다. 게임 화면에서 광고를 다시 눌러 주세요.',
                            status=400, content_type='text/plain; charset=utf-8')
    except DatabaseError:
        return HttpResponse('광고를 확인하지 못했습니다. 게임 화면에서 광고를 다시 눌러 주세요.',
                            status=503, content_type='text/plain; charset=utf-8')
    return HttpResponseRedirect(target, status=303, headers={'Referrer-Policy': 'no-referrer'})


@transaction.non_atomic_requests
@never_cache
@login_required
@require_POST
def report_impression(request, decision_id):
    """노출 알림을 최초 한 번 대기로 저장하고 바로 답한다. 광고 서버로는 백그라운드 작업자가 보낸다."""
    if not apps.is_installed("ad_bridge"):
        return JsonResponse({
            "error": "ads_storage_not_configured", "message": "ad_bridge 앱 등록과 마이그레이션이 필요합니다.",
        }, status=503)
    try:
        owner_id = _owner_id(request.user)
        if owner_id is None:
            return JsonResponse({
                "error": "profile_uuid_missing", "message": "게임 프로필의 사용자 UUID가 없습니다.",
            }, status=409)
        # 브라우저의 사용자·반응 ID·과거 시각을 쓰지 않고 서버가 알림을 확인한 시각을 고정한다.
        occurred_at = timezone.now().astimezone(utc_timezone.utc).isoformat().replace("+00:00", "Z")
        viewer = {}
        # 가명 비밀은 옛 공유 결정에서만 필요하다. 개인 결정은 광고 설정을 읽지 않는다.
        if decision_store.is_shared_decision(decision_id):
            viewer = {"subject_id": _subject_id(owner_id, subject_secret=AdsConfig.from_env().subject_secret)}
        _, created = queue_impression(
            store=impression_store, decision_id=decision_id, owner_id=owner_id, occurred_at=occurred_at, **viewer,
        )
    except impression_store.DecisionNotFound:
        return JsonResponse({
            "error": "decision_not_found", "message": "이 사용자에게 발급한 광고 결정을 찾을 수 없습니다.",
        }, status=404)
    except impression_store.NoAdForDecision:
        return JsonResponse({
            "error": "no_ad_to_report", "message": "광고가 없는 결정은 노출 보고할 수 없습니다.",
        }, status=409)
    except ValueError:
        return JsonResponse({
            "error": "ads_invalid_config", "message": "광고 설정 또는 게임 사용자 UUID를 확인해 주세요.",
        }, status=503)
    except DatabaseError:
        return JsonResponse({
            "error": "ads_storage_unavailable", "message": "노출 기록을 저장하지 못했습니다.",
        }, status=503)
    # 접수는 "보낼 대기에 넣음"이다. 광고 서버 접수·인정은 작업자가 처리하고 브라우저에는 알리지 않는다.
    return JsonResponse({"state": "queued", "duplicate": not created}, status=202)


def _owner_id(user) -> UUID | None:
    """로그인 프로필의 내부 소유자 UUID를 읽고 원문 없는 오류로 검증한다."""
    value = user
    for attribute in getattr(settings, "ADS_SUBJECT_ID_ATTRIBUTE", "campus_profile.user_uuid").split("."):
        value = getattr(value, attribute, None)
        if value is None:
            break
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("게임 프로필의 사용자 UUID가 올바르지 않습니다.") from None


def _subject_id(user_uuid: UUID, *, subject_secret: str) -> UUID:
    """검증된 내부 UUID에서 결정 발급에 사용할 광고 전용 가명을 생성한다."""
    # UUID 표기를 정규화하고 계약의 앞 16바이트를 그대로 사용한다. 버전 비트는 바꾸지 않는다.
    digest = hmac.new(
        subject_secret.encode("utf-8"),
        f"ads-subject/v1:{user_uuid}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return UUID(bytes=digest[:16])


def _result_payload(record):
    # 서버 내부 조회 결과에서도 브라우저에 공개할 필드만 고른다.
    return {
        "decision_id": record.decision_id, "reaction_id": record.reaction_id,
        "delivery_state": record.delivery_state, "request_id": record.request_id,
        "attempts": record.attempts, "last_error": record.last_error,
    }


@transaction.non_atomic_requests
@never_cache
@login_required
@require_POST
def reaction_result(request, decision_id):
    """저장된 전송 상태만 돌려준다. 요청 안에서 광고 서버를 부르지 않는다(AB-1 §4.5)."""
    if not apps.is_installed("ad_bridge"):
        return JsonResponse({"error": "ads_storage_not_configured", "message": "ad_bridge 앱 등록과 마이그레이션이 필요합니다."}, status=503)
    try:
        owner_id = _owner_id(request.user)
        if owner_id is None:
            return JsonResponse({"error": "profile_uuid_missing", "message": "게임 사용자 UUID가 없습니다."}, status=409)
        record = impression_store.get_impression(decision_id=decision_id, owner_id=owner_id)
    except impression_store.DecisionNotFound:
        return JsonResponse({"error": "decision_not_found", "message": "사용자의 노출 기록이 없습니다."}, status=404)
    except DatabaseError:
        return JsonResponse({"error": "ads_storage_unavailable", "message": "노출 기록을 확인하지 못했습니다."}, status=503)
    except ValueError:
        return JsonResponse({"error": "ads_invalid_config", "message": "게임 사용자 UUID를 확인해 주세요."}, status=503)
    return JsonResponse(_result_payload(record), status=200 if record.delivery_state == "ACKNOWLEDGED" else 202)
