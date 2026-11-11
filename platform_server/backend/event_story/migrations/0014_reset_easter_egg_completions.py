from django.db import migrations


def reset_completions(apps, schema_editor):
    # Previous multi-winner records cannot satisfy the new exclusive rules.
    apps.get_model('event_story', 'EventProgress').objects.using(schema_editor.connection.alias).filter(
        event__event_type='EASTER_EGG', status='COMPLETED').delete()


class Migration(migrations.Migration):
    dependencies = [('event_story', '0013_fragile_pepero_achievement')]
    operations = [migrations.RunPython(reset_completions, migrations.RunPython.noop)]
