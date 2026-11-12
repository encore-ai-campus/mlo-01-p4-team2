"""Capture the local measurement prerequisites; never export credentials."""
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import psycopg

OUT=Path('/private/tmp/platform-load100-20261105')
env=json.loads((OUT/'environment.json').read_text())
assert env['POSTGRES_PORT']=='55479'
def config(values):
    with psycopg.connect(dbname=values['POSTGRES_DB'],user=values['POSTGRES_USER'],password=values.get('POSTGRES_PASSWORD',''),
            host=values['POSTGRES_HOST'],port=values['POSTGRES_PORT'],options='-c default_transaction_read_only=on',connect_timeout=3) as db:
        result={key:db.execute('SHOW '+key).fetchone()[0] for key in ('max_connections','shared_buffers','server_version')}
        result['question_counts']=db.execute('SELECT subject,difficulty,question_type,count(*) FROM learning_question WHERE is_active GROUP BY 1,2,3 ORDER BY 1,2,3').fetchall()
        return result
(OUT/'test-db-config.json').write_text(json.dumps(config(env)))
state=Path('/private/tmp/platform-recipe-server-state.json')
assert state.exists(),'The comparison server state file must exist in this local environment'
live=config(json.loads(state.read_text())['backend']['env'])
live['postgres_version']=live['server_version']
(OUT/'live-db-config.json').write_text(json.dumps(live))
versions={p:importlib.metadata.version(p) for p in ('Django','channels','daphne','psycopg','websockets','autobahn')}
(OUT/'versions.json').write_text(json.dumps(versions))
hardware={'platform':platform.platform(),'python':platform.python_version()}
for key,name in [('cpu','machdep.cpu.brand_string'),('logical_cpus','hw.logicalcpu'),('memory_bytes','hw.memsize')]:
    hardware[key]=subprocess.check_output(['sysctl','-n',name],text=True).strip()
(OUT/'hardware.json').write_text(json.dumps(hardware))
print('Non-secret environment metadata recorded')
