"""Local-only role entrypoints. Production source and the system clock stay unchanged."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path('/private/tmp/platform-local33-source-20261106')
OUT=Path('/private/tmp/platform-local33-20261106')
sys.path.insert(0,str(ROOT/'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.conf import settings
assert settings.DATABASES['default']['PORT']=='55480'
assert settings.DATABASES['default']['NAME']=='local33_20261106'
assert settings.CAMPUS_MOVEMENT_REDIS_URL=='redis://127.0.0.1:56391/0'
assert settings.CAMPUS_SERVICE_REDIS_URL=='redis://127.0.0.1:56392/0'
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
    'debug':settings.DEBUG,'event_window_override':os.environ.get('LOCAL33_EVENT_OPEN')=='1'}))
async def serve(app,scope,receive,send):
    async def traced(message):
        if message['type'] in ('http.response.start','websocket.accept'):
            message={**message,'headers':[*message.get('headers',[]),(b'x-local33-worker',identity.encode())]}
        await send(message)
    await app(scope,receive,traced)
async def movement_app(scope,receive,send):await serve(movement,scope,receive,send)
async def service_app(scope,receive,send):await serve(service,scope,receive,send)

if __name__=='__main__':
    from django.core.management import execute_from_command_line
    execute_from_command_line(['manage.py','campus_jobs'])
