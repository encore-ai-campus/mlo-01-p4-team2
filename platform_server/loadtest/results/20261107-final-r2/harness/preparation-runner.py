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
ROOT.mkdir(exist_ok=True);H.mkdir(exist_ok=True);E.mkdir(exist_ok=True)
for p in (BASE/'pipeline/tests/ws-20261106').glob('*.py'):shutil.copy2(p,H/p.name)
for name in ('scenarios.py','expectations.py'):shutil.copy2(BASE/'pipeline/tests/query-20261106'/name,H/name)
p=H/'launch.py';s=p.read_text().replace("else 128","else int(env.get('HTTP_FRONT_BACKLOG','128'))");p.write_text(s)
p=H/'runtime.py';s=p.read_text().replace("('redis://127.0.0.1:57411/0','redis://127.0.0.1:57413/0')","('redis://127.0.0.1:57541/0',)").replace("('redis://127.0.0.1:57412/0','redis://127.0.0.1:57414/0')","('redis://127.0.0.1:57542/0',)");p.write_text(s)
p=H/'refresh_sessions.py';s=p.read_text().replace("env['POSTGRES_DB'] in ('query_ws_front_20261106_v2','query_ws_direct_20261106_v2')","env['POSTGRES_DB'].startswith('query_http128_')");p.write_text(s)
# Preserve original approved measurements and only stop processes owned by the old test cluster.
for name in ('pids.json','front-pids.json'):
 for row in json.loads((OLD/name).read_text()):
  try:
   cmd=subprocess.check_output(['ps','-p',str(row['pid']),'-o','args='],text=True,stderr=subprocess.DEVNULL)
   assert any(s in cmd for s in ('runtime:','/runtime.py','config.movement_asgi','config.service_asgi','campus_jobs')),cmd
   os.kill(row['pid'],signal.SIGTERM)
  except (ProcessLookupError,subprocess.CalledProcessError):pass
for name in ('entry.pid','internal.pid'):
 pid=int((OLD/name).read_text())
 try:
  cmd=subprocess.check_output(['ps','-p',str(pid),'-o','args='],text=True,stderr=subprocess.DEVNULL)
  assert 'nginx' in cmd and str(OLD) in cmd;os.kill(pid,signal.SIGQUIT)
 except (ProcessLookupError,subprocess.CalledProcessError):pass
time.sleep(4)
for port,role in ((57541,'movement'),(57542,'service')):
 subprocess.run(['docker','run','-d','--name','platform-http128-'+role+'-20261107','-p',f'127.0.0.1:{port}:6379','redis:7.0.15','redis-server','--save','','--appendonly','no'],check=True,stdout=subprocess.DEVNULL)
env0=json.loads((OLD/'environment.json').read_text())
initial={}
with psycopg.connect(host='127.0.0.1',port=55500,dbname='postgres',user='event_test',autocommit=True) as db:
 assert db.execute('SHOW data_directory').fetchone()[0]=='/tmp/local33-data'
 for label,cap in (('a1',50),('b1',128),('b2',128),('a2',50)):
  out=ROOT/label;out.mkdir();(out/'nginx-logs').mkdir()
  name='query_http128_'+label+'_20261107'
  db.execute(sql.SQL('CREATE DATABASE {} TEMPLATE backlog_template_20261106').format(sql.Identifier(name)))
  env={**env0,'POSTGRES_DB':name,'HTTP_FRONT_BACKLOG':str(cap),'MOVEMENT_FRONT_BACKLOG':'128','LOCAL44_OUT':str(out),'CAMPUS_DATA_DIR':str(out/'data'),'CAMPUS_MOVEMENT_REDIS_URL':'redis://127.0.0.1:57541/0','CAMPUS_SERVICE_REDIS_URL':'redis://127.0.0.1:57542/0'}
  (out/'environment.json').write_text(json.dumps(env));(out/'environment.json').chmod(0o600)
  for n in ('sessions.json','metadata.json'):shutil.copy2(OLD/n,out/n);(out/n).chmod(0o600)
  for n in ('entry.conf','internal.conf'):
   (out/n).write_text((OLD/n).read_text().replace(str(OLD),str(out)))
  with psycopg.connect(host='127.0.0.1',port=55500,dbname=name,user='event_test') as c:
   counts={t:c.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0] for t in ('auth_user','django_session','learning_question','event_progress')}
  initial[label]={'cap':cap,'row_counts':counts,'sessions_sha256':hashlib.sha256((out/'sessions.json').read_bytes()).hexdigest()}
assert all(v['row_counts']==initial['a1']['row_counts'] and v['sessions_sha256']==initial['a1']['sessions_sha256'] for v in initial.values())
(E/'initial.json').write_text(json.dumps(initial,indent=2))
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
  for port in (57541,57542):redis.Redis(port=port).flushdb()
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
  print('FEATURES_FINISHED',label,flush=True)
  if label in ('a1','b1'):
   run(out,'fixtures.py','casual')
   run(out,'benchmark.py','real','180',allowed=(0,1),timeout=450)
   print('HIGH_LOAD_FINISHED',label,flush=True)
  export(out)
  print('EXPORTED',label,flush=True)
finally:
 if active:
  rows=json.loads((active/'front-pids.json').read_text());row=next(r for r in rows if r['port']==9582)
  try:os.kill(row['pid'],signal.SIGTERM)
  except ProcessLookupError:pass
  time.sleep(2)
  ev=json.loads((active/'environment.json').read_text());ev['HTTP_FRONT_BACKLOG']='128';(active/'environment.json').write_text(json.dumps(ev))
  env={**os.environ,**ev,'DJANGO_SETTINGS_MODULE':'config.settings','PYTHONPATH':'/private/tmp/platform-final-integration-20261107-r2/source/frontend'}
  with (ROOT/'restored-service.log').open('a') as log:
   p=subprocess.Popen([PY,'-B','-m','daphne','-e','tcp:port=9582:interface=127.0.0.1:backlog=128','config.service_asgi:application'],cwd='/private/tmp/platform-final-integration-20261107-r2/source/frontend',env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  row['pid']=p.pid;(active/'front-pids.json').write_text(json.dumps(rows))
  (ROOT/'active.json').write_text(json.dumps({'label':active.name}))
  time.sleep(2)
  queue=subprocess.check_output(['netstat','-Lan'],text=True)
  assert int(next(x for x in queue.splitlines() if '127.0.0.1.9582' in x).split()[0].split('/')[-1])==128
  (E/'final-listen-queues.txt').write_text('\n'.join(x for x in queue.splitlines() if any(f'127.0.0.1.{p}' in x for p in (9581,9582)))+'\n')
shutil.copytree(H,E/'harness')
print('ALL_FOUR_RUNS_COMPLETE',flush=True)
