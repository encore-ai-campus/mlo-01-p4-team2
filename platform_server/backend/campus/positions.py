"""One last-location snapshot per account, separate from movement history."""
import math

from django.db import transaction
from django.utils import timezone

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


def save_position(user, data, observed_at=None, *, session_uuid=None):
    from accounts.models import Position
    position = safe_position(data)
    if not user.is_authenticated or not user.is_active or position is None:
        return False
    observed_at = observed_at or timezone.now()
    with transaction.atomic():
        profile, _ = Profile.objects.get_or_create(user=user, defaults={"nickname": user.get_username()[:20]})
        Profile.objects.select_for_update().get(pk=profile.pk)
        record, _ = Position.objects.get_or_create(profile=profile)
        if record.last_position_at is not None and record.last_position_at >= observed_at:
            return False
        record.last_position, record.last_position_at = position, observed_at
        record.save(update_fields=("last_position", "last_position_at"))
    return True
