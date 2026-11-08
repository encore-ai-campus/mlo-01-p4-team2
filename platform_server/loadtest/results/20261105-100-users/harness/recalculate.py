"""Verify packaged raw evidence without a database, cookies or running server."""
import gzip
import hashlib
import json
from pathlib import Path
from metrics import percentiles,outcomes

root=Path(__file__).resolve().parents[1]
results=json.loads((root/'results.json').read_text())
manifest=json.loads((root/'sha256.json').read_text())
for name,expected in manifest.items():assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
def compressed(name):return gzip.decompress((root/(name+'.gz')).read_bytes()).decode()
summary={}
for mode,label in [('real','stress'),('walk','casual')]:
    parts=[json.loads(compressed(f'raw-{mode}-{i}.json')) for i in range(2)]
    http=percentiles([v*1000 for p in parts for values in p['http_ms'].values() for v in values])
    chat=outcomes([row for p in parts for row in p['chat_requests']])
    controls=[row for p in parts for row in p['control_requests']]
    jump=outcomes([row for row in controls if row['action']=='jump'])
    assert http==results[label]['http_all_ms']
    assert chat==results[label]['chat']
    assert jump==results[label]['controls']['jump']
    summary[label]={'http':http,'chat':chat,'jump':jump}
samples=[json.loads(line) for line in compressed('scenario-samples.jsonl').splitlines()]
for action,expected in results['scenarios'].items():
    rows=[row for row in samples if row['action']==action]
    actual=percentiles([row['ms'] for row in rows])
    assert all(actual[key]==expected[key] for key in ('n','p50','p95','p99','max')),action
    assert sum(not row['expected'] for row in rows)==expected['unexpected'],action
http=[r for r in samples if isinstance(r['status'],int) and r['status']!=101]
summary['http_scenarios']={'responses':len(http),'5xx':sum(r['status']>=500 for r in http)}
summary['verified_manifest_files']=len(manifest)
print(json.dumps(summary,ensure_ascii=False,indent=2))
