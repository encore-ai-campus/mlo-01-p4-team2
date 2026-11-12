"""Read-only independent recomputation: counts, nearest-rank P95, caps and hashes."""
from collections import Counter
import gzip,hashlib,json,math
from pathlib import Path
import sys
ROOT=Path(sys.argv[1]);comparison=json.loads((ROOT/'comparison.json').read_text())
def p95(values):
 values=sorted(values)
 return round(values[math.ceil(.95*len(values))-1],2) if values else None
for label in ('nopool','pool'):
 out=ROOT/label;result=comparison['variants'][label]
 for mode,key in [('walk','casual'),('real','stress')]:
  parts=[json.loads((out/f'raw-{mode}-{i}.json').read_text()) for i in range(2)]
  assert sum(v['welcomed'] for v in parts)==99
  http=[v for part in parts for v in part['http_requests']]
  assert len(http)==result[key]['http_started']['started']
  assert sum('response_ms' not in v for v in http)==result[key]['http_started']['no_response_at_end']
  assert p95([v['response_ms'] for v in http if 'response_ms' in v])==result[key]['http_started']['response_ms']['p95']
  assert not result[key]['chrome']['wsClosed']
 admission=json.loads((out/'admission.json').read_text());assert len(admission)==1000
 assert dict(Counter(str(v['status']) for v in admission))==result['admission']['statuses']
 for trial in range(10):assert len([r for r in admission if r['trial']==trial])==100
 for file in out.glob('scenario-samples*.jsonl'):
  rows=[json.loads(v) for v in file.read_text().splitlines()]
  summary=json.loads((out/file.name.replace('samples','summary').replace('.jsonl','.json')).read_text())
  for action,v in summary.items():
   group=[row for row in rows if row['action']==action]
   assert len(group)==v['n'] and p95([r['ms'] for r in group])==v['p95']
 for name,pattern in [('connection_limit','too many clients already'),('pool_timeout','PoolTimeout')]:
  logs=list((out/'backend-logs').glob('*.gz'));assert len(logs)==8
  count=sum(gzip.decompress(p.read_bytes()).decode().count(pattern) for p in logs)
  assert count==result['backend_error_lines'][name]
  assert (out/'server-errors.txt').read_text().count(pattern)==count
 if label=='pool':
  assert all(v['pool_size_max']<=10 for v in result['pool_stats'].values())
  assert all(v<=10 for k,v in result['all_phases_connections']['by_worker_max'].items() if k.startswith('local44-') and k!='local44-monitor')
for name,digest in json.loads((ROOT/'sha256.json').read_text()).items():
 assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
print('Independent raw P95, sample counts, admission denominators, pool caps and hashes passed')
