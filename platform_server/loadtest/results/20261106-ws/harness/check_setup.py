"""Offline assertions: route-only A/B and frozen optimized source."""
import ast,hashlib,json
from pathlib import Path
ROOT=Path('/private/tmp/platform-ws-20261106');HERE=Path(__file__).resolve().parent
def config(label,name):return (ROOT/label/name).read_text().replace(str(ROOT/label),'VARIANT')
assert config('front','internal.conf')==config('direct','internal.conf')
a=config('front','entry.conf');b=config('direct','entry.conf')
assert a.replace('http://127.0.0.1:9581;','http://127.0.0.1:8097;',1).replace('http://127.0.0.1:9582;','http://127.0.0.1:8098;',1)==b
assert 'location / { proxy_pass http://127.0.0.1:9582; }' in b
assert 'least_conn;' in config('front','internal.conf') and 'hash $cookie_campus_sessionid consistent;' in config('front','internal.conf')
for name,h in json.loads((ROOT/'measurement-files.sha256.json').read_text()).items():
 assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==h,name
source=Path('/private/tmp/platform-query-source-20261106')
for name,h in json.loads((ROOT/'application.sha256.json').read_text()).items():
 assert hashlib.sha256((source/name).read_bytes()).hexdigest()==h,name
for p in HERE.glob('*.py'):ast.parse(p.read_text())
print('Only two WS destinations differ. HTTP, distribution, source and measurement hashes match.')
