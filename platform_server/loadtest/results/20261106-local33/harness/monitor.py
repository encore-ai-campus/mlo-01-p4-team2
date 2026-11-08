"""Observe all six workers and the shared DB/Redis during the same measured windows."""
import json
from pathlib import Path
import subprocess
import time
import psycopg
import redis

OUT=Path('/private/tmp/platform-local33-20261106')
stop=OUT/'monitor.stop'
if stop.exists():stop.unlink()
db=psycopg.connect(dbname='local33_20261106',user='event_test',host='127.0.0.1',port=55480,autocommit=True,application_name='local33-monitor')
stores={name:redis.Redis(host='127.0.0.1',port=port) for name,port in [('movement',56391),('service',56392)]}
with (OUT/'resources.jsonl').open('a') as log:
    while not stop.exists():
        rows=json.loads((OUT/'pids.json').read_text())+json.loads((OUT/'front-pids.json').read_text())
        processes={}
        for row in rows:
            result=subprocess.run(['ps','-p',str(row['pid']),'-o','pid=,pcpu=,rss='],capture_output=True,text=True)
            if result.returncode==0:
                _,cpu,rss=result.stdout.split();processes[str(row['pid'])]={**row,'cpu':float(cpu),'rss_mib':int(rss)/1024}
        active,total,waiting=db.execute("SELECT count(*) FILTER(WHERE state='active'),count(*),count(*) FILTER(WHERE wait_event_type='Lock') FROM pg_stat_activity WHERE datname='local33_20261106'").fetchone()
        result=subprocess.run(['lsof','-nP','-a','-p',','.join(processes),'-iTCP','-sTCP:ESTABLISHED','-Fn'],capture_output=True,text=True)
        counts={str(port):0 for port in range(9551,9554)}
        for line in result.stdout.splitlines():
            for port in counts:
                if line.startswith('n127.0.0.1:'+port+'->'):counts[port]+=1
        record={'at':time.time(),'processes':processes,'db_active':active,'db_total':total,'db_waiting_lock':waiting,
            'movement_connections':counts,'redis':{name:{k:client.info('stats')[k] for k in ('total_commands_processed','instantaneous_ops_per_sec')} for name,client in stores.items()}}
        log.write(json.dumps(record)+'\n');log.flush();time.sleep(2)
