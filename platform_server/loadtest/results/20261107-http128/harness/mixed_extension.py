import os
import http.client
"""Current two-socket client protocol, retaining repository load measurements."""
ACTION_RATE = float(os.environ.get('LOAD_ACTION_RATE', '10'))
draining=False
request_cutoff=math.inf
inflight=set()
def generation_closed():return draining or time.time()>=request_cutoff
def finish_row(row,status,elapsed=None):
    if row is None:return
    elapsed=elapsed if elapsed is not None else (time.monotonic()-row['started'])*1000
    if row['status']=='pending':row.update(status=status,terminal_ms=elapsed)
    else:row.update(late_status=status,late_ms=elapsed)

def expire_row(row):
    if row is not None and row['status']=='pending':finish_row(row,'timeout')

def fetch(path,key,data=None):
    headers={'Cookie':f'campus_sessionid=REDACTED; campus_csrftoken=REDACTED
    body=None
    if data is not None:
        headers.update({'X-CSRFToken':CSRF,'Origin':BASE,'Referer':BASE+'/','Content-Type':'application/json'})
        body=json.dumps(data).encode()
    started=time.monotonic();payload=b''
    try:
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE+path,data=body,headers=headers),timeout=30) as response:
                payload=response.read();status=response.status
        except urllib.error.HTTPError as error:status=error.code;payload=error.read()
    except urllib.error.URLError as error:status='timeout' if isinstance(error.reason,TimeoutError) else 'network_error'
    except TimeoutError:status='timeout'
    except (OSError,http.client.HTTPException):status='network_error'
    return status,time.monotonic()-started,payload

original_stats_init=Stats.__init__
def stats_init(self):
    original_stats_init(self)
    self.control_requests=[]
    self.chat_requests=[]
    self.http_requests=[]
    self.drain_reports=[]
Stats.__init__=stats_init
stats.control_requests=[]
stats.chat_requests=[]
stats.http_requests=[]
stats.drain_reports=[]

async def call(user,path,data=None,label=None):
    if generation_closed():return 0,None
    label=label or path
    row={'path':label,'uri':path,'method':'POST' if data is not None else 'GET',
        'account_index':user.index,'started':time.monotonic(),'started_epoch':time.time(),'status':'pending'} if measuring else None
    if row is not None:stats.http_requests.append(row)
    future=asyncio.get_running_loop().run_in_executor(None,fetch,path,user.key,data)
    inflight.add(future);future.add_done_callback(inflight.discard)
    try:status,seconds,body=await asyncio.wait_for(asyncio.shield(future),45)
    except TimeoutError:
        finish_row(row,'timeout')
        if row is not None:stats.http_status[f'{label} timeout']+=1
        def late(f):
            if not f.cancelled():
                try:status=f.result()[0]
                except Exception:status='network_error'
                finish_row(row,status)
        future.add_done_callback(late)
        return 'timeout',None
    except Exception as error:
        finish_row(row,'network_error')
        if row is not None:row['error']=type(error).__name__;stats.http_status[f'{label} network_error']+=1
        return 'network_error',None
    if row is not None:
        finish_row(row,status);row.update(response_ms=seconds*1000,total_ms=row['terminal_ms'])
        stats.http_status[f'{label} {status}']+=1
        stats.http_ms.setdefault(label,[]).append(seconds)
    try:return status,json.loads(body) if body else None
    except ValueError:return status,None

async def request(user,path):
    await call(user,path,{} if path==ACTIVITY else None)
async def casual_ui(user):
    """Walk mode: 20% moving, occasional chat/panel/jump/poke, otherwise idle."""
    next_chat = time.monotonic()+user.rng.uniform(2,12)
    next_panel = time.monotonic()+user.rng.uniform(2,15)
    while True:
        await asyncio.sleep(user.rng.uniform(1,2))
        if not user.ready: continue
        now=time.monotonic()
        if now>=next_chat:
            next_chat=now+user.rng.uniform(10,20)
            user.chat_count+=1;user.chat_sent=now
            send(user,{'type':'chat','text':f'부하 시험 {user.chat_count}'},'chat')
        if now>=next_panel:
            next_panel=now+user.rng.uniform(12,20)
            later(open_panel(user,pick(PANELS,user.rng)))
        if user.keys and user.rng.random()<.3:
            later(expect(user,'jump',scope(user)))
        elif user.keys and user.rng.random()<.3:
            send(user,{'type':'input','keys':user.keys,'poking':True,'pokeContext':scope(user)},'input')
            await asyncio.sleep(.35)
            send(user,{'type':'input','keys':user.keys},'input')
