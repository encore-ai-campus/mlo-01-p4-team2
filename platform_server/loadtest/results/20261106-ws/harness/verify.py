"""Read-only independent counts/P95, fixed routes, source and approval verification."""
from collections import Counter
import gzip,hashlib,json,math,sys
from pathlib import Path
ROOT=Path(sys.argv[1]).resolve();C=json.loads((ROOT/'comparison.json').read_text())
def p95(xs):
 xs=sorted(xs);return round(xs[math.ceil(.95*len(xs))-1],2) if xs else None
assert p95(list(range(1,101)))==95
def check(rows,v,field='terminal_ms',expected=('ok',)):
 assert len(rows)==v['attempts']==v['success']+v['errors']+v['unresolved']
 assert dict(Counter(str(r['status']) for r in rows))==v['status_counts']
 finalized=[r for r in rows if r['status'] not in ('unresolved','pending',None)]
 assert len(finalized)==len(rows)-v['unresolved']
 assert sum(r['status'] in expected for r in finalized)==v['success']
 assert p95([r[field] for r in finalized])==(v['terminal_ms'] or {}).get('p95')
for label,r in C['variants'].items():
 out=ROOT/label
 for mode,phase in [('walk','casual'),('real','stress')]:
  parts=[json.loads((out/f'raw-{mode}-{i}.json').read_text()) for i in range(2)]
  assert sum(p['welcomed'] for p in parts)==99
  chat=[v for p in parts for v in p['chat_requests']];check(chat,r[phase]['chat_terminal'])
  assert len({v['account_index'] for v in chat})==r[phase]['chat_accounts']==99
  assert r[phase]['chat_missing_ready']==0
  chrome=json.loads((out/f'chrome-{mode}.json').read_text())['metrics']
  assert chrome['welcome'] and '/ws/chat/' in chrome['wsOpened'] and not chrome['wsClosed']
  http=[v for p in parts for v in p['http_requests']]+[{**v,'terminal_ms':v['ms']} for v in chrome['http']]
  check(http,r[phase]['combined_http_terminal'],expected=(200,201))
  for action,v in r[phase]['control_terminal'].items():check([x for p in parts for x in p['control_requests'] if x['action']==action],v)
  assert p95([x*1000 for p in parts for x in p['move_lag']])==r[phase]['movement_latency']['p95']
  assert all(v['unresolved']==0 for p in parts for v in p['drain_reports'])
 admission=json.loads((out/'admission.json').read_text());assert len(admission)==1000
 assert sum(v['welcome'] for v in admission)==r['admission']['welcomed']
 security=json.loads((out/'security.json').read_text())
 for path in ('movement','chat'):
  rows=[v for v in security[:400] if v['action']=='connect:'+path]
  assert len(rows)==r['connections'][path]['attempts']==200
  assert len({v['account_index'] for v in rows})==100
  assert p95([v['ms'] for v in rows])==r['connections'][path]['p95']
 assert all(v['passed']==(v['status']==v['expected']) for v in security)
 assert sum(v['action'].startswith('logout_closes:') for v in security)==2
 assert sum(v['action'].startswith('other_account_alive:') for v in security)==2
 for name,v in r['security'].items():
  rows=[x for x in security if f"{x['action']}[expected={x['expected']}]"==name]
  assert len(rows)==v['attempts'] and sum(x['passed'] for x in rows)==v['success']
  assert p95([x['ms'] for x in rows])==v['p95']
 attest=list(out.glob('attestation-*.json'));assert len(attest)==9
 assert all(json.loads(p.read_text())['pool_options']=={'min_size':1,'max_size':10} for p in attest)
 assert len(r['closed_send_exceptions']['events'])==r['closed_send_exceptions']['count']
 for event in r['closed_send_exceptions']['events']:
  text=gzip.decompress((out/'backend-logs'/(event['log']+'.gz')).read_bytes()).decode().splitlines()
  assert text[event['line']-1].startswith(event['at'])
 a=(out/'entry.conf').read_text();assert 'location / { proxy_pass http://127.0.0.1:9582; }' in a
 for path,port in [('/ws/movement/',9581 if label=='front' else 8097),('/ws/chat/',9582 if label=='front' else 8098)]:
  assert set(r['ws_edge_upstreams'][path])=={f'127.0.0.1:{port}'}
 internal=(out/'internal.conf').read_text();assert 'least_conn;' in internal and 'hash $cookie_campus_sessionid consistent;' in internal
for phase in ('casual','stress'):
 for name,d in C['deltas'][phase].items():
  if d:assert d['saved_ms']==round(d['before_ms']-d['after_ms'],2) and d['saved_pct']==round((d['before_ms']-d['after_ms'])/d['before_ms']*100,2)
for name,h in json.loads((ROOT/'measurement-files.sha256.json').read_text()).items():assert hashlib.sha256((ROOT/'harness'/name).read_bytes()).hexdigest()==h,name
for name,h in json.loads((ROOT/'sha256.json').read_text()).items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==h,name
reviews=json.loads((ROOT/'reviews.json').read_text())
if reviews['status']=='approved':
 final=reviews['rounds'][-1]
 assert len({v['agent'] for v in final['decisions'] if v['decision']=='approved' and v['report_sha256']==final['report_sha256']})==2
 report=(ROOT/'report.md').read_bytes();assert hashlib.sha256(report).hexdigest()==reviews['final_sha256']
 pending=report.decode().replace('- 검토: **독립 검토자 두 명 모두 승인**','- 검토: 독립 검토자 두 명의 승인 대기',1)
 assert hashlib.sha256(pending.encode()).hexdigest()==final['report_sha256']
print('Raw counts/P95,100-account participation, routes, pool limits, hashes and reviews verified')
