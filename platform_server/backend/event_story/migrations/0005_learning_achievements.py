from uuid import UUID
from django.db import migrations

ALL = UUID('c7024a2d-9f1a-46c2-b7c5-99fe57afd105')
CS = UUID('c7024a2d-9f1a-46c2-b7c5-99fe57afd107')


def seed(apps, schema_editor):
    alias = schema_editor.connection.alias
    Definition = apps.get_model('event_story','EventDefinition')
    for identity,name in [(ALL,'삼각지대 탈출'),(CS,'컴퓨터의 속마음')]:
        Definition.objects.using(alias).update_or_create(event_uuid=identity,defaults={
            'parent_event_id':UUID('f50d87cd-4f59-5750-b06b-dfd532576846'),
            'event_type':'EASTER_EGG','event_name':name})


def restore(apps, schema_editor):
    alias = schema_editor.connection.alias
    Session = apps.get_model('learning','LearningSession')
    Progress = apps.get_model('event_story','EventProgress')
    passes = Session.objects.using(alias).filter(completed_at__isnull=False,total_questions=10,
        correct_count__gte=7,subject__in=['python','sql','cs']).order_by('completed_at','pk')
    users = {}
    for lesson in passes.iterator():
        subjects = users.setdefault(lesson.user_id,{})
        subjects.setdefault(lesson.subject,lesson)
        awards = []
        if lesson.subject=='cs' and lesson.difficulty in ('intermediate','advanced'):
            awards.append((CS,lesson.started_at,lesson.completed_at))
        if len(subjects)==3:
            awards.append((ALL,min(s.started_at for s in subjects.values()),max(s.completed_at for s in subjects.values())))
        for event,started,completed in awards:
            record, created = Progress.objects.using(alias).get_or_create(user_id=lesson.user_id,event_id=event,defaults={
                'status':'COMPLETED','started_at':started,'completed_at':completed})
            if created:
                # auto_now_add ignores the supplied historical start on INSERT.
                Progress.objects.using(alias).filter(pk=record.pk).update(started_at=started)


class Migration(migrations.Migration):
    dependencies = [('event_story','0004_seed_easter_eggs'),('learning','0006_cs_subject')]
    operations = [migrations.RunPython(seed,migrations.RunPython.noop),
                  migrations.RunPython(restore,migrations.RunPython.noop)]
