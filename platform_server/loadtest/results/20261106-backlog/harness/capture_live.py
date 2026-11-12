import os
"""Attach to the owned Chrome target, preserving the active test session."""
import base64,json,time,urllib.request
from pathlib import Path
from websockets.sync.client import connect
OUT=Path(os.environ.get('LOCAL44_OUT','/private/tmp/platform-backlog-20261106/template'))
state=json.loads((OUT/'current-chrome.json').read_text())
port=int((Path(state['profile'])/'DevToolsActivePort').read_text().splitlines()[0])
with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list') as response:
    pages=[r for r in json.load(response) if r['type']=='page' and r['url']=='http://127.0.0.1:8096/']
assert len(pages)==1,len(pages)
with connect(pages[0]['webSocketDebuggerUrl'],proxy=None,max_size=2**24,legacy=True) as socket:
    def call(i,method,params=None):
        socket.send(json.dumps({'id':i,'method':method,'params':params or {}}))
        while True:
            row=json.loads(socket.recv(timeout=30))
            if row.get('id')==i:
                assert 'error' not in row,row
                return row['result']
    value=call(1,'Runtime.evaluate',{'expression':'({measuring:__local33.measuring,welcome:__local33.welcome,closed:__local33.wsClosed,text:document.body.innerText.slice(0,1500)})','returnByValue':True})['result']['value']
    value.update(at=time.time(),app_page_targets=len(pages))
    (OUT/'chrome-live.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
    shot=call(2,'Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})['data']
    (OUT/'chrome-real-during.png').write_bytes(base64.b64decode(shot))
    print(json.dumps({k:v for k,v in value.items() if k!='text'}))
