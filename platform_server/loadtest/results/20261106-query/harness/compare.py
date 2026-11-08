"""Compare SQL tuning with pool10 and backlog128 fixed in both conditions."""
from collections import Counter
import gzip,json,subprocess,sys
from pathlib import Path

ROOT=Path('/private/tmp/platform-query-20261106')
result={'changed_variable':'SQL query/read-transaction optimization only','pool_max':10,'movement_frontend_backlog':128,'variants':{}}
for label,backlog in [('before',128),('after',128)]:
 out=ROOT/label
 subprocess.run([sys.executable,str(Path(__file__).with_name('metrics.py')),str(out)],check=True)
 r=json.loads((out/'results.json').read_text())
 stages=[v for v in r['scenario-checks'] if v['phase'].startswith(('connect:','reconnect:'))]
 r['functional_ws']={'attempts':sum(v['users'] for v in stages),
  'errors_502':sum(n for v in stages for error,n in v['errors'].items() if 'HTTP 502' in error),'stages':stages}
 logs=list((out/'backend-logs').glob('*.gz'));assert len(logs)==10
 r['backend_error_lines']={name:sum(gzip.decompress(p.read_bytes()).decode().count(pattern) for p in logs)
  for name,pattern in [('connection_limit','too many clients already'),('pool_timeout','PoolTimeout'),('closed_protocol','Attempt to send on a closed protocol')]}
 attest=[json.loads(p.read_text()) for p in out.glob('attestation-*.json')]
 assert len(attest)==9 and all(v['pool_options']=={'min_size':1,'max_size':10} for v in attest)
 r['attestations']=attest;r['pool_timeout_seconds']=30;r['frontend_backlog']=backlog
 queue=(out/'listen-queues.txt').read_text().splitlines()
 assert int(next(v for v in queue if '127.0.0.1.9581' in v).split()[0].split('/')[-1])==backlog
 assert int(next(v for v in queue if '127.0.0.1.9582' in v).split()[0].split('/')[-1])==50
 assert all(v['pool_size_max']<=10 for v in r['pool_stats'].values())
 edge=[]
 for line in (out/'nginx-logs/native-access.log').read_text().splitlines():
  fields=line.split('|')
  if len(fields)==7 and fields[1]=='8096' and fields[2]=='502':edge.append(fields)
 r['nginx_502']={'count':len(edge),'upstreams':dict(Counter(v[3] for v in edge)),
  'elapsed_ms':sorted(float(v[4])*1000 for v in edge),
  'by_upstream':{address:{'count':sum(v[3]==address for v in edge),
   'elapsed_ms':sorted(float(v[4])*1000 for v in edge if v[3]==address),
   'paths':dict(Counter(v[6] for v in edge if v[3]==address))} for address in sorted({v[3] for v in edge})}}
 error=(out/'nginx-error.log').read_text()
 r['nginx_error_lines']={s:error.count(s) for s in ['Connection reset by peer','Broken pipe','upstream timed out']}
 access=[s.split('|') for s in (out/'nginx-logs/native-access.log').read_text().splitlines()]
 r['nginx_504']={port:sum(len(v)==7 and v[1]==port and v[2]=='504' for v in access) for port in ('8096','8097','8098')}
 r['static_404_paths']=dict(Counter(v[6] for v in access if len(v)==7 and v[1]=='8096' and v[2]=='404' and v[6].startswith('/static/')))
 result['variants'][label]=r
result['initial_comparison']=json.loads((ROOT/'initial.json').read_text())
a,b=(result['initial_comparison'][v] for v in ('before','after'))
assert a['row_counts']==b['row_counts'] and a['sessions_sha256']==b['sessions_sha256']
assert all(v==0 for c in (a,b) for v in c['initial_redis_counts'].values())
def savings(a,b):
 if not a or not b or not a.get('terminal_ms') or not b.get('terminal_ms'):return None
 before=a['terminal_ms']['p95'];after=b['terminal_ms']['p95']
 return {'before_p95':before,'after_p95':after,'saved_ms':round(before-after,2),
  'saved_pct':round((before-after)/before*100,2) if before else None,
  'before_attempts':a['attempts'],'after_attempts':b['attempts']}
old,new=(result['variants'][v] for v in ('before','after'))
result['savings']={phase:{route:savings(old[phase]['combined_api_requests'].get(route),new[phase]['combined_api_requests'].get(route))
 for route in sorted(set(old[phase]['combined_api_requests'])|set(new[phase]['combined_api_requests']))} for phase in ('casual','stress')}
result['scenario_savings']={action:savings(old['scenario_requests'].get(action),new['scenario_requests'].get(action))
 for action in sorted(set(old['scenario_requests'])|set(new['scenario_requests']))}
result['aggregate_savings']={phase:savings(old[phase]['combined_http_terminal'],new[phase]['combined_http_terminal']) for phase in ('casual','stress')}
(ROOT/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('Comparison prepared; equal fixtures, actual queues and pool limits checked')
