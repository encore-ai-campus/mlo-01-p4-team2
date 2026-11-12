"""Reviewed keep-alive comparison. Own DB clones and listeners; no development-server mutation."""
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
ROOT = Path('/private/tmp/platform-keepalive-reviewed-20261107')
OLD = Path('/private/tmp/platform-final-integration-20261107-r2')
SNAP = ROOT/'source'
EVIDENCE = REPO/'loadtest/results/20261107-keepalive-reviewed'
PY = str(ROOT/'venv/bin/python')
PG = 'final-r2-it-pg-20261107'
REDIS = ['keepalive-review-move-20261107','keepalive-review-service-20261107']
PORTS = [8116,8117,8118,9601,9602,*range(9751,9755),*range(9761,9765)]
VARIANTS = ('a1','b1','b2','a2')
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def gate(scope):
    approval = json.loads((ROOT/f'approval-{scope}.json').read_text())
    assert approval['approved'] is True and approval['reviewer']=='/root/test_design_review'
    assert approval['plan_sha256']==digest(REPO/'backend/tests/integration/reviewed_test_plan.json')
    for name, expected in approval['files'].items():
        assert digest(Path(name))==expected, 'Reviewed file changed: '+name
        try:
            relative=Path(name).relative_to(REPO)
        except ValueError:
            continue
        frozen=SNAP/relative
        if frozen.exists():assert digest(frozen)==expected,'Frozen reviewed file changed: '+str(relative)
    manifest=json.loads((ROOT/'source-manifest.json').read_text())
    for name,expected in manifest.items():
        assert digest(SNAP/name)==expected,'Frozen source changed: '+name
    for name,expected in approval.get('harness',{}).items():
        assert digest(ROOT/'harness'/name)==expected,'Harness changed: '+name

def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

def freeze():
    assert not ROOT.exists() and not EVIDENCE.exists()
    ROOT.mkdir();EVIDENCE.mkdir();(ROOT/'harness').mkdir();SNAP.mkdir()
    shutil.copy2(REPO/'stress-test.md', EVIDENCE/'previous-stress-test.md')
    ignore = shutil.ignore_patterns('.env','.env.*','__pycache__','*.pyc','.venv','node_modules','staticfiles','var','db.sqlite3*')
    for folder in ('backend','frontend'):
        shutil.copytree(REPO/folder,SNAP/folder,ignore=ignore)
    (SNAP/'loadtest').mkdir()
    for name in ('campus_load.py','nginx-backend.conf','nginx-edge.conf.template'):
        shutil.copy2(REPO/'loadtest'/name,SNAP/'loadtest'/name)
    for folder in ('deploy',):
        shutil.copytree(REPO/folder,SNAP/folder,ignore=ignore)
    files = {str(p.relative_to(SNAP)):digest(p) for p in SNAP.rglob('*') if p.is_file()}
    (ROOT/'source-manifest.json').write_text(json.dumps(files,indent=2))
    (EVIDENCE/'source-manifest.json').write_text(json.dumps(files,indent=2))
    for source in (OLD/'harness').glob('*.py'):
        text = source.read_text().replace(str(OLD/'source'),str(SNAP)).replace(str(OLD),str(ROOT))
        if source.name=='fixtures.py':
            text=text.replace("'receipt_final_'","'receipt_keepalive_'")
        (ROOT/'harness'/source.name).write_text(text)
    for name in ('receipt_protocol.py','receipt_browser.py'):
        if name=='receipt_browser.py':
            text=(REPO/'backend/tests/integration'/name).read_text().replace(str(REPO),str(SNAP)).replace('8106','8116')
            text=text.replace("ROOT=Path('/private/tmp/platform-receipt-integration-20261107')","ROOT=Path(os.environ['LOCAL44_OUT'])")
        else:
            text=(OLD/'harness'/name).read_text().replace(str(OLD/'source'),str(SNAP))
            text=text.replace("ROOT=Path("+repr(str(OLD))+")","ROOT=Path(os.environ['LOCAL44_OUT'])")
        text=text.replace("connection.settings_dict['NAME']=='receipt_final_20261107_r2'",
                          "connection.settings_dict['NAME'].startswith('receipt_keepalive_')")
        (ROOT/'harness'/name).write_text(text)
    # Shared harness scripts select the active variant through LOCAL44_OUT.
    for source in (ROOT/'harness').glob('*.py'):
        if source.name in ('receipt_protocol.py','receipt_browser.py'):continue
        text=source.read_text().replace("Path('/private/tmp/platform-query-source-20261106')","Path("+repr(str(SNAP))+")")
        source.write_text(text)
    for name in ('keepalive_hooks.py','keepalive_suite.py','keepalive_fixture.py'):
        shutil.copy2(REPO/'backend/tests/integration'/name,ROOT/name)
    print('Source frozen; no test or server executed.',flush=True)

