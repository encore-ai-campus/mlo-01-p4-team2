"""Persistent manager interactions, after campus validates the server-owned target."""
from functools import lru_cache
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from campus.motion import geometry, mission_geometry
from .models import EventDefinition, EventProgress, ManagerInteraction


CAMPAIGN_ID = UUID("f50d87cd-4f59-5750-b06b-dfd532576846")
GREETING_ID = UUID("7b8b6d9b-84c1-53e9-bb0f-32b1c767c610")
POKING_ID = UUID("4804de8c-261b-5eb8-b648-db7ab2b5279d")
PYTHON_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd106")
ALL_SUBJECTS_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd105")
CS_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd107")
SQL_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd111")
TIMER_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd108")
TYPING_ID = UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd109")
ACHIEVEMENT_IMAGES = {
    GREETING_ID: "/static/campus/assets/achievements/manager-greeting.png",
    POKING_ID: "/static/campus/assets/achievements/manager-poking.png",
    PYTHON_ID: "/static/campus/assets/achievements/python-intermediate.png",
    SQL_ID: "/static/campus/assets/achievements/sql-intermediate.png",
    TIMER_ID: "/static/campus/assets/achievements/timer.png",
    TYPING_ID: "/static/campus/assets/achievements/code-typing.png",
    ALL_SUBJECTS_ID: "/static/campus/assets/achievements/all-subjects.png",
    CS_ID: "/static/campus/assets/achievements/cs-intermediate.png",
}


@lru_cache(maxsize=1)
def manager_ids():
    ids = [obj.get("managerId", obj.get("id")) for world in [*geometry()["worlds"].values(), *mission_geometry()["rooms"].values()]
           for obj in world["objects"] if obj.get("action") == "manager_greet"]
    if (len(ids) != 7 or any(not isinstance(value, str) or not value.startswith("manager-")
                             or len(value) > 64 for value in ids) or len(set(ids)) != 7):
        raise ImproperlyConfigured("The generated world must contain seven distinct manager_greet NPCs.")
    return frozenset(ids)


def achievement_snapshot(user):
    definitions = EventDefinition.objects.in_bulk(ACHIEVEMENT_IMAGES)
    if len(definitions) != len(ACHIEVEMENT_IMAGES):
        raise ImproperlyConfigured("Apply the event_story achievement data migrations.")
    progress = {row.event_id: row for row in EventProgress.objects.filter(
        user=user, event_id__in=ACHIEVEMENT_IMAGES,
    )}
    return [
        {"event_uuid": str(event_id), "name": definitions[event_id].event_name,
         "achieved": event_id in progress and progress[event_id].status == EventProgress.Status.COMPLETED,
         "completed_at": progress[event_id].completed_at.isoformat()
         if event_id in progress and progress[event_id].completed_at else None,
         "image_url": image_url}
        for event_id, image_url in ACHIEVEMENT_IMAGES.items()
    ]


def record_manager_action(user, manager_id, action):
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
        progress, _ = EventProgress.objects.get_or_create(
            user=user, event_id=event_id, defaults={"started_at": now},
        )
        interactions = ManagerInteraction.objects.filter(
            user=user, campaign_id=CAMPAIGN_ID, manager_id__in=required,
        )
        ready = (interactions.filter(greeted_at__isnull=False).count() == 7 if action == "greet"
                 else interactions.filter(poke_count__gte=11).count() == 7)
        newly_completed = []
        if ready and progress.status != EventProgress.Status.COMPLETED:
            progress.status = EventProgress.Status.COMPLETED
            progress.completed_at = now
            progress.save(update_fields=["status", "completed_at"])
            newly_completed.append(str(event_id))
        return {"achievements": achievement_snapshot(user), "newly_completed": newly_completed}


def record_learning_completion(user, lesson):
    if (lesson.user_id != user.pk or lesson.completed_at is None or lesson.total_questions != 10
            or lesson.correct_count < 7 or lesson.subject not in ("python", "sql", "cs")):
        return None
    from learning.models import LearningSession
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        event_ids = []
        if lesson.difficulty in ("intermediate", "advanced"):
            event_ids.append({"python": PYTHON_ID, "sql": SQL_ID, "cs": CS_ID}[lesson.subject])
        passed_subjects = set(LearningSession.objects.filter(
            user=user, completed_at__isnull=False, total_questions=10, correct_count__gte=7,
            subject__in=("python", "sql", "cs"),
        ).values_list("subject", flat=True).distinct())
        if passed_subjects == {"python", "sql", "cs"}:
            event_ids.append(ALL_SUBJECTS_ID)
        newly_completed = []
        for event_id in event_ids:
            progress, _ = EventProgress.objects.get_or_create(
                user=user, event_id=event_id, defaults={"started_at": lesson.started_at})
            if progress.status != EventProgress.Status.COMPLETED:
                progress.status = EventProgress.Status.COMPLETED
                progress.completed_at = timezone.now()
                progress.save(update_fields=["status", "completed_at"])
                newly_completed.append(str(event_id))
        return {"achievements": achievement_snapshot(user), "newly_completed": newly_completed}
