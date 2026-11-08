"""Real Chrome checks against the owned receipt integration stack."""
import base64,json,os,subprocess,sys,time,urllib.request
from pathlib import Path
from websockets.sync.client import connect

ROOT=Path('/private/tmp/platform-final-integration-20261107')
REPO=Path('/private/tmp/platform-final-integration-20261107/source')
BASE='http://127.0.0.1:8116'
os.environ.update(json.loads((ROOT/'environment.json').read_text()))
sys.path.insert(0,str(REPO/'backend'))
import django
django.setup()
from django.contrib.auth import get_user_model
from django.db import connections
from event_story import main
from event_story.models import GamePlay
client=json.loads((ROOT/'clients.json').read_text())['browser']
user=get_user_model().objects.get(pk=client['user_id'])
checks=[]
def check(name,condition):
    checks.append({'name':name,'passed':bool(condition)})
    assert condition,name

class Chrome:
    def __init__(self):
        self.log=(ROOT/'logs/chrome.log').open('w')
        (ROOT/'chrome-profile/DevToolsActivePort').unlink(missing_ok=True)
        self.process=subprocess.Popen(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '--headless=new','--no-first-run','--no-default-browser-check','--disable-sync',
            '--remote-debugging-port=0','--window-size=1440,1050',
            '--user-data-dir='+str(ROOT/'chrome-profile'),'about:blank'],stdout=self.log,stderr=self.log)
        active=ROOT/'chrome-profile/DevToolsActivePort'
        for _ in range(150):
            if active.exists():break
            if self.process.poll() is not None:raise RuntimeError('Chrome did not start')
            time.sleep(.1)
        else:raise TimeoutError('Chrome debugging port')
        port=int(active.read_text().splitlines()[0])
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list') as response:
            page=next(p for p in json.load(response) if p['type']=='page')
        self.ws=connect(page['webSocketDebuggerUrl'],proxy=None,open_timeout=5,max_size=2**24,legacy=True)
        self.sequence=0;self.errors=[]
        for method in ('Page.enable','Runtime.enable','Network.enable'):self.command(method)
        self.version=self.command('Browser.getVersion')['product']
        self.command('Network.setCookie',{'name':'campus_sessionid','value':client['session_key'],'domain':'127.0.0.1','path':'/','httpOnly':True,'sameSite':'Lax'})
        self.command('Page.addScriptToEvaluateOnNewDocument',{'source':"""(()=>{
const m=window.__receiptIT={phase:'initial',http:[],starts:[],finishes:[],dropNextStart:false,ws:[]};
const original=window.fetch;
window.fetch=async function(...args){
 const path=new URL(args[0].url||args[0],location.href).pathname;
 let data;try{data=JSON.parse(args[1]?.body||'null')}catch{}
 const row={path,method:args[1]?.method||'GET',action:data?.action,phase:m.phase,status:'pending'};
 const started=performance.now();m.http.push(row);
 try{
  const r=await original.apply(this,args);
  const text=await r.clone().text();let body;try{body=JSON.parse(text)}catch{}
  Object.assign(row,{status:r.status,ms:performance.now()-started,play:body?.play,playStatus:body?.status,first:body?.first,hasState:Boolean(body?.state),request_uuid:data?.request_uuid});
  if(data?.action==='game_start'&&r.ok){
   m.starts.push({request:data,response:body});
   if(m.dropNextStart){m.dropNextStart=false;row.injectedLoss=true;throw new TypeError('Intentional browser receipt loss')}
  }
  if(data?.action==='game_finish')m.finishes.push({request:data,response:body,httpStatus:r.status});
  return r;
 }catch(error){if(row.status==='pending')Object.assign(row,{status:'network_error',ms:performance.now()-started});throw error}
};
const Native=window.WebSocket;
window.WebSocket=class extends Native{constructor(...args){super(...args);this.addEventListener('open',()=>m.ws.push(new URL(args[0],location.href).pathname))}};
})();"""})
        self.command('Page.navigate',{'url':BASE+'/'})
        self.wait("window.CampusStory?.state?.stage===1 && window.__receiptIT?.ws.includes('/ws/movement/')")
        self.wait("window.__receiptIT.http.every(r=>r.status!=='pending')")
        self.evaluate("[...document.querySelectorAll('dialog[open]')].forEach(d=>d.close())")
        time.sleep(.2)
    def command(self,method,params=None):
        self.sequence+=1;key=self.sequence
        self.ws.send(json.dumps({'id':key,'method':method,'params':params or {}}))
        while True:
            response=json.loads(self.ws.recv(timeout=30))
            if response.get('method')=='Runtime.exceptionThrown':
                details=response['params']['exceptionDetails']
                self.errors.append(details.get('text','exception'))
            if response.get('id')==key:
                if 'error' in response:raise RuntimeError(str(response['error']))
                return response.get('result',{})
    def evaluate(self,expression):
        value=self.command('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True})
        if 'exceptionDetails' in value:raise RuntimeError(str(value['exceptionDetails']))
        return value['result'].get('value')
    def wait(self,expression,seconds=20):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if self.evaluate('Boolean('+expression+')'):return
            time.sleep(.05)
        raise TimeoutError(expression)
    def advance(self,label):
        self.evaluate("""(()=>{for(let i=0;i<30;i++){const b=document.querySelector('#mainQuestDialog .mq-story-next');if(b&&!b.hidden)b.click()}})()""")
        text=json.dumps(label,ensure_ascii=False)
        self.wait(f"[...document.querySelectorAll('dialog[open] button')].some(b=>b.textContent==={text}&&!b.hidden)")
    def click(self,label):
        text=json.dumps(label,ensure_ascii=False)
        point=self.evaluate(f"""(()=>{{const b=[...document.querySelectorAll('dialog[open] button')].find(b=>b.textContent==={text}&&!b.hidden);if(!b)throw Error('button missing');const r=b.getBoundingClientRect();return {{x:r.x+r.width/2,y:r.y+r.height/2}}}})()""")
        self.command('Input.dispatchMouseEvent',{'type':'mousePressed','button':'left','clickCount':1,**point})
        self.command('Input.dispatchMouseEvent',{'type':'mouseReleased','button':'left','clickCount':1,**point})
    def screenshot(self,name):
        data=self.command('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})['data']
        (ROOT/name).write_bytes(base64.b64decode(data))
    def close(self):
        try:self.command('Browser.close')
        except Exception:pass
        self.ws.close()
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=5)
        self.log.close()

browser=None
fault=ROOT/'status-fault.json'
try:
    browser=Chrome()
    check('actual_map_and_both_websockets_loaded',browser.evaluate("!!document.querySelector('#viewport canvas')&&__receiptIT.ws.includes('/ws/movement/')&&__receiptIT.ws.includes('/ws/chat/')"))
    fault.write_text(json.dumps({'mode':'delay','seconds':2}))
    browser.evaluate("__receiptIT.phase='investigate-delay';CampusStory.open({action:'story'})")
    browser.advance('단서 확인 · 조사 시작')
    check('opening_investigation_does_not_preload_status',browser.evaluate("__receiptIT.http.filter(r=>r.phase==='investigate-delay'&&r.path.startsWith('/api/event-main/')).length===0"))
    browser.click('단서 확인 · 조사 시작')
    browser.wait("document.querySelector('#mainQuestDialog h2')?.textContent==='조사를 시작합니다'")
    check('first_investigation_receipt_received',browser.evaluate("__receiptIT.http.some(r=>r.action==='investigate'&&r.status===200&&r.first===true&&!r.hasState)"))
    check('success_ui_shown_while_status_is_pending',browser.evaluate("__receiptIT.http.some(r=>r.phase==='investigate-delay'&&r.path==='/api/event-main/status/'&&r.status==='pending')"))
    browser.screenshot('investigation-success.png')
    browser.wait("CampusStory.state.stage===2")
    fault.unlink()
    main.complete(user,'3',client['session_uuid']);connections.close_all()
    browser.evaluate("[...document.querySelectorAll('dialog[open]')].forEach(d=>d.close());__receiptIT.phase='start-loss';CampusStory.guide()")
    browser.wait("document.querySelector('#mainQuestDialog h2')?.textContent==='꾸왕이의 고백'")
    browser.advance('꾸왕이 빼빼로 타격 챌린지')
    fault.write_text(json.dumps({'mode':'fail'}))
    browser.evaluate('__receiptIT.dropNextStart=true')
    browser.click('꾸왕이 빼빼로 타격 챌린지')
    browser.wait("document.querySelector('#mainQuestDialog h2')?.textContent==='게임 시작 확인 필요'")
    check('lost_receipt_offers_same_request_retry',browser.evaluate("[...document.querySelectorAll('#mainQuestDialog button')].some(b=>b.textContent==='같은 시작 요청 다시 확인')"))
    browser.click('같은 시작 요청 다시 확인')
    browser.wait("document.querySelector('#mainQuestGame')?.open")
    browser.wait("__receiptIT.http.some(r=>r.path==='/api/event-main/status/'&&r.phase==='start-loss'&&r.status===500)")
    check('same_uuid_and_original_receipt_recovered',browser.evaluate("""(()=>{const [a,b]=__receiptIT.starts;return __receiptIT.starts.length===2&&a.request.request_uuid===b.request.request_uuid&&a.response.play===b.response.play&&a.response.token===b.response.token&&JSON.stringify(a.response.challenge)===JSON.stringify(b.response.challenge)})()"""))
    play=GamePlay.objects.get(user=user)
    check('one_saved_play_matches_client',str(play.pk)==browser.evaluate('__receiptIT.starts[1].response.play'))
    check('start_ui_survives_status_failure',browser.evaluate("document.querySelector('#mainQuestGame').open&&!!document.querySelector('.mq-touch-start')"))
    browser.screenshot('game-start-recovered.png')
    browser.evaluate("__receiptIT.phase='finish-status-failure'")
    browser.click('10초 타격 시작')
    for _ in range(3):
        time.sleep(.15);browser.evaluate("document.querySelector('.mq-hit-target').click()")
    browser.wait("document.querySelector('#mainQuestDialog h2')?.textContent==='결과 저장 확인 필요'",seconds=20)
    play.refresh_from_db();original_end=play.ended_at
    check('result_saved_despite_failed_snapshot',play.status=='FINISHED' and play.evidence.get('score')==3)
    check('client_retains_result_for_retry',browser.evaluate("__receiptIT.finishes.length===1&&__receiptIT.finishes[0].httpStatus===500"))
    fault.unlink()
    browser.click('저장 다시 시도')
    browser.wait("document.querySelector('#mainQuestDialog h2')?.textContent==='STEP 3 완료 · 제작실이 열렸어요'")
    check('same_play_token_and_result_resent',browser.evaluate("""(()=>{const [a,b]=__receiptIT.finishes;return __receiptIT.finishes.length===2&&b.httpStatus===200&&JSON.stringify(a.request)===JSON.stringify(b.request)&&b.response.score===3})()"""))
    play.refresh_from_db()
    check('finish_retry_preserves_one_record_and_timestamp',GamePlay.objects.filter(user=user).count()==1 and play.ended_at==original_end)
    main_state=browser.evaluate('CampusStory.state.stage')
    check('client_receives_updated_mission_state',main_state==4)
    browser.screenshot('game-result-recovered.png')
    metrics=browser.evaluate('__receiptIT.http')
    browser.command('Page.reload',{'ignoreCache':True})
    browser.wait('window.CampusStory?.state?.stage===4')
    check('reload_restores_db_progress_without_new_play',GamePlay.objects.filter(user=user).count()==1)
    check('no_uncaught_javascript_exceptions',not browser.errors)
    result={'checks':checks,'http':metrics,'chrome':browser.version,'jsErrors':browser.errors,'actualBrowser':True,
       'lossInjection':'fetch rejects after actual POST body was read; raw connection body loss is tested separately',
       'windowOverride':True}
    (ROOT/'browser-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'checks':len(checks),'passed':sum(c['passed'] for c in checks),'chrome':browser.version,'javascriptErrors':browser.errors},ensure_ascii=False))
finally:
    fault.unlink(missing_ok=True)
    if browser:browser.close()
    connections.close_all()
