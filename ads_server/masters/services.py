"""masters 의 규칙. 관리자 전용이며 모든 변경은 감사 로그에 남는다(위협 T23)."""
import hashlib
import hmac
import math
import re
import secrets
import uuid
from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, transaction
from django.db.models import Max, OuterRef, Q, Subquery
from django.utils import timezone

from events import api as events
from foundation.api import MAX_AMOUNT, clean_request_key, clean_text, errors, is_int, parse_uuid, parse_uuids, record_audit, require_admin, violated_constraint

from .models import (
    MAX_SHARE, MIN_FLOOR, MIN_SHARE, SEGMENT_CODE_PATTERN, SHARE_FIELDS, Audience, BidPolicy, Media, MediaDeactivation, MediaKey, Segment, SegmentVersion, Slot,
)
from .types import IssuedKey, MediaIdentity, MediaKeyView, MediaView, PolicyView, SegmentVersionView, SegmentView, SlotView


def _validated_shares(floor, base, cap, impression_share, click_share, conversion_share):
    """정책 값을 검사하고 비율을 {비율 열 이름: 값}(노출·클릭·전환)으로 돌려준다. 비율은 각각 정수 1~98, 합 100 — DB 제약과 같은 규칙을 읽기 쉬운 400 으로."""
    shares = dict(zip(SHARE_FIELDS, (impression_share, click_share, conversion_share)))
    problems = []
    for pointer, value, minimum in (("/floor", floor, MIN_FLOOR), ("/base", base, 1), ("/cap", cap, 1)):  # 기준가·상한은 하한 이상이라 하한 최소를 따른다
        if not is_int(value) or not (minimum <= value <= MAX_AMOUNT):
            problems.append({"pointer": pointer, "message": f"{minimum} 이상 {MAX_AMOUNT} 이하의 정수여야 합니다."})
    for name, value in shares.items():
        if not is_int(value) or not (MIN_SHARE <= value <= MAX_SHARE):
            problems.append({"pointer": f"/{name}", "message": f"{MIN_SHARE} 이상 {MAX_SHARE} 이하의 정수(%)여야 합니다."})
    if problems:
        raise errors.InvalidRequest(errors=problems)
    if not floor <= base <= cap:
        raise errors.InvalidRequest(errors=[{"pointer": "/base", "message": "하한 ≤ 기준가 ≤ 상한이어야 합니다."}])
    if sum(shares.values()) != 100:
        raise errors.InvalidRequest(errors=[{"pointer": "/shares", "message": f"노출·클릭·전환 비율의 합은 100이어야 합니다(지금 {sum(shares.values())})."}])
    return shares


def _policy_view(row):
    return PolicyView(
        bid_policy_id=row.bid_policy_id, slot_id=row.slot_id, policy_version=row.policy_version, floor=row.floor_amount, base=row.base_amount,
        cap=row.cap_amount, **{name: getattr(row, name) for name in SHARE_FIELDS}, changed_by=row.changed_by, changed_at=row.changed_at, reason=row.reason,
    )


def _record_policy_event(policy):
    """정책 버전 하나의 이벤트(`ad.bid_policy_changed`) — 정책 행과 **같은 트랜잭션**에서. 값만 넘긴다(events 는 모델을 모른다, 계획 docs/plans/00 D1)."""
    events.record_bid_policy_changed(
        now=policy.changed_at, slot_id=policy.slot_id, bid_policy_id=policy.bid_policy_id, policy_version=policy.policy_version,
        floor_amount=policy.floor_amount, base_amount=policy.base_amount, cap_amount=policy.cap_amount,
        impression_share=policy.impression_share, click_share=policy.click_share, conversion_share=policy.conversion_share, changed_at=policy.changed_at,
    )


def policy_versions():
    """모든 정책 버전(`PolicyView`)을 훑는다 — 이벤트 누락 점검·기대 이벤트용 읽기."""
    return (_policy_view(p) for p in BidPolicy.objects.iterator(chunk_size=2000))


