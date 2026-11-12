"""Run one complete, sequential 100-account comparison; retain every failure."""
import json,os,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=Path('/private/tmp/platform-query-20261106')
label=sys.argv[1];assert label in ('before','after')
OUT=ROOT/label;env={**os.environ,'LOCAL44_OUT':str(OUT)}
log=(OUT/'run.log').open('w')
def run(script,*args,allowed=(0,),timeout=600):
 command=[sys.executable,'-B',str(HERE/script),*args]
 print(script,*args,flush=True)
 result=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
 log.flush()
 with (OUT/'steps.jsonl').open('a') as f:f.write(json.dumps({'script':script,'args':args,'exit':result.returncode,'at':time.time()})+'\n')
 assert result.returncode in allowed,(script,result.returncode)
run('refresh_sessions.py')
run('switch.py',label)
queue=subprocess.check_output(['netstat','-Lan'],text=True)
lines=[line for line in queue.splitlines() if any('127.0.0.1.'+str(port) in line for port in [9581,9582])]
(OUT/'listen-queues.txt').write_text('\n'.join(lines)+'\n')
value=int(json.loads((OUT/'environment.json').read_text())['MOVEMENT_FRONT_BACKLOG'])
movement=next(line for line in lines if '127.0.0.1.9581' in line)
assert int(movement.split()[0].split('/')[-1])==value,movement
(OUT/'kernel-backlog.txt').write_text(subprocess.check_output(['sysctl','kern.ipc.somaxconn'],text=True))
monitor=subprocess.Popen([sys.executable,'-B',str(HERE/'monitor.py')],env=env,stdout=(OUT/'monitor.log').open('w'),stderr=subprocess.STDOUT)
try:
 for mode in ('walk','real'):
  run('fixtures.py','casual')
  run('benchmark.py',mode,timeout=420)
 run('fixtures.py','casual')
 run('admission.py',timeout=300)
 run('fixtures.py','reset_scenarios')
 run('scenarios.py',allowed=(0,1))
 run('fixtures.py','audit')
finally:
 (OUT/'monitor.stop').touch()
 monitor.wait(timeout=15)
 log.close()
print('Finished',label,flush=True)
