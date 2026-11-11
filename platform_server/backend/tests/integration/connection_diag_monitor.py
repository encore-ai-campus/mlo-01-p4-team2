"""Read-only listener and PostgreSQL sampling for the owned diagnostic DB."""
import json,subprocess,time
from pathlib import Path
import psycopg
R=Path('/private/tmp/platform-connect-diagnosis-20261107')
db=psycopg.connect(host='127.0.0.1',port=55500,user='receipt_test',dbname='receipt_final_connect_diag_20261107',autocommit=True,application_name='connect-diag-monitor')
stop=R/'monitor.stop';stop.unlink(missing_ok=True)
ports={8116,8117,8118,9601,9602,*range(9751,9755),*range(9761,9765)}
with (R/'kernel-db.jsonl').open('a',buffering=1) as f:
    while not stop.exists():
        started=time.monotonic();at=time.time()
        net=subprocess.run(['netstat','-Lan'],capture_output=True,text=True)
        queues={}
        for line in net.stdout.splitlines():
            fields=line.split()
            if len(fields)<2 or '/' not in fields[0]:continue
            try:port=int(fields[1].rsplit('.',1)[1])
            except ValueError:continue
            if port in ports:queues[str(port)]=[int(x) for x in fields[0].split('/')]
        stats=db.execute("SELECT state,wait_event_type,wait_event,count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() GROUP BY 1,2,3").fetchall()
        f.write(json.dumps({'at':at,'queues':queues,'queue_read_error':net.stderr.strip(),'db_states':stats,'ms':(time.monotonic()-started)*1000})+'\n')
        time.sleep(max(0,.1-(time.monotonic()-started)))
db.close()
