"""Clone the same owned fixture DB and sessions; only pool enablement differs."""
import json
from pathlib import Path
import shutil
import psycopg
from psycopg import sql

ROOT=Path('/private/tmp/platform-local44-20261106')
base=ROOT/'template'
env=json.loads((base/'environment.json').read_text())
assert env['POSTGRES_DB']=='local44_template_20261106' and env['POSTGRES_PORT']=='55480'
config=Path('/private/tmp/platform-local33-20261106/nginx-native.conf').read_text()
config=config.replace('/private/tmp/platform-local33-source-20261106','/private/tmp/platform-local44-source-20261106')
config=config.replace('server 127.0.0.1:9553;','server 127.0.0.1:9553;\nserver 127.0.0.1:9554;')
config=config.replace('server 127.0.0.1:9563;','server 127.0.0.1:9563;\nserver 127.0.0.1:9564;')
with psycopg.connect(host='127.0.0.1',port=55480,dbname='postgres',user='event_test',autocommit=True) as db:
 for label,size in [('nopool',0),('pool',10)]:
  out=ROOT/label;out.mkdir(exist_ok=True)
  name='local44_'+label+'_20261106'
  assert not db.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone(),name
  db.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(name),sql.Identifier(env['POSTGRES_DB'])))
  updated={**env,'POSTGRES_DB':name,'POSTGRES_POOL_MAX_SIZE':str(size),'LOCAL44_OUT':str(out),'CAMPUS_DATA_DIR':str(out/'data')}
  (out/'environment.json').write_text(json.dumps(updated));(out/'environment.json').chmod(0o600)
  for n in ['sessions.json','metadata.json']:
   shutil.copy2(base/n,out/n);(out/n).chmod(0o600)
  (out/'nginx-logs').mkdir(exist_ok=True)
  (out/'nginx.conf').write_text(config.replace('/private/tmp/platform-local33-20261106',str(out)))
print('Fresh A/B databases share identical user IDs, sessions, questions and prerequisite state')
