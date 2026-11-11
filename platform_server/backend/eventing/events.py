"""Business records and their outbox events, written in the same transaction.

The application never publishes to Kafka. Each accepted business change also writes one immutable
row to `eventing_eventoutbox`; PostgreSQL CDC reads those INSERTs. The row identity is `outbox_uuid`
(a new UUIDv4 per event) — not `event_uuid`, which is the primary key of `event_definition`.
"""
import json
import logging
import math
import re
import uuid
from datetime import timedelta
from datetime import timezone as datetime_timezone

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import Profile
from .models import ContentOpenRequestLog, EventOutbox, PlatformSession

logger = logging.getLogger(__name__)
SESSION_KEY = "platform_session_uuid"

TOPIC_BY_TYPE = {
    "session_started": "raw.account",
    "session_ended": "raw.account",
    "chat_sent": "raw.chat",
    "post_created": "raw.board",
    "post_updated": "raw.board",
    "post_deleted": "raw.board",
    "post_read": "raw.board",
    "comment_created": "raw.comment",
    "comment_updated": "raw.comment",
    "comment_deleted": "raw.comment",
    "content_open_requested": "raw.content_usage",
    "quiz_answered": "raw.content_usage",
    "point_granted": "raw.points",
    "movement_observed": "raw.movement",
    "floor_transferred": "raw.movement",
    "event_progress_completed": "raw.event",
    "game_play_started": "raw.event",
    "game_play_ended": "raw.event",
    "manager_greeted": "raw.event",
    "manager_poked": "raw.event",
    "story_action_recorded": "raw.event",
    "learning_session_completed": "raw.event",
}
EXTRA_FIELDS = {
    "session_started": {"session_uuid"},
    "session_ended": {"session_uuid", "end_reason"},
    "chat_sent": {"message_uuid", "channel_code", "floor_uuid"},
    "post_created": {"post_uuid", "revision", "zone_uuid"},
    "post_updated": {"post_uuid", "revision", "zone_uuid"},
    "post_deleted": {"post_uuid", "revision", "zone_uuid"},
    "post_read": {"post_uuid", "revision", "zone_uuid"},
    "comment_created": {"comment_uuid", "post_uuid", "parent_comment_uuid", "revision"},
    "comment_updated": {"comment_uuid", "post_uuid", "parent_comment_uuid", "revision"},
    "comment_deleted": {"comment_uuid", "post_uuid", "parent_comment_uuid", "revision"},
    "content_open_requested": {"content_uuid", "category_uuid", "domain", "content_kind", "zone_uuid", "action"},
    "quiz_answered": {"attempt_uuid", "question_uuid", "content_uuid", "question_type", "choice_uuid", "is_correct"},
    "point_granted": {"ledger_uuid", "attempt_uuid", "delta", "balance_after"},
    "movement_observed": {"floor_uuid", "zone_uuid", "x", "y", "input_kind"},
    "floor_transferred": {"from_floor_uuid", "to_floor_uuid", "zone_uuid"},
    # event_definition is referenced as event_definition_uuid: the app already uses event_uuid (its PK) and
    # event_id (FKs, story records) — neither name appears in any event.
    "event_progress_completed": {"progress_uuid", "event_definition_uuid", "event_kind", "completion_order"},
    "game_play_started": {"play_uuid", "game_uuid", "event_definition_uuid"},
    "game_play_ended": {"play_uuid", "game_uuid", "event_definition_uuid", "status", "duration_ms"},
    "manager_greeted": {"interaction_uuid", "event_definition_uuid", "manager_code"},
    "manager_poked": {"interaction_uuid", "event_definition_uuid", "manager_code", "poke_count"},
    "story_action_recorded": {"story_record_uuid", "story_key", "action_key", "action_kind"},
    "learning_session_completed": {"learning_session_id", "subject", "question_type", "difficulty",
                                   "total_questions", "correct_count"},
}
_BASE_FIELDS = {"user_uuid", "session_uuid", "class_group_uuid"}
SCHEMA_VERSION_BY_TYPE = {"quiz_answered": "1.1"}
# Catalog and definition references may be deterministic UUIDv5 values (e.g. the CS learning catalog);
# identities minted per business row stay UUIDv4.
CATALOG_UUID_FIELDS = frozenset({
    "content_uuid", "category_uuid", "zone_uuid", "floor_uuid", "from_floor_uuid", "to_floor_uuid", "class_group_uuid",
    "event_definition_uuid", "game_uuid",
})
_CHOICES = {
    "end_reason": {"LOGOUT", "SESSION_EXPIRED"},
    "channel_code": {"GLOBAL"},
    "domain": {"LEARNING", "REST"},
    "content_kind": {"EXTERNAL_LINK", "QUIZ"},
    "action": {"OPEN_REQUESTED"},
    "input_kind": {"KEYBOARD"},
    "question_type": {"MULTIPLE_CHOICE", "FILL_BLANK"},
    "event_kind": {"MAIN", "EASTER_EGG"},
    "status": {"SUCCESS", "FAILED", "INTERRUPTED", "FINISHED"},
    "subject": {"PYTHON", "SQL", "CS"},
    "difficulty": {"BEGINNER", "INTERMEDIATE", "ADVANCED"},
}
# Counters: the smallest allowed value (all below 2**63).
_COUNTS = {"completion_order": 1, "duration_ms": 0, "poke_count": 1, "learning_session_id": 1,
           "total_questions": 1, "correct_count": 0}