def policy_stream(to_documents):
    """정책 버전을 변경 시각(`changed_at`) 창으로 읽는 흐름(구간 manifest 의 원본 구간). `to_documents(views)` 가 창 하나의 `PolicyView` 를 문서로 바꾼다.
    ponytail: changed_at 색인이 없어 창마다 표를 다 훑는다 — 관리자가 손으로 늘리는 작은 표라 그대로 둔다. 추천 적용(계획 docs/plans/00 §8.3)으로 정책 버전을 자동으로 쓰기 시작할 때 (changed_at) 색인."""
    return events.table_stream(BidPolicy.objects.all(), "changed_at", lambda rows: to_documents([_policy_view(p) for p in rows]))


def _slot_view(slot, policy):
    return SlotView(
        slot_id=slot.slot_id, media_id=slot.media_id, media_name=slot.media.name, media_is_active=slot.media.is_active, name=slot.name,
        audience=slot.audience, is_active=slot.is_active, policy=_policy_view(policy),
    )


def _media_view(media):
    return MediaView(media_id=media.media_id, name=media.name, is_active=media.is_active, created_at=media.created_at)


def _audit(actor, action, target, detail=None):
    record_audit(None, action, target=target, actor=actor, detail=detail)


def _now(now=None):
    return now if now is not None else timezone.now()


def _current_policies():
    """슬롯마다 최대 버전 행 하나. 질의 한 번으로 슬롯·매체까지 가져온다."""
    latest = BidPolicy.objects.filter(slot=OuterRef("slot")).order_by("-policy_version").values("policy_version")[:1]
    return BidPolicy.objects.select_related("slot", "slot__media").filter(policy_version=Subquery(latest))


def _get_slot(slot_id):
    try:
        return Slot.objects.select_related("media").get(pk=parse_uuid(slot_id, "게재 위치"))
    except Slot.DoesNotExist:
        raise errors.NotFound("게재 위치를 찾을 수 없습니다.")


def _get_media(media_id, rows=Media.objects):
    try:
        return rows.get(pk=parse_uuid(media_id, "매체"))
    except Media.DoesNotExist:
        raise errors.NotFound("매체를 찾을 수 없습니다.")


def create_media(actor, name):
    require_admin(actor)
    name = clean_text(name, "매체 이름", "/name")
    try:
        with transaction.atomic():
            media = Media.objects.create(name=name)
            _audit(actor, "masters.media_created", ("masters.media", media.media_id), {"name": name})
    except IntegrityError:
        raise errors.Conflict("같은 이름의 매체가 이미 있습니다.")
    return _media_view(media)


def list_media(actor):
    """관리자 화면용 매체 목록(비활성 포함, 이름순)."""
    require_admin(actor)
    return [_media_view(m) for m in Media.objects.order_by("name")]


def get_media(actor, media_id):
    require_admin(actor)
    return _media_view(_get_media(media_id))


def set_media_active(actor, media_id, active, now=None):
    require_admin(actor)
    media_id = parse_uuid(media_id, "매체")
    with transaction.atomic():
        # 실제로 켜져 있던 매체를 끌 때만 비활성화 기록을 더한다(조건부 UPDATE 라 동시에 꺼도 한 줄)
        if not active and Media.objects.filter(pk=media_id, is_active=True).update(is_active=False):
            MediaDeactivation.objects.create(media_id=media_id, deactivated_at=_now(now))
        elif not Media.objects.filter(pk=media_id).update(is_active=bool(active)):
            raise errors.NotFound("매체를 찾을 수 없습니다.")
        _audit(actor, "masters.media_active_changed", ("masters.media", media_id), {"active": bool(active)})


def first_deactivation(media_id, after, until):
    """`after` 보다 뒤, `until` 까지(포함) 그 매체를 끈 가장 이른 시각. 없으면 `None`. 반응 판정의 `media-period` 규칙이 쓴다(결정 시각 → 접수 시각)."""
    return MediaDeactivation.objects.filter(media_id=media_id, deactivated_at__gt=after, deactivated_at__lte=until).order_by("deactivated_at").values_list("deactivated_at", flat=True).first()


