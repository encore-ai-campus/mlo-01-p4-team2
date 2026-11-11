"""Reproduce learning connection timeouts in an owned clone, without product edits."""
import hashlib,json,os,shutil,socket,subprocess,sys,time,signal
from pathlib import Path
REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
OLD=Path('/private/tmp/platform-final-integration-20261107-r2')
ROOT=Path('/private/tmp/platform-connect-diagnosis-20261107')
PY='/private/tmp/platform-event-postgres-venv/bin/python'
PG='final-r2-it-pg-20261107'
REDIS=['connect-diag-move-20261107','connect-diag-service-20261107']
DB='receipt_final_connect_diag_20261107'
def command(args,**kw):return subprocess.run(args,check=True,**kw)
def setup():
    assert not ROOT.exists()
    for port in [8116,8117,8118,9601,9602,57541,57542,*range(9751,9755),*range(9761,9765)]:
        with socket.socket() as s:s.bind(('127.0.0.1',port))
    ROOT.mkdir();(ROOT/'logs').mkdir();(ROOT/'trace').mkdir();(ROOT/'harness').mkdir()
    (ROOT/'source').symlink_to(OLD/'source',target_is_directory=True)
    (ROOT/'static').symlink_to(OLD/'static',target_is_directory=True)
    state=json.loads(subprocess.check_output(['docker','inspect',PG],text=True))[0]
    assert state['State']['Status']=='exited','Do not take over a running DB'
    command(['docker','start',PG],stdout=subprocess.DEVNULL)
    (ROOT/'started-pg.json').write_text(json.dumps({'name':PG,'original_state':'exited'}))
    import psycopg
    for _ in range(100):
        try:
            with psycopg.connect(host='127.0.0.1',port=55500,user='receipt_test',dbname='postgres',autocommit=True) as db:
                assert not db.execute('SELECT 1 FROM pg_database WHERE datname=%s',(DB,)).fetchone(),'Never replace an existing diagnostic DB'
                db.execute('CREATE DATABASE '+DB+' TEMPLATE receipt_final_20261107_r2')
            break
        except psycopg.OperationalError:time.sleep(.1)
    else:raise RuntimeError('PostgreSQL unavailable')
    for name,port in zip(REDIS,[57541,57542]):
        command(['docker','run','-d','--name',name,'-p',f'127.0.0.1:{port}:6379','redis:7.0.15','redis-server','--save','','--appendonly','no'],stdout=subprocess.DEVNULL)
    env=json.loads((OLD/'environment.json').read_text())
    env.update(POSTGRES_DB=DB,CAMPUS_DATA_DIR=str(ROOT/'data'),DIAG_ROOT=str(ROOT))
    (ROOT/'environment.json').write_text(json.dumps(env));(ROOT/'environment.json').chmod(0o600)
    os.environ.update(env);sys.path.insert(0,str(ROOT/'source/backend'))
    import django
    django.setup()
    from django.contrib.auth import get_user_model,SESSION_KEY,BACKEND_SESSION_KEY,HASH_SESSION_KEY
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.sessions.models import Session
    from accounts.session_policy import start_login
    from django.utils import timezone
    from datetime import timedelta
    from django.db import connection,connections
    assert connection.settings_dict['NAME']==DB
    clients=json.loads((OLD/'clients.json').read_text())
    for row in clients['users']:
        user=get_user_model().objects.get(pk=row['user_id'])
        store=SessionStore()
        data={SESSION_KEY:str(user.pk),BACKEND_SESSION_KEY:'django.contrib.auth.backends.ModelBackend',HASH_SESSION_KEY:user.get_session_auth_hash()}
        Session.objects.update_or_create(session_key=row['session_key'],defaults={'session_data':store.encode(data),'expire_date':timezone.now()+timedelta(days=1)})
        store=SessionStore(session_key=row['session_key']);start_login(store,user);store.save()
    connections.close_all()
    (ROOT/'clients.json').write_text(json.dumps(clients));(ROOT/'clients.json').chmod(0o600)
    shutil.copy2(OLD/'metadata.json',ROOT/'metadata.json')
    (ROOT/'sessions.json').write_text(json.dumps([u['session_key'] for u in clients['users']]));(ROOT/'sessions.json').chmod(0o600)
    for name in ('fixtures.py','scenarios.py','expectations.py'):
        shutil.copy2(OLD/'harness'/name,ROOT/'harness'/name)
    hooks=(REPO/'backend/tests/integration/connection_diag_hooks.py').read_text()
    (ROOT/'diag_hooks.py').write_text(hooks)
    runtime=(OLD/'runtime.py').read_text().replace(str(OLD),str(ROOT))
    runtime=runtime.replace("if __name__=='__main__':","from diag_hooks import install,wrap\ninstall(True)\nmovement_app=wrap(movement_app)\nservice_app=wrap(service_app)\nif __name__=='__main__':")
    (ROOT/'runtime.py').write_text(runtime)
    (ROOT/'diagfront.py').write_text("from config.service_asgi import application\nfrom diag_hooks import install,wrap\ninstall(False)\napplication=wrap(application)\n")
    launch=(OLD/'harness/stack.py').read_text().split('rows=[]\ndef launch',1)[1]
    launch='rows=[]\ndef launch'+launch
    launch=launch.replace("local={**env,'PYTHONPATH':str(REPO/'frontend')}","local={**env,'INTEGRATION_WORKER':'front-'+role,'DIAG_BACKEND':'0','PYTHONPATH':str(OUT)+':'+str(REPO/'frontend')}")
    launch=launch.replace("f'config.{role}_asgi:application'","'diagfront:application'")
    launch=launch.replace("'INTEGRATION_WORKER':f'{role}-{port}'","'DIAG_BACKEND':'1','INTEGRATION_WORKER':f'{role}-{port}'")
    launch=launch.replace("'INTEGRATION_WORKER':'jobs'","'DIAG_BACKEND':'1','INTEGRATION_WORKER':'jobs'")
    injection="""source=source.replace("$upstream_response_time|$uri';","$upstream_response_time|$uri|$upstream_connect_time|$upstream_header_time|$upstream_status|$http_x_diag_id|$request_id';")
    source=source.replace('proxy_set_header Host $http_host;', 'proxy_set_header X-Diag-Id '+('$request_id' if name=='entry.conf' else '$http_x_diag_id')+';\\nproxy_set_header Host $http_host;')
    """
    launch=launch.replace("(OUT/name).write_text(source)",injection+"(OUT/name).write_text(source)")
    scope={'OUT':ROOT,'REPO':ROOT/'source','PY':PY,'env':env,'json':json,'os':os,'hashlib':hashlib,'subprocess':subprocess,'socket':socket,'time':time,'Path':Path,'command':command}
    exec(compile(launch,'<owned-launch>','exec'),scope)
    print('DIAGNOSTIC_READY',flush=True)
