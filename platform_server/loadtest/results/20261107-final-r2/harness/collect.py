"""Offline raw accounting. A traceback's final exception line counts once."""
from collections import Counter
import gzip,hashlib,json,math,subprocess,sys
from pathlib import Path
from exception_records import exception_records
ROOT=Path('/private/tmp/platform-ws-20261106');HERE=Path(__file__).resolve().parent
def stats(values):
 values=sorted(values)
 return {'n':len(values),'p95':round(values[math.ceil(.95*len(values))-1],2)} if values else {'n':0,'p95':None}
result={}
for label in ('front','direct'):
 out=ROOT/label
 subprocess.run([sys.executable,'-B',str(HERE/'metrics.py'),str(out)],check=True)
 r=json.loads((out/'results.json').read_text())
 queue=(out/'listen-queues.txt').read_text().splitlines()
 for port in (9581,9582):
  line=next(line for line in queue if f'127.0.0.1.{port}' in line)
  assert int(line.split()[0].split('/')[-1])==128,line
 r['service_frontend_backlog']=128
 security=json.loads((out/'security.json').read_text())
 assert len(security[:400])==400 and all(v['expected']==101 for v in security[:400])
 r['connections']={path:{'attempts':len(rows),'success':sum(v['passed'] for v in rows),'errors':sum(not v['passed'] for v in rows),**stats([v['ms'] for v in rows])}
  for path in ('movement','chat') if (rows:=[v for v in security[:400] if v['action']=='connect:'+path])}
 r['security']={f'{action}[expected={expected}]':{'purpose':'validation' if expected==403 else 'normal','attempts':len(rows),'success':sum(v['passed'] for v in rows),'errors':sum(not v['passed'] for v in rows),
  'statuses':dict(Counter(str(v['status']) for v in rows)),**stats([v['ms'] for v in rows])}
  for action,expected in sorted({(v['action'],v['expected']) for v in security}) if (rows:=[v for v in security if v['action']==action and v['expected']==expected])}
 env=json.loads((out/'environment.json').read_text())
 secrets=[env[k] for k in ('DJANGO_SECRET_KEY','FRONTEND_SECRET_KEY')]+json.loads((out/'sessions.json').read_text())
 secrets += [v for row in json.loads((out/'metadata.json').read_text())['users'] for v in row['tickets'].values()]
 logs=out/'backend-logs';logs.mkdir(exist_ok=True);count=0;error_rows=[]
 for p in sorted([*out.glob('movement-*.log'),*out.glob('service-*.log'),*out.glob('front-*.log')]):
  count+=1;text=p.read_text(errors='replace')
  for s in set(secrets):text=text.replace(s,'[REDACTED]')
  (logs/(p.name+'.gz')).write_bytes(gzip.compress(text.encode(),mtime=0))
  error_rows.extend(exception_records(text,p.name))
 assert count==10
 r['closed_send_exceptions']={'count':len(error_rows),'events':error_rows}
 access=[v.split('|') for v in (out/'nginx-logs/native-access.log').read_text().splitlines()]
 r['edge_errors']={code:sum(len(v)==7 and v[1]=='8116' and v[2]==code for v in access) for code in ('502','504')}
 r['ws_edge_upstreams']={path:dict(Counter(v[3] for v in access if len(v)==7 and v[1]=='8116' and v[2]=='101' and v[6]==path)) for path in ('/ws/movement/','/ws/chat/')}
 for phase in ('casual','stress'):
  parts=[json.loads((out/f'raw-{"walk" if phase=="casual" else "real"}-{i}.json').read_text()) for i in range(2)]
  r[phase]['unexpected_closes']=dict(sum((Counter(p['closes']) for p in parts),Counter()))
  r[phase]['chat_accounts']=len({v['account_index'] for p in parts for v in p['chat_requests']})
  r[phase]['chat_missing_ready']=sum(n for p in parts for k,n in p['action_errors'].items() if 'missing' in k.lower() and ('token' in k.lower() or 'connection' in k.lower()))
  r[phase]['chrome_chat_open']='/ws/chat/' in r[phase]['chrome']['wsOpened']
  r[phase]['movement_latency']=stats([x*1000 for p in parts for x in p['move_lag']])
  for group in [r[phase]['combined_http_terminal'],r[phase]['chat_terminal'],*r[phase]['control_terminal'].values()]:
   assert group['attempts']==group['success']+group['errors']+group['unresolved']
 result[label]=r
def delta(a,b):
 return {'before_ms':a,'after_ms':b,'saved_ms':round(a-b,2),'saved_pct':round((a-b)/a*100,2)} if a and b else None
deltas={phase:{'movement':delta(result['front'][phase]['movement_latency']['p95'],result['direct'][phase]['movement_latency']['p95']),
 'chat':delta((result['front'][phase]['chat_terminal']['terminal_ms'] or {}).get('p95'),(result['direct'][phase]['chat_terminal']['terminal_ms'] or {}).get('p95'))} for phase in ('casual','stress')}
comparison={'variants':result,'deltas':deltas}
comparison['connection_deltas']={path:delta(result['front']['connections'][path]['p95'],result['direct']['connections'][path]['p95']) for path in ('movement','chat')}
(ROOT/'comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2))
print('Raw terminal outcomes and exact final exception occurrences collected')