def media_active_for_share(media_id):
    """매체 행을 **공유 잠금**(`FOR SHARE`)으로 읽은 활성 여부(없는 매체면 `None`) — 매체 생명주기 잠금(설계 §10 "매체 비활성화와 판정·결정의 순서", 6-2).

    `set_media_active` 는 이 행을 배타 잠금으로 쥔 채 비활성화 시각을 정하고 커밋한다. 일반 조회는 그 커밋을 기다리지 않고 커밋 전 값을 보지만,
    공유 잠금은 커밋까지 기다린 뒤 커밋된 값을 읽는다. 잠금은 호출한 트랜잭션이 끝날 때 놓인다(판정은 지나가기만, 결정은 커밋까지 쥔다). 공유 잠금끼리는 서로 막지 않는다.
    ORM 에 `FOR SHARE` 가 없어 ORM 이 만든 조회에 잠금 절만 붙인다(테이블 이름·값 변환은 ORM 그대로).
    """
    sql, params = Media.objects.filter(pk=parse_uuid(media_id, "매체")).values_list("is_active", flat=True).query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute(f"{sql} FOR SHARE", params)
        row = cursor.fetchone()
    return None if row is None else bool(row[0])


# 토큰 하나를 쓴다. 같은 (매체, 경로군)의 동시 요청은 충돌 행의 잠금으로 줄을 서고, 늦은 쪽은 앞 갱신이 끝난 행으로 다시 계산한다(버스트를 넘겨 통과시키지 않는다).
# SET 의 식은 모두 갱신 전 행(b)을 본다 — tokens·granted 가 같은 충전값(refill.r)으로 계산된다. 시각은 GREATEST 로 뒤로 가지 않는다.
_BUCKET_UPSERT = """
INSERT INTO masters_media_rate_bucket AS b (bucket_id, media_id, route_group, tokens, refilled_at, granted)
VALUES (%(id)s, %(media)s, %(group)s, %(burst)s - 1, %(now)s, true)
ON CONFLICT (media_id, route_group) DO UPDATE SET
    (tokens, granted) = (
        SELECT CASE WHEN refill.r >= 1 THEN refill.r - 1 ELSE refill.r END, refill.r >= 1
        FROM (SELECT LEAST(%(burst)s, b.tokens + GREATEST(0, EXTRACT(EPOCH FROM EXCLUDED.refilled_at - b.refilled_at)::float8) * %(rate)s) AS r) AS refill
    ),
    refilled_at = GREATEST(b.refilled_at, EXCLUDED.refilled_at)
RETURNING granted, tokens
"""


def take_rate_token(media_id, group, rate, burst, now=None):
    """매체 API 요청 제한(설계 §10 "요청 제한 구현"): (매체, 경로군) 버킷에서 토큰 하나를 쓴다. 통과면 `None`, 막히면 다시 보낼 때까지의 **정수 초(1 이상)**.

    **트랜잭션 밖에서 부른다**(C1 데코레이터가 인증 뒤·뷰 전에): autocommit 문장 하나라 버킷 행 잠금은 그 문장 동안만이다.
    트랜잭션 안에서 부르면 그 트랜잭션이 끝날 때까지 행 잠금을 쥐어 같은 매체의 요청이 줄을 선다. 뷰가 실패해도 쓴 토큰은 돌려주지 않는다.
    시각은 앱의 벽시계다. 시계가 뒤로 가면 잠깐 더 엄격해지고 앞으로 뛰면 충전이 많아져도 버스트에서 막힌다 — 피해는 버스트 하나로 묶인다.
    """
    if not (is_int(burst) and burst >= 1) or isinstance(rate, bool) or not isinstance(rate, (int, float)) or not rate > 0:
        raise ImproperlyConfigured(f"요청 제한은 초당 토큰 > 0, 버스트는 1 이상의 정수여야 합니다: rate={rate!r}, burst={burst!r}")
    with connection.cursor() as cursor:
        cursor.execute(_BUCKET_UPSERT, {"id": uuid.uuid7(), "media": media_id, "group": group, "burst": burst, "rate": float(rate), "now": _now(now)})
        granted, tokens = cursor.fetchone()
    return None if granted else max(1, math.ceil((1.0 - tokens) / rate))


