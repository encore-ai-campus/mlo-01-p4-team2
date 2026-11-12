"""One last-location snapshot per account, separate from movement history."""
import math
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import Profile


FLOOR_BOUNDS = {"campus": (2448, 1230), "rooftop": (2448, 1800)}
DIRECTIONS = {"up", "down", "left", "right"}
# Just inside the 18F external entrance (dist/map-data.js, scaled by 1.5).
DEFAULT_POSITION = {"floor": "campus", "x": 1998.75, "y": 910.5, "dir": "up"}


def validate_position(data):
    if not isinstance(data, dict):
        return None
    floor, direction = data.get("floor"), data.get("dir")
    if not isinstance(floor, str) or floor not in FLOOR_BOUNDS:
        return None
    if not isinstance(direction, str) or direction not in DIRECTIONS:
        return None
    x, y = data.get("x"), data.get("y")
    width, height = FLOOR_BOUNDS[floor]
    if (type(x) not in (int, float) or type(y) not in (int, float)
            or not 0 <= x <= width or not 0 <= y <= height
            or not math.isfinite(x) or not math.isfinite(y)):
        return None
    return {"floor": floor, "x": float(x), "y": float(y), "dir": direction}


def safe_position(data):
    position = validate_position(data)
    if position is None:
        return None
    # Exiting should resume inside, without immediately prompting logout again.
    if (position["floor"] == "campus" and 1953 <= position["x"] <= 2044.5
            and 946.5 <= position["y"] <= 1060.5):
        return DEFAULT_POSITION.copy()
    return position


def get_position(user):
    from accounts.models import Position
    record = Position.objects.filter(profile__user=user).first()
    return safe_position(record.last_position if record else None) or DEFAULT_POSITION.copy()


def movement_event_due_at(user):
    """When the stored unsent movement sample may become an outbox event, or None if none is pending."""
    from accounts.models import Position
    record = Position.objects.filter(profile__user=user).first()
    if record is None or not record.movement_event_pending:
        return None
    if record.last_movement_event_at is None:
        return timezone.now()
    return record.last_movement_event_at + timedelta(seconds=settings.CAMPUS_MOVEMENT_EVENT_INTERVAL_SECONDS)


def _emit_pending_movement(user, record, now):
    """Write the newest unsent sample as one movement event at most once per interval (caller holds the lock)."""
    due_at = (
        record.last_movement_event_at + timedelta(seconds=settings.CAMPUS_MOVEMENT_EVENT_INTERVAL_SECONDS)
        if record.last_movement_event_at is not None else now
    )
    if not record.movement_event_pending or now < due_at:
        return False
    from eventing.catalog import floor_uuid
    from eventing.events import enqueue_event

    pending = record.movement_event_pending
    sampled = pending["position"]
    enqueue_event(
        "movement_observed", user, pending["session_uuid"],
        {"floor_uuid": str(floor_uuid(sampled["floor"])), "zone_uuid": None,
         "x": sampled["x"], "y": sampled["y"], "input_kind": "KEYBOARD"},
        occurred_at=parse_datetime(pending["observed_at"]),
    )
    record.last_movement_event_at = now
    record.movement_event_pending = None
    return True


def flush_movement_event(user):
    """Emit a due pending sample without resaving a socket's older position."""
    from accounts.models import Position
    if not user.is_authenticated or not user.is_active:
        return False
    with transaction.atomic():
        profile = Profile.objects.select_for_update().filter(user=user).first()
        record = Position.objects.filter(profile=profile).first() if profile else None
        if record is None:
            return False
        emitted = _emit_pending_movement(user, record, timezone.now())
        if emitted:
            record.save(update_fields=("last_movement_event_at", "movement_event_pending"))
    return emitted


def save_position(user, data, observed_at=None, *, session_uuid=None):
    """Save each latest position; sample movement events at most once per interval."""
    from accounts.models import Position
    position = safe_position(data)
    if not user.is_authenticated or not user.is_active or position is None:
        return False
    observed_at = observed_at or timezone.now()
    with transaction.atomic():
        profile, _ = Profile.objects.get_or_create(user=user, defaults={"nickname": user.get_username()[:20]})
        Profile.objects.select_for_update().get(pk=profile.pk)
        record, _ = Position.objects.get_or_create(profile=profile)
        now = timezone.now()
        previous = safe_position(record.last_position) or DEFAULT_POSITION
        # Facing is useful on resume, but isn't a movement observation.
        moved = any(previous[key] != position[key] for key in ("floor", "x", "y"))
        # Stale or repeated snapshots never overwrite the accepted position.
        updated = record.last_position_at is None or record.last_position_at < observed_at
        fields = []
        if updated:
            record.last_position, record.last_position_at = position, observed_at
            fields.extend(("last_position", "last_position_at"))
            if moved and session_uuid is not None:
                record.movement_event_pending = {
                    "position": position, "observed_at": observed_at.isoformat(), "session_uuid": str(session_uuid),
                }
                fields.append("movement_event_pending")
        if _emit_pending_movement(user, record, now):
            fields.extend(("last_movement_event_at", "movement_event_pending"))
        if fields:
            record.save(update_fields=set(fields))
    return updated
