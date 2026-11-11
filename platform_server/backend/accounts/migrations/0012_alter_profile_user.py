from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [('accounts','0011_position_remove_profile_id_and_more'),('learning','0005_alter_pointledger_profile_alter_pointwallet_profile')]
    operations = [migrations.RemoveField('profile','user_uuid')]
