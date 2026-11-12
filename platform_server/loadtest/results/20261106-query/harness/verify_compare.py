"""Read-only verification of raw codes, expected outcomes, P95 and SQL-only scope."""
from collections import Counter
import gzip,hashlib,json,math,re,sys
from pathlib import Path
ROOT=Path(sys.argv[1]).resolve();C=json.loads((ROOT/'comparison.json').read_text())
RULES=json.loads((ROOT/'expectations.json').read_text())
def p95(values):
 values=sorted(values)
 return round(values[math.ceil(.95*len(values))-1],2) if values else None
assert p95(list(range(1,101)))==95 and p95([]) is None
def code_kind(s):
 if s in ('ok',101) or isinstance(s,int) and 200<=s<300:return 'response_success'
 if s=='rejected' or isinstance(s,int) and 400<=s<500:return 'business_rejected'
 if isinstance(s,int) and 500<=s<600:return 'server_error'
 if s=='timeout':return 'timeout'
 if s in ('error','network_error','transport_error'):return 'network_error'
 return 'unresolved'
def specification(action,statuses):
 if action in RULES['validations']:return RULES['validations'][action]
 if action in RULES['normal_actions']:return RULES['normal_actions'][action]
 return {'purpose':'normal','statuses':list(statuses),'fields':{}}
def verify(rows,result,field,statuses=(200,201)):
 codes=Counter(code_kind(r['status']) for r in rows)
 assert dict(Counter(str(r['status']) for r in rows))==result['status_counts']
 for name in ('response_success','business_rejected','server_error','network_error','timeout','unresolved'):assert codes[name]==result[name],name
 passed=0;purposes=set();values=[]
 for row in rows:
  action=row.get('action','');allowed=(101,) if action=='ws_connect' else ('ok',) if action.startswith('ws:') else statuses
  spec=specification(action,allowed);purposes.add(spec['purpose'])
  if 'expectation' in row:assert row['expectation']==spec,action
  matched=(row['status'] in spec['statuses'] and all(k in row and row[k]==v for k,v in spec['fields'].items()))
  if 'expected' in row:assert row['expected']==matched,action
  if code_kind(row['status'])!='unresolved':
   passed+=matched;values.append(row[field])
  assert row['status']!='pending'
 assert len(purposes)<=1
 assert result['purpose']==next(iter(purposes),'normal')
 assert result['attempts']==len(rows)==result['success']+result['errors']+result['unresolved']
 assert result['success']==passed and result['errors']==len(rows)-codes['unresolved']-passed
 assert len(values)==len(rows)-codes['unresolved']
 assert p95(values)==(result['terminal_ms'] or {}).get('p95')
