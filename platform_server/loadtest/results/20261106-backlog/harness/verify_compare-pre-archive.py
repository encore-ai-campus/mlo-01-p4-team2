"""Read-only independent recomputation; never sends requests or rewrites evidence."""
from collections import Counter
import gzip,hashlib,json,math
from pathlib import Path
import sys,re

ROOT=Path(sys.argv[1]);comparison=json.loads((ROOT/'comparison.json').read_text())
def p95(values):
 values=sorted(values)
 return round(values[math.ceil(.95*len(values))-1],2) if values else None
assert p95(list(range(1,101)))==95 and p95([]) is None
def verify_counts(rows,reported,field):
 counts=Counter()
 for row in rows:
  s=row['status']
  key='success' if s in ('ok',101) or isinstance(s,int) and 200<=s<300 else 'business_rejected' if s=='rejected' or isinstance(s,int) and 400<=s<500 else 'server_error' if isinstance(s,int) and 500<=s<600 else 'timeout' if s=='timeout' else 'network_error' if s in ('error','network_error','transport_error') else 'unresolved'
  counts[key]+=1
 for key in ('success','business_rejected','server_error','timeout','network_error','unresolved'):assert counts[key]==reported[key],key
 assert len(rows)==reported['attempts']==reported['success']+reported['errors']+reported['unresolved']
 values=[r[field] for r in rows if field in r and r['status'] not in ('pending','unresolved')]
 assert len(values)==len(rows)-reported['unresolved']
 assert p95(values)==(reported['terminal_ms'] or {}).get('p95')
 assert not any(r['status']=='pending' for r in rows)
def route(row):
 path=row['uri'].split('?')[0]
 path=re.sub(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',':uuid',path)
 path=re.sub(r'(/sessions/)\d+',r'\1:session_id',path)
 path=re.sub(r'(/questions/)\d+',r'\1:order',path)
 return row['method']+' '+path
for label,backlog in [('baseline',50),('increased',128)]:
 out=ROOT/label;r=comparison['variants'][label]
 for mode,key in [('walk','casual'),('real','stress')]:
  parts=[json.loads((out/f'raw-{mode}-{i}.json').read_text()) for i in range(2)]
  assert sum(v['welcomed'] for v in parts)==99
  rows=[v for part in parts for v in part['http_requests']]
  assert len(rows)==r[key]['http_started']['started']
  assert sum('response_ms' not in v for v in rows)==r[key]['http_started']['no_response_at_end']
  assert p95([v['response_ms'] for v in rows if 'response_ms' in v])==r[key]['http_started']['response_ms']['p95']
  verify_counts(rows,r[key]['http_terminal'],'terminal_ms')
  browser=json.loads((out/f'chrome-{mode}.json').read_text())['metrics']['http']
  verify_counts(browser,r[key]['chrome']['http_terminal'],'ms')
  combined=rows+[{**v,'uri':v['path'],'terminal_ms':v['ms']} if 'ms' in v else {**v,'uri':v['path']} for v in browser]
  verify_counts(combined,r[key]['combined_http_terminal'],'terminal_ms')
  for path,counts in r[key]['combined_api_requests'].items():verify_counts([v for v in combined if route(v)==path],counts,'terminal_ms')
  chat=[v for part in parts for v in part['chat_requests']]
  verify_counts(chat,r[key]['chat_terminal'],'terminal_ms')
  for action,counts in r[key]['control_terminal'].items():
   verify_counts([v for part in parts for v in part['control_requests'] if v['action']==action],counts,'terminal_ms')
  assert not r[key]['chrome']['wsClosed'] and r[key]['chrome']['welcome']
 admission=json.loads((out/'admission.json').read_text());assert len(admission)==1000
 assert dict(Counter(str(v['status']) for v in admission))==r['admission']['statuses']
 assert sum(v['welcome'] for v in admission)==r['admission']['welcomed']
 assert p95([v['ms'] for v in admission if v['welcome']])==r['admission']['welcome_ms']['p95']
 verify_counts([{**v,'status':'timeout' if v.get('error')=='TimeoutError' else v['status']} for v in admission],r['admission']['requests'],'ms')
 for trial in range(10):assert len([v for v in admission if v['trial']==trial])==100
 checks=json.loads((out/'scenario-checks.json').read_text())
 stages=[v for v in checks if v['phase'].startswith(('connect:','reconnect:'))]
 assert sum(v['users'] for v in stages)==r['functional_ws']['attempts']==400
 assert sum(n for v in stages for error,n in v['errors'].items() if 'HTTP 502' in error)==r['functional_ws']['errors_502']
 for file in out.glob('scenario-samples*.jsonl'):
  rows=[json.loads(v) for v in file.read_text().splitlines()]
  summary=json.loads((out/file.name.replace('samples','summary').replace('.jsonl','.json')).read_text())
  for action,v in summary.items():
   group=[row for row in rows if row['action']==action]
   assert len(group)==v['n'] and p95([v['ms'] for v in group])==v['p95']
   verify_counts(group,r['scenario_requests'][action],'ms')
   assert 100-len({v['account_index'] for v in group})==r['scenario_not_reached'][action]
  assert 'main:deliver' not in summary
  assert 'main:deliver:first' in summary and 'main:deliver:retry' in summary
 logs=list((out/'backend-logs').glob('*.gz'));assert len(logs)==10
 for name,pattern in [('connection_limit','too many clients already'),('pool_timeout','PoolTimeout'),('closed_protocol','Attempt to send on a closed protocol')]:
  count=sum(gzip.decompress(p.read_bytes()).decode().count(pattern) for p in logs)
  assert count==r['backend_error_lines'][name]
  assert (out/'server-errors.txt').read_text().count(pattern)==count
 queue=(out/'listen-queues.txt').read_text().splitlines()
 for port,cap in [(9581,backlog),(9582,50)]:
  assert int(next(v for v in queue if f'127.0.0.1.{port}' in v).split()[0].split('/')[-1])==cap
 assert all(v['pool_size_max']<=10 for v in r['pool_stats'].values())
 edge=[v.split('|') for v in (out/'nginx-logs/native-access.log').read_text().splitlines()]
 edge=[v for v in edge if len(v)==7 and v[1]=='8096' and v[2]=='502']
 assert len(edge)==r['nginx_502']['count']
 assert dict(Counter(v[3] for v in edge))==r['nginx_502']['upstreams']
for name,digest in json.loads((ROOT/'sha256.json').read_text()).items():
 assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
for name,digest in json.loads((ROOT/'harness-used.sha256.json').read_text()).items():
 assert hashlib.sha256((ROOT/'harness'/name).read_bytes()).hexdigest()==digest,name
reviews=json.loads((ROOT/'reviews.json').read_text())
if reviews['status']=='approved':
 final=reviews['rounds'][-1]
 assert len(final['decisions'])==2 and len({r['agent'] for r in final['decisions']})==2
 assert all(r['decision']=='approved' and r['report_sha256']==final['report_sha256'] for r in final['decisions'])
 report=ROOT.parents[2]/'stress-test.md'
 if report.exists():
  assert hashlib.sha256(report.read_bytes()).hexdigest()==reviews['final_sha256']
  before=report.read_text().replace('- 검토: **두 독립 검토자 모두 승인**','- 검토: 두 독립 검토자 검토 중',1)
  assert hashlib.sha256(before.encode()).hexdigest()==final['report_sha256']
print('Independent P95, counts, all logs, queues, pool limits and hashes passed')
