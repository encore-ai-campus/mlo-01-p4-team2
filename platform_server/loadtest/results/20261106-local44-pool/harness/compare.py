"""Compare completed measurements; never reclassify missing responses as successes."""
from collections import Counter
import gzip,hashlib,json,subprocess,sys
from pathlib import Path
ROOT=Path('/private/tmp/platform-local44-20261106')
REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
result={'old':json.loads((REPO/'loadtest/results/20261106-local33/results.json').read_text()),'variants':{}}
for label in ('nopool','pool'):
 out=ROOT/label
 subprocess.run([sys.executable,str(Path(__file__).with_name('metrics.py')),str(out)],check=True)
 r=json.loads((out/'results.json').read_text())
 checks=r['scenario-checks']
 stages=[v for v in checks if v['phase'].startswith(('connect:','reconnect:'))]
 r['functional_ws']={'attempts':sum(v['users'] for v in stages),
  'errors_502':sum(n for v in stages for error,n in v['errors'].items() if 'HTTP 502' in error),
  'stages':stages}
 r['backend_error_lines']={name:sum(gzip.decompress(p.read_bytes()).decode().count(pattern) for p in (out/'backend-logs').glob('*.gz'))
  for name,pattern in [('connection_limit','too many clients already'),('pool_timeout','PoolTimeout')]}
 attest=[json.loads(p.read_text()) for p in out.glob('attestation-*.json')]
 assert len(attest)==9
 assert all(v['pool_options']==({'min_size':1,'max_size':10} if label=='pool' else None) for v in attest)
 r['attestations']=attest
 r['pool_timeout_seconds']=30
 result['variants'][label]=r
assert result['variants']['pool']['all_phases_connections']['by_worker_max']
assert all(n<=10 for name,n in result['variants']['pool']['all_phases_connections']['by_worker_max'].items() if name.startswith('local44-') and name!='local44-monitor')
result['initial_comparison']=json.loads((ROOT/'initial-comparison.json').read_text())
(ROOT/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('Comparison prepared with configured and observed connection limits verified')
