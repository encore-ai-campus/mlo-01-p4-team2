"""Stop only owned test processes before changing DB/pool mode and restarting all roles."""
import json,os,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path('/private/tmp/platform-query-20261106')
label=sys.argv[1];assert label in ('before','after')
previous=json.loads((ROOT/'active.json').read_text())['label'] if (ROOT/'active.json').exists() else None
old=ROOT/previous if previous else Path('/private/tmp/platform-local44-20261106/pool')
for name in ('pids.json','front-pids.json'):
    if not (old/name).exists():continue
    for row in json.loads((old/name).read_text()):
        try:
            command=subprocess.check_output(['ps','-p',str(row['pid']),'-o','args='],text=True)
            assert any(s in command for s in ('runtime','config.movement_asgi','config.service_asgi','campus_jobs'))
            os.kill(row['pid'],signal.SIGTERM)
        except (ProcessLookupError,subprocess.CalledProcessError):pass
if (old/'nginx.pid').exists():
    pid=int((old/'nginx.pid').read_text())
    command=subprocess.check_output(['ps','-p',str(pid),'-o','args='],text=True)
    assert 'nginx' in command and str(old) in command
    os.kill(pid,signal.SIGQUIT)
time.sleep(4)
out=ROOT/label
assert not (out/'pids.json').exists(),'Do not overwrite earlier variant results'
env={**os.environ,'LOCAL44_OUT':str(out)}
subprocess.run(['/opt/homebrew/bin/nginx','-t','-p',str(out)+'/', '-c',str(out/'nginx.conf')],check=True)
subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('launch.py')),'--open'],env=env,check=True)
subprocess.run(['/opt/homebrew/bin/nginx','-p',str(out)+'/', '-c',str(out/'nginx.conf')],check=True)
(ROOT/'active.json').write_text(json.dumps({'label':label}))
print('Active:',label,'movement4 + service4 + fronts2 + jobs1')
