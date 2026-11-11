"""Persistent manager interactions, after campus validates the server-owned target."""
from functools import lru_cache
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import F, OuterRef, Subquery
from django.utils import timezone

from campus.motion import geometry, mission_geometry
from learning.services import active_platform_session
from .models import EventDefinition, EventProgress, ManagerInteraction


CAMPAIGN_ID = UUID("f50d87cd-4f59-5750-b06b-dfd532576846")
GREETING_ID = UUID("7b8b6d9b-84c1-53e9-bb0f-32b1c767c610")
POKING_ID = UUID("4804de8c-261b-5eb8-b648-db7ab2b5279d")
PYTHON_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd106")
FRAGILE_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd105")
CS_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd107")
SQL_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd111")
TIMER_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd108")
TYPING_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd109")
RECIPE_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd103")
AD_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd104")
MEMORY_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd110")
ACHIEVEMENT_IMAGES = {
    GREETING_ID: "/static/campus/assets/achievements/manager-greeting.png",
    POKING_ID: "/static/campus/assets/achievements/manager-poking.png",
    PYTHON_ID: "/static/campus/assets/achievements/python-intermediate.png",
    SQL_ID: "/static/campus/assets/achievements/sql-intermediate.png",
    TIMER_ID: "/static/campus/assets/achievements/timer.png",
    TYPING_ID: "/static/campus/assets/achievements/code-typing.png",
    FRAGILE_ID: "/static/campus/assets/achievements/all-subjects.png",
    CS_ID: "/static/campus/assets/achievements/cs-intermediate.png",
    RECIPE_ID: "/static/campus/assets/achievements/secret-recipe.png",
    AD_ID: "/static/campus/assets/achievements/ad-impression.png",
    MEMORY_ID: "/static/campus/assets/achievements/term-match.png",
}


@lru_cache(maxsize=1)
def manager_ids():
    ids = [obj.get("managerId", obj.get("id")) for world in [*geometry()["worlds"].values(), *mission_geometry()["rooms"].values()]
           for obj in world["objects"] if obj.get("action") == "manager_greet"]
    if (len(ids) != 6 or any(not isinstance(value, str) or not value.startswith("manager-")
                             or len(value) > 64 for value in ids) or len(set(ids)) != len(ids)):
        raise ImproperlyConfigured("The generated world must contain six distinct manager_greet NPCs.")
    return frozenset(ids)


DISCOVERY_GROUP = "event.achievement.discoveries"


def discovery_snapshot():
    """Public discovery metadata only; personal progress never enters this group."""
    from accounts.models import get_nickname
    first = EventProgress.objects.filter(
        event_id=OuterRef("pk"), status=EventProgress.Status.COMPLETED,
    ).order_by(F("completion_order").asc(nulls_last=True),
               F("completed_at").asc(nulls_last=True), "pk").values("pk")[:1]
    first_ids = EventDefinition.objects.filter(pk__in=ACHIEVEMENT_IMAGES).annotate(
        first=Subquery(first)).values_list("first", flat=True)
    winners = {row.event_id: row for row in EventProgress.objects.filter(
        pk__in=first_ids).select_related("user", "user__campus_profile")}
    return [{"event_uuid": str(event_id), "discovered": event_id in winners,
             "first_discoverer": {"nickname": get_nickname(winners[event_id].user),
                 "completed_at": winners[event_id].completed_at.isoformat()
                 if winners[event_id].completed_at else None} if event_id in winners else None}
            for event_id in ACHIEVEMENT_IMAGES]


def publish_discoveries():
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    layer = get_channel_layer()
    if layer is not None:
        async_to_sync(layer.group_send)(DISCOVERY_GROUP, {
            "type": "achievement_discoveries", "discoveries": discovery_snapshot(),
        })


def achievement_snapshot(user):
    definitions = EventDefinition.objects.in_bulk(ACHIEVEMENT_IMAGES)
    if len(definitions) != len(ACHIEVEMENT_IMAGES):
        raise ImproperlyConfigured("Apply the event_story achievement data migrations.")
    progress = {row.event_id: row for row in EventProgress.objects.filter(
        user=user, event_id__in=ACHIEVEMENT_IMAGES,
    )}
    discoveries = {row["event_uuid"]: row for row in discovery_snapshot()}
    return [
        {**discoveries[str(event_id)], "event_uuid": str(event_id), "name": definitions[event_id].event_name,
         "achieved": event_id in progress and progress[event_id].status == EventProgress.Status.COMPLETED,
         "completed_at": progress[event_id].completed_at.isoformat()
         if event_id in progress and progress[event_id].completed_at else None,
         "completion_order": progress[event_id].completion_order if event_id in progress else None,
         "image_url": image_url}
        for event_id, image_url in ACHIEVEMENT_IMAGES.items()
    ]


