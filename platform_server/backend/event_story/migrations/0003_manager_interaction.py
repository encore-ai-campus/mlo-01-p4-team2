import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("event_story", "0002_gamedefinition_eventdefinition_gameplay_and_more"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(
        name="ManagerInteraction",
        fields=[
            ("interaction_uuid", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("manager_id", models.CharField(max_length=64)),
            ("greeted_at", models.DateTimeField(blank=True, null=True)),
            ("poke_count", models.PositiveBigIntegerField(default=0)),
            ("campaign", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="event_story.eventdefinition")),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
        ],
        options={"db_table":"manager_interaction", "constraints":[models.UniqueConstraint(fields=("user","campaign","manager_id"),name="manager_user_campaign_unique")]},
    )]
