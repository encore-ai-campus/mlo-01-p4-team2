import os
"""Real installed Chrome in a dedicated profile, controlled through CDP by user request."""
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
from websockets.sync.client import connect

OUT=Path(os.environ.get('LOCAL44_OUT','/private/tmp/platform-query-20261106/template'))
class Chrome:
    def __init__(self):
        self.profile=Path(tempfile.mkdtemp(prefix='chrome-',dir=OUT));self.log=(OUT/'chrome.log').open('a')
        self.process=subprocess.Popen(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '--headless=new','--no-first-run','--no-default-browser-check','--disable-sync','--remote-debugging-port=0',
            '--window-size=1440,1050','--user-data-dir='+str(self.profile),'about:blank'],stdout=self.log,stderr=self.log)
        for _ in range(100):
            if (self.profile/'DevToolsActivePort').exists():break
            if self.process.poll() is not None:raise RuntimeError('Dedicated Chrome stopped')
            time.sleep(.1)
        else:raise TimeoutError('Dedicated Chrome debugging endpoint unavailable')
        port=int((self.profile/'DevToolsActivePort').read_text().splitlines()[0])
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list',timeout=5) as response:
            target=next(row for row in json.load(response) if row['type']=='page')
        self.socket=connect(target['webSocketDebuggerUrl'],proxy=None,open_timeout=5,max_size=2**24,legacy=True)
        self.sequence=0;self.errors=[]
        (OUT/'current-chrome.json').write_text(json.dumps({'pid':self.process.pid,'profile':str(self.profile),'headless':True}))
        self.command('Page.enable');self.command('Runtime.enable');self.command('Network.enable')
        self.version=self.command('Browser.getVersion')
        self.command('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False})
        key=json.loads((OUT/'sessions.json').read_text())[99]
        self.command('Network.setCookie',{'name':'campus_sessionid','value':key,'domain':'127.0.0.1','path':'/','httpOnly':True,'sameSite':'Lax'})
        self.command('Page.addScriptToEvaluateOnNewDocument',{'source':'''(()=>{
 const m=window.__local33={measuring:false,stopped:false,inflight:0,frames:[],http:[],wsOpened:[],wsClosed:[],welcome:false};
 let prior=null;function frame(t){if(m.measuring&&prior!==null)m.frames.push(t-prior);prior=m.measuring?t:null;requestAnimationFrame(frame)}requestAnimationFrame(frame);
 const fetch0=window.fetch;window.fetch=function(...args){let path;try{path=new URL(args[0].url||args[0],location.href).pathname}catch{return fetch0.apply(this,args)}
 if(m.stopped)return Promise.reject(new DOMException('Measurement ended','AbortError'));
 const start=performance.now();const row=m.measuring?{path,method:args[1]?.method||args[0]?.method||'GET',status:'pending'}:null;
 if(row){m.http.push(row);m.inflight++}
 const finish=status=>{if(!row)return;const ms=performance.now()-start;if(row.status==='pending')Object.assign(row,{status,ms});else Object.assign(row,{late_status:status,late_ms:ms})};
 const timer=row?setTimeout(()=>finish('timeout'),45000):null;
 return fetch0.apply(this,args).then(async r=>{try{await r.clone().arrayBuffer();finish(r.status)}catch{finish('network_error')}return r},e=>{finish('network_error');throw e}).finally(()=>{if(row){clearTimeout(timer);m.inflight--}})};
 const WS=window.WebSocket;window.WebSocket=class extends WS{constructor(...args){super(...args);const path=new URL(args[0],location.href).pathname;
 this.addEventListener('open',()=>m.wsOpened.push(path));this.addEventListener('close',e=>m.wsClosed.push({path,code:e.code}));
 this.addEventListener('message',e=>{try{const d=JSON.parse(e.data);if(d.type==='welcome')m.welcome=true}catch{}})}};
})();'''})
        self.command('Page.navigate',{'url':'http://127.0.0.1:8096/'})
        self.wait('window.__local33?.welcome && document.querySelector("canvas")')
        self.command('Page.bringToFront')
        self.evaluate('[...document.querySelectorAll("dialog[open]")].forEach(d=>{const b=d.querySelector("button[aria-label=닫기],button.mq-close");if(b)b.click();else d.close()})')
    def command(self,method,params=None):
        self.sequence+=1;key=self.sequence;self.socket.send(json.dumps({'id':key,'method':method,'params':params or {}}))
        while True:
            response=json.loads(self.socket.recv(timeout=30))
            if response.get('method')=='Runtime.exceptionThrown':self.errors.append(response['params'].get('exceptionDetails',{}).get('text','exception'))
            if response.get('id')==key:
                if 'error' in response:raise RuntimeError(str(response['error']))
                return response.get('result',{})
    def evaluate(self,expression):
        result=self.command('Runtime.evaluate',{'expression':expression,'returnByValue':True})
        if 'exceptionDetails' in result:raise RuntimeError(str(result['exceptionDetails']))
        return result['result'].get('value')
    def wait(self,expression):
        for _ in range(200):
            if self.evaluate('Boolean('+expression+')'):return
            time.sleep(.1)
        raise TimeoutError('Chrome page condition not reached: '+expression)
    def screenshot(self,name):
        data=self.command('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})['data']
        (OUT/name).write_bytes(base64.b64decode(data))
    def start(self):self.evaluate('Object.assign(__local33,{measuring:true,stopped:false,frames:[],http:[]})')
    def stop(self):self.evaluate('Object.assign(__local33,{measuring:false,stopped:true})')
    def end(self):
        self.stop()
        deadline=time.monotonic()+65
        while self.evaluate('__local33.inflight') and time.monotonic()<deadline:time.sleep(.05)
        self.evaluate("__local33.http.forEach(r=>{if(r.status==='pending')r.status='unresolved'})")
        return {'metrics':self.evaluate('__local33'),'version':self.version,'js_errors':self.errors,'headless':True,
            'visibility':self.evaluate('document.visibilityState'),'canvases':self.evaluate('document.querySelectorAll("canvas").length')}
    def key(self,down,key='d',code='KeyD',number=68):
        self.command('Input.dispatchKeyEvent',{'type':'keyDown' if down else 'keyUp','key':key,'code':code,'windowsVirtualKeyCode':number})
    def close(self):
        try:self.command('Browser.close')
        except Exception:pass
        self.socket.close()
        try:self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=5)
        self.log.close()

if __name__=='__main__':
    browser=Chrome()
    try:
        print(json.dumps({'title':browser.evaluate('document.title'),'dialogs':browser.evaluate('[...document.querySelectorAll("dialog[open]")].map(d=>({id:d.id,text:d.innerText.slice(0,250)}))'),
            'welcome':browser.evaluate('__local33.welcome'),'version':browser.version},ensure_ascii=False))
        browser.screenshot('chrome-pilot.png')
    finally:browser.close()
