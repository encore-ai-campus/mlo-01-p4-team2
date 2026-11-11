"""Retire the learning prerequisite without rewriting historical progress IDs."""
import uuid
from django.db import migrations


def retire_learning(apps, schema_editor):
    definition = apps.get_model('event_story', 'EventDefinition')
    rows = definition.objects.using(schema_editor.connection.alias)
    uid = lambda code: uuid.uuid5(uuid.NAMESPACE_URL, 'playdata/pepero-2026/v3/' + code)
    # Clear sibling orders first to avoid transient uniqueness conflicts.
    rows.filter(pk__in=[uid(str(i)) for i in range(1, 7)]).update(step_order=None)
    rows.filter(pk=uid('2')).update(parent_event=None, event_type='ARCHIVED')
    for order, code in enumerate(('1', '3', '4', '5', '6'), 1):
        rows.filter(pk=uid(code)).update(step_order=order)


class Migration(migrations.Migration):
    dependencies = [('event_story', '0009_term_match_achievement')]
    operations = [migrations.RunPython(retire_learning, migrations.RunPython.noop)]