original_message = Client.onMessage
def current_message(self, payload, is_binary):
    if payload.startswith((b'{"type": "control_result"',b'{"type":"control_result"')):
        result=json.loads(payload)
        row=self.factory.user.request_rows.pop(result.get('requestId'),None)
        if row is not None:
            elapsed=(time.monotonic()-row['started'])*1000
            row['response_ms']=elapsed
            row['response_status']='rejected' if 'error' in result else 'ok'
            finish_row(row,row['response_status'],elapsed)
    if b'chatToken' in payload:
        message = json.loads(payload)
        self.factory.user.actor_token = message['chatToken']
    original_message(self, payload, is_binary)
Client.onMessage = current_message
original_close=Client.onClose
def movement_close(self,was_clean,code,reason):
    user=self.factory.user
    for row in user.request_rows.values():finish_row(row,'network_error')
    user.request_rows.clear()
    original_close(self,was_clean,code,reason)
Client.onClose=movement_close

class ChatClient(WebSocketClientProtocol):
    def onOpen(self):
        self.factory.user.chat_proto = self
    def onMessage(self, payload, is_binary):
        message = json.loads(payload)
        user = self.factory.user
        if message.get('type') == 'chat':
            if message['message'].get('playerId') == user.player_id:
                entry=user.chat_pending.pop(message['message'].get('text'),None)
            else: entry=None
            started=entry[0] if entry else None
            if started is not None:
                if entry[1] is not None:
                    elapsed=(time.monotonic()-started)*1000
                    finish_row(entry[1],'ok',elapsed);entry[1]['response_ms']=elapsed
                if measuring:
                    stats.action_ms.setdefault('chat', []).append(time.monotonic() - started)
                user.chat_sent = None
        elif message.get('type') == 'chat_error':
            if user.chat_pending:
                _,row=user.chat_pending.pop(next(iter(user.chat_pending)))
                finish_row(row,'rejected')
            count(stats.action_errors, 'chat: ' + str(message.get('message')))
    def onClose(self, was_clean, code, reason):
        self.factory.user.chat_proto = None
        for _,row in self.factory.user.chat_pending.values():finish_row(row,'network_error')
        self.factory.user.chat_pending.clear()
        if measuring:
            stats.closes['chat:' + str(code)] += 1