def create_slot(actor, media_id, name, floor, base, cap, reason, *, audience, impression_share, click_share, conversion_share, now=None):
    """슬롯과 최초 정책(버전 1)을 한 트랜잭션에 만든다. 따라서 모든 슬롯은 항상 현재 정책을 가진다.
    정책에는 반응 유형별 과금 비율(노출·클릭·전환 %)이 들어간다(6-10b).

    `audience`(개인/일괄)는 **만든 뒤 바꿀 수 없고 기본값이 없다** — 만드는 쪽이 늘 정한다(모델과 같다, 6-13f).
    사유는 만들 때만 비워도 된다 — 비면 "없음"(6-8c). 가격을 바꾸는 정책 변경·일괄 변경은 사유가 필수다.
    """
    require_admin(actor)
    name = clean_text(name, "구성 이름", "/name")  # 화면 용어 "구성"(6-10e) — 운영 화면의 칸 옆에 보인다
    if reason is None or isinstance(reason, str) and not reason.strip():
        reason = "없음"
    reason = clean_text(reason, "사유", "/reason", 200)
    if audience not in Audience.values:
        raise errors.InvalidRequest(errors=[{"pointer": "/audience", "message": f"{', '.join(Audience.values)} 중 하나여야 합니다."}])
    shares = _validated_shares(floor, base, cap, impression_share, click_share, conversion_share)
    try:
        with transaction.atomic():
            media = _get_media(media_id)
            slot = Slot.objects.create(media=media, name=name, audience=audience)
            policy = BidPolicy.objects.create(
                slot=slot, policy_version=1, floor_amount=floor, base_amount=base, cap_amount=cap, **shares,
                changed_by=actor.pk, changed_at=_now(now), reason=reason,
            )
            _record_policy_event(policy)
            _audit(actor, "masters.slot_created", ("masters.slot", slot.slot_id),
                   {"name": name, "audience": audience, "floor": floor, "base": base, "cap": cap, **shares, "reason": reason})
    except IntegrityError:
        raise errors.Conflict("이 매체에 같은 이름의 구성이 이미 있습니다.")
    return _slot_view(slot, policy)


def _add_version(actor, slot_id, floor, base, cap, shares, reason, expected_version, now):
    """`expected_version` 이 현재와 다르면 StaleVersion. 호출한 쪽이 바깥 트랜잭션을 연다."""
    slot = _get_slot(slot_id)
    current = BidPolicy.objects.filter(slot=slot).order_by("-policy_version").first()
    if current.policy_version != expected_version:
        raise errors.StaleVersion(f"{slot.name}: 다른 곳에서 먼저 바뀌었습니다(현재 버전 {current.policy_version}).")
    try:
        with transaction.atomic():
            policy = BidPolicy.objects.create(
                slot=slot, policy_version=current.policy_version + 1, floor_amount=floor, base_amount=base,
                cap_amount=cap, **shares, changed_by=actor.pk, changed_at=_now(now), reason=reason,
            )
    except IntegrityError:  # 같은 버전을 동시에 만든 쪽의 충돌은 유일 제약이 막는다(뒤쪽이 유일 색인에서 앞쪽 커밋을 기다렸다가 충돌한다 — DB-1c 에서 동시 시험)
        raise errors.StaleVersion()
    _record_policy_event(policy)  # 저장점 뒤, 호출한 쪽의 바깥 트랜잭션 안
    _audit(actor, "masters.policy_added", ("masters.slot", slot.slot_id),
           {"version": policy.policy_version, "floor": floor, "base": base, "cap": cap, **shares, "reason": reason,
            "previous": {"floor": current.floor_amount, "base": current.base_amount, "cap": current.cap_amount,
                         **{name: getattr(current, name) for name in SHARE_FIELDS}}})
    return _policy_view(policy)


def add_policy_version(actor, slot_id, floor, base, cap, reason, expected_version, *, impression_share, click_share, conversion_share, now=None):
    require_admin(actor)
    reason = clean_text(reason, "사유", "/reason", 200)
    shares = _validated_shares(floor, base, cap, impression_share, click_share, conversion_share)
    with transaction.atomic():
        return _add_version(actor, slot_id, floor, base, cap, shares, reason, expected_version, now)


