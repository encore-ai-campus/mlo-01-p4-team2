"""Invoke approved regression suites against the frozen source."""
import json
import os
import subprocess
import sys
from pathlib import Path
from keepalive_suite import ROOT,SNAP,PY,gate

def main():
    gate('unit')
    phase=sys.argv[1]
    out=ROOT/'unit-logs'
    if phase=='frontend':
        with (out/'frontend-python.log').open('w') as log:
            first=subprocess.run([PY,'-B','manage.py','test','pages','--noinput','--verbosity=2'],
                cwd=SNAP/'frontend',stdout=log,stderr=subprocess.STDOUT)
        with (out/'frontend-node.log').open('w') as log:
            second=subprocess.run(['node','--test',*map(str,sorted((SNAP/'frontend/tests').glob('*.test.cjs')))],
                cwd=SNAP,stdout=log,stderr=subprocess.STDOUT)
        print(json.dumps({'python_exit':first.returncode,'node_exit':second.returncode}),flush=True)
        return bool(first.returncode or second.returncode)
    env=json.loads((Path('/private/tmp/platform-final-integration-20261107-r2')/'environment.json').read_text())
    env.update(POSTGRES_DB='receipt_keepalive_unit_20261107',DJANGO_SETTINGS_MODULE='config.postgres_test_settings',
        CAMPUS_REDIS_URL='',CAMPUS_MOVEMENT_REDIS_URL='',CAMPUS_SERVICE_REDIS_URL='',
        CAMPUS_DATA_DIR=str(ROOT/'unit-data'))
    os.environ.update(env)
    import psycopg
    with psycopg.connect(host='127.0.0.1',port=55500,user='receipt_test',dbname='postgres',autocommit=True) as db:
        exists=db.execute('SELECT 1 FROM pg_database WHERE datname=%s',(env['POSTGRES_DB'],)).fetchone()
        if not exists:db.execute('CREATE DATABASE receipt_keepalive_unit_20261107')
        assert not db.execute('SELECT 1 FROM pg_database WHERE datname=%s',('test_'+env['POSTGRES_DB'],)).fetchone(),'Never reuse another test DB'
    sys.path.insert(0,str(SNAP/'backend'));os.chdir(SNAP/'backend')
    import django
    django.setup()
    from django.conf import settings
    from django.test.utils import get_runner
    kwargs={'tags':{'performance'}} if phase=='performance' else {'exclude_tags':{'performance'}}
    result=get_runner(settings)(verbosity=2,interactive=False,**kwargs).run_tests([])
    return bool(result)

if __name__=='__main__':raise SystemExit(main())