# App-defined codes, not free text: format and column length only, so new codes need no new schema version.
_CODES = {
    "manager_code": re.compile(r"manager-[a-z0-9_.:-]{1,56}"),
    "story_key": re.compile(r"[a-z0-9_.:-]{1,40}"),
    "action_key": re.compile(r"[a-z0-9_.:-]{1,100}"),
    "action_kind": re.compile(r"[a-z0-9_.:-]{1,40}"),
}
# Story records the app reports: recipe steps and the mission-gating manager dialogue (not screen notices).
STORY_ACTION_KINDS = frozenset({
    "recipe_note_acquired", "recipe_recognize", "recipe_defer", "recipe_start", "recipe_topping", "recipe_finish",
    "npc_dialogue",
})


def get_profile(user):
    return Profile.objects.get_or_create(user=user, defaults={"nickname": user.get_username()[:20]})[0]


def raw_identity(user, session_uuid):
    """The three fields every event carries. The cohort is the profile's ClassGroup UUID itself."""
    profile = get_profile(user)
    return {
        "user_uuid": str(user.pk),
        "session_uuid": str(session_uuid) if session_uuid is not None else None,
        "class_group_uuid": str(profile.cohort_id),
    }


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _uuid_ok(key, value):
    try:
        parsed = uuid.UUID(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or str(parsed) != value:
        return False
    return parsed.version in (4, 5) if key in CATALOG_UUID_FIELDS else parsed.version == 4


def validate_raw_data(event_type, raw_data):
    """Reject extra fields and nested/free-text values without recording their contents."""
    valid = event_type in EXTRA_FIELDS and isinstance(raw_data, dict)
    if valid:
        valid = set(raw_data) == _BASE_FIELDS | EXTRA_FIELDS[event_type]
    nullable = {"zone_uuid", "parent_comment_uuid"}
    if event_type == "chat_sent":
        nullable.add("floor_uuid")
    if event_type == "quiz_answered" and isinstance(raw_data, dict) and raw_data.get("question_type") == "FILL_BLANK":
        nullable.add("choice_uuid")
    if event_type == "event_progress_completed":
        # An asynchronous completion (an ad impression) has no known session.
        nullable |= {"completion_order", "session_uuid"}
    if event_type in {"game_play_started", "game_play_ended"}:
        nullable |= {"event_definition_uuid", "duration_ms"}
    if valid:
        for key, value in raw_data.items():
            if value is None and key in nullable:
                continue
            if key.endswith("_uuid"):
                valid = _uuid_ok(key, value)
            elif key == "revision":
                valid = type(value) is int and value >= 1
            elif key in {"delta", "balance_after"}:
                valid = type(value) is int and (0 < value < 2**63 if key == "delta" else 0 <= value < 2**63)
            elif key in _COUNTS:
                valid = type(value) is int and _COUNTS[key] <= value < 2**63
            elif key in _CODES:
                valid = isinstance(value, str) and _CODES[key].fullmatch(value) is not None
            elif key == "is_correct":
                valid = type(value) is bool
            elif key in {"x", "y"}:
                valid = type(value) in (int, float) and value >= 0
                if valid:
                    try:
                        valid = math.isfinite(value)
                    except OverflowError:
                        valid = False
            else:
                valid = isinstance(value, str) and value in _CHOICES.get(key, set())
            if not valid:
                break
    if valid and event_type == "quiz_answered" and raw_data["question_type"] == "FILL_BLANK":
        valid = raw_data["choice_uuid"] is None
    if valid and event_type == "learning_session_completed":
        valid = raw_data["correct_count"] <= raw_data["total_questions"]
    if not valid:
        logger.error("Raw event rejected: fields or value types do not match the allowed contract")
        raise ValueError("Raw event fields or value types do not match the allowed contract")


def build_envelope(outbox_uuid, event_type, occurred_at, raw_data):
    """The C-publisher envelope, kept in payload_json: identity, manifest (schema, source, fingerprint) and raw data."""
    validate_raw_data(event_type, raw_data)
    try:
        parsed = uuid.UUID(str(outbox_uuid))
    except (ValueError, TypeError, AttributeError):
        parsed = None
    if parsed is None or parsed.version != 4:
        logger.error("Raw event rejected: event identity is not UUIDv4")
        raise ValueError("Raw event identity must be UUIDv4")
    try:
        import xxhash
    except ImportError:
        return None
    occurred_text = occurred_at.astimezone(datetime_timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    fingerprint = xxhash.xxh3_64(canonical_json(raw_data).encode("utf-8")).hexdigest().lower()
    return {
        "outbox_uuid": str(parsed),
        "raw_manifest": {
            "schema_version": SCHEMA_VERSION_BY_TYPE.get(event_type, "1.0"),
            "event_type": event_type,
            "occurred_at": occurred_text,
            "source": settings.CAMPUS_RAW_SOURCE,
            "fingerprint_algorithm": "xxh3_64",
            "fingerprint": fingerprint,
        },
        "raw_data": raw_data,
    }


def enqueue_event(event_type, user, session_uuid, attributes, *, occurred_at=None):
    """Validate the strict contract and persist one immutable outbox row in the caller's transaction."""
    if event_type not in TOPIC_BY_TYPE or not isinstance(attributes, dict) or set(attributes) != EXTRA_FIELDS[event_type]:
        logger.error("Raw event rejected: attributes do not match the allowed contract")
        raise ValueError("Raw event attributes do not match the allowed contract")
    raw_data = raw_identity(user, session_uuid)
    raw_data.update(attributes)
    validate_raw_data(event_type, raw_data)
    outbox_uuid = uuid.uuid4()
    occurred_at = occurred_at or timezone.now()
    return EventOutbox.objects.create(
        outbox_uuid=outbox_uuid,
        event_type=event_type,
        topic=TOPIC_BY_TYPE[event_type],
        message_key=raw_data["user_uuid"],
        raw_data=raw_data,
        occurred_at=occurred_at,
        payload_json=build_envelope(outbox_uuid, event_type, occurred_at, raw_data),
    )


def start_platform_session(user, session, *, now=None):
    now = now or timezone.now()
    session_uuid = uuid.uuid4()
    with transaction.atomic():
        PlatformSession.objects.create(session_uuid=session_uuid, user=user, started_at=now, last_seen_at=now)
        session[SESSION_KEY] = str(session_uuid)
        enqueue_event("session_started", user, session_uuid, {"session_uuid": str(session_uuid)}, occurred_at=now)
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
        enqueue_event(
            "session_ended", user, session_uuid,
            {"session_uuid": str(session_uuid), "end_reason": end_reason},
            occurred_at=now,
        )
        from event_story.models import GamePlay
        # Row by row (locked, still RUNNING after the wait) so each interrupted play gets its own end event.
        for play in GamePlay.objects.select_for_update().filter(session=record, status="RUNNING"):
            play.status, play.ended_at = "INTERRUPTED", now
            play.save(update_fields=["status", "ended_at"])
            record_play_ended(play)
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
        enqueue_event(
            "chat_sent", user, session_uuid,
            {"message_uuid": str(message.message_uuid), "channel_code": "GLOBAL",
             "floor_uuid": str(floor.floor_uuid) if floor else None},
            occurred_at=now,
        )
    return str(message.message_uuid)


def record_floor_transfer(user, session_uuid, from_floor_code, to_floor_code, position, observed_at=None):
    from campus.positions import FLOOR_BOUNDS, save_position
    from .catalog import floor_uuid

    now = observed_at or timezone.now()
    if (from_floor_code not in FLOOR_BOUNDS or to_floor_code not in FLOOR_BOUNDS
            or from_floor_code == to_floor_code or not isinstance(position, dict)
            or position.get("floor") != to_floor_code):
        return False
    with transaction.atomic():
        if not save_position(user, position, now, session_uuid=session_uuid):
            return False
        enqueue_event(
            "floor_transferred", user, session_uuid,
            {"from_floor_uuid": str(floor_uuid(from_floor_code)),
             "to_floor_uuid": str(floor_uuid(to_floor_code)), "zone_uuid": None},
            occurred_at=now,
        )
    return True


def record_post_event(event_type, user, session_uuid, post):
    return enqueue_event(
        event_type, user, session_uuid,
        {"post_uuid": str(post.post_uuid), "revision": post.revision, "zone_uuid": None},
    )


def record_comment_event(event_type, user, session_uuid, comment):
    return enqueue_event(
        event_type, user, session_uuid,
        {"comment_uuid": str(comment.comment_uuid), "post_uuid": str(comment.post.post_uuid),
         "parent_comment_uuid": str(comment.parent_id) if comment.parent_id else None,
         "revision": comment.revision},
    )


def record_progress_completed(progress):
    """One event when a progress row becomes COMPLETED, written after its final values are saved."""
    enqueue_event(
        "event_progress_completed", progress.user, progress.session_id,
        {"progress_uuid": str(progress.pk), "event_definition_uuid": str(progress.event_id),
         "event_kind": progress.event.event_type, "completion_order": progress.completion_order},
        occurred_at=progress.completed_at,
    )


def _play_fields(play):
    return {"play_uuid": str(play.pk), "game_uuid": str(play.game_id),
            "event_definition_uuid": str(play.event_id) if play.event_id else None}


def record_play_started(play):
    # The play owner, not the requester, is the event's user (an operator stop ends other users' plays).
    enqueue_event("game_play_started", play.user, play.session_id, _play_fields(play), occurred_at=play.started_at)


def record_play_ended(play):
    enqueue_event(
        "game_play_ended", play.user, play.session_id,
        {**_play_fields(play), "status": play.status, "duration_ms": play.duration_ms},
        occurred_at=play.ended_at,
    )


def record_story_action(record, session_uuid):
    """A newly created story record of a reported kind; callers pass only rows they just created.

    The story record keeps its session as free text, so only a well-formed platform session UUID is reported.
    """
    if record.kind in STORY_ACTION_KINDS and _uuid_ok("session_uuid", str(session_uuid)):
        enqueue_event(
            "story_action_recorded", record.progress.user, session_uuid,
            {"story_record_uuid": str(record.event_id), "story_key": record.progress.event_key,
             "action_key": record.key, "action_kind": record.kind},
            occurred_at=record.created_at,
        )


def record_content_open(user, session_uuid, content):
    """One accepted open request: the append-only log row and its event commit together."""
    profile = get_profile(user)
    with transaction.atomic():
        record = ContentOpenRequestLog.objects.create(
            user=user, platform_session_id=session_uuid, content=content,
            class_group_id=profile.cohort_id, category_id=content.category_id,
            zone_id=content.zone_id, domain=content.category.domain, content_kind=content.content_kind,
        )
        enqueue_event(
            "content_open_requested", user, session_uuid,
            {"content_uuid": str(content.content_uuid), "category_uuid": str(content.category_id),
             "domain": content.category.domain, "content_kind": content.content_kind,
             "zone_uuid": str(content.zone_id) if content.zone_id else None, "action": "OPEN_REQUESTED"},
            occurred_at=record.occurred_at,
        )
    return record
