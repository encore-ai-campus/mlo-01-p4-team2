from uuid import UUID

from django.db import migrations


EVENT_ID = UUID('c7024a2d-9f1a-46c2-b7c5-99fe57afd105')


def seed(apps, schema_editor):
    alias = schema_editor.connection.alias
    apps.get_model('event_story', 'EventDefinition').objects.using(alias).filter(
        pk=EVENT_ID).update(event_name='부서지기 직전')
    # Old three-subject awards do not prove the EE-15 inventory condition.
    apps.get_model('event_story', 'EventProgress').objects.using(alias).filter(event_id=EVENT_ID).delete()


def reverse(apps, schema_editor):
    alias = schema_editor.connection.alias
    apps.get_model('event_story', 'EventDefinition').objects.using(alias).filter(
        pk=EVENT_ID).update(event_name='삼각지대 탈출')
    apps.get_model('event_story', 'EventProgress').objects.using(alias).filter(event_id=EVENT_ID).delete()


class Migration(migrations.Migration):
    dependencies = [('event_story', '0012_eventprogress_session')]
    operations = [migrations.RunPython(seed, reverse)]
