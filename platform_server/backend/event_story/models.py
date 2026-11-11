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




class GameDefinition(models.Model):
    game_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    game_code = models.CharField(max_length=48, unique=True)
    game_name = models.CharField(max_length=120)
    is_enabled = models.BooleanField(default=True)
    is_placed = models.BooleanField(default=True)
    class Meta:
        db_table = 'game_definition'


class EventDefinition(models.Model):
    event_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent_event = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    event_type = models.CharField(max_length=16)
    event_name = models.CharField(max_length=120)
    step_order = models.PositiveSmallIntegerField(null=True)
    game = models.ForeignKey(GameDefinition, null=True, on_delete=models.PROTECT)
    starts_at = models.DateTimeField(null=True)
    ends_at = models.DateTimeField(null=True)
    class Meta:
        db_table = 'event_definition'
        constraints = [models.UniqueConstraint(fields=['parent_event', 'step_order'], name='event_sibling_order_unique')]


class EventProgress(models.Model):
    class Status:
        IN_PROGRESS = "IN_PROGRESS"
        COMPLETED = "COMPLETED"
    progress_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    event = models.ForeignKey(EventDefinition, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, default='IN_PROGRESS')
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)
    completion_order = models.PositiveBigIntegerField(null=True)
    class Meta:
        db_table = 'event_progress'
        constraints = [models.UniqueConstraint(fields=['user', 'event'], name='event_progress_user_unique'), models.UniqueConstraint(fields=['event', 'completion_order'], name='event_completion_order_unique')]


class GamePlay(models.Model):
    class Status:
        RUNNING = "RUNNING"
        SUCCESS = "SUCCESS"
        FAILED = "FAILED"
        INTERRUPTED = "INTERRUPTED"
        FINISHED = "FINISHED"
    play_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    session = models.ForeignKey('eventing.PlatformSession', on_delete=models.PROTECT)
    game = models.ForeignKey(GameDefinition, on_delete=models.PROTECT)
    event = models.ForeignKey(EventDefinition, null=True, on_delete=models.PROTECT)
    difficulty = models.CharField(max_length=20, null=True)
    status = models.CharField(max_length=16, default='RUNNING')
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True)
    duration_ms = models.PositiveBigIntegerField(null=True)
    evidence = models.JSONField(default=dict)
    class Meta:
        db_table = 'game_play'


class ManagerInteraction(models.Model):
    interaction_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    campaign = models.ForeignKey(EventDefinition, on_delete=models.PROTECT)
    manager_id = models.CharField(max_length=64)
    greeted_at = models.DateTimeField(null=True, blank=True)
    poke_count = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "manager_interaction"
        constraints = [models.UniqueConstraint(
            fields=["user", "campaign", "manager_id"], name="manager_user_campaign_unique",
        )]
