"""Owned 4+4 integration stack; never changes the existing development servers."""
import hashlib,json,os,secrets,signal,socket,subprocess,sys,time
from pathlib import Path

REPO=Path('/private/tmp/platform-final-integration-20261107/source')
OUT=Path('/private/tmp/platform-final-integration-20261107')
PY='/private/tmp/platform-event-postgres-venv/bin/python'
NAMES=['final-it-pg-20261107','final-it-move-20261107','final-it-service-20261107']

def command(args,**kwargs):
    return subprocess.run(args,check=True,**kwargs)

def stop():
    if not OUT.exists():return
    for row in reversed(json.loads((OUT/'processes.json').read_text())):
        try:
            args=subprocess.check_output(['ps','-p',str(row['pid']),'-o','args='],text=True)
            if row['marker'] not in args:raise RuntimeError('PID ownership mismatch')
            os.kill(row['pid'],signal.SIGTERM)
        except (ProcessLookupError,subprocess.CalledProcessError):pass
    for name in ('entry.pid','internal.pid'):
        try:os.kill(int((OUT/name).read_text()),signal.SIGQUIT)
        except (ProcessLookupError,FileNotFoundError):pass
    command(['docker','stop',*NAMES],stdout=subprocess.DEVNULL)
    print('Owned stack stopped; logs and test DB container retained.')

if len(sys.argv)>1 and sys.argv[1]=='stop':
    stop();raise SystemExit
assert not (OUT/'environment.json').exists(),'Refuse to replace an existing test run'
ports=[55500,57541,57542,8116,8117,8118,9601,9602,*range(9751,9755),*range(9761,9765)]
for port in ports:
    with socket.socket() as listener:listener.bind(('127.0.0.1',port))
OUT.mkdir(exist_ok=True);(OUT/'logs').mkdir(exist_ok=True);(OUT/'static').mkdir()
command(['docker','run','-d','--name',NAMES[0],'-p','127.0.0.1:55500:5432','-e','POSTGRES_USER=receipt_test','-e','POSTGRES_DB=receipt_final_20261107','-e','POSTGRES_HOST_AUTH_METHOD=trust','postgres:17.7','-c','max_connections=100'],stdout=subprocess.DEVNULL)
for name,port in zip(NAMES[1:],[57541,57542]):
    command(['docker','run','-d','--name',name,'-p',f'127.0.0.1:{port}:6379','redis:7.0.15','redis-server','--save','','--appendonly','no'],stdout=subprocess.DEVNULL)
import psycopg
for attempt in range(100):
    try:
        with psycopg.connect(host='127.0.0.1',port=55500,user='receipt_test',dbname='receipt_final_20261107',connect_timeout=1):pass
        break
    except psycopg.OperationalError:time.sleep(.2)
else:raise RuntimeError('Owned PostgreSQL did not start')
env={**os.environ,'DJANGO_SETTINGS_MODULE':'config.settings','DATABASE_ENGINE':'postgresql',
 'DJANGO_SECRET_KEY':secrets.token_urlsafe(40),'FRONTEND_SECRET_KEY':secrets.token_urlsafe(40),
 'DJANGO_DEBUG':'0','FRONTEND_DEBUG':'0','DJANGO_ALLOWED_HOSTS':'127.0.0.1,localhost',
 'FRONTEND_ALLOWED_HOSTS':'127.0.0.1,localhost','DJANGO_CSRF_TRUSTED_ORIGINS':'http://127.0.0.1:8116',
 'POSTGRES_HOST':'127.0.0.1','POSTGRES_PORT':'55500','POSTGRES_USER':'receipt_test','POSTGRES_PASSWORD':'REDACTED',
 'POSTGRES_DB':'receipt_final_20261107','POSTGRES_POOL_MAX_SIZE':'10',
 'CAMPUS_REDIS_URL':'','CAMPUS_MOVEMENT_REDIS_URL':'redis://127.0.0.1:57541/0',
 'CAMPUS_SERVICE_REDIS_URL':'redis://127.0.0.1:57542/0','CAMPUS_DATA_DIR':str(OUT/'data'),
 'CAMPUS_SESSION_CLEANUP_ENABLED':'0','CAMPUS_LOGIN_MAX_AGE_SECONDS':'7200',
 'CAMPUS_LOGIN_IDLE_TIMEOUT_SECONDS':'3600','KAFKA_BOOTSTRAP_SERVERS':'',
 'BACKEND_URL':'http://127.0.0.1:8118','MOVEMENT_BACKEND_URL':'http://127.0.0.1:8117',
 'PYTHONUNBUFFERED':'1'}
