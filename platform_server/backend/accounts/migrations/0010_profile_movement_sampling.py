from django.db import migrations, models

import config.fields


class Migration(migrations.Migration):
    dependencies = [("accounts", "0009_profile_aio_cohort")]

    operations = [
        migrations.AddField(
            model_name="profile", name="last_movement_event_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="profile", name="movement_event_pending",
            field=config.fields.LosslessJSONField(null=True, blank=True),
        ),
    ]
