from django.conf import settings
from django.db import models


class FreezeState(models.Model):
    mode = models.CharField(max_length=6, choices=[("MANUAL","수동"),("AUTO","자동")], default="MANUAL")
    # One shared DB row, including across movement/service hosts and restarts.
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    paused = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)
    message = models.CharField(max_length=160, default='강의 중입니다. 쉬는 시간에 다시 이용할 수 있어요.')
    updated_at = models.DateTimeField(auto_now=True)


class FreezeChange(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    paused = models.BooleanField()
    revision = models.PositiveIntegerField(unique=True)
    message = models.CharField(max_length=160)
    changed_at = models.DateTimeField(auto_now_add=True)


class EventAccessState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    mode = models.CharField(max_length=6, choices=[('AUTO','자동'),('OPEN','강제 열기'),('CLOSED','강제 닫기')], default='AUTO')
    revision = models.PositiveIntegerField(default=0)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)