def add_policy_version_bulk(actor, expected_versions, floor, base, cap, reason, *, impression_share, click_share, conversion_share, now=None):
    """같은 값(비율 포함)을 여러 슬롯에 새 버전으로. `expected_versions` = {slot_id: 본 버전}. 하나라도 어긋나면 전부 취소."""
    require_admin(actor)
    reason = clean_text(reason, "사유", "/reason", 200)
    shares = _validated_shares(floor, base, cap, impression_share, click_share, conversion_share)
    if not expected_versions:
        raise errors.InvalidRequest(errors=[{"pointer": "/slots", "message": "슬롯을 하나 이상 고르세요."}])
    with transaction.atomic():
        return [
            _add_version(actor, slot_id, floor, base, cap, shares, reason, version, now)
            for slot_id, version in expected_versions.items()
        ]


def list_policy_versions(actor, slot_id):
    """슬롯의 입찰 정책 이력(최신 버전부터). 관리자 화면용."""
    require_admin(actor)
    slot = _get_slot(slot_id)
    return [_policy_view(p) for p in BidPolicy.objects.filter(slot=slot).order_by("-policy_version")]


def set_slot_active(actor, slot_id, active):
    require_admin(actor)
    slot_id = parse_uuid(slot_id, "게재 위치")
    with transaction.atomic():
        if not Slot.objects.filter(pk=slot_id).update(is_active=bool(active)):
            raise errors.NotFound("게재 위치를 찾을 수 없습니다.")
        _audit(actor, "masters.slot_active_changed", ("masters.slot", slot_id), {"active": bool(active)})


def get_slot(slot_id):
    try:
        row = _current_policies().filter(slot_id=parse_uuid(slot_id, "게재 위치")).get()
    except BidPolicy.DoesNotExist:
        raise errors.NotFound("게재 위치를 찾을 수 없습니다.")
    return _slot_view(row.slot, row)


def slot_names(slot_ids):
    """슬롯 이름(**표시용**, 바뀔 수 있다)을 질의 한 번으로. 없는·형식이 틀린 ID 는 결과에 없다."""
    return dict(Slot.objects.filter(pk__in=parse_uuids(slot_ids)).values_list("pk", "name"))


def list_slots(active_only=True, media_id=None):
    qs = _current_policies()
    if active_only:
        qs = qs.filter(slot__is_active=True, slot__media__is_active=True)
    if media_id is not None:
        qs = qs.filter(slot__media_id=parse_uuid(media_id, "매체"))
    return [_slot_view(row.slot, row) for row in qs.order_by("slot__media__name", "slot__name")]


def get_slots_with_policy(slot_ids):
    """여러 슬롯의 현재 정책을 **질의 한 번**으로(결정 조립용). 없는·형식이 틀린 ID 는 결과에 없다."""
    rows = _current_policies().filter(slot_id__in=parse_uuids(slot_ids))
    return {row.slot_id: _slot_view(row.slot, row) for row in rows}


# ---------------------------------------------------------------- 대상 그룹 (설계 §10 "대상 그룹 타기팅" ②, DRD M7·M8)

_SEGMENT_CODE = re.compile(SEGMENT_CODE_PATTERN)


def _segment_version_view(v, segment):
    return SegmentVersionView(
        segment_version_id=v.segment_version_id, segment_id=segment.segment_id, media_id=segment.media_id, code=segment.code,
        version=v.version, name=v.name, is_active=v.is_active, changed_by=v.changed_by, changed_at=v.changed_at,
    )


def _add_segment_version(actor, segment, version, now):
    """판 한 줄(그룹 행의 지금 상태 전체)과 그 이벤트(`ad.segment_changed`) — 그룹 행과 **같은 트랜잭션**에서. 값만 넘긴다(계획 docs/plans/00 D1)."""
    v = SegmentVersion.objects.create(segment=segment, version=version, name=segment.name, is_active=segment.is_active, changed_by=actor.pk, changed_at=_now(now))
    events.record_segment_changed(
        now=v.changed_at, segment_id=segment.segment_id, segment_version_id=v.segment_version_id, version=v.version, media_id=segment.media_id,
        code=segment.code, name=v.name, is_active=v.is_active, changed_at=v.changed_at,
    )
    return v


def _segment_view(segment, version):
    return SegmentView(segment_id=segment.segment_id, media_id=segment.media_id, code=segment.code, name=segment.name, is_active=segment.is_active,
                       version=version, created_at=segment.created_at)


