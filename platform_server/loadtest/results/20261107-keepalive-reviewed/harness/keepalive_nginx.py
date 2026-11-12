"""Actual entry -> Front -> internal Nginx -> four TCP upstreams; no application DB."""
import base64
import hashlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from websockets.sync.client import connect
from websockets.exceptions import InvalidStatus
from keepalive_suite import ROOT,SNAP,PY,gate

OUT=ROOT/'nginx-contract'
class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,port):
        self.accepts=0;self.rows=[]
        super().__init__(('127.0.0.1',port),Handler)
    def get_request(self):
        value=super().get_request();self.accepts+=1;return value
class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*_):pass
    def do_POST(self):self.do_GET()
    def do_GET(self):
        if self.path.startswith('/ws/'):
            return self.websocket()
        body=self.rfile.read(int(self.headers.get('Content-Length','0')))
        self.server.rows.append({'path':self.path,'method':self.command,'body':body.decode(),
                                 'cookie':self.headers.get('Cookie'),'origin':self.headers.get('Origin')})
        if self.path=='/api/drop':
            self.close_connection=True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        value=json.dumps({'ok':True}).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(value)));self.end_headers()
        self.wfile.write(value);self.wfile.flush()
    def websocket(self):
        if self.headers.get('Origin')!='http://127.0.0.1:18716' or self.headers.get('Cookie')!='campus_sessionid=contract-test':
            self.send_response(403);self.send_header('Content-Length','0');self.end_headers();return
        key=self.headers['Sec-WebSocket-Key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
        self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade')
        self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1(key.encode()).digest()).decode());self.end_headers()
        while True:
            header=self.rfile.read(2)
            if len(header)!=2:return
            opcode=header[0]&15;size=header[1]&127
            if size>125:raise AssertionError('Contract only sends small frames')
            mask=self.rfile.read(4) if header[1]&128 else None
            payload=self.rfile.read(size)
            if mask:payload=bytes(byte^mask[i%4] for i,byte in enumerate(payload))
            reply=8 if opcode==8 else 10 if opcode==9 else opcode
            self.wfile.write(bytes([128|reply,len(payload)])+payload);self.wfile.flush()
            if opcode==8:return

