"""Publish allowlisted evidence; exclude every session, token and browser profile."""
import gzip,hashlib,json,shutil
from pathlib import Path

ROOT=Path('/private/tmp/platform-backlog-20261106')
REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
DEST=REPO/'loadtest/results/20261106-backlog';DEST.mkdir(parents=True,exist_ok=True)
for name in ('comparison.json','initial.json','application-change.patch','pool-change.patch','application-files.sha256.json',
 'source-base-commit.txt','launcher-tests.log','accounting-tests.log','harness-used.sha256.json','reviews.json','versions.json','hardware.json','persistence.log','api-results.md'):
 if (ROOT/name).exists():shutil.copy2(ROOT/name,DEST/name)
patterns=['raw-*.json','benchmark-*.log','chrome-walk.json','chrome-real.json','chrome-live.json',
 'chrome-*-before.png','chrome-*-after.png','chrome-real-during.png','resources.jsonl','pool-stats-*.jsonl',
 'scenario-samples*.jsonl','scenario-summary*.json','scenario-checks*.json','scenario-baseline.json',
 'admission.json','results.json','audit.json','persistence.json','attestation-*.json','nginx.conf',
 'nginx-error.log','server-errors.txt','listen-queues.txt','kernel-backlog.txt','steps.jsonl']
for label in ('baseline','increased'):
 source=ROOT/label;target=DEST/label;target.mkdir(exist_ok=True)
 for pattern in patterns:
  for p in source.glob(pattern):shutil.copy2(p,target/p.name)
 shutil.copytree(source/'backend-logs',target/'backend-logs',dirs_exist_ok=True)
 (target/'nginx-logs').mkdir(exist_ok=True)
 shutil.copy2(source/'nginx-logs/native-access.log',target/'nginx-logs/native-access.log')
shutil.copytree(Path(__file__).resolve().parent,DEST/'harness',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
secrets=[]
for label in ('baseline','increased'):
 source=ROOT/label;env=json.loads((source/'environment.json').read_text())
 secrets.extend(env[k] for k in ('DJANGO_SECRET_KEY','FRONTEND_SECRET_KEY'))
 secrets.extend(json.loads((source/'sessions.json').read_text()))
 secrets.extend(v for row in json.loads((source/'metadata.json').read_text())['users'] for v in row['tickets'].values())
for p in DEST.rglob('*'):
 if not p.is_file() or p.suffix=='.png':continue
 raw=gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes()
 assert not any(v.encode() in raw for v in set(secrets)),f'Secret found in {p}'
manifest={str(p.relative_to(DEST)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.rglob('*')) if p.is_file() and p.name!='sha256.json'}
assert not any(Path(p).name in ('environment.json','metadata.json','sessions.json','expected-api.json') for p in manifest)
(DEST/'sha256.json').write_text(json.dumps(manifest,indent=2))
print('Evidence packaged; credential exclusion and manifest checks passed:',DEST)
