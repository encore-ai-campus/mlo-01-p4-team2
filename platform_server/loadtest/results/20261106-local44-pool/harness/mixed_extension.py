import os
"""Current two-socket client protocol, retaining repository load measurements."""
ACTION_RATE = float(os.environ.get('LOAD_ACTION_RATE', '10'))
original_stats_init=Stats.__init__
def stats_init(self):
    original_stats_init(self)
    self.control_requests=[]
    self.chat_requests=[]
    self.http_requests=[]
Stats.__init__=stats_init
stats.control_requests=[]
stats.chat_requests=[]
stats.http_requests=[]

async def call(user,path,data=None,label=None):
    label=label or path
    row={'path':label,'started':time.monotonic(),'status':'pending'} if measuring else None
    if row is not None:stats.http_requests.append(row)
    status,seconds,body=await asyncio.get_running_loop().run_in_executor(None,fetch,path,user.key,data)
    if row is not None:row.update(status=status,response_ms=seconds*1000,total_ms=(time.monotonic()-row['started'])*1000)
    if measuring:
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
            if row['status']=='pending':row['status']=row['response_status']
    if b'chatToken' in payload:
        message = json.loads(payload)
        self.factory.user.actor_token = message['chatToken']
    original_message(self, payload, is_binary)
Client.onMessage = current_message

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
                    entry[1].update(status='ok',response_ms=(time.monotonic()-started)*1000)
                if measuring:
                    stats.action_ms.setdefault('chat', []).append(time.monotonic() - started)
                user.chat_sent = None
        elif message.get('type') == 'chat_error':
            count(stats.action_errors, 'chat: ' + str(message.get('message')))
    def onClose(self, was_clean, code, reason):
        self.factory.user.chat_proto = None
        if measuring:
            stats.closes['chat:' + str(code)] += 1

original_run_user = run_user
async def run_user(user, delay, args):
    user.actor_token = user.chat_proto = None
    user.chat_pending = {}
    user.request_rows = {}
    async def attach_chat():
        await asyncio.sleep(delay)
        factory = WebSocketClientFactory(f'ws://{HOST}:{PORT}/ws/chat/', origin=BASE,
            headers={'Cookie': f'campus_sessionid=REDACTED
        factory.protocol, factory.user = ChatClient, user
        await asyncio.get_running_loop().create_connection(factory, HOST, PORT)
    asyncio.create_task(attach_chat())
    if args.behavior=='walk': asyncio.create_task(casual_ui(user))
    await original_run_user(user, delay, args)

original_send = send
def send(user, obj, kind):
    if kind == 'chat':
        if user.chat_proto and user.actor_token:
            started=time.monotonic()
            row={'started':started,'status':'pending'} if measuring else None
            if row is not None:stats.chat_requests.append(row)
            user.chat_pending[obj['text']]=(started,row)
            user.chat_proto.sendMessage(json.dumps({**obj, 'actorToken': user.actor_token}).encode())
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
    future=original_command(user,action,data)
    if measuring:
        row={'action':action,'started':time.monotonic(),'status':'pending'}
        user.request_rows[str(user.sequence)]=row
        stats.control_requests.append(row)
    return future

async def expect(user,action,data=None):
    future=command(user,action,data)
    row=user.request_rows.get(str(user.sequence))
    try:return await asyncio.wait_for(future,10)
    except TimeoutError:
        count(stats.action_errors,f'{action}: 응답 없음(10초)')
        if row is not None:row['status']='timeout'
        return None

def worker(keys,args,index,start_at,end_at,queue,stop):
    with suppress(TimeoutError):
        asyncio.run(asyncio.wait_for(process_main(keys,args,index,start_at,end_at,stop),timeout=end_at-time.time()+5))
    for row in stats.control_requests+stats.chat_requests+stats.http_requests:
        row['observed_ms']=max(0,(time.monotonic()-row['started'])*1000)
    from pathlib import Path
    tag=os.environ.get('LOAD_OUTPUT_TAG',args.behavior)
    Path(os.environ['LOCAL44_OUT'],f'raw-{tag}-{index}.json').write_text(json.dumps(stats.__dict__))
    queue.put(stats.__dict__)