def route(row):
 path=row['uri'].split('?')[0]
 path=re.sub(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',':uuid',path)
 path=re.sub(r'(/sessions/)\d+',r'\1:session_id',path)
 path=re.sub(r'(/questions/)\d+',r'\1:order',path)
 return row['method']+' '+path
for label in ('before','after'):
 out=ROOT/label;r=C['variants'][label]
 for mode,key in [('walk','casual'),('real','stress')]:
  parts=[json.loads((out/f'raw-{mode}-{i}.json').read_text()) for i in range(2)]
  assert sum(p['welcomed'] for p in parts)==99
  http=[v for p in parts for v in p['http_requests']]
  verify(http,r[key]['http_terminal'],'terminal_ms')
  browser=json.loads((out/f'chrome-{mode}.json').read_text())['metrics']
  assert browser['welcome'] and not browser['wsClosed']
  verify(browser['http'],r[key]['chrome']['http_terminal'],'ms')
  combined=http+[{**v,'uri':v['path'],**({'terminal_ms':v['ms']} if 'ms' in v else {})} for v in browser['http']]
  verify(combined,r[key]['combined_http_terminal'],'terminal_ms')
  assert set(r[key]['combined_api_requests'])=={route(v) for v in combined}
  for path,counts in r[key]['combined_api_requests'].items():verify([v for v in combined if route(v)==path],counts,'terminal_ms')
  verify([v for p in parts for v in p['chat_requests']],r[key]['chat_terminal'],'terminal_ms',('ok',))
  for action,counts in r[key]['control_terminal'].items():
   verify([v for p in parts for v in p['control_requests'] if v['action']==action],counts,'terminal_ms',('ok',))
  assert all(v['unresolved']==0 for v in r[key]['drain_reports'])
 admission=json.loads((out/'admission.json').read_text());assert len(admission)==1000
 for trial in range(10):assert sum(v['trial']==trial for v in admission)==100
 verify([{**v,'status':'timeout' if v.get('error')=='TimeoutError' else v['status']} for v in admission],r['admission']['requests'],'ms',(101,))
 assert p95([v['ms'] for v in admission if v['welcome']])==r['admission']['welcome_ms']['p95']
 rows=[json.loads(line) for line in (out/'scenario-samples.jsonl').read_text().splitlines()]
 assert set(r['scenario_requests'])=={v['action'] for v in rows}
 for action,counts in r['scenario_requests'].items():
  group=[v for v in rows if v['action']==action];verify(group,counts,'ms')
  assert r['scenario_not_reached'][action]==100-len({v['account_index'] for v in group})
 assert 'main:deliver' not in r['scenario_requests']
 assert {'main:deliver:first','main:deliver:retry'}<=set(r['scenario_requests'])
 assert set(r['normal_scenario_requests'])=={k for k,v in r['scenario_requests'].items() if v['purpose']=='normal'}
 assert set(r['validation_requests'])=={k for k,v in r['scenario_requests'].items() if v['purpose']=='validation'}
 assert r['scenario_requests']['ws_connect']['attempts']==400
 for port,cap in [(9581,128),(9582,50)]:
  line=next(s for s in (out/'listen-queues.txt').read_text().splitlines() if f'127.0.0.1.{port}' in s)
  assert int(line.split()[0].split('/')[-1])==cap
 attest=list(out.glob('attestation-*.json'));assert len(attest)==9
 assert all(json.loads(p.read_text())['pool_options']=={'min_size':1,'max_size':10} for p in attest)
 assert all(v['pool_size_max']<=10 for v in r['pool_stats'].values())
 logs=list((out/'backend-logs').glob('*.gz'));assert len(logs)==10
 for name,pattern in [('connection_limit','too many clients already'),('pool_timeout','PoolTimeout'),('closed_protocol','Attempt to send on a closed protocol')]:
  count=sum(gzip.decompress(p.read_bytes()).decode().count(pattern) for p in logs)
  assert count==r['backend_error_lines'][name]==(out/'server-errors.txt').read_text().count(pattern)
for phase in ('casual','stress'):
 for name,delta in C['savings'][phase].items():
  if delta:
   a=C['variants']['before'][phase]['combined_api_requests'][name]['terminal_ms']['p95'];b=C['variants']['after'][phase]['combined_api_requests'][name]['terminal_ms']['p95']
   assert delta['saved_ms']==round(a-b,2) and delta['saved_pct']==round((a-b)/a*100,2)
for action,delta in C['scenario_savings'].items():
 if delta:
  a=C['variants']['before']['scenario_requests'][action]['terminal_ms']['p95'];b=C['variants']['after']['scenario_requests'][action]['terminal_ms']['p95']
  assert delta['saved_ms']==round(a-b,2) and delta['saved_pct']==(round((a-b)/a*100,2) if a else None)
initial=C['initial_comparison'];a=initial['before'];b=initial['after']
assert a['row_counts']==b['row_counts'] and a['sessions_sha256']==b['sessions_sha256']
assert all(v==0 for condition in (a,b) for v in condition['initial_redis_counts'].values())
before=json.loads((ROOT/'before-code-sha256.json').read_text());after=json.loads((ROOT/'after-code-sha256.json').read_text())
assert set(before)==set(after)
assert {p for p in before if before[p]!=after[p]}=={'backend/event_story/main.py','backend/event_story/main_views.py','backend/event_story/recipe.py','backend/event_story/game_services.py','backend/event_story/achievement_services.py','backend/learning/services.py'}
used=json.loads((ROOT/'harness-used.sha256.json').read_text())
assert used['expectations.py']==RULES['source_sha256']
for name,digest in used.items():assert hashlib.sha256((ROOT/'harness'/name).read_bytes()).hexdigest()==digest,name
for name,digest in json.loads((ROOT/'sha256.json').read_text()).items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
reviews=json.loads((ROOT/'reviews.json').read_text())
if reviews['status']=='approved':
 final=reviews['rounds'][-1];assert len({v['agent'] for v in final['decisions'] if v['decision']=='approved' and v['report_sha256']==final['report_sha256']})==2
 report=ROOT/'report.md' if (ROOT/'report.md').exists() else ROOT.parents[2]/'stress-test.md'
 assert hashlib.sha256(report.read_bytes()).hexdigest()==reviews['final_sha256']
 pending=report.read_text().replace('- 검토: **독립 검토자 두 명 모두 승인**','- 검토: 독립 검토자 두 명의 승인 대기',1)
 assert hashlib.sha256(pending.encode()).hexdigest()==final['report_sha256']
print('Raw responses, expected outcomes, normal/validation P95, deltas, fixed settings and hashes passed')
