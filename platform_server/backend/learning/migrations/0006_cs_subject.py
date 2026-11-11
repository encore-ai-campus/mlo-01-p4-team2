from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('learning', '0005_alter_pointledger_profile_alter_pointwallet_profile')]
    operations = [
        migrations.RemoveConstraint('question', 'question_subject_valid'),
        migrations.RemoveConstraint('learningsession', 'session_subject_valid'),
        migrations.AlterField('question', 'subject', models.CharField(max_length=10, choices=[('python','Python'),('sql','SQL'),('cs','CS')], default='python')),
        migrations.AlterField('learningsession', 'subject', models.CharField(max_length=10, choices=[('python','Python'),('sql','SQL'),('cs','CS')], default='python')),
        migrations.AddConstraint('question', models.CheckConstraint(condition=models.Q(subject__in=['python','sql','cs']), name='question_subject_valid')),
        migrations.AddConstraint('learningsession', models.CheckConstraint(condition=models.Q(subject__in=['python','sql','cs']), name='session_subject_valid')),
    ]
