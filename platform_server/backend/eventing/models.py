"""Operational catalog, sessions, chat and append-only content requests."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from config.fields import LosslessJSONField


class ClassGroup(models.Model):
    class_group_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=80)
    status = models.CharField(max_length=16, default="ACTIVE")


class MapFloor(models.Model):
    floor_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=80)


class MapZone(models.Model):
    zone_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    floor = models.ForeignKey(MapFloor, on_delete=models.PROTECT, related_name="zones")
    code = models.CharField(max_length=48)
    zone_type = models.CharField(max_length=24)
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["floor", "code"], name="eventing_floor_zone_code")]


class ContentCategory(models.Model):
    class Domain(models.TextChoices):
        LEARNING = "LEARNING", "학습"
        REST = "REST", "휴식"

    category_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=48, unique=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    domain = models.CharField(max_length=16, choices=Domain.choices)
    name = models.CharField(max_length=100)


class ContentItem(models.Model):
    class ContentKind(models.TextChoices):
        EXTERNAL_LINK = "EXTERNAL_LINK", "외부 링크"
        QUIZ = "QUIZ", "퀴즈"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "노출"
        HIDDEN = "HIDDEN", "숨김"

    content_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=48, unique=True)
    category = models.ForeignKey(ContentCategory, on_delete=models.PROTECT, related_name="contents")
    content_kind = models.CharField(max_length=24, choices=ContentKind.choices)
    title = models.CharField(max_length=120)
    external_url = models.URLField(max_length=500, null=True, blank=True)
    zone = models.ForeignKey(MapZone, null=True, blank=True, on_delete=models.PROTECT, related_name="contents")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PlatformSession(models.Model):
    session_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="platform_sessions")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=24, null=True, blank=True)
    last_seen_at = models.DateTimeField()


class ChatMessage(models.Model):
    message_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="campus_chat_messages")
    channel_code = models.CharField(max_length=16, default="GLOBAL")
    body = models.CharField(max_length=200)
    floor = models.ForeignKey(MapFloor, null=True, blank=True, on_delete=models.SET_NULL, related_name="chat_messages")
    sent_at = models.DateTimeField()
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-sent_at"]
        indexes = [models.Index(fields=["expires_at"], name="eventing_chat_expiry")]


class ContentOpenRequestLog(models.Model):
    open_request_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, db_column="user_uuid", on_delete=models.PROTECT)
    platform_session = models.ForeignKey(PlatformSession, db_column="session_uuid", on_delete=models.PROTECT)
    content = models.ForeignKey(ContentItem, db_column="content_uuid", on_delete=models.PROTECT)
    class_group = models.ForeignKey(ClassGroup, db_column="class_group_uuid", on_delete=models.PROTECT)
    category = models.ForeignKey(ContentCategory, db_column="category_uuid", on_delete=models.PROTECT)
    zone = models.ForeignKey(MapZone, db_column="zone_uuid", null=True, on_delete=models.PROTECT)
    domain = models.CharField(max_length=16, choices=ContentCategory.Domain.choices)
    content_kind = models.CharField(max_length=24, choices=ContentItem.ContentKind.choices)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "content_open_request_log"
