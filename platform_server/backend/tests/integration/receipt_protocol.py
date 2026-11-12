"""100-account HTTP integration. Full-body latency; all submitted requests are drained."""
import hashlib,http.client,json,math,os,sys,threading,time
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID,uuid4

ROOT=Path('/private/tmp/platform-receipt-integration-20261107')
REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
os.environ.update(json.loads((ROOT/'environment.json').read_text()))
sys.path.insert(0,str(REPO/'backend'))
import django
django.setup()
from django.core import signing
from django.contrib.auth import get_user_model
from django.db import connection,connections
from django.db.models import Count
from django.utils import timezone
from event_story import main
from event_story.models import GamePlay,EventProgress
from eventing.models import PlatformSession

assert connection.settings_dict['NAME']=='receipt_integration_20261107' and str(connection.settings_dict['PORT'])=='55490'
assert not (ROOT/'results.json').exists(),'Fresh fixture required'
users=json.loads((ROOT/'clients.json').read_text())['users']
samples=[];checks=[];tracked={};submitted=Counter();active=Counter();peaks=Counter();lock=threading.Lock();audit={}
fault=ROOT/'status-fault.json'
def check(name,condition):
    checks.append({'name':name,'passed':bool(condition)})
    assert condition,name
def headers(client):
    return {'Host':'127.0.0.1:8106','Origin':'http://127.0.0.1:8106','Referer':'http://127.0.0.1:8106/',
      'Cookie':'campus_sessionid='+client['session_key']+'; campus_csrftoken='+'a'*32,
      'X-CSRFToken':'a'*32,'Content-Type':'application/json','Connection':'close'}
def valid(client,payload,body):
    if not isinstance(body,dict):return False
    action=(payload or {}).get('action')
    if action=='game_start':
        if set(body)!={'play','token','challenge','practice','status'}:return False
        value=signing.loads(body['token'],salt=main.SALT,max_age=7200)
        return (UUID(body['play']).version==4 and value['play']==body['play'] and value['user']==client['user_id']
          and value['challenge']==body['challenge'] and body['practice'] is False
          and body['status'] in ('RUNNING','FINISHED','INTERRUPTED','SUCCESS','FAILED'))
    if action=='investigate':return set(body)=={'first','correct'} and type(body['first']) is bool and body['correct'] is True
    if action=='game_finish':return body.get('success') is None and body.get('score')==2 and isinstance(body.get('state'),dict)
    if action=='deliver':return body.get('correct') is True and body.get('state',{}).get('completed') is True
    return isinstance(body.get('steps'),list) and isinstance(body.get('ranking'),dict)
def request(client,label,payload=None,port=8106,expected=200,error=None):
    with lock:
        active[label]+=1;peaks[label]=max(peaks[label],active[label])
    start=time.perf_counter();body=None
    row={'request':label,'status':None,'complete':False,'outcome':'no_response','route':'entry' if port==8106 else 'direct'}
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=30)
    try:
        conn.request('POST' if payload else 'GET','/api/event-main/action/' if payload else '/api/event-main/status/',
          body=json.dumps(payload).encode() if payload else None,headers=headers(client))
        response=conn.getresponse();row['status']=response.status;row['worker']=response.getheader('x-integration-worker')
        raw=response.read();row['complete']=True;row['bytes']=len(raw)
        try:body=json.loads(raw)
        except ValueError:pass
        row['ms']=(time.perf_counter()-start)*1000
        if response.status!=expected:row['outcome']='unexpected_error'
        elif expected==500:row['outcome']='expected_fault'
        elif expected!=200:row['outcome']='expected_rejection' if body and body.get('error')==error else 'unexpected_error'
        else:row['outcome']='success' if valid(client,payload,body) else 'unexpected_error'
    except Exception as exc:
        row['ms']=(time.perf_counter()-start)*1000
        row['error_type']=type(exc).__name__
        row['outcome']='unexpected_error' if row['complete'] else 'no_response'
    finally:
        conn.close()
        with lock:active[label]-=1;samples.append(row)
    return {'row':row,'body':body}
def call(client,label,payload=None,**kwargs):
    submitted[label]+=1
    return request(client,label,payload,**kwargs)
