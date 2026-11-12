"""Offline checks for the reporting rules and end-of-window request tracking."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch
import urllib.error
from metrics import api_route,request_counts

HERE=Path(__file__).resolve().parent
def context():
 c={'__name__':'test_load_context'}
 source=Path('/private/tmp/platform-local44-source-20261106/loadtest/campus_load.py').read_text()
 exec(compile(source,'campus_load.py','exec'),c)
 exec(compile((HERE/'mixed_extension.py').read_text(),'mixed_extension.py','exec'),c)
 return c

class AccountingTests(unittest.TestCase):
 def test_all_outcomes_balance_and_unresolved_has_no_fake_latency(self):
  rows=[{'status':v,'terminal_ms':i+1} for i,v in enumerate([200,400,502,'network_error','timeout'])]+[{'status':'unresolved'}]
  r=request_counts(rows)
  self.assertEqual((r['attempts'],r['success'],r['errors'],r['unresolved']),(6,1,4,1))
  self.assertEqual(r['terminal_ms']['n'],5)
  self.assertEqual(api_route({'method':'POST','uri':'/api/learning/sessions/01234567-0123-0123-0123-0123456789ab/questions/10/answers/'}),
   'POST /api/learning/sessions/:uuid/questions/:order/answers/')
  self.assertEqual(api_route({'method':'POST','uri':'/api/learning/sessions/123/questions/10/answers/'}),
   'POST /api/learning/sessions/:session_id/questions/:order/answers/')
 def test_timeout_is_not_overwritten_by_late_success(self):
  c=context();row={'started':time.monotonic(),'status':'pending'}
  c['finish_row'](row,'timeout',10000);c['finish_row'](row,200,11000)
  self.assertEqual((row['status'],row['terminal_ms'],row['late_status']),('timeout',10000,200))
 def test_http_timeout_and_connection_failure_are_distinct(self):
  c=context()
  for error,status in [(urllib.error.URLError(TimeoutError()),'timeout'),(ConnectionRefusedError(),'network_error')]:
   with patch('urllib.request.urlopen',side_effect=error):self.assertEqual(c['fetch']('/test','fake')[0],status)
 def test_http_error_body_read_failure_is_final_network_error(self):
  c=context();error=urllib.error.HTTPError('http://test',503,'failed',None,None)
  error.read=Mock(side_effect=ConnectionResetError())
  with patch('urllib.request.urlopen',side_effect=error):self.assertEqual(c['fetch']('/test','fake')[0],'network_error')
 def test_control_and_chat_send_failure_are_recorded_before_send(self):
  c=context();c['measuring']=True
  async def run():
   proto=SimpleNamespace(sendMessage=Mock(side_effect=ConnectionResetError()))
   user=SimpleNamespace(index=0,sequence=0,pending={},request_rows={},ready=True,proto=proto,
    chat_proto=proto,actor_token='REDACTED',chat_pending={})
   self.assertIsNone(await c['command'](user,'jump'))
   c['send'](user,{'type':'chat','text':'test'},'chat')
   self.assertEqual(c['stats'].control_requests[0]['status'],'network_error')
   self.assertEqual(c['stats'].chat_requests[0]['status'],'network_error')
  asyncio.run(run())
 def test_functional_connection_and_send_errors_keep_attempt_counts(self):
  with tempfile.TemporaryDirectory() as out,patch.dict(os.environ,{'LOCAL44_OUT':out}):
   spec=importlib.util.spec_from_file_location('test_scenarios',HERE/'scenarios.py')
   module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
   async def run():
    class Rejected(Exception):pass
    rejected=Rejected();rejected.response=SimpleNamespace(status_code=502)
    module.InvalidStatus=Rejected
    for error in (rejected,TimeoutError(),ConnectionResetError()):
     module.connect=AsyncMock(side_effect=error)
     user=module.User('fake',0,{})
     with self.assertRaises(type(error)):await user.open()
    user=module.User('fake',0,{})
    with self.assertRaisesRegex(RuntimeError,'prerequisite'):await user.control('game_start',{})
    user.ws=SimpleNamespace(send=AsyncMock(side_effect=ConnectionResetError()))
    with self.assertRaises(ConnectionResetError):await user.control('game_start',{})
    closed,sent=asyncio.Event(),asyncio.Event()
    class Socket:
     async def recv(self):return json.dumps({'type':'welcome','selfId':'fake'})
     async def send(self,*args):sent.set()
     async def close(self):closed.set()
     def __aiter__(self):
      async def messages():
       await closed.wait()
       if False:yield ''
      return messages()
    module.connect=AsyncMock(return_value=Socket())
    user=module.User('fake',0,{});await user.open()
    task=asyncio.create_task(user.control('game_start',{}));await sent.wait();closed.set()
    with self.assertRaises(ConnectionError):await asyncio.wait_for(task,1)
    await user.close()
   try:asyncio.run(run())
   finally:module.raw.close()
   self.assertEqual([r['status'] for r in module.samples],[502,'timeout','network_error','network_error',101,'network_error'])
 def test_existing_request_finishes_after_generation_stops(self):
  c=context();calls=[]
  def fetch(*args):calls.append(args);time.sleep(.03);return 200,.03,b'{}'
  c['fetch']=fetch;c['measuring']=True
  user=SimpleNamespace(index=0,key='fake')
  async def run():
   task=asyncio.create_task(c['call'](user,'/first'))
   await asyncio.sleep(.01);c['measuring']=False;c['draining']=True
   self.assertEqual(await c['call'](user,'/blocked'),(0,None))
   self.assertIsNone(await c['expect'](user,'blocked'))
   await task
  asyncio.run(run())
  self.assertEqual(len(calls),1)
  row=c['stats'].http_requests[0]
  self.assertEqual(row['status'],200);self.assertGreaterEqual(row['terminal_ms'],20)
  self.assertFalse(c['inflight'])

if __name__=='__main__':unittest.main()
