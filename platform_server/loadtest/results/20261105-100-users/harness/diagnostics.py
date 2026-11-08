"""Temporary instrumentation for the isolated backend. No application source edits."""
import json
from pathlib import Path
import re
import threading
import time

OUT=Path('/private/tmp/platform-load100-20261105')
lock=threading.Lock()
def write(row):
    row['at']=time.time()
    with lock:
        with (OUT/'diagnostics.jsonl').open('a') as output:output.write(json.dumps(row)+'\n')

class SQLCounts:
    def __init__(self):self.count=0;self.ms=0;self.lock_ms=0;self.verbs={};self.tables={}
    def __call__(self,execute,sql,params,many,context):
        start=time.perf_counter()
        try:return execute(sql,params,many,context)
        finally:
            elapsed=(time.perf_counter()-start)*1000
            self.count+=1;self.ms+=elapsed
            if 'FOR UPDATE' in sql.upper():self.lock_ms+=elapsed
            verb=sql.split(None,1)[0].upper() if sql.strip() else 'EMPTY'
            self.verbs[verb]=self.verbs.get(verb,0)+1
            for table in re.findall(r'FROM "([A-Za-z_]+)"',sql):
                self.tables[table]=self.tables.get(table,0)+1

class SQLTimingMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        from django.db import connection
        stats=SQLCounts();start=time.perf_counter()
        try:
            with connection.execute_wrapper(stats):response=self.get_response(request)
            return response
        finally:
            path=re.sub(r'/[0-9a-f-]{20,}/','/<uuid>/',request.path)
            path=re.sub(r'/\d+/','/<id>/',path)
            write({'kind':'http','path':path,'elapsed_ms':(time.perf_counter()-start)*1000,
                'sql_count':stats.count,'sql_ms':stats.ms,'for_update_ms':stats.lock_ms,'sql_verbs':stats.verbs,'read_tables':stats.tables,
                'baseline_probe':request.META.get('HTTP_X_LOAD_PROBE')=='query-count'})

def install_socket_probe():
    from campus import socket_db
    from django.db import connection
    original=socket_db.run
    async def measured(fn,*args,**kwargs):
        enqueued=time.perf_counter();stats=SQLCounts();details={}
        def call():
            details['queue_ms']=(time.perf_counter()-enqueued)*1000
            start=time.perf_counter()
            try:
                with connection.execute_wrapper(stats):return fn(*args,**kwargs)
            finally:details['function_ms']=(time.perf_counter()-start)*1000
        try:return await original(call)
        finally:
            write({'kind':'socket_db','function':getattr(fn,'__qualname__',type(fn).__name__),
                'elapsed_ms':(time.perf_counter()-enqueued)*1000,'sql_count':stats.count,'sql_ms':stats.ms,**details})
    socket_db.run=measured

if __name__=='__main__':
    import os
    import runpy
    import sys
    sys.modules['diagnostics']=sys.modules[__name__]
    root=Path('/private/tmp/platform-event-pr-53')
    os.environ.update(json.loads((OUT/'environment.json').read_text()))
    sys.path.insert(0,str(root/'backend'))
    import django
    django.setup()
    from django.conf import settings
    settings.MIDDLEWARE=['diagnostics.SQLTimingMiddleware',*settings.MIDDLEWARE]
    install_socket_probe()
    runpy.run_path(str(root/'backend/run-campus.py'),run_name='__main__')
