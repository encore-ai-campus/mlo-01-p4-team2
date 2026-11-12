"""Sequential fixed100-account A/B; retain all raw terminal outcomes."""
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=Path('/private/tmp/platform-ws-20261106')
label=sys.argv[1];assert label in ('front','direct')
out=ROOT/label;env={**os.environ,'LOCAL44_OUT':str(out)}
with (out/'run.log').open('w') as log:
 def run(script,*args,timeout=600):
  print(script,*args,flush=True)
  result=subprocess.run([sys.executable,'-B',str(HERE/script),*args],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
  with (out/'steps.jsonl').open('a') as f:f.write(json.dumps({'script':script,'args':args,'exit':result.returncode,'at':time.time()})+'\n')
  assert result.returncode==0,(script,result.returncode)
 run('refresh_sessions.py');run('switch.py',label)
 (out/'listen-queues.txt').write_text(subprocess.check_output(['netstat','-Lan'],text=True))
 monitor=subprocess.Popen([sys.executable,'-B',str(HERE/'monitor.py')],env=env,stdout=(out/'monitor.log').open('w'),stderr=subprocess.STDOUT)
 try:
  for mode in ('walk','real'):
   run('fixtures.py','casual');run('benchmark.py',mode,timeout=420)
  run('fixtures.py','casual');run('admission.py',timeout=300)
  run('security.py',timeout=300)
 finally:
  (out/'monitor.stop').touch();monitor.wait(timeout=15)
print('Finished',label,flush=True)