(OUT/'environment.json').write_text(json.dumps(env));(OUT/'environment.json').chmod(0o600)
os.environ.update(env);sys.path.insert(0,str(REPO/'backend'))
import django
django.setup()
from django.core.management import call_command
call_command('migrate',verbosity=0,interactive=False)
from django.contrib.auth import get_user_model
from django.contrib.auth import SESSION_KEY,BACKEND_SESSION_KEY,HASH_SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.core import signing
from django.utils import timezone
from django.db import connections
from accounts.session_policy import start_login
from tests.factories import create_profile
from event_story import main
from event_story.access import SALT
from event_story.models import StoryProgress,StoryRecord
main.catalog()
rows=[]
for index in range(101):
    user=get_user_model().objects.create_user(username=f'final-it-{index:03}')
    create_profile(user=user,nickname=f'통합{index:03}',guide_completed=True,
        last_position={'floor':'campus','x':1250.25,'y':195,'dir':'up'},last_position_at=timezone.now())
    session=SessionStore()
    session[SESSION_KEY]=str(user.pk);session[BACKEND_SESSION_KEY]='django.contrib.auth.backends.ModelBackend'
    session[HASH_SESSION_KEY]=user.get_session_auth_hash()
    start_login(session,user);session.save()
    session_uuid=session['platform_session_uuid']
    if index<100:
        for code in ('1','3','4','5',*[c for c,_ in main.GAMES]):main.complete(user,code,session_uuid)
        progress=StoryProgress.objects.create(user=user,event_key='pepero-2026-room1-dialogue')
        StoryRecord.objects.create(progress=progress,key='yeongseok-intro',kind='npc_dialogue')
    rows.append({'user_id':str(user.pk),'session_key':session.session_key,'session_uuid':session_uuid,
        'tickets':{target:signing.dumps({'user':str(user.pk),'target':target},salt=SALT)
            for target in ('roof-guide','investigate','room1','room2','final-guide')}})
(OUT/'clients.json').write_text(json.dumps({'users':rows[:100],'browser':rows[100]}))
(OUT/'clients.json').chmod(0o600)
connections.close_all()
for name,path in [('campus',REPO/'frontend/dist'),('learning',REPO/'frontend/learning/static/learning'),('accounts',REPO/'frontend/accounts/static/accounts')]:
    (OUT/'static'/name).symlink_to(path,target_is_directory=True)
runtime="""import asyncio,hashlib,json,os,sys,time
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
"""
(OUT/'runtime.py').write_text(runtime)
rows=[]
def launch(name,args,cwd,local,marker):
    log=(OUT/'logs'/f'{name}.log').open('w')
    process=subprocess.Popen(args,cwd=cwd,env=local,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    rows.append({'role':name,'pid':process.pid,'marker':marker})
    (OUT/'processes.json').write_text(json.dumps(rows))
for role,ports in [('movement',range(9751,9755)),('service',range(9761,9765))]:
    for port in ports:
        local={**env,'INTEGRATION_WORKER':f'{role}-{port}','PGAPPNAME':f'receipt-{role}-{port}','PYTHONPATH':str(OUT)+':'+str(REPO/'backend')}
        launch(f'{role}-{port}',[PY,'-B','-m','daphne','-e',f'tcp:port={port}:interface=127.0.0.1:backlog=128',f'runtime:{role}_app'],REPO/'backend',local,f'port={port}')
local={**env,'INTEGRATION_WORKER':'jobs','PGAPPNAME':'receipt-jobs','PYTHONPATH':str(OUT)+':'+str(REPO/'backend')}
launch('jobs',[PY,'-B',str(OUT/'runtime.py')],REPO/'backend',local,str(OUT/'runtime.py'))
for role,port in [('movement',9601),('service',9602)]:
    local={**env,'PYTHONPATH':str(REPO/'frontend')}
    launch('front-'+role,[PY,'-B','-m','daphne','-e',f'tcp:port={port}:interface=127.0.0.1:backlog=128',f'config.{role}_asgi:application'],REPO/'frontend',local,f'port={port}')
template=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse/loadtest/results/20261107-http128/a2')
for name in ('internal.conf','entry.conf'):
    source=(template/name).read_text().replace('/private/tmp/platform-http128-20261107/a2',str(OUT)).replace('/private/tmp/platform-ws-20261106/staticfiles',str(OUT/'static')).replace('0.0.0.0:8096','127.0.0.1:8096')
    for old,new in [(8096,8116),(8097,8117),(8098,8118),(9581,9601),(9582,9602),*zip(range(9551,9555),range(9751,9755)),*zip(range(9561,9565),range(9761,9765))]:
        source=source.replace(str(old),str(new))
    source=source.replace('nginx-logs/native-access.log','logs/nginx-access.log')
    (OUT/name).write_text(source)
    command(['/opt/homebrew/bin/nginx','-t','-p',str(OUT)+'/', '-c',str(OUT/name)],stdout=subprocess.DEVNULL)
    command(['/opt/homebrew/bin/nginx','-p',str(OUT)+'/', '-c',str(OUT/name)])
for port in [8116,8117,8118,9601,9602,*range(9751,9755),*range(9761,9765)]:
    for attempt in range(100):
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.2):pass
            break
        except OSError:time.sleep(.1)
    else:raise RuntimeError(f'Port {port} unavailable')
manifest={'users':100,'browserUsers':1,'movementWorkers':4,'serviceWorkers':4,'frontends':2,'jobs':1,
 'postgresVersion':'17.7','postgresPort':55500,'maxConnections':100,'poolMin':1,'poolMax':10,
 'entryPort':8116,'movementRedisPort':57541,'serviceRedisPort':57542,'windowOverride':True,
 'sourceFiles':{str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [REPO/'backend/event_story/main.py',REPO/'frontend/dist/main-quest.js',REPO/'frontend/dist/index.html']}}
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps(manifest),flush=True)
