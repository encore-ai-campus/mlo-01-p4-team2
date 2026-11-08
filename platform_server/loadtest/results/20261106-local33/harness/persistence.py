"""Restart only owned test apps; compare all 100 accounts with stored state."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import psycopg

OUT=Path('/private/tmp/platform-local33-20261106')
ROOT=Path('/private/tmp/platform-local33-source-20261106')
env=json.loads((OUT/'environment.json').read_text())
db=psycopg.connect(dbname='local33_20261106',user='event_test',host='127.0.0.1',port=55480,autocommit=True)
assert db.execute('SHOW data_directory').fetchone()[0]=='/tmp/local33-data'
def snapshot():
    tables=db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND (tablename='event_progress' OR tablename LIKE 'event_story_story%') ORDER BY tablename").fetchall()
    result={}
    for (table,) in tables:
        from psycopg import sql
        rows=db.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text').format(sql.Identifier(table))).fetchall()
        result[table]={'rows':len(rows),'sha256':hashlib.sha256(json.dumps(rows).encode()).hexdigest()}
    return result
before=snapshot()
from launch import launch
launch(open_window=True)
sys.path.insert(0,str(ROOT/'backend'));os.environ.update(env)
import django
django.setup()
from django.contrib.auth import get_user_model
from event_story import main,recipe
from event_story.models import EventProgress
from event_story.achievement_services import ACHIEVEMENT_IMAGES
users=list(get_user_model().objects.filter(username__startswith='load').order_by('username'))
expected=[{'main':main.done(user,'6'),'recipe':recipe.status(user),
    'achievements':{str(p.event_id):[p.completed_at.isoformat(),p.completion_order] for p in EventProgress.objects.filter(user=user,event_id__in=ACHIEVEMENT_IMAGES,status='COMPLETED')}} for user in users]
keys=json.loads((OUT/'sessions.json').read_text())
def fetch(index):
    def get(path):
        request=urllib.request.Request('http://127.0.0.1:8096'+path,headers={'Cookie':'REDACTED'+keys[index]})
        with urllib.request.urlopen(request,timeout=20) as response:return json.loads(response.read())
    assert get('/api/event-main/status/')['completed']==expected[index]['main']
    assert get('/api/event-story/recipe/status/')==expected[index]['recipe']
    actual={row['event_uuid']:[row['completed_at'],row['completion_order']] for row in get('/api/event-story/achievements/')['achievements'] if row['achieved']}
    assert actual==expected[index]['achievements']
    return True
async def verify():
    gate=asyncio.Semaphore(10)
    async def one(i):
        async with gate:return await asyncio.to_thread(fetch,i)
    return await asyncio.gather(*(one(i) for i in range(100)),return_exceptions=True)
results=asyncio.run(verify());after=snapshot()
report={'accounts':100,'http_concurrency':10,'passed':sum(value is True for value in results),
    'errors':[str(value) for value in results if isinstance(value,BaseException)],
    'progress_and_history_unchanged':before==after,'before':before,'after':after}
(OUT/'persistence.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
assert report['passed']==100 and before==after
