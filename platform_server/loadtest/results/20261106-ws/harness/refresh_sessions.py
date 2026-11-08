"""Refresh test login activity before each condition, preserving keys and UUIDs."""
import json,os,sys
from pathlib import Path
OUT=Path(os.environ['LOCAL44_OUT']);env=json.loads((OUT/'environment.json').read_text())
assert env['POSTGRES_DB'] in ('query_ws_front_20261106_v2','query_ws_direct_20261106_v2') and env['POSTGRES_PORT']=='55480'
os.environ.update(env);sys.path.insert(0,'/private/tmp/platform-query-source-20261106/backend')
import django
django.setup()
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.auth import SESSION_KEY
from accounts.session_policy import start_login
from django.db import connection
keys=json.loads((OUT/'sessions.json').read_text());assert len(keys)==100
for key in keys:
 session=SessionStore(session_key=key);assert session.get(SESSION_KEY);start_login(session);session.save()
connection.close();print('Refreshed 100 owned test sessions; cookie keys retained')