def run():
    label=sys.argv[2]
    assert label.replace('-','').isalnum()
    out=ROOT/label;assert not out.exists();out.mkdir()
    for name in ('environment.json','metadata.json','sessions.json'):shutil.copy2(ROOT/name,out/name)
    env=json.loads((ROOT/'environment.json').read_text())
    env['LOCAL44_OUT']=str(out)
    if '--fresh-ports' in sys.argv:env['DIAG_CLIENT_PORT_BASE']=sys.argv[sys.argv.index('--fresh-ports')+1]
    if '--full' in sys.argv:
        command([PY,'-B',str(ROOT/'harness/fixtures.py'),'reset_scenarios'],env={**os.environ,**env})
    else:
        data=json.loads((out/'metadata.json').read_text())
        for row in data['users']:row['tickets']={}
        (out/'metadata.json').write_text(json.dumps(data))
    args=[] if '--full' in sys.argv else ['--learning-only']
    started=time.time()
    with (out/'scenarios.log').open('w') as log:
        result=subprocess.run([PY,'-B',str(ROOT/'harness/scenarios.py'),*args],env={**os.environ,**env},stdout=log,stderr=subprocess.STDOUT)
    (out/'run.json').write_text(json.dumps({'started':started,'ended':time.time(),'exit':result.returncode,'mode':'full' if not args else 'learning-only'}))
    print(json.dumps({'label':label,'exit':result.returncode,'out':str(out)}),flush=True)
def stop():
    if (ROOT/'processes.json').exists():
        for row in reversed(json.loads((ROOT/'processes.json').read_text())):
            r=subprocess.run(['ps','-p',str(row['pid']),'-o','args='],capture_output=True,text=True)
            if r.returncode==0:
                assert row['marker'] in r.stdout
                os.kill(row['pid'],signal.SIGTERM)
    for name in ('entry.pid','internal.pid'):
        try:os.kill(int((ROOT/name).read_text()),signal.SIGQUIT)
        except (ProcessLookupError,FileNotFoundError):pass
    command(['docker','stop',*REDIS],stdout=subprocess.DEVNULL)
    command(['docker','stop',PG],stdout=subprocess.DEVNULL)
    print('DIAGNOSTIC_STOPPED')
if __name__=='__main__':
    {'setup':setup,'run':run,'stop':stop}[sys.argv[1]]()