def create_segment(actor, media_id, code, name, now=None):
    """대상 그룹 등록(판 1). 코드는 매체가 C1 `segment` 로 보내는 값 그대로이고 바꿀 수 없다 — 공백을 다듬지 않는다(다듬으면 매체가 보내는 값과 갈린다)."""
    require_admin(actor)
    if not isinstance(code, str) or not _SEGMENT_CODE.fullmatch(code):
        raise errors.InvalidRequest(errors=[{"pointer": "/code", "message": "코드는 영문 소문자로 시작하고 영문 소문자·숫자·하이픈(-)만 쓰는 1~32자여야 합니다."}])
    name = clean_text(name, "그룹 이름", "/name")
    try:
        with transaction.atomic():
            media = _get_media(media_id)
            segment = Segment.objects.create(media=media, code=code, name=name, created_at=_now(now))
            _add_segment_version(actor, segment, 1, segment.created_at)
            _audit(actor, "masters.segment_created", ("masters.segment", segment.segment_id), {"media_id": str(media.media_id), "code": code, "name": name})
    except IntegrityError as error:
        if violated_constraint(error) != "masters_segment_code_per_media":  # 같은 블록의 판·감사·outbox 결함을 코드 충돌로 가리지 않는다(plans/00 §11 create_slot 과 같은 결함 모양)
            raise
        raise errors.Conflict("이 매체에 같은 코드의 대상 그룹이 이미 있습니다.")
    return _segment_view(segment, 1)


def update_segment(actor, segment_id, expected_version, *, name=None, is_active=None, now=None):
    """이름 바꾸기·보관(`is_active=False`)·되살리기(`True`). 바뀐 것이 있으면 그룹 행을 고치고 새 판 한 줄을 더한다(같은 트랜잭션).
    `expected_version` 은 화면이 본 판 — 그 사이 바뀌었으면 거절한다. 그룹 행 하나만 잠근다(설계 §10 "대상 그룹 타기팅" ⑧). 바뀐 것이 없으면 판을 쓰지 않는다."""
    require_admin(actor)
    if name is not None:
        name = clean_text(name, "그룹 이름", "/name")
    if is_active is not None and not isinstance(is_active, bool):
        raise errors.InvalidRequest(errors=[{"pointer": "/is_active", "message": "참·거짓이어야 합니다."}])
    with transaction.atomic():
        try:
            segment = Segment.objects.select_for_update().get(pk=parse_uuid(segment_id, "대상 그룹"))
        except Segment.DoesNotExist:
            raise errors.NotFound("대상 그룹을 찾을 수 없습니다.")
        current = segment.versions.order_by("-version").values_list("version", flat=True).first()
        if current != expected_version:
            raise errors.StaleVersion(f"{segment.code}: 다른 곳에서 먼저 바뀌었습니다(현재 판 {current}).")
        before = {"name": segment.name, "is_active": segment.is_active}
        segment.name = before["name"] if name is None else name
        segment.is_active = before["is_active"] if is_active is None else is_active
        if {"name": segment.name, "is_active": segment.is_active} == before:
            return _segment_view(segment, current)
        segment.save(update_fields=["name", "is_active"])
        _add_segment_version(actor, segment, current + 1, now)
        _audit(actor, "masters.segment_changed", ("masters.segment", segment.segment_id),
               {"version": current + 1, "name": segment.name, "is_active": segment.is_active, "previous": before})
    return _segment_view(segment, current + 1)


def list_segments(actor, media_id):
    """매체의 대상 그룹(보관 포함, 코드순)과 각각의 현재 판. 운영 화면용, 질의 한 번."""
    require_admin(actor)
    rows = Segment.objects.filter(media_id=parse_uuid(media_id, "매체")).annotate(version=Max("versions__version")).order_by("code")
    return [_segment_view(s, s.version) for s in rows]


def segment_versions():
    """모든 대상 그룹 판(`SegmentVersionView`)을 훑는다 — 이벤트 누락 점검·기대 이벤트용 읽기."""
    return (_segment_version_view(v, v.segment) for v in SegmentVersion.objects.select_related("segment").iterator(chunk_size=2000))


