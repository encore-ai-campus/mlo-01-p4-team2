"""Isolated local load-test environment. Never uses the live database."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

ROOT = Path('/private/tmp/platform-event-pr-53')
OUT = Path('/private/tmp/platform-load100-20261105')
PYTHON = '/private/tmp/platform-event-postgres-venv/bin/python'
OUT.mkdir(exist_ok=True)
env = os.environ.copy()
env.update(DATABASE_ENGINE='postgresql', POSTGRES_DB='load100_20261105',
    POSTGRES_USER='event_test', POSTGRES_PASSWORD='REDACTED', POSTGRES_HOST='127.0.0.1',
    POSTGRES_PORT='55479', DJANGO_SETTINGS_MODULE='config.settings',
    DJANGO_SECRET_KEY=secrets.token_hex(32), DJANGO_DEBUG='1', EVENT_STORY_PREVIEW='1',
    ADS_ENABLED='0', CAMPUS_DATA_DIR=str(OUT / 'data'), CAMPUS_REDIS_URL='',
    CAMPUS_MOVEMENT_REDIS_URL='', CAMPUS_SERVICE_REDIS_URL='',
    CAMPUS_SESSION_CLEANUP_ENABLED='0', BACKEND_URL='http://127.0.0.1:9055',
    MOVEMENT_BACKEND_URL='http://127.0.0.1:9055', PYTHONDONTWRITEBYTECODE='1')
import psycopg
for attempt in range(30):
    try:
        db = psycopg.connect(dbname='postgres', user='event_test', host='127.0.0.1', port=55479, autocommit=True)
        break
    except psycopg.OperationalError:
        if attempt == 29: raise
        time.sleep(.2)
with db:
    assert db.execute('SHOW data_directory').fetchone()[0] == '/tmp/load100-data'
    if not db.execute('SELECT 1 FROM pg_database WHERE datname=%s', ('load100_20261105',)).fetchone():
        db.execute('CREATE DATABASE load100_20261105')
(OUT / 'environment.json').write_text(json.dumps(env))
(OUT / 'environment.json').chmod(0o600)
subprocess.run([PYTHON, 'manage.py', 'migrate', '--noinput'], cwd=ROOT / 'backend', env=env, check=True,
    stdout=(OUT / 'migrations.log').open('w'), stderr=subprocess.STDOUT)
os.environ.update(env)
sys.path.insert(0, str(ROOT / 'backend'))
import django
django.setup()
from django.core.management import call_command
call_command('seed_learning_questions', verbosity=0)
from django.contrib.auth import get_user_model, SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY
from django.conf import settings
from importlib import import_module
from accounts.session_policy import start_login
from tests.factories import create_profile
from event_story import main
main.catalog()
keys = []
for i in range(100):
    user = get_user_model().objects.create_user(username=f'load{i:03d}')
    user.set_unusable_password(); user.save()
    create_profile(user=user, nickname=f'부하{i:03d}', setup_complete=True, guide_completed=True)
    session = import_module(settings.SESSION_ENGINE).SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = 'django.contrib.auth.backends.ModelBackend'
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    start_login(session, user)
    keys.append(session.session_key)
(OUT / 'sessions.json').write_text(json.dumps(keys))
(OUT / 'sessions.json').chmod(0o600)
pids = []
for directory, args, name in [('backend', ['run-campus.py', '--port', '9055', '--bind', '127.0.0.1', '--noreload'], 'backend'),
        ('frontend', ['manage.py', 'runserver', '127.0.0.1:8055', '--noreload'], 'frontend')]:
    process = subprocess.Popen([PYTHON, '-B', *args], cwd=ROOT / directory, env=env,
        stdout=(OUT / f'{name}.log').open('w'), stderr=subprocess.STDOUT, start_new_session=True)
    pids.append(process.pid)
(OUT / 'server-pids.json').write_text(json.dumps(pids))
print('Isolated users=100; PostgreSQL=55479; frontend=8055; backend=9055; pids=', pids)
