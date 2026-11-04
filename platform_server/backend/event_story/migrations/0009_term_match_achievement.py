from uuid import UUID
from django.db import migrations


def seed(apps, schema_editor):
    alias = schema_editor.connection.alias
    game, _ = apps.get_model('event_story', 'GameDefinition').objects.using(alias).get_or_create(
        game_code='term_match', defaults={'game_uuid': UUID('2ded6158-641d-4d6d-b1c0-000000000010'),
                                        'game_name': 'IT 용어 카드 뒤집기'})
    apps.get_model('event_story', 'EventDefinition').objects.using(alias).update_or_create(
        event_uuid=UUID('c7024a2d-9f1a-46c2-b7c5-99fe57afd110'), defaults={
            'parent_event_id': UUID('f50d87cd-4f59-5750-b06b-dfd532576846'),
            'event_type': 'EASTER_EGG', 'event_name': '기억력 도둑', 'game_id': game.pk})


class Migration(migrations.Migration):
    dependencies = [('event_story', '0008_easter_egg_completion_order')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
