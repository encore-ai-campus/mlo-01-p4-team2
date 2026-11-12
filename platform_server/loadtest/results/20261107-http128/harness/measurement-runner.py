import os,json,shutil,subprocess,sys,signal,time,socket,hashlib
from pathlib import Path
import psycopg,redis
from psycopg import sql
BASE=Path('/Users/sindong-yeob/projects/platform-server')
REPO=BASE/'playdatametaverse'
ROOT=Path('/private/tmp/platform-http128-20261107')
H=ROOT/'harness'
E=REPO/'loadtest/results/20261107-http128'
OLD=Path('/private/tmp/platform-ws-20261106/direct')
PY=sys.executable
active=None
def stop(out):
 for n in ('pids.json','front-pids.json'):
  for row in json.loads((out/n).read_text()):
   try:os.kill(row['pid'],signal.SIGTERM)
   except ProcessLookupError:pass
 for n in ('entry.pid','internal.pid'):
  try:os.kill(int((out/n).read_text()),signal.SIGQUIT)
  except ProcessLookupError:pass
 time.sleep(3)
def run(out,name,*args,allowed=(0,),timeout=600):
 env={**os.environ,'LOCAL44_OUT':str(out)}
 with (out/(name.replace('.py','')+'-'+('-'.join(args) or 'default')+'.runner.log')).open('w') as log:
  r=subprocess.run([PY,'-B',str(H/name),*args],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
 with (out/'steps.jsonl').open('a') as f:f.write(json.dumps({'script':name,'args':args,'exit':r.returncode,'at':time.time()})+'\n')
 assert r.returncode in allowed,(name,args,r.returncode)
def export(out):
 target=E/out.name;target.mkdir(exist_ok=True)
 allowed=('*.json','*.jsonl','*.log','*.conf','listen-queues.txt')
 forbidden=('environment.json','sessions.json','metadata.json','current-chrome.json','expected-api.json')
 secrets=list(json.loads((out/'sessions.json').read_text()))
 ev=json.loads((out/'environment.json').read_text());secrets.extend(ev[k] for k in ('DJANGO_SECRET_KEY','FRONTEND_SECRET_KEY') if ev.get(k))
 secrets.extend(v for row in json.loads((out/'metadata.json').read_text())['users'] for v in row['tickets'].values())
 import gzip
 for pat in allowed:
  for p in out.glob(pat):
   if p.name in forbidden:continue
   text=p.read_text(errors='replace')
   for secret in set(secrets):text=text.replace(secret,'[REDACTED]')
   if p.suffix=='.log':(target/(p.name+'.gz')).write_bytes(gzip.compress(text.encode(),mtime=0))
   else:(target/p.name).write_text(text)
 for p in (out/'nginx-logs').glob('*.log'):
  (target/(p.name+'.gz')).write_bytes(gzip.compress(p.read_bytes(),mtime=0))
 try:
  for label,cap in (('a1',50),('b1',128),('b2',128),('a2',50)):
   pass
 except Exception:raise
try:
 for label,cap in (('a1',50),('b1',128),('b2',128),('a2',50)):
  if active:stop(active)
  out=ROOT/label;active=out
  ev=json.loads((out/'environment.json').read_text());ev['HTTP_FRONT_BACKLOG']=str(cap);(out/'environment.json').write_text(json.dumps(ev))
  for port in (57521,57522):redis.Redis(port=port).flushdb()
  print('START',label,'service_backlog',cap,flush=True)
  run(out,'refresh_sessions.py')
  run(out,'launch.py','--open')
  for config in ('internal.conf','entry.conf'):
   subprocess.run(['/opt/homebrew/bin/nginx','-t','-p',str(out)+'/', '-c',str(out/config)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   subprocess.run(['/opt/homebrew/bin/nginx','-p',str(out)+'/', '-c',str(out/config)],check=True)
  time.sleep(2)
  queue=subprocess.check_output(['netstat','-Lan'],text=True)
  for port,expected in ((9581,128),(9582,cap)):
   line=next(x for x in queue.splitlines() if f'127.0.0.1.{port}' in x);assert int(line.split()[0].split('/')[-1])==expected,line
  (out/'listen-queues.txt').write_text(queue)
  run(out,'fixtures.py','reset_scenarios')
  run(out,'scenarios.py',*(() if label in ('a1','b1') else ('--learning-only',)),allowed=(0,1))
  summary=json.loads((out/('scenario-summary.json' if label in ('a1','b1') else 'scenario-summary-fullbank.json')).read_text())
  assert 'learning:start' in summary and summary['learning:start']['n']==100,summary
  print('FEATURES_FINISHED',label,flush=True)
  if label in ('a1','b1'):
   run(out,'fixtures.py','casual')
   run(out,'benchmark.py','real','180',allowed=(0,1),timeout=450)
   print('HIGH_LOAD_FINISHED',label,flush=True)
  export(out)
  print('EXPORTED',label,flush=True)
finally:
 if active and (active/'front-pids.json').exists():
  rows=json.loads((active/'front-pids.json').read_text());row=next(r for r in rows if r['port']==9582)
  try:os.kill(row['pid'],signal.SIGTERM)
  except ProcessLookupError:pass
  time.sleep(2)
  ev=json.loads((active/'environment.json').read_text());ev['HTTP_FRONT_BACKLOG']='128';(active/'environment.json').write_text(json.dumps(ev))
  env={**os.environ,**ev,'DJANGO_SETTINGS_MODULE':'config.settings','PYTHONPATH':'/private/tmp/platform-query-source-20261106/frontend'}
  with (ROOT/'restored-service.log').open('a') as log:
   p=subprocess.Popen([PY,'-B','-m','daphne','-e','tcp:port=9582:interface=127.0.0.1:backlog=128','config.service_asgi:application'],cwd='/private/tmp/platform-query-source-20261106/frontend',env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  row['pid']=p.pid;(active/'front-pids.json').write_text(json.dumps(rows))
  (ROOT/'active.json').write_text(json.dumps({'label':active.name}))
  time.sleep(2)
  queue=subprocess.check_output(['netstat','-Lan'],text=True)
  assert int(next(x for x in queue.splitlines() if '127.0.0.1.9582' in x).split()[0].split('/')[-1])==128
  (E/'final-listen-queues.txt').write_text('\n'.join(x for x in queue.splitlines() if any(f'127.0.0.1.{p}' in x for p in (9581,9582)))+'\n')
shutil.copytree(H,E/'harness')
print('ALL_FOUR_RUNS_COMPLETE',flush=True)
