from uuid import UUID
from django.db import migrations


def seed(apps, schema_editor):
    apps.get_model('event_story', 'EventDefinition').objects.using(schema_editor.connection.alias).update_or_create(
        event_uuid=UUID('c7024a2d-9f1a-46c2-b7c5-99fe57afd104'), defaults={
            'parent_event_id': UUID('f50d87cd-4f59-5750-b06b-dfd532576846'),
            'event_type': 'EASTER_EGG', 'event_name': '시선 강탈 사건'})


class Migration(migrations.Migration):
    dependencies = [('event_story', '0006_secret_recipe_achievement')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
