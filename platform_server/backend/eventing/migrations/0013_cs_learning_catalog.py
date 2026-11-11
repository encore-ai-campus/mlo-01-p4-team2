from uuid import NAMESPACE_URL, uuid5
from django.db import migrations


def add_cs(apps, schema_editor):
    alias = schema_editor.connection.alias if schema_editor else 'default'
    floor = apps.get_model('eventing', 'MapFloor').objects.using(alias).get(code='campus')
    zone, _ = apps.get_model('eventing', 'MapZone').objects.using(alias).get_or_create(
        floor_id=floor.pk, code='cs-classroom', defaults={
            'zone_uuid': uuid5(NAMESPACE_URL, 'playdata:learning:cs:zone'),
            'zone_type':'CLASSROOM', 'name':'CS 학습교실'})
    category, _ = apps.get_model('eventing', 'ContentCategory').objects.using(alias).get_or_create(
        code='cs', defaults={'category_uuid':uuid5(NAMESPACE_URL, 'playdata:learning:cs:category'),
                             'domain':'LEARNING', 'name':'CS 학습'})
    for kind, title in [('multiple-choice','CS 객관식'), ('fill-blank','CS 개념 빈칸 채우기')]:
        apps.get_model('eventing', 'ContentItem').objects.using(alias).get_or_create(
            code='cs-'+kind, defaults={'content_uuid':uuid5(NAMESPACE_URL, 'playdata:learning:cs:'+kind),
            'category_id':category.pk, 'zone_id':zone.pk, 'title':title,
            'content_kind':'QUIZ', 'external_url':None, 'status':'ACTIVE'})


class Migration(migrations.Migration):
    dependencies = [('eventing','0012_contentopenrequestlog_user_and_more')]
    operations = [migrations.RunPython(add_cs, migrations.RunPython.noop)]