def batch(label,jobs,workers=100):
    submitted[label]+=len(jobs);capacity=min(workers,len(jobs));gate=threading.Barrier(capacity,timeout=30)
    def one(item):
        index,(client,payload,port)=item
        if index<capacity:gate.wait()
        return request(client,label,payload,port)
    with ThreadPoolExecutor(max_workers=capacity) as pool:
        results=list(pool.map(one,enumerate(jobs)))
    check(label+'.full_expected_responses',all(r['row']['outcome']=='success' for r in results))
    return [r['body'] for r in results]
def data(client,game='kkuwang_touch'):
    target='roof-guide' if game=='kkuwang_touch' else 'room1' if game=='sql_memory' else 'room2'
    return {'action':'game_start','game':game,'practice':False,'request_uuid':str(uuid4()),'ticket':client['tickets'][target]}
def base(body):return {key:body[key] for key in ('play','token','challenge','practice')}
def track(client,payload,body):
    for value in tracked.values():
        if value['user']==client['user_id'] and value['status']=='RUNNING':value['status']='INTERRUPTED'
    tracked[body['play']]={'user':client['user_id'],'session':client['session_uuid'],'request':payload['request_uuid'],
      'game':payload['game'],'receipt':base(body),'status':'RUNNING'}
def snapshot(query):
    return {str(p.pk):(p.status,p.started_at,p.completed_at,p.session_id,p.completion_order) for p in query.all()}
def inspect_plays(name):
    rows=list(GamePlay.objects.filter(user_id__in=[c['user_id'] for c in users]))
    good=len(rows)==len(tracked)
    for row in rows:
        value=tracked.get(str(row.pk))
        good=good and value is not None
        if value:
            good=good and (str(row.user_id)==value['user'] and str(row.session_id)==value['session']
              and row.evidence.get('request_uuid')==value['request'] and row.evidence.get('start_receipt')==value['receipt']
              and row.evidence.get('challenge')==value['receipt']['challenge'] and row.status==value['status']
              and row.game_id==main.uid('game/'+value['game']))
    duplicates=list(GamePlay.objects.filter(user_id__in=[c['user_id'] for c in users]).values('user_id','evidence__request_uuid').annotate(n=Count('pk')).filter(n__gt=1))
    check(name,good and not duplicates)
    audit['plays']={'checked_at':name,'rows':len(rows),'requests':len(tracked),'duplicateGroups':len(duplicates),'statuses':dict(Counter(p.status for p in rows))}
def set_fault(value):
    if value is None:fault.unlink(missing_ok=True)
    else:
        path=ROOT/'status-fault.next';path.write_text(json.dumps(value));path.replace(fault)
