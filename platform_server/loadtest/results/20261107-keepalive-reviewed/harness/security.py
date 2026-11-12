"""Fixed expectations: both sockets authenticate, reject bad Origin, and expire on logout."""
import asyncio,json,os,time,urllib.request,urllib.error
from pathlib import Path
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus,ConnectionClosed
OUT=Path(os.environ['LOCAL44_OUT']);ROOT=OUT.parent
keys=json.loads((OUT/'sessions.json').read_text())
ip='127.0.0.1'
rows=[]
async def opening(index,path,host='127.0.0.1',key=None,origin=None,expected=101):
 start=time.perf_counter();ws=None
 try:
  ws=await connect(f'ws://{host}:8116/ws/{path}/?control=server',origin=origin or f'http://{host}:8116',additional_headers={'Cookie':'campus_sessionid='+(keys[index] if key is None else key)},open_timeout=40,ping_interval=None,max_size=2**23)
  want='welcome' if path=='movement' else 'chat_ready'
  while json.loads(await asyncio.wait_for(ws.recv(),40)).get('type')!=want:pass
  code=101
 except InvalidStatus as error:code=error.response.status_code
 except ConnectionClosed as error:code=error.rcvd.code if error.rcvd else 'network_error'
 except TimeoutError:code='timeout'
 except OSError:code='network_error'
 row={'action':'connect:'+path,'account_index':index,'host':host,'expected':expected,'status':code,'ms':(time.perf_counter()-start)*1000,'passed':code==expected}
 rows.append(row)
 return ws
async def wave(index):
 for repeat in range(2):
  for path in ('movement','chat'):
   ws=await opening(index,path)
   if ws:await ws.close()
async def closed(ws,path):
 start=time.perf_counter()
 try:
  while True:await asyncio.wait_for(ws.recv(),20)
 except ConnectionClosed as error:code=error.rcvd.code if error.rcvd else 'network_error'
 except TimeoutError:code='timeout'
 rows.append({'action':'logout_closes:'+path,'status':code,'expected':4401,'passed':code==4401,'ms':(time.perf_counter()-start)*1000})
async def main():
 await asyncio.gather(*(wave(i) for i in range(100)))
 for path in ('movement','chat'):
  for key,origin in [('',None),('invalid-session',None),(None,'https://untrusted.invalid')]:
   ws=await opening(0,path,key=key,origin=origin,expected=403)
   if ws:await ws.close()
  ws=await opening(0,path,host=ip)
  if ws:await ws.close()
 guards=[await opening(98,p) for p in ('movement','chat')]
 sockets=[await opening(99,p) for p in ('movement','chat')]
 csrf='a'*32
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args):return None
 def logout():
  req=urllib.request.Request('http://127.0.0.1:8116/accounts/logout/',data=b'',headers={'Cookie':f'campus_sessionid={keys[99]}; campus_csrftoken={csrf}','X-CSRFToken':csrf,'Origin':'http://127.0.0.1:8116','Referer':'http://127.0.0.1:8116/'})
  try:
   with urllib.request.build_opener(NoRedirect).open(req,timeout=40) as r:return r.status
  except urllib.error.HTTPError as e:e.read();return e.code
 start=time.perf_counter();code=await asyncio.to_thread(logout)
 rows.append({'action':'logout_http','status':code,'expected':302,'passed':code==302,'ms':(time.perf_counter()-start)*1000})
 await asyncio.gather(*(closed(ws,p) for ws,p in zip(sockets,('movement','chat')) if ws))
 assert sum(r['action'].startswith('logout_closes:') for r in rows)==2
 for ws,path in zip(guards,('movement','chat')):
  start=time.perf_counter();code='not_connected'
  if ws:
   try:
    await ws.send('{"type":"ping"}')
    async with asyncio.timeout(10):
     while True:
      if json.loads(await ws.recv()).get('type')=='pong':code='pong';break
   except (ConnectionClosed,TimeoutError):code='closed_or_timeout'
   await ws.close()
  rows.append({'action':'other_account_alive:'+path,'status':code,'expected':'pong','passed':code=='pong','ms':(time.perf_counter()-start)*1000})
 for path in ('movement','chat'):
  ws=await opening(99,path,expected=403)
  if ws:await ws.close()
asyncio.run(main())
(OUT/'security.json').write_text(json.dumps(rows,indent=2))
assert all(r['passed'] for r in rows),[r for r in rows if not r['passed']]
print('Security/reconnect tests passed:',len(rows))