def prepare():
    gate('load')
    state=json.loads(subprocess.check_output(['docker','inspect',PG],text=True))[0]['State']['Status']
    assert state=='exited' or (state=='running' and (ROOT/'started-pg.json').exists()),'DB owned by another session'
    for port in [*PORTS,57541,57542]:
        with socket.socket() as listener:listener.bind(('127.0.0.1',port))
    command(['docker','start',PG],stdout=subprocess.DEVNULL)
    (ROOT/'started-pg.json').write_text(json.dumps({'name':PG,'original_state':'exited'}))
    import psycopg
    from psycopg import sql
    for _ in range(100):
        try:
            with psycopg.connect(host='127.0.0.1',port=55500,user='receipt_test',dbname='postgres',autocommit=True) as db:
                assert db.execute('SHOW max_connections').fetchone()[0]=='100'
                for label in VARIANTS:
                    name='receipt_keepalive_'+label+'_20261107'
                    assert not db.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone()
                    db.execute(sql.SQL('CREATE DATABASE {} TEMPLATE receipt_final_20261107_r2').format(sql.Identifier(name)))
            break
        except psycopg.OperationalError:time.sleep(.1)
    else:raise RuntimeError('Owned PostgreSQL did not start')
    owned_redis=[]
    for name,port in zip(REDIS,[57541,57542]):
        command(['docker','run','-d','--name',name,'-p',f'127.0.0.1:{port}:6379','redis:7.0.15',
                 'redis-server','--save','','--appendonly','no'],stdout=subprocess.DEVNULL)
        owned_redis.append(name)
        (ROOT/'owned-redis.json').write_text(json.dumps(owned_redis))
    for label in VARIANTS:
        out=ROOT/label;out.mkdir();(out/'logs').mkdir();(out/'trace').mkdir()
        env=json.loads((OLD/'environment.json').read_text())
        env.update(POSTGRES_DB='receipt_keepalive_'+label+'_20261107',CAMPUS_DATA_DIR=str(out/'data'),
                   KEEPALIVE_RUN=str(out),BACKEND_HTTP_KEEPALIVE='1' if label.startswith('b') else '0',
                   BACKEND_HTTP_POOL_MAXSIZE='100',BACKEND_HTTP_IDLE_TIMEOUT='20',LOCAL44_OUT=str(out),
                   LOCAL33_EVENT_OPEN='1',LOCAL33_DIAGNOSTICS='0')
        (out/'environment.json').write_text(json.dumps(env));(out/'environment.json').chmod(0o600)
        for name in ('clients.json','sessions.json','metadata.json'):
            shutil.copy2(OLD/name,out/name);(out/name).chmod(0o600)
        command([PY,'-B',str(REPO/'backend/tests/integration/keepalive_fixture.py'),'refresh'],env={**os.environ,**env})
    print('Four identical owned DB clones ready.',flush=True)

def wait_ports_clear():
    deadline=time.monotonic()+120
    suffixes={'.'+str(p) for p in PORTS}
    while True:
        data=subprocess.check_output(['netstat','-an','-p','tcp'],text=True)
        active=[line for line in data.splitlines() if 'TIME_WAIT' in line and
                any(any(field.endswith(s) for s in suffixes) for field in line.split()[3:5])]
        if not active:break
        assert time.monotonic()<deadline,'Own TIME_WAIT tuples did not clear; comparison blocked'
        time.sleep(.25)
    for port in PORTS:
        with socket.socket() as listener:listener.bind(('127.0.0.1',port))

