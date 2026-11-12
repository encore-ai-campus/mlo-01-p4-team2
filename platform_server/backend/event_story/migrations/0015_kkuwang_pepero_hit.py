from uuid import UUID

from django.db import migrations, transaction


EVENT_ID = UUID('c7024a2d-9f1a-46c2-b7c5-99fe57afd104')
NAME = '한 입 달랬지, 한 대 달랬니?'


def seed(apps, schema_editor):
    alias = schema_editor.connection.alias
    with transaction.atomic(using=alias):
        definition = apps.get_model('event_story', 'EventDefinition').objects.using(alias).select_for_update().get(pk=EVENT_ID)
        if definition.event_name == NAME and definition.event_type == 'EASTER_EGG':
            return
        if definition.event_name != '시선 강탈 사건' or definition.event_type != 'EASTER_EGG':
            raise RuntimeError('교체할 광고 업적 정의를 확인해 주세요.')
        if apps.get_model('event_story', 'EventProgress').objects.using(alias).filter(event_id=EVENT_ID).exists():
            raise RuntimeError('기존 광고 업적 기록이 있습니다. 기록을 보존하고 교체를 중단합니다.')
        definition.event_name = NAME
        definition.save(using=alias, update_fields=['event_name'])


class Migration(migrations.Migration):
    dependencies = [('event_story', '0014_reset_easter_egg_completions')]
    operations = [migrations.RunPython(seed)]
