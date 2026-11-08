import os
"""Observe all eight workers and the shared DB/Redis during the measured windows."""
import json
from pathlib import Path
import subprocess
import time
import psycopg
import redis

OUT=Path(os.environ.get('LOCAL44_OUT','/private/tmp/platform-ws-20261106/template'))
stop=OUT/'monitor.stop'
if stop.exists():stop.unlink()
db=psycopg.connect(dbname=json.loads((OUT/'environment.json').read_text())['POSTGRES_DB'],user='event_test',host='127.0.0.1',port=55500,autocommit=True,application_name='query-monitor')
env=json.loads((OUT/'environment.json').read_text())
stores={name:redis.Redis.from_url(env[key]) for name,key in [('movement','CAMPUS_MOVEMENT_REDIS_URL'),('service','CAMPUS_SERVICE_REDIS_URL')]}
with (OUT/'resources.jsonl').open('a') as log:
    while not stop.exists():
        rows=json.loads((OUT/'pids.json').read_text())+json.loads((OUT/'front-pids.json').read_text())
        processes={}
        for row in rows:
            result=subprocess.run(['ps','-p',str(row['pid']),'-o','pid=,pcpu=,rss='],capture_output=True,text=True)
            if result.returncode==0:
                _,cpu,rss=result.stdout.split();processes[str(row['pid'])]={**row,'cpu':float(cpu),'rss_mib':int(rss)/1024}
        active,total,waiting=db.execute("SELECT count(*) FILTER(WHERE state='active'),count(*),count(*) FILTER(WHERE wait_event_type='Lock') FROM pg_stat_activity WHERE datname=current_database()").fetchone()
        result=subprocess.run(['lsof','-nP','-a','-p',','.join(processes),'-iTCP','-sTCP:ESTABLISHED','-Fn'],capture_output=True,text=True)
        counts={str(port):0 for port in range(9551,9555)}
        for line in result.stdout.splitlines():
            for port in counts:
                if line.startswith('n127.0.0.1:'+port+'->'):counts[port]+=1
        workers=db.execute("SELECT application_name,count(*) FROM pg_stat_activity WHERE datname=current_database() GROUP BY application_name").fetchall()
        cluster=db.execute("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend'").fetchone()[0]
        record={'cluster_client_connections':cluster,'connections_by_worker':dict(workers),'at':time.time(),'processes':processes,'db_active':active,'db_total':total,'db_waiting_lock':waiting,
            'movement_connections':counts,'redis':{name:{k:client.info('stats')[k] for k in ('total_commands_processed','instantaneous_ops_per_sec')} for name,client in stores.items()}}
        log.write(json.dumps(record)+'\n');log.flush();time.sleep(2)
