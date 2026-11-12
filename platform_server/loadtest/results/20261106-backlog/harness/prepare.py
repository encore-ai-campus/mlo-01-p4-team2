"""Build identical owned fixtures for backlog50 and backlog128, preserving real data."""
import json,os,subprocess,sys,shutil
from pathlib import Path
import psycopg
from psycopg import sql
ROOT=Path('/private/tmp/platform-backlog-20261106');base=ROOT/'template';base.mkdir(exist_ok=True)
old=Path('/private/tmp/platform-local44-20261106/template')
env=json.loads((old/'environment.json').read_text())
with psycopg.connect(host='127.0.0.1',port=55480,dbname='postgres',user='event_test',autocommit=True) as db:
 assert not db.execute("SELECT 1 FROM pg_database WHERE datname='backlog_template_20261106'").fetchone()
 db.execute('CREATE DATABASE backlog_template_20261106 TEMPLATE local44_template_20261106')
env.update(POSTGRES_DB='backlog_template_20261106',POSTGRES_POOL_MAX_SIZE='0',LOCAL44_OUT=str(base),CAMPUS_DATA_DIR=str(base/'data'))
(base/'environment.json').write_text(json.dumps(env));(base/'environment.json').chmod(0o600)
for n in ('sessions.json','metadata.json'):
 shutil.copy2(old/n,base/n);(base/n).chmod(0o600)
os.environ.update(env);sys.path.insert(0,'/private/tmp/platform-local44-source-20261106/backend')
import django
django.setup()
from django.db import connection
from django.contrib.sessions.backends.db import SessionStore
from accounts.session_policy import start_login
from django.contrib.auth import SESSION_KEY
assert connection.settings_dict['NAME']=='backlog_template_20261106'
keys=json.loads((base/'sessions.json').read_text());assert len(keys)==100
for key in keys:
 session=SessionStore(session_key=key)
 assert session.get(SESSION_KEY)
 start_login(session);session.save()
connection.close()
nginx=Path('/private/tmp/platform-local44-20261106/pool/nginx.conf').read_text()
with psycopg.connect(host='127.0.0.1',port=55480,dbname='postgres',user='event_test',autocommit=True) as db:
 for label,backlog,ports in [('baseline',50,(56397,56398)),('increased',128,(56399,56400))]:
  out=ROOT/label;out.mkdir();(out/'nginx-logs').mkdir()
  name='backlog_'+label+'_20261106'
  db.execute(sql.SQL('CREATE DATABASE {} TEMPLATE backlog_template_20261106').format(sql.Identifier(name)))
  current={**env,'POSTGRES_DB':name,'POSTGRES_POOL_MAX_SIZE':'10','LOCAL44_OUT':str(out),'CAMPUS_DATA_DIR':str(out/'data'),
   'MOVEMENT_FRONT_BACKLOG':str(backlog),'CAMPUS_MOVEMENT_REDIS_URL':f'redis://127.0.0.1:{ports[0]}/0','CAMPUS_SERVICE_REDIS_URL':f'redis://127.0.0.1:{ports[1]}/0'}
  (out/'environment.json').write_text(json.dumps(current));(out/'environment.json').chmod(0o600)
  for n in ('sessions.json','metadata.json'):
   shutil.copy2(base/n,out/n);(out/n).chmod(0o600)
  (out/'nginx.conf').write_text(nginx.replace('/private/tmp/platform-local44-20261106/pool',str(out)))
  for role,port in zip(('movement','service'),ports):
   container=f'platform-backlog-{label}-{role}-redis-20261106'
   subprocess.run(['docker','run','-d','--name',container,'-p',f'127.0.0.1:{port}:6379','redis:7.0.15','redis-server','--save','','--appendonly','no'],check=True,stdout=subprocess.DEVNULL)
print('Two fresh DB copies and independent Redis stores prepared; same sessions renewed in common template')
