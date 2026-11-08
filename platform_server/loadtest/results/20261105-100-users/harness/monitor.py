"""Record only owned test processes and PostgreSQL connections."""
import json
from pathlib import Path
import subprocess
import time
import psycopg

out = Path('/private/tmp/platform-load100-20261105')
pids = json.loads((out / 'server-pids.json').read_text())
db = psycopg.connect(dbname='load100_20261105', user='event_test', host='127.0.0.1', port=55479, autocommit=True)
with (out / 'resources.jsonl').open('a') as stream:
    for _ in range(360):
        result = subprocess.run(['ps', '-p', ','.join(map(str, pids)), '-o', 'pid=,pcpu=,rss='], capture_output=True, text=True, check=True)
        active, connections = db.execute("SELECT count(*) FILTER (WHERE state='active'), count(*) FROM pg_stat_activity WHERE datname='load100_20261105'").fetchone()
        stream.write(json.dumps({'at': time.time(), 'processes': result.stdout.strip().splitlines(), 'db_active': active, 'db_connections': connections}) + '\n')
        stream.flush()
        time.sleep(2)
