from django.conf import settings
import uuid

from django.db import models
from config.fields import LosslessJSONField

from .avatars import AVATAR_CHOICES, AVATAR_IDS, DEFAULT_AVATAR, OPERATOR_AVATAR_IDS
from .cohorts import COHORT_CHOICES, course_for_cohort


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True, related_name="campus_profile"
    )
    nickname = models.CharField(max_length=20)
    avatar = models.CharField(max_length=8, choices=AVATAR_CHOICES, default=DEFAULT_AVATAR)
    cohort = models.ForeignKey("eventing.ClassGroup", on_delete=models.PROTECT, db_column="cohort",
                               default=uuid.UUID("31a2cc4c-ddd8-4c6a-bcd0-846dbe774f32"))
    # Existing accounts keep their current identity; signup explicitly starts incomplete.
    setup_complete = models.BooleanField(default=True)
    guide_completed = models.BooleanField(default=False)

    @property
    def course(self):
        return course_for_cohort(self.cohort.code)

    @property
    def user_uuid(self):
        # Existing integrations use this attribute; it is no longer a DB column.
        return self.user_id


class Position(models.Model):
    profile = models.OneToOneField(Profile, primary_key=True, db_column="user_uuid",
                                   related_name="position", on_delete=models.CASCADE)
    last_position = LosslessJSONField(null=True, blank=True)
    last_position_at = models.DateTimeField(null=True, blank=True)
    # Movement events are sampled: at most one outbox row per interval from the newest unsent position.
    last_movement_event_at = models.DateTimeField(null=True, blank=True)
    movement_event_pending = LosslessJSONField(null=True, blank=True)


class ActiveLogin(models.Model):
    """The latest accepted login key, retained after logout to reject superseded sessions."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True,
        related_name="active_campus_login",
    )
    session_key = models.CharField(max_length=40, unique=True)
    updated_at = models.DateTimeField(auto_now=True)


def get_nickname(user):
    # Accounts created before nicknames were introduced can still enter the map.
    profile = getattr(user, "campus_profile", None)
    return profile.nickname if profile and profile.nickname else user.get_username()


def get_avatar(user):
    profile = getattr(user, "campus_profile", None)
    avatar = getattr(profile, "avatar", DEFAULT_AVATAR)
    if avatar in OPERATOR_AVATAR_IDS and not (user.is_staff or user.is_superuser):
        return DEFAULT_AVATAR
    return avatar if avatar in AVATAR_IDS else DEFAULT_AVATAR


def get_identity(user):
    return {"nickname": get_nickname(user), "avatar": get_avatar(user)}


def needs_profile_setup(user):
    profile = getattr(user, "campus_profile", None)
    return profile is not None and not getattr(profile, "setup_complete", True)
