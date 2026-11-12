"""Prove role separation, closed-event behavior and propagation across different workers."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request
import urllib.error
import uuid
from websockets.asyncio.client import connect

OUT=Path('/private/tmp/platform-local33-20261106')
ROOT=Path('/private/tmp/platform-local33-source-20261106')
env=json.loads((OUT/'environment.json').read_text());os.environ.update(env);sys.path.insert(0,str(ROOT/'backend'))
import django
django.setup()
from django.contrib.auth import get_user_model,SESSION_KEY,BACKEND_SESSION_KEY,HASH_SESSION_KEY
from django.conf import settings
from django.utils import timezone
from accounts.session_policy import start_login
from event_story.achievement_services import complete_achievement,TIMER_ID
from event_story.models import EventProgress
keys=json.loads((OUT/'sessions.json').read_text())
checks={}
def request(path,data=None,index=0,port=8096):
    csrf='a'*32;headers={'Cookie':f'campus_sessionid=REDACTED; campus_csrftoken=REDACTED
    if data is not None:headers.update({'Content-Type':'application/json','X-CSRFToken':csrf,'Origin':'http://127.0.0.1:8096'})
    req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=None if data is None else json.dumps(data).encode(),headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=10) as response:return response.status,json.loads(response.read())
    except urllib.error.HTTPError as error:return error.code,error.read().decode()

class Peer:
    def __init__(self):self.messages=[];self.pending={};self.closed=None
    async def open(self,port,path,index):
        self.ws=await connect(f'ws://127.0.0.1:{port}{path}',origin='http://127.0.0.1:8096',
            additional_headers={'Cookie':'REDACTED'+keys[index]},ping_interval=None,max_size=2**23)
        self.worker=self.ws.response.headers.get('x-local33-worker') or ('movement-' if 9551<=port<=9553 else 'service-')+str(port)
        async def read():
            try:
                async for data in self.ws:
                    message=json.loads(data);self.messages.append(message)
                    if message.get('type')=='control_result':
                        future=self.pending.pop(message.get('requestId'),None)
                        if future and not future.done():future.set_result(message)
            except Exception:pass
            finally:self.closed=self.ws.close_code
        self.reader=asyncio.create_task(read())
    async def wait(self,predicate,timeout=8):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            result=next((message for message in self.messages if predicate(message)),None)
            if result:return result
            await asyncio.sleep(.02)
        raise TimeoutError('expected cross-worker packet not received')
    async def control(self,action,data):
        key=uuid.uuid4().hex;future=asyncio.get_running_loop().create_future();self.pending[key]=future
        await self.ws.send(json.dumps({'type':'control','requestId':key,'action':action,'data':data}))
        result=await asyncio.wait_for(future,10);assert 'error' not in result,result
        return result['result']
    async def close(self):await self.ws.close();await self.reader

def refresh_first_session():
    user=get_user_model().objects.get(username='load000');session=importlib.import_module(settings.SESSION_ENGINE).SessionStore()
    session[SESSION_KEY]=str(user.pk);session[BACKEND_SESSION_KEY]='django.contrib.auth.backends.ModelBackend'
    session[HASH_SESSION_KEY]=user.get_session_auth_hash();start_login(session,user)
    keys[0]=session.session_key
    (OUT/'sessions.json').write_text(json.dumps(keys));(OUT/'sessions.json').chmod(0o600)

async def run():
    a,b,ca,cb=Peer(),Peer(),Peer(),Peer()
    await a.open(9551,'/ws/movement/?control=server',0);await b.open(9552,'/ws/movement/?control=server',1)
    wa=await a.wait(lambda m:m.get('type')=='welcome');wb=await b.wait(lambda m:m.get('type')=='welcome')
    checks['distinct_movement_workers']=[a.worker,b.worker];assert a.worker!=b.worker
    await a.ws.send('{"type":"input","keys":["d"]}');await asyncio.sleep(.35);await a.ws.send('{"type":"input","keys":[]}')
    await b.wait(lambda m:(m.get('type')=='move' and m.get('player',{}).get('id')==wa['selfId']) or (m.get('type')=='moves' and any(p['id']==wa['selfId'] for p in m['players'])))
    checks['cross_worker_movement']=True
    await a.control('jump',{'context':'world','floor':'campus'})
    await b.wait(lambda m:(m.get('type')=='jump' and m.get('actorId')==wa['selfId']) or any(e.get('type')=='jump' and e.get('actorId')==wa['selfId'] for e in m.get('effects',[])))
    checks['cross_worker_jump']=True
    await a.control('poke',{'context':'world','floor':'campus'})
    await b.wait(lambda m:(m.get('type')=='poke' and m.get('actorId')==wa['selfId']) or any(e.get('type')=='poke' and e.get('actorId')==wa['selfId'] for e in m.get('effects',[])))
    checks['cross_worker_poke']=True
    await ca.open(9561,'/ws/chat/',0);await cb.open(9562,'/ws/chat/',1)
    await ca.wait(lambda m:m.get('type')=='chat_ready');await cb.wait(lambda m:m.get('type')=='chat_ready')
    await ca.ws.send(json.dumps({'type':'chat','text':'local33-cross-worker-check','actorToken':wa['chatToken']}))
    await cb.wait(lambda m:m.get('type')=='chat' and m['message']['text']=='local33-cross-worker-check')
    checks['distinct_chat_workers']=[ca.worker,cb.worker];checks['cross_worker_chat']=True
    def claim():
        user=get_user_model().objects.get(username='load000')
        return complete_achievement(user,TIMER_ID,timezone.now())
    assert await asyncio.to_thread(claim)
    await b.wait(lambda m:'discover' in m.get('type',''))
    checks['cross_worker_achievement_discovery']=True
    await asyncio.to_thread(request,'/accounts/logout/',{},0,9563)
    deadline=time.monotonic()+8
    while time.monotonic()<deadline and (a.closed is None or ca.closed is None):await asyncio.sleep(.05)
    checks['logout_from_third_service_worker_closes_movement_and_chat']=[a.closed,ca.closed]
    assert a.closed is not None and ca.closed is not None
    await b.ws.send('{"type":"ping"}');await b.wait(lambda m:m.get('type')=='pong')
    checks['other_account_remains_connected']=True
    for peer in (a,b,ca,cb):await peer.close()

if __name__=='__main__':
    attest=[json.loads(path.read_text()) for path in OUT.glob('attestation-*.json')]
    assert len(attest)==7 and len({r['secret_fingerprint'] for r in attest})==1
    assert all(not r['debug'] and not r['event_window_override'] for r in attest)
    checks['attestations']=attest
    status,state=request('/api/event-main/status/');assert status==200 and state['window']['phase']=='before'
    status,_=request('/api/event-main/action/',{'action':'investigate'});assert status==423
    checks['real_event_window_rejects_early_action']=423
    assert request('/api/event-main/status/',port=9551)[0]==404
    checks['movement_rejects_http_api']=True
    try:asyncio.run(run())
    finally:
        (OUT/'preflight.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
        EventProgress.objects.filter(event_id=TIMER_ID,user__username='load000').delete()
        refresh_first_session()
    print('Role separation and cross-process Redis checks passed')