def main():
    gate('load')
    assert not OUT.exists();OUT.mkdir();(OUT/'trace').mkdir();(OUT/'logs').mkdir()
    servers=[Server(port) for port in range(18721,18725)]
    threads=[threading.Thread(target=server.serve_forever,daemon=True) for server in servers]
    for thread in threads:thread.start()
    local={**os.environ,'DJANGO_SETTINGS_MODULE':'config.settings','FRONTEND_DEBUG':'0',
        'FRONTEND_ALLOWED_HOSTS':'127.0.0.1','BACKEND_URL':'http://127.0.0.1:18717','BACKEND_HTTP_KEEPALIVE':'1',
        'INTEGRATION_WORKER':'contract-front','KEEPALIVE_RUN':str(OUT),
        'PYTHONPATH':str(ROOT)+':'+str(SNAP/'frontend')}
    runtime=OUT/'contract_front.py'
    runtime.write_text('from config.service_asgi import application\nfrom keepalive_hooks import install\ninstall(False)\n')
    local['PYTHONPATH']=str(OUT)+':'+local['PYTHONPATH']
    log=(OUT/'logs/front.log').open('w')
    front=subprocess.Popen([PY,'-B','-m','daphne','-e','tcp:port=18718:interface=127.0.0.1:backlog=128',
                            'contract_front:application'],cwd=SNAP/'frontend',env=local,stdout=log,stderr=log)
    replacements={'RUNTIME':str(OUT),'SERVICE_HOST':'127.0.0.1','MOVEMENT_HOST':'127.0.0.1',
                  'SERVICE_FRONTEND_PORT':'18718','SERVICE_NGINX_PORT':'18717','MOVEMENT_NGINX_PORT':'18717',
                  'BIND':'127.0.0.1','ENTRY_PORT':'18716','NGINX_PORT':'18717','STATIC':str(SNAP/'frontend/dist'),
                  **{'BACKEND_'+str(i):str(18720+i) for i in range(1,5)}}
    def expand(text):
        for key,value in replacements.items():text=text.replace('@'+key+'@',value)
        text=text.replace('/etc/nginx/mime.types','/opt/homebrew/etc/nginx/mime.types')
        text=text.replace('http {','http {\n log_format contract \'$server_port|$uri|$connection|$connection_requests\';',1)
        import re
        return re.sub(r'access_log [^;]+;', 'access_log '+str(OUT/'contract-nginx.log')+' contract;',text,count=1)
    try:
        for source,name in [('deploy/onprem/nginx-service.conf','internal.conf'),('deploy/onprem/nginx-entry.conf.example','entry.conf')]:
            destination=OUT/name;destination.write_text(expand((SNAP/source).read_text()))
            subprocess.run(['/opt/homebrew/bin/nginx','-t','-p',str(OUT)+'/', '-c',str(destination)],check=True,stdout=log,stderr=log)
            subprocess.run(['/opt/homebrew/bin/nginx','-p',str(OUT)+'/', '-c',str(destination)],check=True,stdout=log,stderr=log)
        for port in (18716,18717,18718):
            for _ in range(100):
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.2):pass
                    break
                except OSError:time.sleep(.05)
            else:raise AssertionError('Listener did not start')
        headers={'Cookie':'campus_sessionid=contract-test','Origin':'http://127.0.0.1:18716','Content-Type':'application/json'}
        for _ in range(8):
            client=http.client.HTTPConnection('127.0.0.1',18716,timeout=5)
            client.request('POST','/api/echo',body=b'original-body',headers=headers)
            response=client.getresponse();assert response.status==200;response.read();client.close()
        rows=[row for server in servers for row in server.rows]
        assert len(rows)==8 and all(row['body']=='original-body' for row in rows)
        assert all(row['cookie']==headers['Cookie'] and row['origin']==headers['Origin'] for row in rows)
        (OUT/'upstream-counts.json').write_text(json.dumps({str(server.server_port):{
            'accepts':server.accepts,'requests':len(server.rows)} for server in servers},indent=2))
        assert sum(server.accepts for server in servers)==1
        time.sleep(.1)
        trace=[json.loads(line) for f in (OUT/'trace').glob('*.jsonl') for line in f.read_text().splitlines()]
        echo=[row for row in trace if row.get('path')=='/api/echo']
        assert len(echo)==8 and len({row['connection_id'] for row in echo})==1
        internal=[line.split('|') for line in (OUT/'contract-nginx.log').read_text().splitlines()
                  if line.startswith('18717|/api/echo|')]
        assert len(internal)==8 and len({row[2] for row in internal})==1
        assert sorted(int(row[3]) for row in internal)==list(range(1,9))
        client=http.client.HTTPConnection('127.0.0.1',18716,timeout=5)
        client.request('POST','/api/drop',body=b'one-side-effect',headers=headers)
        response=client.getresponse();assert response.status==502;response.read();client.close()
        drops=[row for server in servers for row in server.rows if row['path']=='/api/drop']
        assert len(drops)==1,'An ambiguous POST was replayed'
        with connect('ws://127.0.0.1:18716/ws/chat/',origin=headers['Origin'],
                     additional_headers={'Cookie':'campus_sessionid=contract-test'},proxy=None,compression=None) as ws:
            ws.send('echo-through-nginx');assert ws.recv(timeout=3)=='echo-through-nginx'
            assert ws.ping(b'alive').wait(3),'Pong missing'
        for origin,cookie in [('http://untrusted.test','campus_sessionid=contract-test'),(headers['Origin'],'campus_sessionid=wrong')]:
            try:
                connect('ws://127.0.0.1:18716/ws/chat/',origin=origin,
                        additional_headers={'Cookie':cookie},proxy=None,compression=None)
            except InvalidStatus as error:assert error.response.status_code==403
            else:raise AssertionError('Invalid origin/authentication accepted')
        for index,name in enumerate(('deploy/service-split/nginx-service.conf',
                'deploy/campus-multiprocess/nginx-campus-backend.conf','loadtest/nginx-backend.conf',
                'loadtest/nginx-edge.conf.template')):
            text=(SNAP/name).read_text().replace('backend:9003','127.0.0.1:18721').replace('frontend:8004','127.0.0.1:18718')
            text=text.replace('$'+'{EDGE_WS_UPSTREAM}','edge_frontends')
            destination=OUT/f'syntax-{index}.conf'
            destination.write_text('pid '+str(OUT/f'syntax-{index}.pid')+'; events{} http{'+text+'}')
            subprocess.run(['/opt/homebrew/bin/nginx','-t','-p',str(OUT)+'/', '-c',str(destination)],check=True,stdout=log,stderr=log)
        result={'passed':True,'echo_requests':8,'front_upstream_channels':1,'front_to_internal_connections':1,'final_upstream_connections':1,
                'ambiguous_post_process_count':len(drops),'websocket_echo':True,'websocket_ping':True,
                'invalid_origin_or_auth_rejected':2,'templates_syntax_passed':6}
        (OUT/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
    finally:
        for name in ('entry.pid','nginx.pid'):
            try:os.kill(int((OUT/name).read_text()),signal.SIGQUIT)
            except (FileNotFoundError,ProcessLookupError):pass
        front.terminate();front.wait(timeout=5);log.close()
        for server in servers:server.shutdown();server.server_close()

if __name__=='__main__':main()
