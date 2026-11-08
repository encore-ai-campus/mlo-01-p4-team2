"""Prepare identical query comparisons: pool10 and movement backlog128."""
import hashlib,json,shutil,subprocess
from pathlib import Path
import psycopg
from psycopg import sql
ROOT=Path('/private/tmp/platform-query-20261106');ROOT.mkdir()
old=Path('/private/tmp/platform-backlog-20261106/template')
env=json.loads((old/'environment.json').read_text());initial={}
with psycopg.connect(host='127.0.0.1',port=55480,dbname='postgres',user='event_test',autocommit=True) as db:
 assert db.execute('SHOW data_directory').fetchone()[0]=='/tmp/local33-data'
 for label,ports in [('before',(56401,56402)),('after',(56403,56404))]:
  out=ROOT/label;out.mkdir();(out/'nginx-logs').mkdir()
  name='query_'+label+'_20261106'
  db.execute(sql.SQL('CREATE DATABASE {} TEMPLATE backlog_template_20261106').format(sql.Identifier(name)))
  current={**env,'POSTGRES_DB':name,'POSTGRES_POOL_MAX_SIZE':'10','LOCAL44_OUT':str(out),'CAMPUS_DATA_DIR':str(out/'data'),
   'MOVEMENT_FRONT_BACKLOG':'128','CAMPUS_MOVEMENT_REDIS_URL':f'redis://127.0.0.1:{ports[0]}/0','CAMPUS_SERVICE_REDIS_URL':f'redis://127.0.0.1:{ports[1]}/0'}
  (out/'environment.json').write_text(json.dumps(current));(out/'environment.json').chmod(0o600)
  for n in ('sessions.json','metadata.json'):shutil.copy2(old/n,out/n);(out/n).chmod(0o600)
  nginx=Path('/private/tmp/platform-backlog-20261106/increased/nginx.conf').read_text()
  (out/'nginx.conf').write_text(nginx.replace('/private/tmp/platform-backlog-20261106/increased',str(out)))
  for role,port in zip(('movement','service'),ports):
   subprocess.run(['docker','run','-d','--name',f'platform-query-{label}-{role}-redis-20261106','-p',f'127.0.0.1:{port}:6379',
    'redis:7.0.15','redis-server','--save','','--appendonly','no'],check=True,stdout=subprocess.DEVNULL)
  with psycopg.connect(host='127.0.0.1',port=55480,dbname=name,user='event_test') as check:
   counts={t:check.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0]
    for t in ('auth_user','django_session','learning_question','event_progress','event_story_storyprogress')}
  initial[label]={'row_counts':counts,'sessions_sha256':hashlib.sha256((out/'sessions.json').read_bytes()).hexdigest(),
   'initial_redis_counts':{'CAMPUS_MOVEMENT_REDIS_URL':0,'CAMPUS_SERVICE_REDIS_URL':0},'movement_front_backlog':128,'database':name}
assert initial['before']['row_counts']==initial['after']['row_counts']
(ROOT/'initial.json').write_text(json.dumps(initial,indent=2))
(ROOT/'active.json').write_text(json.dumps({'label':'/private/tmp/platform-backlog-20261106/increased'}))
print('Two DB copies and four dedicated Redis stores prepared; pool10/backlog128 fixed')
