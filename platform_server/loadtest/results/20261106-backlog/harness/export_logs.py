"""Publish complete redacted app logs plus the exact lines used for error counts."""
import gzip,json
from pathlib import Path
ROOT=Path('/private/tmp/platform-backlog-20261106')
for label in ('baseline','increased'):
 out=ROOT/label;env=json.loads((out/'environment.json').read_text())
 secrets=[env[k] for k in ('DJANGO_SECRET_KEY','FRONTEND_SECRET_KEY')]
 secrets+=json.loads((out/'sessions.json').read_text())
 secrets+=[v for row in json.loads((out/'metadata.json').read_text())['users'] for v in row['tickets'].values()]
 dest=out/'backend-logs';dest.mkdir(exist_ok=True);matches=[]
 files=sorted([*out.glob('service-*.log'),*out.glob('movement-*.log'),*out.glob('front-*.log')]);assert len(files)==10
 for path in files:
  text=path.read_text(errors='replace')
  for secret in set(secrets):text=text.replace(secret,'[REDACTED]')
  (dest/(path.name+'.gz')).write_bytes(gzip.compress(text.encode(),mtime=0))
  for index,line in enumerate(text.splitlines(),1):
   if any(v in line for v in ('too many clients already','PoolTimeout','Attempt to send on a closed protocol')):
    matches.append(f'{path.name}:{index}: {line}')
 (out/'server-errors.txt').write_text('\n'.join(matches)+ ('\n' if matches else ''))
 print(label,'complete redacted logs:',len(files),'matched lines:',len(matches))