def run():
    ids=[c['user_id'] for c in users]
    check('owned_fresh_fixture',len(ids)==100 and get_user_model().objects.filter(pk__in=ids,username__startswith='receipt-it-').count()==100 and not GamePlay.objects.filter(user_id__in=ids).exists())
    check('empty_final_ranking',not EventProgress.objects.filter(event_id=main.uid('6')).exists())
    check('active_sessions',all(PlatformSession.objects.filter(pk=c['session_uuid'],user_id=c['user_id'],ended_at__isnull=True).exists() for c in users))
    initial=snapshot(EventProgress.objects.filter(user_id__in=ids))
    check('warmup_status',call(users[0],'status_baseline')['row']['outcome']=='success')
    b=[data(c) for c in users]
    jobs=[(client,b[i],8106) for i,client in enumerate(users) for _ in range(2)]
    replies=batch('start_same_request',jobs)
    check('original_receipt_for_every_duplicate',all(replies[2*i]==replies[2*i+1] for i in range(100)))
    for i,client in enumerate(users):track(client,b[i],replies[2*i])
    inspect_plays('duplicate_start_creates_100_plays')
    c=[data(u) for u in users]
    current=batch('start_fresh',[(u,c[i],8106) for i,u in enumerate(users)])
    for i,u in enumerate(users):track(u,c[i],current[i])
    old=batch('start_old_receipt',[(u,b[i],8106) for i,u in enumerate(users)])
    check('old_receipt_is_interrupted_and_unchanged',all(v=={**replies[2*i],'status':'INTERRUPTED'} for i,v in enumerate(old)))
    inspect_plays('old_replay_does_not_interrupt_current_play')
    latest=GamePlay.objects.filter(pk__in=[r['play'] for r in current]).latest('started_at').started_at
    time.sleep(max(0,10.1-(timezone.now()-latest).total_seconds()))
    finish=[{'action':'game_finish','play':r['play'],'token':r['token'],'proof':{'taps':[100,300]}} for r in current]
    first=batch('finish',[(u,finish[i],8106) for i,u in enumerate(users)])
    for r in current:tracked[r['play']]['status']='FINISHED'
    saved={str(p.pk):(p.ended_at,p.duration_ms,p.evidence) for p in GamePlay.objects.filter(pk__in=[r['play'] for r in current])}
    again=batch('finish_retry',[(u,finish[i],8106) for i,u in enumerate(users)])
    # Older touch receipts omit the unused coatingTiming field on the first response.
    def normalized(body):return {k:v for k,v in body.items() if k!='coatingTiming' or v is not None}
    check('result_receipt_replay_unchanged',list(map(normalized,first))==list(map(normalized,again)))
    check('result_db_replay_unchanged',saved=={str(p.pk):(p.ended_at,p.duration_ms,p.evidence) for p in GamePlay.objects.filter(pk__in=saved)})
    check('finished_result_evidence_and_server_timing',all(v[0] and v[1]>=9900 and v[2].get('score')==2 and v[2].get('result')=={'taps':[100,300]} for v in saved.values()))
    check('conflicting_result_rejected',call(users[0],'finish_conflict',{**finish[0],'proof':{'taps':[100]}},expected=409,error='result_conflict')['row']['outcome']=='expected_rejection')
    deliver=[{'action':'deliver','ticket':u['tickets']['final-guide']} for u in users]
    batch('deliver',[(u,deliver[i],8106) for i,u in enumerate(users)])
    finals=EventProgress.objects.filter(event_id=main.uid('6'),user_id__in=ids);final_before=snapshot(finals)
    ranks={str(p.user_id):p.completion_order for p in finals}
    check('rank_1_to_100_unique',sorted(ranks.values())==list(range(1,101)))
    check('completion_time_and_session',all(p.completed_at and str(p.session_id)==next(u['session_uuid'] for u in users if u['user_id']==str(p.user_id)) for p in finals))
    repeated=batch('deliver_retry',[(u,deliver[i],8106) for i,u in enumerate(users)])
    check('completion_rank_return_and_db_unchanged',snapshot(finals)==final_before and all(r['state']['ranking']['myRank']==ranks[u['user_id']] for r,u in zip(repeated,users)))
    audit['ranks']={'count':len(ranks),'unique':len(set(ranks.values())),'minimum':min(ranks.values()),'maximum':max(ranks.values())}
    for game in ('sql_memory','card_match','coating','topping','wrapping'):
        payloads=[data(u,game) for u in users[:2]]
        started=batch('start_'+game,[(u,payloads[i],8106) for i,u in enumerate(users[:2])],2)
        retried=batch('retry_'+game,[(u,payloads[i],8106) for i,u in enumerate(users[:2])],2)
        check(game+'_original_challenge_and_token',started==retried)
        for i,u in enumerate(users[:2]):track(u,payloads[i],started[i])
    payload=data(users[0])
    direct=batch('cross_process_start',[(users[0],payload,9661),(users[0],payload,9662)],2)
    check('cross_process_same_account_one_receipt',direct[0]==direct[1]);track(users[0],payload,direct[0])
    payload=data(users[1]);conn=http.client.HTTPConnection('127.0.0.1',8106,timeout=30)
    try:
        conn.request('POST','/api/event-main/action/',body=json.dumps(payload).encode(),headers=headers(users[1]))
        response=conn.getresponse();check('unread_body_success_headers',response.status==200);response.close()
    finally:conn.close()
    stored=GamePlay.objects.get(user_id=users[1]['user_id'],evidence__request_uuid=payload['request_uuid'])
    retried=call(users[1],'retry_after_unread_body',payload)
    check('unread_body_retry_recovers_saved_receipt',retried['row']['outcome']=='success' and base(retried['body'])==stored.evidence['start_receipt'])
    track(users[1],payload,retried['body']);audit['intentionalUnreadBodyRequests']=1
    investigation={'action':'investigate','ticket':users[0]['tickets']['investigate']}
    try:
        for mode in ('fail','delay'):
            set_fault({'mode':mode,'seconds':2})
            index=2 if mode=='fail' else 3;payload=data(users[index])
            start=call(users[index],'start_status_'+mode,payload)
            inspected=call(users[0],'investigate_status_'+mode,investigation)
            state=call(users[index],'status_'+mode,expected=500 if mode=='fail' else 200)
            check('write_receipt_survives_status_'+mode,start['row']['outcome']=='success' and inspected['row']['outcome']=='success' and inspected['body']['first'] is False and state['row']['outcome']==('expected_fault' if mode=='fail' else 'success'))
            track(users[index],payload,start['body'])
            if mode=='delay':
                audit['delayMs']={'start':start['row']['ms'],'investigate':inspected['row']['ms'],'status':state['row']['ms']}
                check('status_2sec_delay_does_not_delay_writes',state['row']['ms']>=1800 and max(start['row']['ms'],inspected['row']['ms'])+1000<state['row']['ms'])
    finally:set_fault(None)
    check('status_after_cleanup',call(users[0],'status_restored')['row']['outcome']=='success')
    check('previous_progress_timestamps_and_sessions_unchanged',snapshot(EventProgress.objects.filter(pk__in=initial))==initial)
    check('final_progress_unchanged',snapshot(finals)==final_before)
    inspect_plays('final_integrity')
    check('all_responses_valid_and_received',all(r['outcome'] in ('success','expected_rejection','expected_fault') for r in samples))
    check('all_submitted_requests_drained',sum(submitted.values())==len(samples))
