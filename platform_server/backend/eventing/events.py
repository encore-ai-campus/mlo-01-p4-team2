"""Business records captured by PostgreSQL CDC; no application Kafka publisher."""
import uuid
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from accounts.models import Profile
from .models import PlatformSession, ContentOpenRequestLog

SESSION_KEY = "platform_session_uuid"


def get_profile(user):
    return Profile.objects.get_or_create(user=user, defaults={"nickname": user.get_username()[:20]})[0]


def start_platform_session(user, session, *, now=None):
    now = now or timezone.now()
    session_uuid = uuid.uuid4()
    with transaction.atomic():
        PlatformSession.objects.create(session_uuid=session_uuid, user=user, started_at=now, last_seen_at=now)
        session[SESSION_KEY] = str(session_uuid)
    return str(session_uuid)


def ensure_platform_session(user, session, *, now=None, touch=True):
    """Attach a DRD session UUID to a legacy authenticated Django session if needed."""
    now = now or timezone.now()
    raw_session_uuid = session.get(SESSION_KEY)
    try:
        session_uuid = uuid.UUID(str(raw_session_uuid))
    except (ValueError, TypeError, AttributeError):
        session_uuid = None
    if session_uuid is not None:
        record = PlatformSession.objects.filter(session_uuid=session_uuid, user=user).first()
        if record and record.ended_at is None:
            if touch and (now - record.last_seen_at).total_seconds() >= 15:
                PlatformSession.objects.filter(
                    session_uuid=session_uuid, ended_at__isnull=True, last_seen_at=record.last_seen_at
                ).update(last_seen_at=now)
            return str(session_uuid)
    with transaction.atomic():
        session_uuid = start_platform_session(user, session, now=now)
        if session.session_key:
            session.save()
    return session_uuid


def end_platform_session(user, session_data, end_reason, *, now=None):
    now = now or timezone.now()
    raw_session_uuid = session_data.get(SESSION_KEY) if hasattr(session_data, "get") else None
    try:
        session_uuid = uuid.UUID(str(raw_session_uuid))
    except (ValueError, TypeError, AttributeError):
        return False
    with transaction.atomic():
        record = PlatformSession.objects.select_for_update().filter(
            session_uuid=session_uuid, user=user, ended_at__isnull=True
        ).first()
        if record is None:
            return False
        record.ended_at = now
        record.end_reason = end_reason
        record.save(update_fields=["ended_at", "end_reason"])
    return True


def touch_platform_session(session_uuid, *, now=None):
    now = now or timezone.now()
    try:
        session_uuid = uuid.UUID(str(session_uuid))
    except (ValueError, TypeError, AttributeError):
        return False
    return bool(PlatformSession.objects.filter(
        session_uuid=session_uuid, ended_at__isnull=True, last_seen_at__lt=now - timedelta(seconds=15)
    ).update(last_seen_at=now))


def record_chat(user, session_uuid, text, floor_code):
    from datetime import timedelta

    from .catalog import floor_uuid
    from .models import ChatMessage, MapFloor

    now = timezone.now()
    with transaction.atomic():
        try:
            floor = MapFloor.objects.get(floor_uuid=floor_uuid(floor_code))
        except MapFloor.DoesNotExist:
            floor = None
        message = ChatMessage.objects.create(
            message_uuid=uuid.uuid4(),
            user=user,
            channel_code="GLOBAL",
            body=text,
            floor=floor,
            sent_at=now,
            expires_at=now + timedelta(days=30),
        )
    return str(message.message_uuid)


def record_floor_transfer(user, session_uuid, from_floor_code, to_floor_code, position, observed_at=None):
    from campus.positions import FLOOR_BOUNDS, save_position

    now = observed_at or timezone.now()
    if (from_floor_code not in FLOOR_BOUNDS or to_floor_code not in FLOOR_BOUNDS
            or from_floor_code == to_floor_code or not isinstance(position, dict)
            or position.get("floor") != to_floor_code):
        return False
    with transaction.atomic():
        if not save_position(user, position, now, session_uuid=session_uuid):
            return False
    return True


def record_content_open(user, session_uuid, content):
    profile = get_profile(user)
    return ContentOpenRequestLog.objects.create(
        user=user, platform_session_id=session_uuid, content=content,
        class_group_id=profile.cohort_id, category_id=content.category_id,
        zone_id=content.zone_id, domain=content.category.domain, content_kind=content.content_kind,
    )
