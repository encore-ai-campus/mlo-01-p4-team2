"""Stop only owned previous test processes; preserve user port8004 and DB55474."""
import json,os,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path('/private/tmp/platform-ws-20261106')
label=sys.argv[1];assert label in ('front','direct')
old=Path(json.loads((ROOT/'active.json').read_text())['label'])
if not old.is_absolute():old=ROOT/old
for name in ('pids.json','front-pids.json'):
 for row in json.loads((old/name).read_text()):
  try:
   command=subprocess.check_output(['ps','-p',str(row['pid']),'-o','args='],text=True)
   assert any(s in command for s in ('runtime','config.movement_asgi','config.service_asgi','campus_jobs'))
   os.kill(row['pid'],signal.SIGTERM)
  except (ProcessLookupError,subprocess.CalledProcessError):pass
for name in ('nginx.pid','entry.pid','internal.pid'):
 if (old/name).exists():
  pid=int((old/name).read_text())
  try:
   command=subprocess.check_output(['ps','-p',str(pid),'-o','args='],text=True)
   assert 'nginx' in command and str(old) in command
   os.kill(pid,signal.SIGQUIT)
  except (ProcessLookupError,subprocess.CalledProcessError):pass
time.sleep(6)
out=ROOT/label;assert not (out/'pids.json').exists()
for config in ('internal.conf','entry.conf'):
 subprocess.run(['/opt/homebrew/bin/nginx','-t','-p',str(out)+'/', '-c',str(out/config)],check=True)
subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('launch.py')),'--open'],env={**os.environ,'LOCAL44_OUT':str(out)},check=True)
for config in ('internal.conf','entry.conf'):
 subprocess.run(['/opt/homebrew/bin/nginx','-p',str(out)+'/', '-c',str(out/config)],check=True)
(ROOT/'active.json').write_text(json.dumps({'label':label}))
print('Active:',label,'two Nginx processes; fixed4+4 pool10')
