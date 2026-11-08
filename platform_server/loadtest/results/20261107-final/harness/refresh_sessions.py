"""Refresh test login activity before each condition, preserving keys and UUIDs."""
import json,os,sys
from pathlib import Path
OUT=Path(os.environ['LOCAL44_OUT']);env=json.loads((OUT/'environment.json').read_text())
assert env['POSTGRES_DB'].startswith('query_http128_') and env['POSTGRES_PORT']=='55500'
os.environ.update(env);sys.path.insert(0,'/private/tmp/platform-final-integration-20261107/source/backend')
import django
django.setup()
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.auth import SESSION_KEY
from accounts.session_policy import start_login
from django.db import connection
keys=json.loads((OUT/'sessions.json').read_text());assert len(keys)==100

import psycopg
from django.contrib.sessions.models import Session
from django.utils import timezone
from datetime import timedelta
with psycopg.connect(host='127.0.0.1',port=55500,dbname='backlog_template_20261106',user='event_test') as template:
    originals=dict(template.execute('SELECT session_key,session_data FROM django_session').fetchall())
for key in keys:
    Session.objects.update_or_create(session_key=key,defaults={'session_data':originals[key],'expire_date':timezone.now()+timedelta(days=1)})
for key in keys:
 session=SessionStore(session_key=key);assert session.get(SESSION_KEY);start_login(session);session.save()
connection.close();print('Refreshed 100 owned test sessions; cookie keys retained')