original_run_user = run_user
async def run_user(user, delay, args):
    user.actor_token = user.chat_proto = None
    user.chat_pending = {}
    user.request_rows = {}
    async def attach_chat():
        await asyncio.sleep(delay)
        if generation_closed():return
        factory = WebSocketClientFactory(f'ws://{HOST}:{PORT}/ws/chat/', origin=BASE,
            headers={'Cookie': f'campus_sessionid=REDACTED
        factory.protocol, factory.user = ChatClient, user
        await asyncio.get_running_loop().create_connection(factory, HOST, PORT)
    asyncio.create_task(attach_chat())
    if args.behavior=='walk': asyncio.create_task(casual_ui(user))
    await original_run_user(user, delay, args)

original_send = send
def send(user, obj, kind):
    if generation_closed() and kind!='ping':return
    if kind == 'chat':
        if user.chat_proto and user.actor_token:
            started=time.monotonic()
            row={'started':started,'account_index':user.index,'status':'pending'} if measuring else None
            if row is not None:stats.chat_requests.append(row)
            if row is not None:asyncio.get_running_loop().call_later(10,expire_row,row)
            user.chat_pending[obj['text']]=(started,row)
            try:user.chat_proto.sendMessage(json.dumps({**obj, 'actorToken': user.actor_token}).encode())
            except Exception as error:
                user.chat_pending.pop(obj['text'],None);finish_row(row,'network_error')
                if row is not None:row['error']=type(error).__name__
                return
            count(stats.sent, kind)
        else:
            count(stats.action_errors, 'chat: missing ready connection/token')
    else:
        original_send(user, obj, kind)

PANELS = (*PANELS, ('event', .5))
original_panel = open_panel
async def open_panel(user, name):
    if name != 'event':
        return await original_panel(user, name)
    for path in ('/api/event-main/status/', '/api/event-main/ranking/',
            '/api/event-story/achievements/', '/api/event-story/recipe/status/'):
        status, _ = await call(user, path)
        if status != 200:
            count(stats.action_errors, f'{path}: {status}')

original_command=command
def command(user,action,data=None):
    if generation_closed():
        future=asyncio.get_running_loop().create_future();future.set_result(None);return future
    row=None;request_id=str(user.sequence+1)
    if measuring:
        row={'action':action,'account_index':user.index,'started':time.monotonic(),'status':'pending'}
        user.request_rows[request_id]=row
        stats.control_requests.append(row)
    try:
        if user.proto is None or not user.ready:raise ConnectionError('Movement socket is closed')
        future=original_command(user,action,data)
    except Exception as error:
        finish_row(row,'network_error');user.request_rows.pop(request_id,None);user.pending.pop(request_id,None)
        if row is not None:row['error']=type(error).__name__
        future=asyncio.get_running_loop().create_future();future.set_result(None)
    return future

async def expect(user,action,data=None):
    if generation_closed():return None
    future=command(user,action,data)
    row=user.request_rows.get(str(user.sequence))
    try:return await asyncio.wait_for(future,10)
    except TimeoutError:
        count(stats.action_errors,f'{action}: 응답 없음(10초)')
        finish_row(row,'timeout')
        return None

async def process_main(keys,args,index,start_at,end_at,stop):
    global measuring,draining,request_cutoff
    request_cutoff=end_at
    rng=random.Random(1000+index)
    users=[User(key,random.Random(rng.random()),watch=i<args.watchers) for i,key in enumerate(keys)]
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(32))
    asyncio.create_task(watch_loop_lag())
    for i,user in enumerate(users):
        user.index=i*args.procs+index
        asyncio.create_task(run_user(user,args.ramp*user.index/max(1,args.users),args))
    await asyncio.sleep(max(0,start_at-time.time()));measuring=True
    while time.time()<end_at and not stop.is_set():await asyncio.sleep(.05)
    measuring=False;draining=True;started=time.monotonic()
    rows=stats.control_requests+stats.chat_requests+stats.http_requests
    pending_at_end=sum(row['status']=='pending' for row in rows)
    while time.monotonic()-started<65:
        now=time.monotonic()
        for row in stats.control_requests+stats.chat_requests:
            if row['status']=='pending' and now-row['started']>=10:finish_row(row,'timeout')
        if not inflight and all(row['status']!='pending' for row in rows):break
        await asyncio.sleep(.02)
    for row in rows:
        if row['status']=='pending':row.update(status='unresolved',observed_ms=(time.monotonic()-row['started'])*1000)
    stats.drain_reports.append({'worker':index,'pending_at_end':pending_at_end,
        'drain_seconds':time.monotonic()-started,'unresolved':sum(r['status']=='unresolved' for r in rows),
        'generation_stopped_epoch':time.time()-(time.monotonic()-started)})

def worker(keys,args,index,start_at,end_at,queue,stop):
    with suppress(TimeoutError):
        asyncio.run(asyncio.wait_for(process_main(keys,args,index,start_at,end_at,stop),timeout=end_at-time.time()+100))
    for row in stats.control_requests+stats.chat_requests+stats.http_requests:
        row['observed_ms']=max(0,(time.monotonic()-row['started'])*1000)
    from pathlib import Path
    tag=os.environ.get('LOAD_OUTPUT_TAG',args.behavior)
    Path(os.environ['LOCAL44_OUT'],f'raw-{tag}-{index}.json').write_text(json.dumps(stats.__dict__))
    queue.put(stats.__dict__)
