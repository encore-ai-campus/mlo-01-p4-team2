"""Eight ASGI workers, two role frontends and one jobs process, owned by this test."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path('/private/tmp/platform-final-integration-20261107-r2/source')
OUT=Path(os.environ.get('LOCAL44_OUT','/private/tmp/platform-ws-20261106/template'))
HERE=Path(__file__).resolve().parent
def launch(open_window=False):
    env=json.loads((OUT/'environment.json').read_text())
    old=OUT/'pids.json'
    if old.exists():
        for item in json.loads(old.read_text()):
            try:
                command=subprocess.check_output(['ps','-p',str(item['pid']),'-o','args='],text=True)
                assert any(word in command for word in ('runtime','config.movement_asgi','config.service_asgi','campus_jobs'))
                os.kill(item['pid'],signal.SIGTERM)
            except subprocess.CalledProcessError:pass
            except ProcessLookupError:pass
        time.sleep(3)
    rows=[]
    env['LOCAL33_EVENT_OPEN']='1' if open_window else '0'
    env['LOCAL33_DIAGNOSTICS']='1' if '--diagnostics' in sys.argv else '0'
    for role,ports in [('movement',range(9551,9555)),('service',range(9561,9565))]:
        for port in ports:
            worker=f'{role}-{port}'
            local={**env,'LOCAL33_WORKER':worker,'PGAPPNAME':'query-'+worker,'PYTHONPATH':str(HERE)+':'+str(ROOT/'backend')}
            process=subprocess.Popen([sys.executable,'-B','-m','daphne','-b','127.0.0.1','-p',str(port),f'runtime:{role}_app'],
                cwd=ROOT/'backend',env=local,stdout=(OUT/f'{worker}.log').open('a'),stderr=subprocess.STDOUT,start_new_session=True)
            rows.append({'role':role,'port':port,'pid':process.pid})
    local={**env,'LOCAL33_WORKER':'jobs','PGAPPNAME':'backlog-jobs','PYTHONPATH':str(HERE)+':'+str(ROOT/'backend')}
    process=subprocess.Popen([sys.executable,'-B',str(HERE/'runtime.py')],cwd=ROOT/'backend',env=local,
        stdout=(OUT/'jobs.log').open('a'),stderr=subprocess.STDOUT,start_new_session=True)
    rows.append({'role':'jobs','pid':process.pid})
    (OUT/'pids.json').write_text(json.dumps(rows))
    if not (OUT/'front-pids.json').exists():
        fronts=[]
        for role,port in [('movement',9581),('service',9582)]:
            local={**env,'DJANGO_SETTINGS_MODULE':'config.settings','PYTHONPATH':str(ROOT/'frontend')}
            backlog=int(env.get('MOVEMENT_FRONT_BACKLOG','128')) if role=='movement' else int(env.get('HTTP_FRONT_BACKLOG','128'))
            process=subprocess.Popen([sys.executable,'-B','-m','daphne','-e',f'tcp:port={port}:interface=127.0.0.1:backlog={backlog}',f'config.{role}_asgi:application'],
                cwd=ROOT/'frontend',env=local,stdout=(OUT/f'front-{role}.log').open('a'),stderr=subprocess.STDOUT,start_new_session=True)
            fronts.append({'role':'front-'+role,'port':port,'pid':process.pid})
        (OUT/'front-pids.json').write_text(json.dumps(fronts))
    time.sleep(4)
    print('movement=4, service=4, fronts=2, jobs=1; event override=',open_window)
if __name__=='__main__':launch('--open' in sys.argv)
