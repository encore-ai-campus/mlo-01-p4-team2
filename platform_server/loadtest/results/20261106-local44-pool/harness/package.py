"""Package only allowlisted evidence, excluding sessions, tokens and browser profiles."""
import hashlib,json,shutil
from pathlib import Path
ROOT=Path('/private/tmp/platform-local44-20261106')
REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
DEST=REPO/'loadtest/results/20261106-local44-pool';DEST.mkdir(parents=True,exist_ok=True)
for name in ('comparison.json','initial-comparison.json','application-change.patch','application-files.sha256.json',
 'source-base-commit.txt','focused-tests-final.log','focused-tests-final-exit.txt','test_connection_pool.py','reviews.json','versions.json','hardware.json'):
 if (ROOT/name).exists():shutil.copy2(ROOT/name,DEST/name)
patterns=['raw-*.json','benchmark-*.log','runner-*.log','chrome-walk.json','chrome-real.json','chrome-live.json',
 'chrome-*-before.png','chrome-*-after.png','chrome-real-during.png','resources.jsonl','pool-stats-*.jsonl',
 'scenario-samples*.jsonl','scenario-summary*.json','scenario-checks*.json','scenario-baseline.json',
 'admission.json','admission.log','results.json','audit.json','persistence.json','attestation-*.json','nginx.conf','nginx-error.log','server-errors.txt']
for label in ('nopool','pool'):
 source=ROOT/label;target=DEST/label;target.mkdir(exist_ok=True)
 for pattern in patterns:
  for p in source.glob(pattern):shutil.copy2(p,target/p.name)
 shutil.copytree(source/'backend-logs',target/'backend-logs',dirs_exist_ok=True)
 (target/'nginx-logs').mkdir(exist_ok=True)
 shutil.copy2(source/'nginx-logs/native-access.log',target/'nginx-logs/native-access.log')
# The pilot and incomplete generators are kept as supplementary records, separate from final results.
pre=DEST/'preliminary';pre.mkdir(exist_ok=True)
for name in ('benchmark-walk.log','chrome-walk.json','raw-walk-0.json','raw-walk-1.json'):
 p=ROOT/'pilot-shared-redis'/name
 if p.exists():shutil.copy2(p,pre/('shared-redis-'+name))
for p in (ROOT/'nopool/exploratory').glob('admission-before-drain-*'):
 shutil.copy2(p,pre/p.name)
shutil.copytree(Path(__file__).resolve().parent,DEST/'harness',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
manifest={str(p.relative_to(DEST)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.rglob('*')) if p.is_file() and p.name!='sha256.json'}
assert not any(Path(p).name in ('environment.json','metadata.json','sessions.json','expected-api.json') for p in manifest)
(DEST/'sha256.json').write_text(json.dumps(manifest,indent=2))
print('Published allowlisted evidence:',DEST)
