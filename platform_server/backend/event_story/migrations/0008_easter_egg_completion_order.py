from django.db import migrations
from django.db.models import F, Max


def backfill(apps, schema_editor):
    alias = schema_editor.connection.alias
    Definition = apps.get_model('event_story', 'EventDefinition')
    Progress = apps.get_model('event_story', 'EventProgress')
    for definition in Definition.objects.using(alias).select_for_update().filter(
            event_type='EASTER_EGG').order_by('event_uuid'):
        records = Progress.objects.using(alias).filter(event_id=definition.pk)
        last = records.aggregate(last=Max('completion_order'))['last'] or 0
        # shortcut: historical ties use the record UUID; exact arrival order was never stored.
        pending = records.filter(status='COMPLETED', completion_order__isnull=True).order_by(
            F('completed_at').asc(nulls_last=True), 'progress_uuid')
        for rank, record in enumerate(pending, start=last + 1):
            records.filter(pk=record.pk).update(completion_order=rank)


class Migration(migrations.Migration):
    dependencies = [('event_story', '0007_ad_impression_achievement')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
