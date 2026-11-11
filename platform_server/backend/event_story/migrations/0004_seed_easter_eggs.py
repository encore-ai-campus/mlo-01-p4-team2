from uuid import UUID
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from django.db import migrations

GAME_EVENTS = {"timer":UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd108"),"typing":UUID("c7024a2d-9f1a-46c2-b7c5-99fe57afd109")}

def seed_0(apps, schema_editor):
    Definition = apps.get_model("event_story", "EventDefinition")
    definitions = Definition.objects.using(schema_editor.connection.alias)
    campaign_id = UUID("f50d87cd-4f59-5750-b06b-dfd532576846")
    definitions.update_or_create(event_uuid=campaign_id, defaults={
        "event_type": "CAMPAIGN", "event_name": "사라진 11번째 빼빼로",
        "starts_at": datetime(2026,11,9,8,30,tzinfo=ZoneInfo("Asia/Seoul")),
        "ends_at": datetime(2026,11,11,22,tzinfo=ZoneInfo("Asia/Seoul")),
    })
    for event_id, name in (
        ("7b8b6d9b-84c1-53e9-bb0f-32b1c767c610", "수상한 친화력"),
        ("4804de8c-261b-5eb8-b648-db7ab2b5279d", "참을성 테스트"),
    ):
        definitions.update_or_create(event_uuid=UUID(event_id), defaults={
            "parent_event_id": campaign_id, "event_type": "EASTER_EGG", "event_name": name,
        })

def seed_1(apps, schema_editor):
    Definition = apps.get_model("event_story", "EventDefinition")
    for event_id, name in (
        ("c7024a2d-9f1a-46c2-b7c5-99fe57afd106", "뱀을 길들이는 자"),
        ("c7024a2d-9f1a-46c2-b7c5-99fe57afd111", "데이터의 비밀문"),
    ):
        Definition.objects.using(schema_editor.connection.alias).update_or_create(
            event_uuid=UUID(event_id), defaults={
                "parent_event_id": UUID("f50d87cd-4f59-5750-b06b-dfd532576846"),
                "event_type": "EASTER_EGG", "event_name": name,
            })

def seed_2(apps, schema_editor):
    Game = apps.get_model("event_story", "GameDefinition")
    for code, game_id, name in (
        ("timer", "2ded6158-641d-4d6d-b1c0-000000000008", "타이머 게임"),
        ("typing", "2ded6158-641d-4d6d-b1c0-000000000009", "코드 타이핑 게임"),
    ):
        Game.objects.using(schema_editor.connection.alias).get_or_create(
            game_code=code, defaults={"game_uuid": uuid.UUID(game_id), "game_name": name,
                                     "is_enabled": True, "is_placed": True})

def seed_3(apps, schema_editor):
    Definition = apps.get_model("event_story", "EventDefinition")
    for code, name in (("timer", "찰나의 기적"), ("typing", "손가락에 불났다")):
        Definition.objects.using(schema_editor.connection.alias).update_or_create(
            event_uuid=GAME_EVENTS[code], defaults={
                "parent_event_id": UUID("f50d87cd-4f59-5750-b06b-dfd532576846"),
                "event_type": "EASTER_EGG", "event_name": name,
            })

def seed(apps, schema_editor):
    seed_0(apps, schema_editor)
    seed_1(apps, schema_editor)
    seed_2(apps, schema_editor)
    seed_3(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [("event_story", "0003_manager_interaction")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
