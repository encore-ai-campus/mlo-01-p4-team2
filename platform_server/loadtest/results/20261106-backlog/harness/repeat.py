"""Preserve the pilot; clone the same template for the final drained measurements."""
import json,os,shutil,signal,subprocess,sys,time
from pathlib import Path
import psycopg,redis
from psycopg import sql

ROOT=Path('/private/tmp/platform-backlog-20261106');old=ROOT/'increased'
assert (old/'audit.json').exists()
for name in ('pids.json','front-pids.json'):
 for row in json.loads((old/name).read_text()):
  command=subprocess.check_output(['ps','-p',str(row['pid']),'-o','args='],text=True)
  assert any(v in command for v in ('runtime','config.movement_asgi','config.service_asgi','campus_jobs'))
  os.kill(row['pid'],signal.SIGTERM)
pid=int((old/'nginx.pid').read_text())
assert str(old) in subprocess.check_output(['ps','-p',str(pid),'-o','args='],text=True)
os.kill(pid,signal.SIGQUIT);time.sleep(4)
pilot=ROOT/'preliminary';pilot.mkdir()
for label in ('baseline','increased'):shutil.move(ROOT/label,pilot/label)
shutil.copy2(ROOT/'initial.json',pilot/'initial.json')
(ROOT/'active.json').write_text(json.dumps({'label':'preliminary/increased'}))
base=ROOT/'template';env=json.loads((base/'environment.json').read_text())
os.environ.update(env);sys.path.insert(0,'/private/tmp/platform-local44-source-20261106/backend')
import django
django.setup()
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.auth import SESSION_KEY
from accounts.session_policy import start_login
from django.db import connection
assert connection.settings_dict['NAME']=='backlog_template_20261106'
for key in json.loads((base/'sessions.json').read_text()):
 session=SessionStore(session_key=key);assert session.get(SESSION_KEY);start_login(session);session.save()
connection.close();initial={}
with psycopg.connect(host='127.0.0.1',port=55480,dbname='postgres',user='event_test',autocommit=True) as admin:
 for label in ('baseline','increased'):
  out=ROOT/label;out.mkdir();(out/'nginx-logs').mkdir()
  current=json.loads((pilot/label/'environment.json').read_text())
  current['POSTGRES_DB']='backlog_final_'+label+'_20261106'
  admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE backlog_template_20261106').format(sql.Identifier(current['POSTGRES_DB'])))
  (out/'environment.json').write_text(json.dumps(current));(out/'environment.json').chmod(0o600)
  for name in ('sessions.json','metadata.json'):shutil.copy2(base/name,out/name);(out/name).chmod(0o600)
  shutil.copy2(pilot/label/'nginx.conf',out/'nginx.conf')
  for key in ('CAMPUS_MOVEMENT_REDIS_URL','CAMPUS_SERVICE_REDIS_URL'):
   assert current[key] in [f'redis://127.0.0.1:{p}/0' for p in (56397,56398,56399,56400)]
   store=redis.Redis.from_url(current[key]);store.flushdb();assert store.dbsize()==0
  import hashlib
  with psycopg.connect(host='127.0.0.1',port=55480,dbname=current['POSTGRES_DB'],user='event_test') as db:
   counts={name:db.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(name))).fetchone()[0]
    for name in ('auth_user','django_session','learning_question','event_progress','event_story_storyprogress')}
  initial[label]={'row_counts':counts,'sessions_sha256':hashlib.sha256((out/'sessions.json').read_bytes()).hexdigest(),
   'initial_redis_counts':{'CAMPUS_MOVEMENT_REDIS_URL':0,'CAMPUS_SERVICE_REDIS_URL':0},'movement_front_backlog':int(current['MOVEMENT_FRONT_BACKLOG'])}
(ROOT/'initial.json').write_text(json.dumps(initial,indent=2))
assert initial['baseline']['row_counts']==initial['increased']['row_counts']
print('Pilot preserved; final DB copies and owned Redis reset from identical fixtures')
