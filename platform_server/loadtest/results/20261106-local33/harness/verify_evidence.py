"""Independent raw-count checks, with no server or database access."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys
out=Path(sys.argv[1])
r=json.loads((out/'results.json').read_text())
def p95(values):
    values=sorted(values)
    return round(values[math.ceil(len(values)*.95)-1],2) if values else None
for mode,label in [('walk','casual'),('real','stress')]:
    parts=[json.loads((out/f'raw-{mode}-{i}.json').read_text()) for i in range(2)]
    assert sum(p['welcomed'] for p in parts)==99
    rows=[x for p in parts for x in p['http_requests']]
    expected=r[label]['http_started']
    assert len(rows)==expected['started']
    assert sum('response_ms' not in x for x in rows)==expected['no_response_at_end']
    assert p95([x['response_ms'] for x in rows if 'response_ms' in x])==expected['response_ms']['p95']
    assert 'p95_lower_bound_ms' not in expected
    assert not r[label]['chrome']['wsClosed'] and r[label]['chrome']['welcome']
for raw in out.glob('scenario-samples*.jsonl'):
    rows=[json.loads(x) for x in raw.read_text().splitlines()]
    summary=json.loads((out/raw.name.replace('samples','summary').replace('.jsonl','.json')).read_text())
    for action,v in summary.items():
        items=[x for x in rows if x['action']==action]
        assert len(items)==v['n'] and p95([x['ms'] for x in items])==v['p95']
        assert dict(Counter(str(x['status']) for x in items))==v['statuses']
manifest=out/'sha256.json'
if manifest.exists():
    for name,digest in json.loads(manifest.read_text()).items():
        assert hashlib.sha256((out/name).read_bytes()).hexdigest()==digest,name
print('Raw counts, nearest-rank P95, scenario statuses and available hashes verified')