def start(label):
    gate('load');wait_ports_clear()
    out=ROOT/label
    env=json.loads((out/'environment.json').read_text())
    command([PY,'-B',str(REPO/'backend/tests/integration/keepalive_fixture.py'),'tickets'],env={**os.environ,**env})
    import redis
    for port in (57541,57542):redis.Redis(host='127.0.0.1',port=port).flushdb()
    static=out/'static';static.mkdir(exist_ok=True)
    for name,path in [('campus',SNAP/'frontend/dist'),('learning',SNAP/'frontend/learning/static/learning'),
                      ('accounts',SNAP/'frontend/accounts/static/accounts')]:
        if not (static/name).exists():(static/name).symlink_to(path,target_is_directory=True)
    runtime=(OLD/'runtime.py').read_text().replace(str(OLD),str(out)).replace(str(OLD/'source'),str(SNAP))
    runtime=runtime.replace(str(out/'source'),str(SNAP))
    runtime=runtime.replace("if __name__=='__main__':","from keepalive_hooks import install\ninstall(True)\nif __name__=='__main__':")
    (out/'runtime.py').write_text(runtime)
    (out/'front_runtime.py').write_text("import os\nfrom importlib import import_module\nfrom keepalive_hooks import install\napplication=import_module('config.'+os.environ['FRONT_ROLE']+'_asgi').application\ninstall(False)\n")
    rows=[]
    def launch(role,args,cwd,local,marker):
        log=(out/'logs'/f'{role}.log').open('w')
        proc=subprocess.Popen(args,cwd=cwd,env=local,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        rows.append({'role':role,'pid':proc.pid,'marker':marker})
        (out/'processes.json').write_text(json.dumps(rows))
    (ROOT/'active.json').write_text(json.dumps({'label':label}))
    for role,ports in [('movement',range(9751,9755)),('service',range(9761,9765))]:
        for port in ports:
            local={**env,'INTEGRATION_WORKER':f'{role}-{port}','PGAPPNAME':f'keepalive-{role}-{port}',
                   'PYTHONPATH':str(out)+':'+str(ROOT)+':'+str(SNAP/'backend')}
            launch(f'{role}-{port}',[PY,'-B','-m','daphne','-e',f'tcp:port={port}:interface=127.0.0.1:backlog=128',
                                    f'runtime:{role}_app'],SNAP/'backend',local,f'port={port}')
    local={**env,'INTEGRATION_WORKER':'jobs','PGAPPNAME':'keepalive-jobs','PYTHONPATH':str(out)+':'+str(ROOT)+':'+str(SNAP/'backend')}
    launch('jobs',[PY,'-B',str(out/'runtime.py')],SNAP/'backend',local,str(out/'runtime.py'))
    for role,port in [('movement',9601),('service',9602)]:
        local={**env,'FRONT_ROLE':role,'INTEGRATION_WORKER':'front-'+role,
               'PYTHONPATH':str(out)+':'+str(ROOT)+':'+str(SNAP/'frontend')}
        launch('front-'+role,[PY,'-B','-m','daphne','-e',f'tcp:port={port}:interface=127.0.0.1:backlog=128',
                             'front_runtime:application'],SNAP/'frontend',local,f'port={port}')
    launch('monitor',[PY,'-B',str(REPO/'backend/tests/integration/keepalive_monitor.py')],SNAP/'backend',env,'keepalive_monitor.py')
    for name in ('entry.conf','internal.conf'):
        text=(OLD/name).read_text().replace(str(OLD),str(out))
        if label.startswith('b'):
            text=text.replace("'' close;","'' \"\";")
            keep=' keepalive 128; keepalive_timeout 30s; keepalive_requests 1000;'
            if name=='internal.conf':
                text=text.replace('server 127.0.0.1:9764;}','server 127.0.0.1:9764;'+keep+'}')
            else:
                text=text.replace('server { listen 127.0.0.1:8116;',
                    'upstream front_http { server 127.0.0.1:9602;'+keep+'}\nserver { listen 127.0.0.1:8116;')
                text=text.replace('proxy_pass http://127.0.0.1:9602;','proxy_pass http://front_http;')
        text=text.replace("$upstream_response_time|$uri';","$upstream_response_time|$uri|$upstream_connect_time|$upstream_status|$connection|$connection_requests';")
        (out/name).write_text(text)
        command(['/opt/homebrew/bin/nginx','-t','-p',str(out)+'/', '-c',str(out/name)],stdout=subprocess.DEVNULL)
        command(['/opt/homebrew/bin/nginx','-p',str(out)+'/', '-c',str(out/name)])
    for port in PORTS:
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.2):pass
                break
            except OSError:time.sleep(.1)
        else:raise RuntimeError(f'Port {port} did not start')
    command([PY,'-B',str(REPO/'backend/tests/integration/keepalive_fixture.py'),'preflight'],env={**os.environ,**env})
    (ROOT/'active.json').write_text(json.dumps({'label':label}))
    print('READY '+label,flush=True)

