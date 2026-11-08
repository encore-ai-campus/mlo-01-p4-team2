"""Ten identical 100-account handshake bursts; every attempt is counted."""
import asyncio,json,os,time
from collections import Counter
from pathlib import Path
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus
OUT=Path(os.environ['LOCAL44_OUT'])
keys=json.loads((OUT/'sessions.json').read_text())
assert len(keys)==100
async def run():
    attempts=[]
    for trial in range(10):
        sockets=[];readers=[]
        async def one(index,key):
            start=time.perf_counter();row={'at':time.time(),'trial':trial,'account_index':index,'status':None,'welcome':False}
            try:
                ws=await connect('ws://127.0.0.1:8116/ws/movement/?control=server',origin='http://127.0.0.1:8116',
                    additional_headers={'Cookie':'REDACTED'+key},proxy=None,ping_interval=None,open_timeout=40,max_size=2**23)
                sockets.append(ws);row['status']=101
                while True:
                    message=json.loads(await asyncio.wait_for(ws.recv(),40))
                    if message.get('type')=='welcome':row['welcome']=True;break
                async def drain():
                    try:
                        async for _ in ws:pass
                    except Exception:pass
                readers.append(asyncio.create_task(drain()))
            except InvalidStatus as error:row['status']=error.response.status_code
            except Exception as error:row.update(status='error',error=type(error).__name__)
            row['ms']=(time.perf_counter()-start)*1000
            return row
        rows=await asyncio.gather(*(one(i,key) for i,key in enumerate(keys)))
        attempts.extend(rows)
        await asyncio.gather(*(ws.close() for ws in sockets),return_exceptions=True)
        await asyncio.gather(*readers,return_exceptions=True)
        (OUT/'admission.json').write_text(json.dumps(attempts,indent=2))
        print(json.dumps({'trial':trial,'attempts':len(rows),'statuses':dict(Counter(str(r['status']) for r in rows)),
            'welcomed':sum(r['welcome'] for r in rows)}),flush=True)
        await asyncio.sleep(1)
asyncio.run(run())
