"""Publish only non-secret measurements; keep credentials and profiles private."""
import hashlib,json,shutil
from pathlib import Path
OUT=Path('/private/tmp/platform-local33-20261106')
REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
DEST=REPO/'loadtest/results/20261106-local33'
DEST.mkdir(parents=True,exist_ok=True)
patterns=['raw-*.json','chrome-walk.json','chrome-real.json','chrome-live.json','chrome-*-before.png','chrome-*-after.png','chrome-real-during.png',
 'scenario-samples*.jsonl','scenario-summary*.json','scenario-checks*.json','*-regression.log','regression-result.json','regression-summary.json',
 'preflight.json','audit.json','persistence.json','scenario-baseline.json','diagnostics.jsonl','resources.jsonl',
 'diagnostic-mixed.log','query-probe.log','server-errors.txt','reviews.json','results.json','hardware.json','versions.json','*-db-config.json','question-bank-counts.json','attestation-*.json',
 'benchmark-*.log','runner-*.log','nginx-native.conf','source-commit.txt','nginx-version.txt','topology.json']
for pattern in patterns:
 for path in OUT.glob(pattern):shutil.copy2(path,DEST/path.name)
(DEST/'nginx-logs').mkdir(exist_ok=True)
shutil.copy2(OUT/'nginx-logs/native-access.log',DEST/'nginx-logs/native-access.log')
shutil.copy2(OUT/'nginx-error.log',DEST/'nginx-error.log')
for name in ('exploratory-docker-proxy','pilot-native'):
 if (OUT/name).exists():shutil.copytree(OUT/name,DEST/name,dirs_exist_ok=True)
shutil.copytree(Path(__file__).resolve().parent,DEST/'harness',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
manifest={str(p.relative_to(DEST)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.rglob('*')) if p.is_file() and p.name!='sha256.json'}
assert not any(Path(name).name in ('environment.json','sessions.json','metadata.json','expected-api.json') for name in manifest)
(DEST/'sha256.json').write_text(json.dumps(manifest,indent=2))
print('Published non-secret evidence:',DEST)
