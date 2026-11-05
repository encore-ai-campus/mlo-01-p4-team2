import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('event_story', '0011_remove_gameplay_difficulty'),
        ('eventing', '0013_cs_learning_catalog'),
    ]
    operations = [
        migrations.AddField(
            model_name='eventprogress', name='session',
            field=models.ForeignKey(
                blank=True, db_column='session_uuid', null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to='eventing.platformsession',
            ),
        ),
    ]
