"""Equal optimized DBs; two native Nginx processes, with only WS destinations differing."""
import hashlib,json,shutil,subprocess
from pathlib import Path
import psycopg,redis
from psycopg import sql
ROOT=Path('/private/tmp/platform-ws-20261106');ROOT.mkdir()
OLD=Path('/private/tmp/platform-backlog-20261106/template')
env=json.loads((OLD/'environment.json').read_text())
ip=subprocess.check_output(['ipconfig','getifaddr','en0'],text=True).strip()
assert ip.startswith(('10.','172.','192.168.'))
static=ROOT/'staticfiles';shutil.copytree('/private/tmp/platform-local44-source-20261106/frontend/staticfiles',static)
shutil.copytree('/private/tmp/platform-final-integration-20261107/source/frontend/dist',static/'campus',dirs_exist_ok=True)
initial={}
common='''worker_processes 1;
events { worker_connections 8192; }
http {
log_format measurements '$msec|$server_port|$status|$upstream_addr|$request_time|$upstream_response_time|$uri';
access_log ACCESS measurements;
proxy_http_version 1.1;
map $http_upgrade $connection_upgrade { default upgrade; '' close; }
proxy_set_header Host $http_host;
proxy_set_header Cookie $http_cookie;
proxy_set_header Origin $http_origin;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection $connection_upgrade;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_connect_timeout 5s;
proxy_read_timeout 3600s;
client_max_body_size 1m;
'''
with psycopg.connect(host='127.0.0.1',port=55500,dbname='postgres',user='event_test',autocommit=True) as db:
 assert db.execute('SHOW data_directory').fetchone()[0]=='/tmp/local33-data'
 for label,ports in [('front',(57411,57412)),('direct',(57413,57414))]:
  out=ROOT/label;out.mkdir();(out/'nginx-logs').mkdir()
  name='query_ws_'+label+'_20261106_v2'
  db.execute(sql.SQL('CREATE DATABASE {} TEMPLATE backlog_template_20261106').format(sql.Identifier(name)))
  current={**env,'POSTGRES_DB':name,'POSTGRES_POOL_MAX_SIZE':'10','LOCAL44_OUT':str(out),'CAMPUS_DATA_DIR':str(out/'data'),
   'MOVEMENT_FRONT_BACKLOG':'128','DJANGO_ALLOWED_HOSTS':env.get('DJANGO_ALLOWED_HOSTS','127.0.0.1,localhost')+','+ip,
   'CAMPUS_MOVEMENT_REDIS_URL':f'redis://127.0.0.1:{ports[0]}/0','CAMPUS_SERVICE_REDIS_URL':f'redis://127.0.0.1:{ports[1]}/0'}
  (out/'environment.json').write_text(json.dumps(current));(out/'environment.json').chmod(0o600)
  for n in ('sessions.json','metadata.json'):shutil.copy2(OLD/n,out/n);(out/n).chmod(0o600)
  for role,port in zip(('movement','service'),ports):
   subprocess.run(['docker','run','-d','--name',f'platform-ws-{label}-{role}-20261106-v2','-p',f'127.0.0.1:{port}:6379','redis:7.0.15','redis-server','--save','','--appendonly','no'],check=True,stdout=subprocess.DEVNULL)
   assert redis.Redis(port=port).dbsize()==0
  header=common.replace('ACCESS',str(out/'nginx-logs/native-access.log'))
  entry=f'pid {out}/entry.pid;\nerror_log {out}/entry-error.log;\n'+header
  entry+=f'''include /opt/homebrew/etc/nginx/mime.types;
server {{ listen 0.0.0.0:8116;
location = /healthz {{ return 200 'WS A/B edge ready'; }}
location /static/ {{ alias {static}/; }}
location = /ws/movement/ {{ proxy_pass http://127.0.0.1:{9581 if label=='front' else 8097}; }}
location = /ws/chat/ {{ proxy_pass http://127.0.0.1:{9582 if label=='front' else 8098}; }}
location = /ws/campus/ {{ return 404; }}
location / {{ proxy_pass http://127.0.0.1:9582; }}
}}
}}'''
  internal=f'pid {out}/internal.pid;\nerror_log {out}/internal-error.log;\n'+header
  internal+='upstream movement_workers { least_conn; '+''.join(f'server 127.0.0.1:{p};' for p in range(9551,9555))+'}\n'
  internal+='upstream service_workers { hash $cookie_campus_sessionid consistent; '+''.join(f'server 127.0.0.1:{p};' for p in range(9561,9565))+'}\n'
  internal+='server { listen 127.0.0.1:8097; location = /ws/movement/ { proxy_pass http://movement_workers; } location / { return 404; }}\n'
  internal+='server { listen 127.0.0.1:8098; location = /ws/movement/ { return 404; } location = /ws/campus/ { return 404; } location / { proxy_pass http://service_workers; }}\n}'
  (out/'entry.conf').write_text(entry);(out/'internal.conf').write_text(internal)
  with psycopg.connect(host='127.0.0.1',port=55500,dbname=name,user='event_test') as check:
   counts={t:check.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0] for t in ('auth_user','django_session','learning_question','event_progress')}
  initial[label]={'row_counts':counts,'sessions_sha256':hashlib.sha256((out/'sessions.json').read_bytes()).hexdigest(),'redis_initial':0,'database':name}
assert initial['front']['row_counts']==initial['direct']['row_counts']
(ROOT/'initial.json').write_text(json.dumps({'variants':initial,'private_ip':ip,'same_physical_host':True},indent=2))
(ROOT/'active.json').write_text(json.dumps({'label':'/private/tmp/platform-query-20261106/after'}))
print('Equal DBs, Redis stores and two-Nginx configurations prepared; private IP:',ip)
