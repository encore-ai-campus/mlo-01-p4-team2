import asyncio,hashlib,json,os,sys,time
from pathlib import Path
ROOT=Path('/private/tmp/platform-final-integration-20261107/source')
OUT=Path('/private/tmp/platform-final-integration-20261107')
sys.path.insert(0,str(ROOT/'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.conf import settings
from event_story import main
main.window=lambda:{'phase':'open','preview':False,'end':'2026-11-11T22:00:00+09:00','integrationWindowOverride':True}
original_status=main.status
def fault_status(user):
    path=OUT/'status-fault.json'
    if path.exists():
        fault=json.loads(path.read_text())
        if fault.get('mode')=='fail':raise RuntimeError('Intentional integration status failure')
        if fault.get('mode')=='delay':time.sleep(fault.get('seconds',2))
    return original_status(user)
main.status=fault_status
from config.movement_asgi import application as movement
from config.service_asgi import application as service
identity=os.environ['INTEGRATION_WORKER']
(OUT/('attestation-'+identity+'.json')).write_text(json.dumps({'pid':os.getpid(),'worker':identity,'db':settings.DATABASES['default']['NAME'],'port':settings.DATABASES['default']['PORT'],'pool':settings.DATABASES['default']['OPTIONS']['pool'],'debug':settings.DEBUG,'windowOverride':True,'files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'backend/event_story/main.py',ROOT/'frontend/dist/main-quest.js']}}))
async def serve(app,scope,receive,send):
    async def traced(message):
        if message['type'] in ('http.response.start','websocket.accept'):
            message={**message,'headers':[*message.get('headers',[]),(b'x-integration-worker',identity.encode())]}
        await send(message)
    await app(scope,receive,traced)
async def movement_app(scope,receive,send):await serve(movement,scope,receive,send)
async def service_app(scope,receive,send):await serve(service,scope,receive,send)
if __name__=='__main__':
    from django.core.management import execute_from_command_line
    execute_from_command_line(['manage.py','campus_jobs'])
