"""Allowlist evidence and reject leaked test credentials before publication."""
import gzip,hashlib,json,shutil
from pathlib import Path
ROOT=Path('/private/tmp/platform-ws-20261106');REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
DEST=REPO/'loadtest/results/20261106-ws';DEST.mkdir(parents=True,exist_ok=True)
shutil.copy2(REPO/'stress-test.md',DEST/'report.md')
for name in ('initial.json','comparison.json','measurement-files.sha256.json','application.sha256.json','security-preexecution-revision.json','reviews.json'):
 if (ROOT/name).exists():shutil.copy2(ROOT/name,DEST/name)
secrets=[]
for label in ('front','direct'):
 source=ROOT/label;target=DEST/label;target.mkdir(exist_ok=True)
 env=json.loads((source/'environment.json').read_text())
 secrets += [env[k] for k in ('DJANGO_SECRET_KEY','FRONTEND_SECRET_KEY')]+json.loads((source/'sessions.json').read_text())
 secrets += [v for row in json.loads((source/'metadata.json').read_text())['users'] for v in row['tickets'].values()]
 for pattern in ('raw-*.json','chrome-walk.json','chrome-real.json','chrome-*-before.png','chrome-*-after.png','benchmark-*.log','resources.jsonl','pool-stats-*.jsonl','attestation-*.json','admission.json','security.json','results.json','entry.conf','internal.conf','entry-error.log','internal-error.log','listen-queues.txt','steps.jsonl'):
  for p in source.glob(pattern):shutil.copy2(p,target/p.name)
 shutil.copytree(source/'backend-logs',target/'backend-logs',dirs_exist_ok=True)
 shutil.copytree(source/'nginx-logs',target/'nginx-logs',dirs_exist_ok=True)
shutil.copytree(Path(__file__).resolve().parent,DEST/'harness',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
for p in DEST.rglob('*'):
 if not p.is_file() or p.suffix=='.png':continue
 data=gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes()
 assert not any(s.encode() in data for s in set(secrets)),p
manifest={str(p.relative_to(DEST)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.rglob('*')) if p.is_file() and p.name!='sha256.json'}
assert not any(Path(p).name in ('environment.json','metadata.json','sessions.json') for p in manifest)
(DEST/'sha256.json').write_text(json.dumps(manifest,indent=2))
print('Evidence packaged; credentials excluded:',DEST)