def run(label,name,*args):
    gate('load')
    out=ROOT/label;env={**os.environ,**json.loads((out/'environment.json').read_text())}
    log=out/'logs'/('run-'+name+'-'+('-'.join(args) or 'default')+'.log')
    started=time.time()
    with log.open('w') as output:
        result=subprocess.run([PY,'-B',str(ROOT/'harness'/(name+'.py')),*args],env=env,stdout=output,stderr=subprocess.STDOUT,timeout=800)
    with (out/'steps.jsonl').open('a') as f:f.write(json.dumps({'name':name,'args':args,'exit':result.returncode,'started':started,'ended':time.time()})+'\n')
    print(json.dumps({'label':label,'name':name,'exit':result.returncode,'log':str(log)}),flush=True)
    return result.returncode

def stop(label):
    out=ROOT/label
    rows=json.loads((out/'processes.json').read_text()) if (out/'processes.json').exists() else []
    if rows:
        for row in reversed(rows):
            current=subprocess.run(['ps','-p',str(row['pid']),'-o','args='],capture_output=True,text=True)
            if current.returncode==0:
                assert row['marker'] in current.stdout,'PID ownership mismatch'
                os.kill(row['pid'],signal.SIGTERM)
    nginx_pids=[]
    for name in ('entry.pid','internal.pid'):
        try:
            pid=int((out/name).read_text());nginx_pids.append(pid);os.kill(pid,signal.SIGQUIT)
        except (ProcessLookupError,FileNotFoundError):pass
    pids=[row['pid'] for row in rows]+nginx_pids
    deadline=time.monotonic()+20
    while True:
        live=[]
        for pid in pids:
            check=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True)
            if check.returncode==0 and not check.stdout.strip().startswith('Z'):live.append(pid)
        if not live:break
        assert time.monotonic()<deadline,'Owned process did not stop: '+str(live)
        time.sleep(.1)
    (out/'stopped-processes.json').write_text(json.dumps({'pids':pids,'remaining':[],'at':time.time()}))
    (ROOT/'active.json').unlink(missing_ok=True)
    print('STOPPED '+label,flush=True)

if __name__=='__main__':
    phase=sys.argv[1]
    if phase=='freeze':freeze()
    elif phase=='prepare':prepare()
    elif phase=='start':start(sys.argv[2])
    elif phase=='run':raise SystemExit(run(*sys.argv[2:]))
    elif phase=='stop':stop(sys.argv[2])
    elif phase=='cleanup':
        if (ROOT/'active.json').exists():stop(json.loads((ROOT/'active.json').read_text())['label'])
        names=json.loads((ROOT/'owned-redis.json').read_text()) if (ROOT/'owned-redis.json').exists() else []
        if (ROOT/'started-pg.json').exists():names.append(PG)
        if names:command(['docker','stop',*names],stdout=subprocess.DEVNULL)
        print('Owned resources stopped; evidence and DB clones retained.')
