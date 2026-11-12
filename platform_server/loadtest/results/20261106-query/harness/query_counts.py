"""Count queries on the same completed test fixture, outside load measurements."""
import hashlib,json,os,sys
from pathlib import Path
ROOT=Path('/private/tmp/platform-query-20261106');label=sys.argv[1]
assert label in ('before','after')
source=Path('/private/tmp/platform-query-source-20261106') if label=='before' else Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
env=json.loads((ROOT/'before/environment.json').read_text());assert env['POSTGRES_DB']=='query_before_20261106'
os.environ.update(env);sys.path.insert(0,str(source/'backend'))
import django
django.setup()
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from event_story import main
user=get_user_model().objects.get(username='load000')
def count(function):
 with CaptureQueriesContext(connection) as queries:payload=function()
 return {'queries':len(queries),'writes':sum(not q['sql'].lstrip().upper().startswith('SELECT') for q in queries),
  'payload_sha256':hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()}
result={'status_response':count(lambda:main.status(user)),'ranking':count(lambda:main.ranking(user))}
if label=='after':
 def prepared():main.prepare_status(user);return main.status(user)
 result['status_with_preparation']=count(prepared)
(ROOT/f'query-counts-{label}.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
connection.close()
