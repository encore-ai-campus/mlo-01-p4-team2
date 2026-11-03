import uuid
from django.conf import settings
from django.db import models


class StoryProgress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    event_key = models.CharField(max_length=40)
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    flags = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'event_key'], name='story_user_event_unique')]


class StoryRecord(models.Model):
    """Durable, idempotent service-DB history, committed with progress."""
    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    progress = models.ForeignKey(StoryProgress, on_delete=models.PROTECT, related_name='records')
    key = models.CharField(max_length=100)
    kind = models.CharField(max_length=40)
    session_uuid = models.CharField(max_length=40, blank=True)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['progress', 'key'], name='story_record_key_unique')]