def pct(values,q):
    return round(sorted(values)[math.ceil(len(values)*q)-1],2) if values else None
def metrics():
    grouped=defaultdict(list)
    for r in samples:grouped[r['request']].append(r)
    result=[]
    for label in sorted(set(grouped)|set(submitted)):
        rows=grouped[label]
        outcomes=Counter(r['outcome'] for r in rows);complete=[r['ms'] for r in rows if r['complete']]
        result.append({'request':label,'submitted':submitted[label],'requests':len(rows),'success':outcomes['success'],
          'expected_rejection':outcomes['expected_rejection'],'expected_fault':outcomes['expected_fault'],
          'errors':outcomes['unexpected_error'],'no_response':outcomes['no_response'],
          'not_started':submitted[label]-len(rows),'complete_responses':len(complete),'peak_inflight':peaks[label],
          'p50_ms':pct(complete,.5),'p95_ms':pct(complete,.95) if len(complete)==submitted[label] else None,
          'observed_response_p95_ms':pct(complete,.95),'max_ms':round(max(complete),2) if complete else None,
          'http_statuses':dict(Counter(str(r['status']) for r in rows))})
    return result
outcome='passed';failure=None
try:
    set_fault(None);run()
except Exception as exc:
    outcome='failed';failure={'type':type(exc).__name__}
    if isinstance(exc,AssertionError):failure['check']=str(exc)
finally:
    set_fault(None);connections.close_all()
    result={'outcome':outcome,'checks':checks,'metrics':metrics(),'audit':audit,'failure':failure,
      'condition':{'accounts':100,'httpConcurrencyLimit':100,'movement':4,'service':4,'frontends':2,'jobs':1,
       'windowOverride':True,'latency':'full HTTP body and JSON parse; thread-pool queue excluded; nearest-rank percentiles',
       'duplicateBatch':'100 accounts x 2 requests; 50 accounts with paired requests per initial wave; fresh-start batch has 100 accounts concurrently',
       'movementChatLoad':False,'timingComparison':'current source only, no before/after claim'}}
    (ROOT/'raw-samples.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in samples))
    (ROOT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'outcome':outcome,'checks':len(checks),'requests':len(samples),'outcomes':dict(Counter(r['outcome'] for r in samples)),'failure':failure}))
raise SystemExit(0 if outcome=='passed' else 1)
