from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('event_story', '0010_remove_main_learning_step')]
    operations = [migrations.RemoveField(model_name='gameplay', name='difficulty')]
