import config.fields
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


def require_empty_profiles(apps, schema_editor):
    if apps.get_model('accounts', 'Profile').objects.using(schema_editor.connection.alias).exists():
        raise RuntimeError('Create a fresh PostgreSQL database and use export_platform_data/import_platform_data; do not alter a populated legacy database in place.')


class Migration(migrations.Migration):
    dependencies = [('accounts','0010_profile_movement_sampling'),('eventing','0010_aio_class_group'),('learning','0004_beginner_prompt_wording'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.RunPython(require_empty_profiles, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='profile',
            name='id',
        ),
        migrations.AlterField('profile','user',models.OneToOneField(to=settings.AUTH_USER_MODEL,on_delete=django.db.models.deletion.CASCADE,primary_key=True,serialize=False,related_name='campus_profile')),
        migrations.CreateModel(
            name='Position',
            fields=[
                ('profile', models.OneToOneField(db_column='user_uuid', on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name='position', serialize=False, to='accounts.profile')),
                ('last_position', config.fields.LosslessJSONField(blank=True, null=True)),
                ('last_position_at', models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.RemoveField(
            model_name='profile',
            name='last_movement_event_at',
        ),
        migrations.RemoveField(
            model_name='profile',
            name='last_position',
        ),
        migrations.RemoveField(
            model_name='profile',
            name='last_position_at',
        ),
        migrations.RemoveField(
            model_name='profile',
            name='movement_event_pending',
        ),
        migrations.AlterField(
            model_name='profile',
            name='cohort',
            field=models.ForeignKey(db_column='cohort', default=uuid.UUID('31a2cc4c-ddd8-4c6a-bcd0-846dbe774f32'), on_delete=django.db.models.deletion.PROTECT, to='eventing.classgroup'),
        ),
    ]
