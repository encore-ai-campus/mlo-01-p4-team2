"""Fresh PostgreSQL plus reference questions copied read-only from the current local DB."""
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import psycopg
from psycopg import sql

ROOT=Path('/private/tmp/platform-local33-source-20261106')
OUT=Path('/private/tmp/platform-local33-20261106')
HERE=Path(__file__).resolve().parent
env=os.environ.copy()
env.update(DATABASE_ENGINE='postgresql',POSTGRES_HOST='127.0.0.1',POSTGRES_PORT='55480',POSTGRES_DB='local33_20261106',
    POSTGRES_USER='event_test',POSTGRES_PASSWORD='REDACTED',DJANGO_SECRET_KEY=secrets.token_hex(32),FRONTEND_SECRET_KEY=secrets.token_hex(32),
    DJANGO_DEBUG='0',FRONTEND_DEBUG='0',EVENT_STORY_PREVIEW='0',ADS_ENABLED='0',
    DJANGO_SETTINGS_MODULE='config.settings',DJANGO_ALLOWED_HOSTS='localhost,127.0.0.1,host.docker.internal',
    FRONTEND_ALLOWED_HOSTS='localhost,127.0.0.1,host.docker.internal',DJANGO_CSRF_TRUSTED_ORIGINS='http://127.0.0.1:8096,http://localhost:8096',
    CAMPUS_DATA_DIR=str(OUT/'data'),CAMPUS_REDIS_URL='',CAMPUS_MOVEMENT_REDIS_URL='redis://127.0.0.1:56391/0',
    CAMPUS_SERVICE_REDIS_URL='redis://127.0.0.1:56392/0',CAMPUS_SESSION_CLEANUP_ENABLED='0',
    BACKEND_URL='http://127.0.0.1:8098',MOVEMENT_BACKEND_URL='http://127.0.0.1:8097',PYTHONDONTWRITEBYTECODE='1')
for attempt in range(50):
    try:db=psycopg.connect(dbname='postgres',user='event_test',host='127.0.0.1',port=55480,autocommit=True);break
    except psycopg.OperationalError:
        if attempt==49:raise
        time.sleep(.2)
assert db.execute('SHOW data_directory').fetchone()[0]=='/tmp/local33-data'
assert not db.execute('SELECT 1 FROM pg_database WHERE datname=%s',('local33_20261106',)).fetchone(),'Use a fresh owned database'
db.execute('CREATE DATABASE local33_20261106');db.close()
(OUT/'environment.json').write_text(json.dumps(env));(OUT/'environment.json').chmod(0o600)
with (OUT/'migrations.log').open('w') as log:
    subprocess.run([sys.executable,'-B','manage.py','migrate','--noinput'],cwd=ROOT/'backend',env=env,check=True,stdout=log,stderr=subprocess.STDOUT)
source=json.loads(Path('/private/tmp/platform-recipe-server-state.json').read_text())['backend']['env']
with psycopg.connect(dbname=source['POSTGRES_DB'],user=source['POSTGRES_USER'],password=source.get('POSTGRES_PASSWORD',''),
        host=source['POSTGRES_HOST'],port=source['POSTGRES_PORT'],options='-c default_transaction_read_only=on') as original:
    with psycopg.connect(dbname=env['POSTGRES_DB'],user='event_test',host='127.0.0.1',port=55480) as target:
        for table,key in [('learning_question','question_id'),('learning_questionchoice','choice_id')]:
            assert target.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]==0
            data=io.BytesIO()
            with original.cursor().copy(sql.SQL('COPY {} TO STDOUT (FORMAT BINARY)').format(sql.Identifier(table))) as stream:
                for block in stream:data.write(block)
            with target.cursor().copy(sql.SQL('COPY {} FROM STDIN (FORMAT BINARY)').format(sql.Identifier(table))) as stream:stream.write(data.getvalue())
            target.execute(sql.SQL("SELECT setval(pg_get_serial_sequence(%s,%s),(SELECT max({}) FROM {}))").format(sql.Identifier(key),sql.Identifier(table)),(table,key))
        assert target.execute('SELECT count(*) FROM learning_question').fetchone()[0]==3600
os.environ.update(env);sys.path.insert(0,str(ROOT/'backend'))
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.contrib.auth import get_user_model,SESSION_KEY,BACKEND_SESSION_KEY,HASH_SESSION_KEY
from importlib import import_module
from accounts.session_policy import start_login
from tests.factories import create_profile
from event_story import main
main.catalog();keys=[]
for i in range(100):
    user=get_user_model().objects.create_user(username=f'load{i:03d}')
    user.set_unusable_password();user.save()
    create_profile(user=user,nickname=f'부하{i:03d}',setup_complete=True,guide_completed=True,
        last_position={'floor':'campus','x':640,'y':480,'dir':'up'})
    session=import_module(settings.SESSION_ENGINE).SessionStore()
    session[SESSION_KEY]=str(user.pk);session[BACKEND_SESSION_KEY]='django.contrib.auth.backends.ModelBackend'
    session[HASH_SESSION_KEY]=user.get_session_auth_hash();start_login(session,user);keys.append(session.session_key)
(OUT/'sessions.json').write_text(json.dumps(keys));(OUT/'sessions.json').chmod(0o600)
frontenv={**env,'DJANGO_SETTINGS_MODULE':'config.settings','PYTHONPATH':str(ROOT/'frontend')}
subprocess.run([sys.executable,'-B','manage.py','collectstatic','--noinput'],cwd=ROOT/'frontend',env=frontenv,check=True,
    stdout=(OUT/'static.log').open('w'),stderr=subprocess.STDOUT)
def upstream(name,ports,sticky=False):
    lines=[f'upstream {name} {{', 'hash $cookie_campus_sessionid consistent;' if sticky else 'least_conn;']
    lines += [f'server host.docker.internal:{p};' for p in ports];return '\n'.join([*lines,'}'])
config='''worker_processes auto;
events { worker_connections 8192; }
http {
include /etc/nginx/mime.types;
log_format measurements '$msec|$server_port|$status|$upstream_addr|$request_time|$upstream_response_time|$uri';
access_log /logs/access.log measurements;
map $http_upgrade $connection_upgrade { default upgrade; '' close; }
proxy_http_version 1.1;
proxy_set_header Host $http_host;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection $connection_upgrade;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_read_timeout 3600s;
proxy_connect_timeout 5s;
client_max_body_size 1m;
'''
config+=upstream('movement_workers',range(9551,9554))+'\n'+upstream('service_workers',range(9561,9564),True)+'\n'
config+='''server { listen 80;
location = /healthz { return 200 'local33 edge ready'; }
location /static/ { alias /static/; }
location = /ws/movement/ { proxy_pass http://host.docker.internal:9581; }
location = /ws/campus/ { return 404; }
location / { proxy_pass http://host.docker.internal:9582; }
}
server { listen 81;
location = /ws/movement/ { proxy_pass http://movement_workers; }
location / { return 404; }
}
server { listen 82;
location = /ws/movement/ { return 404; }
location = /ws/campus/ { return 404; }
location / { proxy_pass http://service_workers; }
}
}
'''
(OUT/'nginx.conf').write_text(config);(OUT/'nginx-logs').mkdir(exist_ok=True)
print('Fresh DB with 3600 reference questions, 100 isolated accounts, production-mode environment ready')
