"""Trace client connection stages only; do not record cookies or payloads."""
import http.client,json,socket,threading,time,urllib.request,os
from pathlib import Path
from urllib.parse import urlsplit
TLS=threading.local()
LOCK=threading.Lock()
PLOCK=threading.Lock()
PENDING={}
import struct,subprocess
NEXT_PORT=int(os.environ.get("DIAG_CLIENT_PORT_BASE","0"))
PORT_LOCK=threading.Lock()
FILE=None
def event(stage,operation):
    def wrapper(*args,**kw):
        row=getattr(TLS,'row',None)
        if row is None:return operation(*args,**kw)
        item={'stage':stage,'at':time.time()};started=time.perf_counter()
        if stage=='tcp_connect':
            if NEXT_PORT and tuple(args[1])==('127.0.0.1',8116):
                while True:
                    with PORT_LOCK:
                        port=globals()['NEXT_PORT'];globals()['NEXT_PORT']=port+1
                    assert 20000<=port<45000
                    try:args[0].bind(('127.0.0.1',port));break
                    except OSError as error:
                        if error.errno!=48:raise
            try:
                item['target']=list(args[1])
                with PLOCK:PENDING[id(args[0])]=(args[0],row['path'],time.monotonic())
            except Exception:pass
        if stage=='dns':item['target']=str(args[0])
        try:
            result=operation(*args,**kw);item['outcome']='ok';return result
        except BaseException as error:
            item.update(outcome=type(error).__name__,errno=getattr(error,'errno',None),message=str(error)[:100]);raise
        finally:
            if stage=='tcp_connect':
                try:item['local']=list(args[0].getsockname())
                except OSError:pass
                with PLOCK:PENDING.pop(id(args[0]),None)
            item['ms']=(time.perf_counter()-started)*1000;row['stages'].append(item)
    return wrapper
def socket_samples():
    path=Path(os.environ['LOCAL44_OUT'])/'pending-sockets.jsonl'
    with path.open('w',buffering=1) as out:
        snapshot_at=0
        while True:
            with PLOCK:items=list(PENDING.values())
            for sock,path,started in items:
                if time.monotonic()-started<.2:continue
                row={'at':time.time(),'path':path,'elapsed_ms':(time.monotonic()-started)*1000}
                try:
                    values=struct.unpack('<4B13I7Q',sock.getsockopt(socket.IPPROTO_TCP,0x106,112))
                    row.update(state=values[0],rto_ms=values[6],tx_packets=values[17],rx_packets=values[20],retransmit_packets=values[23],local=list(sock.getsockname()))
                except OSError as error:row['error']=str(error)
                out.write(json.dumps(row)+'\n')
            if items and time.monotonic()-snapshot_at>1 and any(time.monotonic()-x[2]>.5 for x in items):
                ports=set()
                for sock,_,_ in items:
                    try:ports.add(sock.getsockname()[1])
                    except OSError:pass
                probe=subprocess.run(['netstat','-anv','-p','tcp'],capture_output=True,text=True)
                matched=[line for line in probe.stdout.splitlines() if any('.'+str(p)+' ' in line for p in ports)]
                out.write(json.dumps({'at':time.time(),'kind':'tcp_table','ports':sorted(ports),'lines':matched,'stderr':probe.stderr.strip()})+'\n')
                snapshot_at=time.monotonic()
            time.sleep(.1)
def install(namespace):
    global FILE
    FILE=(Path(os.environ['LOCAL44_OUT'])/'client-stages.jsonl').open('w',buffering=1)
    threading.Thread(target=socket_samples,daemon=True).start()
    socket.socket.connect=event('tcp_connect',socket.socket.connect)
    socket.getaddrinfo=event('dns',socket.getaddrinfo)
    urllib.request.proxy_bypass=event('proxy_bypass',urllib.request.proxy_bypass)
    urllib.request.getproxies=event('getproxies',urllib.request.getproxies)
    http.client.HTTPConnection.getresponse=event('response_headers',http.client.HTTPConnection.getresponse)
    http.client.HTTPResponse.read=event('response_body',http.client.HTTPResponse.read)
    original=namespace['fetch']
    def traced(key,path,data):
        row={'path':path,'at':time.time(),'stages':[]};TLS.row=row;started=time.perf_counter()
        try:
            result=original(key,path,data);row['status']=result[0];return result
        finally:
            row['ms']=(time.perf_counter()-started)*1000;TLS.row=None
            with LOCK:FILE.write(json.dumps(row)+'\n')
    namespace['fetch']=traced
