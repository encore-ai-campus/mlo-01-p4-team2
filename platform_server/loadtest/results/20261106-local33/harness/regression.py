"""Run all backend regressions in a second disposable DB, then frontend tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
OUT=Path('/private/tmp/platform-local33-20261106')
ROOT=Path('/private/tmp/platform-local33-source-20261106')
env=os.environ.copy();env.update(json.loads((OUT/'environment.json').read_text()))
env.update(DJANGO_SETTINGS_MODULE='config.postgres_test_settings',CAMPUS_DATA_DIR=str(OUT/'regression-data'))
assert env['POSTGRES_PORT']=='55480'
with (OUT/'backend-regression.log').open('w') as log:
    backend=subprocess.run([sys.executable,'-B','manage.py','test','--noinput','--verbosity=1'],cwd=ROOT/'backend',env=env,stdout=log,stderr=subprocess.STDOUT)
tests=sorted(str(path) for path in (ROOT/'frontend/tests').glob('*.test.cjs'))
with (OUT/'frontend-regression.log').open('w') as log:
    frontend=subprocess.run(['node','--test',*tests],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
(OUT/'regression-result.json').write_text(json.dumps({'backend_exit':backend.returncode,'frontend_exit':frontend.returncode}))
raise SystemExit(bool(backend.returncode or frontend.returncode))
