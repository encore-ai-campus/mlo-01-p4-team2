import os
import http.client
"""100 authenticated protocol clients; staged fixtures are documented separately."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

OUT = Path(os.environ.get('LOCAL44_OUT','/private/tmp/platform-backlog-20261106/template'))
ROOT = Path('/private/tmp/platform-local44-source-20261106')
BASE = 'http://127.0.0.1:8096'
CSRF = 'a' * 32
samples, checks = [], []
SUFFIX = '-poke' if '--private-poke' in sys.argv else '-fullbank' if '--learning-only' in sys.argv else '-remaining' if '--remaining' in sys.argv else '-npc' if '--npc' in sys.argv else ''
raw = (OUT / f'scenario-samples{SUFFIX}.jsonl').open('w')

def record(label, started, status, expected, facts=None):
    row = dict(action=label, ms=(time.perf_counter()-started)*1000, status=status,
        expected=status in expected,**(facts or {}))
    samples.append(row); raw.write(json.dumps(row)+'\n'); raw.flush()

def fetch(key, path, data):
    headers = {'Cookie': f'campus_sessionid=REDACTED; campus_csrftoken=REDACTED 'Accept': 'application/json'}
    body = None
    if data is not None:
        headers.update({'X-CSRFToken': CSRF, 'Origin': BASE, 'Referer': BASE+'/', 'Content-Type': 'application/json'})
        body = json.dumps(data).encode()
    request = urllib.request.Request(BASE+path, data=body, headers=headers)
    try:
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            try: payload = json.loads(error.read())
            except ValueError: payload = {'error': 'non-json response'}
            return error.code, payload
    except urllib.error.URLError as error:
        return ('timeout' if isinstance(error.reason,TimeoutError) else 'network_error'), {'error': type(error.reason).__name__}
    except TimeoutError as error:
        return 'timeout', {'error': type(error).__name__}
    except (OSError, ValueError,http.client.HTTPException) as error:
        return 'network_error', {'error': type(error).__name__}

class User:
    def __init__(self, key, index, metadata):
        self.key, self.index, self.metadata = key, index, metadata
        self.ws, self.reader, self.state, self.heartbeat = None, None, None, None
        self.pending = {}

    async def http(self, path, data=None, label=None, expected=(200,201)):
        started = time.perf_counter()
        status, result = await asyncio.to_thread(fetch, self.key, path, data)
        facts={}
        if isinstance(result,dict) and isinstance(result.get('newly_completed'),list):
            facts['new_awards']=len(result['newly_completed'])
        if isinstance(result,dict) and result.get('status') in ('SUCCESS','FAILED','INTERRUPTED'):
            facts['game_status']=result['status']
        if isinstance(result,dict) and isinstance(result.get('success'),bool):
            facts['success']=result['success']
        facts.update(account_index=self.index,uri=path,method='POST' if data is not None else 'GET')
        record(label or path, started, status, expected,facts)
        if status not in expected:
            raise RuntimeError(f'{label or path}: {status} {str(result)[:160]}')
        return result

    async def open(self):
        started = time.perf_counter()
        self.ws=None
        try:
            self.ws = await connect('ws://127.0.0.1:8096/ws/movement/?control=server', origin=BASE,
                additional_headers={'Cookie': 'REDACTED'+self.key}, open_timeout=40, max_size=2**23,ping_interval=None)
            while True:
                message = json.loads(await asyncio.wait_for(self.ws.recv(), 40))
                if message.get('type') == 'welcome': break
        except Exception as error:
            status=error.response.status_code if isinstance(error,InvalidStatus) else 'timeout' if isinstance(error,TimeoutError) else 'network_error'
            record('ws_connect',started,status,(101,),{'account_index':self.index,'error':type(error).__name__})
            socket,self.ws=self.ws,None
            if socket:
                try:await asyncio.wait_for(socket.close(),2)
                except Exception:pass
            raise
        self.player_id=message['selfId'];self.inventory=0
        record('ws_connect', started, 101, (101,),{'account_index':self.index})
        async def read():
            try:
                async for payload in self.ws:
                    message = json.loads(payload)
                    if message.get('type') == 'control_state': self.state = message['state']
                    if message.get('type') == 'pepero_status':self.inventory=message['remaining']
                    if message.get('type') == 'control_result':
                        future = self.pending.pop(message.get('requestId'), None)
                        if future and not future.done(): future.set_result(message)
            except Exception:pass
            finally:
                for future in self.pending.values():
                    if not future.done():future.set_exception(ConnectionError('Movement socket disconnected'))
        self.reader = asyncio.create_task(read())
        async def heartbeat():
            while True:
                await asyncio.sleep(5)
                await self.ws.send('{"type":"ping"}')
        self.heartbeat=asyncio.create_task(heartbeat())

    async def control(self, action, data, expected_error=False):
        if self.ws is None:raise RuntimeError('prerequisite_connection_failed')
        request = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request] = future
        started = time.perf_counter()
        try:await self.ws.send(json.dumps({'type':'control','requestId':request,'action':action,'data':data}))
        except Exception as error:
            record('ws:'+action,started,'network_error',('ok',),{'account_index':self.index,'error':type(error).__name__})
            self.pending.pop(request,None);raise
        try:
            result = await asyncio.wait_for(future,40)
            status = 'rejected' if 'error' in result else 'ok'
        except TimeoutError:
            record('ws:'+action, started, 'timeout', ('ok',),{'account_index':self.index}); raise
        except Exception as error:
            record('ws:'+action,started,'network_error',('ok',),{'account_index':self.index,'error':type(error).__name__})
            self.pending.pop(request,None);raise
        facts={'account_index':self.index}
        details=result.get('result') or {}
        if isinstance(details.get('newly_completed'),list):facts['new_awards']=len(details['newly_completed'])
        record('ws:'+action, started, status, ('rejected',) if expected_error else ('ok',),facts)
        if status != ('rejected' if expected_error else 'ok'): raise RuntimeError(str(result.get('error')))
        return result.get('result')

    async def close(self):
        if self.heartbeat:
            self.heartbeat.cancel()
            await asyncio.gather(self.heartbeat,return_exceptions=True)
        if self.ws: await self.ws.close()
        if self.reader: await asyncio.gather(self.reader,return_exceptions=True)

    async def main(self, action, _label=None, **data):
        label=_label or 'main:'+action+(':'+data['game'] if action=='game_start' else '')
        return await self.http('/api/event-main/action/', {'action':action, **data}, label=label)

    def ticket(self, target): return self.metadata['tickets'][target]

async def batch(name, users, fn):
    started = time.monotonic()
    async def one(index,user):
        if '--ramp-connect' in sys.argv and name.startswith(('connect:','reconnect:')):
            await asyncio.sleep(index*.04)
        return await fn(user)
    results = await asyncio.gather(*(one(i,user) for i,user in enumerate(users)), return_exceptions=True)
    errors = [str(result) for result in results if isinstance(result, BaseException)]
    row = {'phase': name, 'users':len(users), 'seconds':round(time.monotonic()-started,2),
        'passed':len(users)-len(errors), 'errors':dict(Counter(errors))}
    checks.append(row)
    (OUT / f'scenario-checks{SUFFIX}.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
    print(json.dumps(row,ensure_ascii=False),flush=True)
    # Finish independent scenarios even when another feature fails under load.

def fixture(phase):
    subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('fixtures.py')),phase],check=True)

async def run():
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(100))
    metadata = json.loads((OUT/'metadata.json').read_text())
    from django.core.signing import b62_decode
    for row in metadata['users']:
        for token in row['tickets'].values():
            assert time.time()-b62_decode(token.rsplit(':',2)[-2])<300,'Refresh fixture tickets before testing'
    users = [User(key,i,metadata['users'][i]) for i,key in enumerate(json.loads((OUT/'sessions.json').read_text()))]
    for code in (() if SUFFIX else ('timer','typing','term_match')):
        fixture('spawn:'+code)
        await batch('connect:'+code,users,lambda u:u.open())
        async def game(u):
            play = await u.control('game_start', {'objectId':'minigame-'+code.replace('_','-')})
            duration = 11110 if code=='timer' else 1000
            payload = {'status':'SUCCESS','duration_ms':duration}
            if code=='term_match':
                deck=play['config']['deck']; pairs=[[i for i,value in enumerate(deck) if value==term] for term in sorted(set(deck))]
                payload['picks']=[[*pair,(i+1)*125] for i,pair in enumerate(pairs)]
            await asyncio.sleep(11.2 if code=='timer' else 6 if code=='term_match' else 1.2)
            path='/api/event-story/plays/'+play['play_uuid']+'/result/'
            first=await u.http(path,payload,label=code+':result')
            assert first['status']=='SUCCESS'
            second=await u.http(path,payload,label=code+':retry')
            assert second['ended_at']==first['ended_at'] and not second['newly_completed']
            own=lambda response:{row['event_uuid']:(row['completed_at'],row['completion_order']) for row in response['achievements'] if row['achieved']}
            assert own(first)==own(second)
            if code=='typing':
                retry=await u.control('game_start',{'objectId':'minigame-typing'})
                await u.http('/api/event-story/plays/'+retry['play_uuid']+'/result/',
                    {'status':'SUCCESS','duration_ms':11111},label='typing:over_limit',expected=(400,))
                await u.http('/api/event-story/plays/'+retry['play_uuid']+'/result/',
                    {'status':'INTERRUPTED','duration_ms':None},label='typing:interrupt')
        await batch('standalone:'+code,users,game)
        await batch('far_guide_rejection:'+code,users,lambda u:u.control('guide_talk',{'objectId':'campus-guide-kkuwang'},True))
        if code!='term_match':
            await batch('disconnect:'+code,users,lambda u:u.close())
            await asyncio.sleep(7)
    if SUFFIX:
        await batch('connect:remaining',users,lambda u:u.open())
    # All clients remain connected during the HTTP scenarios below.
    questions=json.loads((ROOT/'backend/event_story/sql_questions.json').read_text())
    async def main_flow(u):
        await u.main('investigate',ticket=u.ticket('investigate'))
        await u.main('room1_briefing',ticket=u.ticket('room1'))
        for code,target in [('sql_memory','room1'),('kkuwang_touch','roof-guide'),('card_match','room2'),('coating','room2'),('topping','room2'),('wrapping','room2')]:
            play=await u.main('game_start',game=code,ticket=u.ticket(target))
            c=play['challenge']
            if code=='sql_memory':
                seq=questions[c['problem']]['sequence']; proof={'success':True,'rounds':[seq[:i] for i in range(1,len(seq)+1)]}; delay=len(seq)*.3+.2
            elif code=='kkuwang_touch': proof={'taps':[100,300,500]}; delay=10.1
            elif code=='card_match': proof={'success':True,'picks':[[i for i,v in enumerate(c['deck']) if v==term] for term in range(6)]}; delay=1.2
            elif code=='coating':
                start=0; trials=[]
                for spec in c['rounds']:
                    stop=int(start+c['leadInMs']+spec['fillMs']*spec['targetPct']/100)+5
                    trials.append({'startMs':start,'stopMs':stop}); start=stop+200
                proof={'success':True,'trials':trials}; delay=start/1000+.2
            elif code=='topping': proof={'positions':[400]*601}; delay=30.1
            else: proof={'success':True,'animationMs':4500,'sequence':c['sequence']}; delay=4.6
            await asyncio.sleep(delay)
            result=await u.main('game_finish',play=play['play'],token=play['token'],proof=proof,_label='main:game_finish:'+code)
            if code not in ('kkuwang_touch','topping'): assert result['success'] is True, code
            if code=='sql_memory': await u.main('sql_answer',ticket=u.ticket('room1'),answer='C')
        final=await u.main('deliver',ticket=u.ticket('final-guide'),_label='main:deliver:first')
        assert final['state']['completed']
        rank=final['state']['ranking']['myRank']
        repeated=await u.main('deliver',ticket=u.ticket('final-guide'),_label='main:deliver:retry')
        assert repeated['state']['ranking']['myRank']==rank
    if not SUFFIX:
        await batch('main_games_and_final_rank',users,main_flow)
    async def recipe(u):
        path='/api/event-story/recipe/action/'
        for action in ('acquire','recognize','defer','start','topping','finish'):
            result=await u.http(path,{'action':action,'ticket':u.ticket('recipe-note' if action=='acquire' else 'recipe-guide'),
                **({'topping':'thanks' if u.index%2 else 'promise'} if action=='topping' else {})},label='recipe:'+action)
        assert result['state']['secretCompleted']
        saved=await u.http('/api/event-story/recipe/status/',label='recipe:restore')
        assert saved['secretCompleted'] and saved['hasNote']
    if '--learning-only' not in sys.argv:
        await batch('recipe_full_branches',users,recipe)
    async def learn(u):
        subject=('python','sql','cs')[u.index%3]; kind='multiple_choice' if u.index%2 else 'fill_blank'
        lesson=await u.http('/api/learning/sessions/',{'subject':subject,'question_type':kind,'difficulty':'intermediate'},label='learning:start')
        for order in range(1,11):
            base=f"/api/learning/sessions/{lesson['session_id']}/questions/{order}/"
            question=(await u.http(base,label='learning:question'))['question']
            answer=metadata['answers'][str(question['question_id'])]
            payload={'answer':answer,'submission_request_uuid':str(uuid.uuid4())}
            if kind=='multiple_choice': payload['choice_uuid']=next(c['choice_uuid'] for c in question['choices'] if c['choice_text']==answer)
            receipt=await u.http(base+'answers/',payload,label='learning:answer')
            repeated=await u.http(base+'answers/',payload,label='learning:answer_retry')
            assert receipt==repeated
        complete=await u.http(f"/api/learning/sessions/{lesson['session_id']}/complete/",{},label='learning:complete')
        assert complete['correct_count']==10 and complete['completed_at']
    await batch('learning_all_subjects_and_types',users,learn)
    fixture('snapshot_api')
    expected=json.loads((OUT/'expected-api.json').read_text())
    await batch('disconnect:final',users,lambda u:u.close())
    await asyncio.sleep(2)
    await batch('reconnect:restore',users,lambda u:u.open())
    async def restore(u):
        assert (await u.http('/api/event-main/status/',label='main:restore'))['completed']==expected[u.index]['main_completed'],'main state differs from DB'
        assert (await u.http('/api/event-story/recipe/status/',label='recipe:restore'))==expected[u.index]['recipe'],'recipe state differs from DB'
        achievements=(await u.http('/api/event-story/achievements/',label='achievements:restore'))['achievements']
        actual={row['event_uuid']:[row['completed_at'],row['completion_order']] for row in achievements if row['achieved']}
        assert actual==expected[u.index]['achievements'],'achievement state differs from DB'
    await batch('state_after_reconnect',users,restore)
    await batch('disconnect:cleanup',users,lambda u:u.close())

def summarize():
    result={}
    for label in sorted({r['action'] for r in samples}):
        rows=[r for r in samples if r['action']==label]; values=sorted(r['ms'] for r in rows)
        percentile=lambda p:round(values[max(0,math.ceil(p*len(values))-1)],2)
        result[label]={'n':len(rows),'p50':percentile(.5),'p95':percentile(.95),'p99':percentile(.99),'max':round(max(values),2),
            'unexpected':sum(not r['expected'] for r in rows),'statuses':dict(Counter(str(r['status']) for r in rows))}
    (OUT/f'scenario-summary{SUFFIX}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    assert BASE=='http://127.0.0.1:8096'
    try:
        asyncio.run(run())
        if any(row['passed'] != row['users'] for row in checks):
            raise SystemExit(1)
    finally: summarize(); raw.close()
