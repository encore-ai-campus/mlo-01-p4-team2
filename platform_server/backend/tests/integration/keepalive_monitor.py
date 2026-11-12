"""One read-only DB observer per condition; no SQL text or user data is exported."""
import json
import os
import signal
import time
from pathlib import Path
import psycopg

OUT=Path(os.environ['LOCAL44_OUT'])
env=json.loads((OUT/'environment.json').read_text())
assert env['POSTGRES_DB'].startswith('receipt_keepalive_') and env['POSTGRES_PORT']=='55500'
running=True
def stop(*_):
    global running
    running=False
signal.signal(signal.SIGTERM,stop)
with psycopg.connect(host='127.0.0.1',port=55500,user='receipt_test',dbname=env['POSTGRES_DB'],
                     application_name='keepalive-observer',autocommit=True) as db, (OUT/'db-monitor.jsonl').open('w',buffering=1) as log:
    while running:
        total,owned,waiting=db.execute("SELECT count(*), count(*) FILTER(WHERE datname=%s), count(*) FILTER(WHERE datname=%s AND wait_event_type='Lock') FROM pg_stat_activity WHERE backend_type='client backend'",(env['POSTGRES_DB'],env['POSTGRES_DB'])).fetchone()
        log.write(json.dumps({'at':time.time(),'cluster_connections':total,'run_db_connections':owned,'lock_waiting':waiting})+'\n')
        time.sleep(.25)
