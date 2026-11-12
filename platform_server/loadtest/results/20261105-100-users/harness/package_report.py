"""Publish only credential-free test evidence and the requested local report."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil

OUT=Path('/private/tmp/platform-load100-20261105')
DEST=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
evidence=DEST/'loadtest/results/20261105-100-users'
evidence.mkdir(parents=True,exist_ok=True)
secrets=json.loads((OUT/'sessions.json').read_text())
env=json.loads((OUT/'environment.json').read_text())
secrets += [value for key,value in env.items() if any(word in key for word in ('PASSWORD','SECRET','TOKEN')) and len(value)>=12]
def safe(data):
    assert not any(value.encode() in data for value in secrets),'Sensitive test state found in deliverable'
    return data
names=['results.json','scenario-summary.json','scenario-checks.json','scenario-summary-npc.json','scenario-checks-npc.json',
    'scenario-summary-poke.json','scenario-checks-poke.json','regression-result.json','regression-failures.json',
    'audit.json','persistence.json','hardware.json','versions.json','test-db-config.json','live-db-config.json','question-bank-counts.json',
    'stress100-final.log','casual100-final.log','scenarios.log','npc.log','private-poke.log','diagnostic-load.log',
    'backend-regression.log','frontend-regression.log']
names += [name for name in ('review.md','cleanup.json','protection-check.json') if (OUT/name).exists()]
for name in names:(evidence/name).write_bytes(safe((OUT/name).read_bytes()))
for name in ['scenario-samples.jsonl','scenario-samples-npc.jsonl','scenario-samples-poke.jsonl','diagnostics.jsonl',
        'raw-real-0.json','raw-real-1.json','raw-walk-0.json','raw-walk-1.json','resources.jsonl']:
    (evidence/(name+'.gz')).write_bytes(gzip.compress(safe((OUT/name).read_bytes()),mtime=0))
harness=evidence/'harness';harness.mkdir(exist_ok=True)
for path in Path(__file__).parent.glob('*.py'):(harness/path.name).write_bytes(safe(path.read_bytes()))
for path in Path(__file__).parent.glob('*.md'):(harness/path.name).write_bytes(safe(path.read_bytes()))
source=OUT/'exploratory/navigate'
target=evidence/'exploratory-navigate';target.mkdir(exist_ok=True)
for name in ('npc.log','scenario-summary-npc.json','scenario-checks-npc.json'):
    if (source/name).exists():(target/name).write_bytes(safe((source/name).read_bytes()))
errors=[]
for path in (OUT/'backend.log',OUT/'backend-restart.log'):
    if path.exists():errors.extend(line for line in path.read_text().splitlines() if 'FATAL:  sorry, too many clients already' in line)
(evidence/'db-connection-errors.txt').write_text('\n'.join(errors)+'\n')
(DEST/'stress-test.md').write_bytes(safe((OUT/'stress-test.md').read_bytes()))
manifest={str(path.relative_to(evidence)):hashlib.sha256(path.read_bytes()).hexdigest() for path in evidence.rglob('*') if path.is_file() and path.name!='sha256.json'}
(evidence/'sha256.json').write_text(json.dumps(manifest,indent=2))
print(DEST/'stress-test.md');print('Evidence files:',len(manifest))