def segment_stream(to_documents):
    """대상 그룹 판을 변경 시각(`changed_at`) 창으로 읽는 흐름(구간 manifest 의 원본 구간). `to_documents(views)` 가 창 하나의 `SegmentVersionView` 를 문서로 바꾼다.
    ponytail: changed_at 색인이 없어 창마다 표를 다 훑는다 — 관리자가 손으로 늘리는 작은 표(입찰 정책 `policy_stream` 과 같다). 자동으로 쓰기 시작하면 (changed_at) 색인."""
    return events.table_stream(SegmentVersion.objects.select_related("segment"), "changed_at",
                               lambda rows: to_documents([_segment_version_view(v, v.segment) for v in rows]))


# ---------------------------------------------------------------- 매체 키 (C1 인증)

KEY_RE = re.compile(r"[0-9a-f]{32}\.[A-Za-z0-9_-]{43}")  # `<key_id hex>.<token_urlsafe(32)>`
MAX_GRACE = timedelta(days=30)
_DUMMY_HASH = hashlib.sha256(b"no such key").hexdigest()  # 없는 키도 같은 비교를 하게 해 응답 시간으로 존재 여부를 알 수 없게 한다


def _hash_secret(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _key_active(k, now):
    """폐기되지 않았고 만료 시각 전이다(만료 시각에 닿으면 무효). 목록 표시와 인증이 같은 판정을 쓴다."""
    return k.revoked_at is None and (k.expires_at is None or k.expires_at > now)


def _key_view(k, now):
    return MediaKeyView(
        key_id=k.key_id, media_id=k.media_id, label=k.label, created_at=k.created_at, expires_at=k.expires_at,
        revoked_at=k.revoked_at, active=_key_active(k, now),
    )


def _issue(actor, media, label, now, action, extra=None, request_key=None):
    secret = secrets.token_urlsafe(32)
    row = MediaKey.objects.create(media=media, secret_hash=_hash_secret(secret), label=label, created_at=now, request_key=request_key)
    # 감사 로그에는 키 식별자만 남긴다. 키(평문)·해시는 기록하지 않는다
    _audit(actor, action, ("masters.media_key", row.key_id), {"media_id": str(media.media_id), "label": label, **(extra or {})})
    return IssuedKey(key_id=row.key_id, media_id=media.media_id, key=f"{row.key_id.hex}.{secret}", created_at=now)


def _media_for_key_admin(media_id):
    """키를 바꾸는 동작(발급·교체)은 **매체 행을 잠근다** — 같은 매체의 교체가 줄을 서서, 뒤의 교체가 앞의 교체가 만든 키까지 보고 만료시킨다(외부 검토 3-1d)."""
    return _get_media(media_id, Media.objects.select_for_update())


def _issue_once(request_key, work):
    """`work()`(발급·교체)를 한 트랜잭션으로. **같은 요청 키는 한 번만**: 키 행의 유일 제약이 두 번째 INSERT 를 막고(동시에 오면 첫 번째의 커밋을 기다렸다가),
    트랜잭션 전체가 되돌려진다(교체의 기존 키 만료도). 두 번 누름·동시 제출·발급 뒤 응답이 실패한 재시도 모두 새 키를 만들지 않는다.
    평문은 어디에도 저장하지 않아 **다시 보여 줄 수 없으므로** 409 로 키 식별자를 알려, 받지 못한 키를 폐기하게 한다."""
    try:
        with transaction.atomic():
            return work()
    except IntegrityError:
        done = MediaKey.objects.filter(request_key=request_key).first() if request_key is not None else None
        if done is None:
            raise
        raise errors.Conflict(f"이미 처리한 발급 요청입니다(키 {done.key_id}). 평문은 다시 볼 수 없습니다 — 키를 받지 못했다면 그 키를 폐기하고 새로 발급하세요.")


def issue_media_key(actor, media_id, label="", now=None, request_key=None):
    """새 키를 발급한다. 평문은 반환값에만 있다. 기존 키는 그대로 유효하다. `request_key` 는 `_issue_once` 참고."""
    require_admin(actor)
    now = _now(now)
    label = clean_text(label, "라벨", "/label") if label else ""
    request_key = clean_request_key(request_key)

    def work():
        return _issue(actor, _media_for_key_admin(media_id), label, now, "masters.media_key_issued", request_key=request_key)
    return _issue_once(request_key, work)


def rotate_media_key(actor, media_id, grace_seconds, label="", now=None, request_key=None):
    """새 키를 발급하고 **기존 유효 키는 유예 시간 뒤에 만료**시킨다(그동안 두 키가 모두 유효 — 무중단 교체).

    `grace_seconds` 는 0 이상 30일 이하의 정수. 0 이면 기존 키가 바로 만료된다. 이미 더 일찍 만료될 키는 그대로 둔다. `request_key` 는 `_issue_once` 참고.
    """
    require_admin(actor)
    now = _now(now)
    if not is_int(grace_seconds) or not 0 <= grace_seconds <= MAX_GRACE.total_seconds():
        raise errors.InvalidRequest(errors=[{"pointer": "/grace_seconds", "message": "0 이상 30일(초) 이하의 정수여야 합니다."}])
    label = clean_text(label, "라벨", "/label") if label else ""
    request_key = clean_request_key(request_key)

    def work():
        media = _media_for_key_admin(media_id)
        deadline = now + timedelta(seconds=grace_seconds)
        older = MediaKey.objects.filter(media=media, revoked_at__isnull=True).exclude(expires_at__lte=now)
        previous = [str(k) for k in older.values_list("key_id", flat=True)]
        # 더 일찍 만료될 키는 그대로 둔다 — 조건을 UPDATE 에 둬서 DB 의 지금 값으로 판단한다(파이썬에서 읽은 값으로 판단하면 그 사이 앞당겨진 만료를 되돌린다)
        older.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=deadline)).update(expires_at=deadline)
        return _issue(actor, media, label, now, "masters.media_key_rotated", {"grace_seconds": grace_seconds, "previous": previous},
                      request_key=request_key)
    return _issue_once(request_key, work)