@transaction.atomic
def complete_achievement(user, event_id, completed_at, *, started_at=None, session_uuid=None):
    # Every entry point locks the account before the shared egg definition.
    get_user_model().objects.select_for_update().get(pk=user.pk)
    definition = EventDefinition.objects.select_for_update().get(pk=event_id)
    if definition.event_type != 'EASTER_EGG':
        raise ValueError('이스터에그 정의를 확인해 주세요.')
    completed = EventProgress.objects.filter(status=EventProgress.Status.COMPLETED)
    if (completed.filter(event=definition).exists()
            or completed.filter(user=user, event__event_type='EASTER_EGG').exists()):
        return False
    progress, _ = EventProgress.objects.get_or_create(
        user=user, event=definition,
        defaults={"started_at": started_at} if started_at is not None else {},
    )
    progress.completion_order = 1
    progress.status, progress.completed_at = EventProgress.Status.COMPLETED, completed_at
    progress.session = active_platform_session(user, session_uuid) if session_uuid is not None else None
    progress.save(update_fields=["status", "completed_at", "completion_order", "session"])
    transaction.on_commit(publish_discoveries, robust=True)
    return True


def record_ad_impression(impression):
    """A validated 200/201 impression receipt grants one account-owned badge."""
    if (impression.owner_id is None or impression.delivery_state != 'ACKNOWLEDGED'
            or impression.status not in ('RECEIVED', 'ACCEPTED', 'REJECTED')):
        return None
    from accounts.models import ActiveLogin
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from campus.session_registry import session_group

    with transaction.atomic():
        user = get_user_model().objects.select_for_update().filter(pk=impression.owner_id, is_active=True).first()
        if user is None:
            return None
        newly_completed = []
        if complete_achievement(user, AD_ID, timezone.now()):
            newly_completed.append(str(AD_ID))
        result = {'achievements': achievement_snapshot(user), 'newly_completed': newly_completed}
        key = ActiveLogin.objects.filter(user=user).values_list('session_key', flat=True).first()
        if newly_completed and key:
            def notify():
                layer = get_channel_layer()
                if layer is not None:
                    async_to_sync(layer.group_send)(session_group(key), {'type': 'achievement_update', 'result': result})
            transaction.on_commit(notify, robust=True)
        return result


def record_manager_action(user, manager_id, action, session_uuid=None):
    required = manager_ids()
    if not isinstance(manager_id, str) or manager_id not in required:
        raise ValueError("매니저 NPC를 확인해 주세요.")
    if action not in ("greet", "poke"):
        raise ValueError("매니저와의 상호작용을 확인해 주세요.")
    if not user.is_authenticated or user.pk is None:
        raise ValueError("로그인한 계정으로 이용해 주세요.")
    with transaction.atomic():
        # Different NPC hits must share one lock, including across server processes.
        get_user_model().objects.select_for_update().get(pk=user.pk)
        now = timezone.now()
        interaction, _ = ManagerInteraction.objects.get_or_create(
            user=user, campaign_id=CAMPAIGN_ID, manager_id=manager_id,
        )
        if action == "greet" and interaction.greeted_at is None:
            interaction.greeted_at = now
            interaction.save(update_fields=["greeted_at"])
        elif action == "poke":
            interaction.poke_count += 1
            interaction.save(update_fields=["poke_count"])
        event_id = GREETING_ID if action == "greet" else POKING_ID
        interactions = ManagerInteraction.objects.filter(
            user=user, campaign_id=CAMPAIGN_ID, manager_id__in=required,
        )
        ready = (interactions.filter(greeted_at__isnull=False).count() == len(required) if action == "greet"
                 else interactions.filter(poke_count__gte=11).count() == len(required))
        newly_completed = []
        if ready:
            if complete_achievement(user, event_id, now, started_at=now, session_uuid=session_uuid):
                newly_completed.append(str(event_id))
        else:
            EventProgress.objects.get_or_create(user=user, event_id=event_id, defaults={"started_at": now})
        return {"achievements": achievement_snapshot(user), "newly_completed": newly_completed}


def record_guide_talk(user, remaining, session_uuid=None):
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        newly_completed = []
        if type(remaining) is int and remaining == 1:
            from .game_services import require_open
            require_open()
            if complete_achievement(user, FRAGILE_ID, timezone.now(), session_uuid=session_uuid):
                newly_completed.append(str(FRAGILE_ID))
        return {"achievements": achievement_snapshot(user), "newly_completed": newly_completed}


def record_learning_completion(user, lesson, session_uuid=None):
    if (lesson.user_id != user.pk or lesson.completed_at is None or lesson.total_questions != 10
            or lesson.correct_count < 7 or lesson.subject not in ("python", "sql", "cs")):
        return None
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        event_ids = []
        if lesson.difficulty in ("intermediate", "advanced"):
            event_ids.append({"python": PYTHON_ID, "sql": SQL_ID, "cs": CS_ID}[lesson.subject])
        newly_completed = []
        for event_id in event_ids:
            if complete_achievement(user, event_id, timezone.now(), started_at=lesson.started_at, session_uuid=session_uuid):
                newly_completed.append(str(event_id))
        return {"achievements": achievement_snapshot(user), "newly_completed": newly_completed}
