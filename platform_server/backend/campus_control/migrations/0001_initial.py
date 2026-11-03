from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='FreezeState', fields=[
            ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
            ('paused', models.BooleanField(default=False)), ('revision', models.PositiveIntegerField(default=0)),
            ('message', models.CharField(default='강의 중입니다. 쉬는 시간에 다시 이용할 수 있어요.', max_length=160)),
            ('updated_at', models.DateTimeField(auto_now=True))]),
        migrations.CreateModel(name='FreezeChange', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('paused', models.BooleanField()), ('revision', models.PositiveIntegerField(unique=True)),
            ('message', models.CharField(max_length=160)), ('changed_at', models.DateTimeField(auto_now_add=True)),
            ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL))]),
    ]
