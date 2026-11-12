"""100 paced clients request only Nginx /healthz: no Front, Django or DB."""
import concurrent.futures,http.client,json,os,sys,threading,time
from pathlib import Path
ROOT=Path('/private/tmp/platform-connect-diagnosis-20261107')
OUT=ROOT/'health1'
OUT.mkdir()
os.environ['LOCAL44_OUT']=str(OUT)
sys.path.insert(0,str(ROOT/'harness'))
def fetch(key,path,data):
    connection=http.client.HTTPConnection('127.0.0.1',8116,timeout=5)
    try:
        connection.request('GET',path,headers={'Connection':'close'})
        response=connection.getresponse()
        body=response.read()
        return response.status,{'bytes':len(body)}
    except OSError as error:return 'timeout' if isinstance(error,TimeoutError) else 'network_error',{'errno':error.errno}
    finally:connection.close()
import client_hooks
client_hooks.install(globals())
barrier=threading.Barrier(100)
def user(index):
    barrier.wait();rows=[]
    for iteration in range(40):
        started=time.monotonic();status,body=fetch(str(index),'/healthz',None)
        rows.append({'account':index,'iteration':iteration,'status':status,'ms':(time.monotonic()-started)*1000,**body})
        time.sleep(max(0,.5-(time.monotonic()-started)))
    return rows
started=time.time()
with concurrent.futures.ThreadPoolExecutor(100) as pool:
    rows=[row for batch in pool.map(user,range(100)) for row in batch]
result={'started':started,'ended':time.time(),'requests':len(rows),'success':sum(r['status']==200 for r in rows),'errors':sum(r['status']!=200 for r in rows)}
assert len(rows)==4000
(OUT/'requests.json').write_text(json.dumps(rows))
(OUT/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
