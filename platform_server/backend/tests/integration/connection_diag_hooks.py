"""Temporary diagnostic hooks. Payloads, cookies and SQL text are never logged."""
import asyncio,contextvars,http.client,json,os,queue,threading,time
from pathlib import Path
from twisted.internet import reactor
CTX=contextvars.ContextVar('diag',default=None)
Q=queue.SimpleQueue()
ROOT=Path(os.environ['DIAG_ROOT'])
ROLE=os.environ['INTEGRATION_WORKER']
ACTIVE=0
def emit(row):Q.put({'at':time.time(),'role':ROLE,**row})
def writer():
    f=(ROOT/'trace'/f'{ROLE}.jsonl').open('a',buffering=1)
    deadline=time.monotonic()
    while True:
        try:f.write(json.dumps(Q.get(timeout=.1))+'\n')
        except queue.Empty:pass
        now=time.monotonic()
        if now>=deadline:
            pool={}
            if os.environ.get('DIAG_BACKEND')=='1':
                from django.db.backends.postgresql.base import DatabaseWrapper
                p=DatabaseWrapper._connection_pools.get('default')
                if p:pool=p.get_stats()
            f.write(json.dumps({'at':time.time(),'role':ROLE,'kind':'sample','cpu_seconds':time.process_time(),'threads':threading.active_count(),'active_http':ACTIVE,'pool':pool})+'\n')
            deadline=now+.1
def heartbeat(expected):
    now=time.monotonic()
    emit({'kind':'loop','lag_ms':max(0,(now-expected)*1000)})
    reactor.callLater(.05,heartbeat,now+.05)
def install(backend):
    threading.Thread(target=writer,name='diag-writer',daemon=True).start()
    reactor.callLater(.05,heartbeat,time.monotonic()+.05)
    if backend:
        from psycopg_pool import ConnectionPool
        original_get=ConnectionPool.getconn
        def getconn(self,*a,**kw):
            started=time.perf_counter()
            try:return original_get(self,*a,**kw)
            finally:
                row=CTX.get()
                if row is not None:row['pool_wait_ms']+=(time.perf_counter()-started)*1000
        ConnectionPool.getconn=getconn
        from django.db.backends.utils import CursorWrapper
        original_execute=CursorWrapper._execute
        def execute(self,*a,**kw):
            started=time.perf_counter()
            try:return original_execute(self,*a,**kw)
            finally:
                row=CTX.get()
                if row is not None:row['sql_ms']+=(time.perf_counter()-started)*1000;row['sql_count']+=1
        CursorWrapper._execute=execute
    else:
        original_request=http.client.HTTPConnection.request
        original_connect=http.client.HTTPConnection.connect
        original_response=http.client.HTTPConnection.getresponse
        def request(self,method,url,body=None,headers=None,**kw):
            headers=dict(headers or {})
            row=CTX.get()
            if row:headers['X-Diag-Id']=row['id']
            return original_request(self,method,url,body,headers,**kw)
        def connect(self,*a,**kw):
            started=time.perf_counter();outcome='ok'
            try:return original_connect(self,*a,**kw)
            except BaseException as error:outcome=type(error).__name__;raise
            finally:
                row=CTX.get()
                if row:emit({'kind':'proxy_connect','id':row['id'],'ms':(time.perf_counter()-started)*1000,'outcome':outcome})
        def response(self,*a,**kw):
            started=time.perf_counter()
            try:return original_response(self,*a,**kw)
            finally:
                row=CTX.get()
                if row:emit({'kind':'proxy_response','id':row['id'],'ms':(time.perf_counter()-started)*1000})
        http.client.HTTPConnection.request=request
        http.client.HTTPConnection.connect=connect
        http.client.HTTPConnection.getresponse=response
def wrap(app):
    async def measured(scope,receive,send):
        global ACTIVE
        if scope['type']!='http':return await app(scope,receive,send)
        identity=next((v.decode() for k,v in scope['headers'] if k.lower()==b'x-diag-id'),str(time.time_ns()))
        row={'kind':'request','id':identity,'path':scope['path'],'started_at':time.time(),'pool_wait_ms':0.,'sql_ms':0.,'sql_count':0,'status':None}
        token=CTX.set(row);started=time.perf_counter();ACTIVE+=1
        async def traced(message):
            if message['type']=='http.response.start':row['status']=message['status'];row['headers_ms']=(time.perf_counter()-started)*1000
            await send(message)
        try:return await app(scope,receive,traced)
        finally:
            ACTIVE-=1;row['ms']=(time.perf_counter()-started)*1000;emit(row);CTX.reset(token)
    return measured
