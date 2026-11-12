"""Local-only role entrypoints. Production source and the system clock stay unchanged."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path('/private/tmp/platform-query-source-20261106')
OUT=Path(os.environ.get('LOCAL44_OUT','/private/tmp/platform-ws-20261106/template'))
sys.path.insert(0,str(ROOT/'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.conf import settings
assert settings.DATABASES['default']['PORT']=='55480'
assert settings.DATABASES['default']['NAME'].startswith('query_')
assert settings.CAMPUS_MOVEMENT_REDIS_URL in ('redis://127.0.0.1:57411/0','redis://127.0.0.1:57413/0')
assert settings.CAMPUS_SERVICE_REDIS_URL in ('redis://127.0.0.1:57412/0','redis://127.0.0.1:57414/0')
assert not settings.DEBUG
if os.environ.get('LOCAL33_DIAGNOSTICS')=='1':
    import diagnostics
    settings.MIDDLEWARE=['diagnostics.SQLTimingMiddleware',*settings.MIDDLEWARE]
    diagnostics.install_socket_probe()
from event_story import main
if os.environ.get('LOCAL33_EVENT_OPEN')=='1':
    main.window=lambda:{'phase':'open','preview':False,'end':'2026-11-11T22:00:00+09:00','localTestWindowOverride':True}
from config.movement_asgi import application as movement
from config.service_asgi import application as service
identity=os.environ['LOCAL33_WORKER']
(OUT/f'attestation-{identity}.json').write_text(json.dumps({'worker':identity,'pid':os.getpid(),
    'database':settings.DATABASES['default']['NAME'],'db_port':settings.DATABASES['default']['PORT'],
    'movement_redis':settings.CAMPUS_MOVEMENT_REDIS_URL,'service_redis':settings.CAMPUS_SERVICE_REDIS_URL,
    'secret_fingerprint':hashlib.sha256(settings.SECRET_KEY.encode()).hexdigest(),
    'debug':settings.DEBUG,'event_window_override':os.environ.get('LOCAL33_EVENT_OPEN')=='1','pool_options':settings.DATABASES['default']['OPTIONS'].get('pool'), 'connection_max_age':settings.DATABASES['default']['CONN_MAX_AGE']}))
last_snapshot=0
def pool_snapshot():
    global last_snapshot
    now=time.monotonic()
    if now-last_snapshot<1:return
    last_snapshot=now
    from django.db import connection
    pool=connection.pool if settings.DATABASES['default']['OPTIONS'].get('pool') else None
    with (OUT/f'pool-stats-{identity}.jsonl').open('a') as log:
        log.write(json.dumps({'at':time.time(),'worker':identity,'stats':pool.get_stats() if pool else None})+'\n')

async def serve(app,scope,receive,send):
    async def traced(message):
        if message['type'] in ('http.response.start','websocket.accept'):
            message={**message,'headers':[*message.get('headers',[]),(b'x-local33-worker',identity.encode())]}
        await send(message)
        pool_snapshot()
    await app(scope,receive,traced)
async def movement_app(scope,receive,send):await serve(movement,scope,receive,send)
async def service_app(scope,receive,send):await serve(service,scope,receive,send)

if __name__=='__main__':
    from django.core.management import execute_from_command_line
    execute_from_command_line(['manage.py','campus_jobs'])