def revoke_media_key(actor, key_id, now=None):
    """키를 즉시 폐기한다(유출 대응). 이미 폐기된 키는 변화 없음."""
    require_admin(actor)
    now = _now(now)
    with transaction.atomic():
        try:
            k = MediaKey.objects.get(key_id=parse_uuid(key_id, "키"))
        except MediaKey.DoesNotExist:
            raise errors.NotFound("키를 찾을 수 없습니다.")
        if MediaKey.objects.filter(pk=k.pk, revoked_at__isnull=True).update(revoked_at=now):  # 조건은 DB 의 지금 값으로 — 동시에 두 번 폐기해도 한 번만 바꾸고 한 번만 감사한다(리팩터 감사 D6)
            _audit(actor, "masters.media_key_revoked", ("masters.media_key", k.key_id), {"media_id": str(k.media_id)})


def list_media_keys(actor, media_id, now=None):
    require_admin(actor)
    now = _now(now)
    return [_key_view(k, now) for k in MediaKey.objects.filter(media_id=parse_uuid(media_id, "매체")).order_by("-created_at")]


def authenticate_media_key(raw_key, now=None):
    """`X-Media-Key` 값을 검증한다. 성공하면 `MediaIdentity`, 아니면 `None`(호출하는 쪽이 401).

    형식이 틀린 값은 DB 를 보지 않고 거절한다. 형식이 맞으면 `key_id` 로 행을 찾고 비밀의 해시를 **상수시간으로 비교**한다
    (없는 키도 더미 해시와 같은 비교를 한다). 폐기·만료된 키는 거절한다. 매체가 비활성이어도 신원은 돌려준다(403 은 호출하는 쪽의 몫).
    """
    now = _now(now)
    if not isinstance(raw_key, str) or not KEY_RE.fullmatch(raw_key):
        return None
    key_hex, secret = raw_key.split(".", 1)
    row = MediaKey.objects.select_related("media").filter(key_id=uuid.UUID(key_hex)).first()
    expected = row.secret_hash if row is not None else _DUMMY_HASH
    matches = hmac.compare_digest(_hash_secret(secret), expected)
    if row is None or not matches or not _key_active(row, now):
        return None
    return MediaIdentity(media_id=row.media_id, key_id=row.key_id, media_is_active=row.media.is_active)
