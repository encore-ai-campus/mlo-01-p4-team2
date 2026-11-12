"""Collect complete outcomes, audit the owned DB, and export sanitized evidence."""
import gzip,hashlib,json,math,os,re,shutil,sys
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path('/private/tmp/platform-final-integration-20261107')
REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
OUT=REPO/'loadtest/results/20261107-final'
def read(name):return json.loads((ROOT/name).read_text())
def rows(name):return [json.loads(s) for s in (ROOT/name).read_text().splitlines() if s]
def pct(values,p):
    return round(sorted(values)[math.ceil(len(values)*p)-1],2) if values else None
def metric(name,items,key='ms'):
    counts=Counter(r['outcome'] for r in items)
    missing=counts['no_response']
    values=[r[key] for r in items if r.get(key) is not None]
    return {'name':name,'n':len(items),'success':counts['success'],'handled':counts['handled'],'errors':counts['error'],'no_response':missing,
        'p95_ms':pct(values,.95) if not missing and len(values)==len(items) else None,
        'p99_ms':pct(values,.99) if not missing and len(values)==len(items) else None}
def response_outcome(status,error=''):
    if status in ('timeout','unresolved','pending','network_error'):return 'no_response'
    if status in ('ok',200,201):return 'success'
    if status=='rejected' and error in ALLOWED_REJECTIONS:return 'handled'
    return 'error'
ALLOWED_REJECTIONS={'착지한 뒤 다시 점프해 주세요.','현재 공간에서 다시 점프해 주세요.',
 '부스 중앙의 E 표시 가까이로 이동해 주세요.','학습 영역이나 출입구 가까이에서 이용해 주세요.','잠시 후 다시 보내 주세요.'}
summary={'metrics':{},'mixed':{},'checks':{},'diagnostics':{}}
functional=rows('scenario-samples.jsonl')
for r in functional:
    r['outcome']='error' if not r['expected'] else 'handled' if r['expectation']['purpose']=='validation' or r['action']=='typing:interrupt' else 'success'
summary['metrics']['functional_all']=metric('기능 요청 전체',functional)
groups={
 'main_start':lambda s:s['action'].startswith('main:game_start:'),
 'main_finish':lambda s:s['action'].startswith('main:game_finish:'),
 'main_deliver':lambda s:s['action']=='main:deliver:first',
 'recipe_finish':lambda s:s['action']=='recipe:finish',
 'learning_start':lambda s:s['action']=='learning:start',
 'learning_question':lambda s:s['action']=='learning:question',
 'learning_answer':lambda s:s['action']=='learning:answer',
 'learning_answer_retry':lambda s:s['action']=='learning:answer_retry',
 'learning_complete':lambda s:s['action']=='learning:complete',
 'timer_result':lambda s:s['action']=='timer:result',
 'typing_result':lambda s:s['action']=='typing:result',
 'pairing_result':lambda s:s['action']=='term_match:result'}
for name,test in groups.items():summary['metrics'][name]=metric(name,[r for r in functional if test(r)])
for mode in ('walk','real'):
    parts=[read(f'raw-{mode}-{i}.json') for i in range(2)]
    browser=read(f'chrome-{mode}.json')
    http=[dict(r,outcome=response_outcome(r['status'],r.get('error_message','')),ms=r.get('terminal_ms')) for p in parts for r in p['http_requests']]
    for r in browser['metrics']['http']:
        http.append(dict(r,outcome=response_outcome(r['status']),ms=r.get('ms')))
    chat=[dict(r,outcome=response_outcome(r['status'],r.get('error_message','')),ms=r.get('terminal_ms')) for p in parts for r in p['chat_requests']]
    controls=[dict(r,outcome=response_outcome(r['status'],r.get('error_message','')),ms=r.get('terminal_ms')) for p in parts for r in p['control_requests']]
    movement=[x*1000 for p in parts for x in p['move_lag']]
    unexpected=[r for r in http+chat+controls if r['outcome'] not in ('success','handled')]
    reasons=Counter(r.get('error_message','') for r in chat+controls if r['outcome']=='handled')
    measured_closes=[r for p in parts for r in p.get('socket_closes',[]) if r.get('measuring')]
    drains=[r for p in parts for r in p['drain_reports']]
    assert not browser['metrics']['inflight'] and all(r['unresolved']==0 for r in drains)
    assert sum(p['welcomed'] for p in parts)==99 and browser['metrics']['welcome']
    assert len({r['account_index'] for r in chat})==99
    summary['mixed'][mode]={'http':metric(mode+' HTTP',http),'chat':metric(mode+' 채팅',chat),
       'control':metric(mode+' 제어',controls),'movement':{'n':len(movement),'p95_ms':pct(movement,.95),'p99_ms':pct(movement,.99)},
       'normal_rejection_reasons':dict(reasons),'measured_socket_closes':len(measured_closes),
       'chrome_js_errors':browser['js_errors'],'chrome_socket_closes':browser['metrics']['wsClosed'],
       'drain':drains,'unexpected':unexpected,
       'generator_cutoff_counters':sum(v for p in parts for k,v in p['action_errors'].items() if k.endswith(': 0'))}
    by_api=defaultdict(list)
    for r in http:by_api[r.get('uri',r.get('path'))].append(r)
    summary['mixed'][mode]['apis']={k:metric(k,v) for k,v in sorted(by_api.items())}
receipt=rows('raw-samples.jsonl')
for r in receipt:r['outcome']='handled' if r['outcome'] in ('expected_rejection','expected_fault') else r['outcome']
assert all(r['complete'] for r in receipt)
summary['metrics']['receipt_all']=metric('재전송·응답 복구 요청',receipt)
checks=read('scenario-checks.json')
summary['checks']['functional']={'cases':sum(c['users'] for c in checks),'passed':sum(c['passed'] for c in checks),'failed':sum(c['users']-c['passed'] for c in checks),'phases':len(checks)}
summary['checks']['receipt']={'cases':len(read('results.json')['checks']),'passed':sum(x['passed'] for x in read('results.json')['checks'])}
summary['checks']['browser']={'cases':len(read('browser-result.json')['checks']),'passed':sum(x['passed'] for x in read('browser-result.json')['checks'])}
for file,name in [('security.json','security'),('admission.json','admission')]:
    data=read(file)
    success=lambda x:x['passed'] if 'passed' in x else x['status']==101 and x['welcome']
    summary['checks'][name]={'cases':len(data),'passed':sum(success(x) for x in data),'failed':sum(not success(x) for x in data)}
    summary['metrics'][name]=metric(name,[dict(x,outcome='success' if success(x) else 'error') for x in data])
backend=(ROOT/'logs/backend.log').read_text()
failed_ids=set(re.findall(r'^(?:FAIL|ERROR): [^(]+\(([^)]+)\)',backend,re.M))
n=int(re.search(r'Ran (\d+) tests',backend).group(1));skipped=int(re.search(r'skipped=(\d+)',backend).group(1))
summary['checks']['backend']={'cases':n,'passed':n-skipped-len(failed_ids),'failed':len(failed_ids),'skipped':skipped,'failure_events':{'assertions':27,'errors':30}}
front=(ROOT/'logs/frontend.log').read_text()
field=lambda name:int(re.search(r'(?:#|ℹ)\s+'+name+r'\s+(\d+)',front).group(1))
summary['checks']['frontend']={'cases':field('tests'),'passed':field('pass'),'failed':field('fail'),'skipped':field('skipped')}
summary['diagnostics']={'failed_test_ids':sorted(failed_ids),'fixture_contract_mismatches':55,
 'reproduced_latency_criterion':{'tests':1,'full_suite_gap_seconds':.403432667,'isolated_gap_seconds':.3626014171168208,'limit_seconds':.3},
 'isolated_retest':{'cases':2,'passed':0,'failed':2,'reconnect_failure_before_new_connect':True},
 'normal_errors_excluded':True}
summary['assets']=read('assets/results.json')['results']
for result in summary['assets']:
    assert result['requests']==result['users']*result['files_per_user'] and result['errors']==0
os.environ.update(read('environment.json'));sys.path.insert(0,str(ROOT/'source/backend'))
import django
django.setup()
from django.db import connection,connections
from django.db.models import Count,F,Q,Sum
from event_story import main
from event_story.models import EventProgress,GamePlay,StoryProgress
from event_story.achievement_services import ACHIEVEMENT_IMAGES
from learning.models import UserAnswer,PointLedger,PointWallet,SessionQuestion,LearningSession,Question
from learning.point_policy import points_for
assert connection.settings_dict['NAME']=='receipt_final_20261107' and str(connection.settings_dict['PORT'])=='55500'
def duplicates(query,*fields):return query.values(*fields).annotate(n=Count('pk')).filter(n__gt=1).count()
eggs=EventProgress.objects.filter(event_id__in=ACHIEVEMENT_IMAGES,status='COMPLETED')
finals=EventProgress.objects.filter(event_id=main.uid('6'),status='COMPLETED')
ranks=list(finals.values_list('completion_order',flat=True))
ledger_sums=dict(PointLedger.objects.values('profile_id').annotate(total=Sum('delta')).values_list('profile_id','total'))
bad_wallets=sum(w.balance!=ledger_sums.get(w.profile_id,0) for w in PointWallet.objects.all())
bad_rewards=sum(l.delta!=points_for(l.answer.session_question.session.difficulty) for l in PointLedger.objects.select_related('answer__session_question__session'))
integrity={
 'question_bank':Question.objects.count(),'main_completed':finals.count(),'rank_unique_contiguous':sorted(ranks)==list(range(1,101)),
 'egg_completed':eggs.count(),'duplicate_egg_accounts':duplicates(eggs,'user_id'),'duplicate_egg_owners':duplicates(eggs,'event_id'),
 'missing_egg_metadata':eggs.filter(Q(completed_at__isnull=True)|Q(completion_order__isnull=True)|Q(session__isnull=True)).count(),
 'play_session_owner_mismatches':GamePlay.objects.exclude(user_id=F('session__user_id')).count(),
 'duplicate_main_start_requests':duplicates(GamePlay.objects.filter(evidence__has_key='request_uuid'),'user_id','evidence__request_uuid'),
 'duplicate_answer_requests':duplicates(UserAnswer.objects.filter(submission_request_uuid__isnull=False),'user_id','submission_request_uuid'),
 'answer_owner_mismatches':UserAnswer.objects.exclude(user_id=F('session_question__session__user_id')).count(),
 'answer_session_owner_mismatches':UserAnswer.objects.filter(platform_session__isnull=False).exclude(user_id=F('platform_session__user_id')).count(),
 'choice_question_mismatches':UserAnswer.objects.filter(selected_choice__isnull=False).exclude(selected_choice__question_id=F('session_question__question_id')).count(),
 'incorrect_answer_rewards':PointLedger.objects.filter(answer__is_correct=False).count(),
 'ledger_owner_mismatches':PointLedger.objects.exclude(profile__user_id=F('answer__user_id')).count(),
 'wallet_ledger_sum_mismatches':bad_wallets,'reward_policy_mismatches':bad_rewards,
 'learning_completed':LearningSession.objects.filter(completed_at__isnull=False).count(),
 'answers':UserAnswer.objects.count(),'ledger_rows':PointLedger.objects.count(),
 'recipe_completed':StoryProgress.objects.filter(event_key='pepero-2026-secret-recipe',completed_at__isnull=False).count()}
for key,value in integrity.items():
    if 'mismatch' in key or key.startswith('duplicate') or key in ('missing_egg_metadata','incorrect_answer_rewards'):assert value==0,(key,value)
assert integrity['rank_unique_contiguous']
summary['integrity']=integrity
secret_tokens=[]
def find_secrets(obj):
    if isinstance(obj,dict):
        for key,value in obj.items():
            if any(word in key.lower() for word in ('token','ticket','session_key','secret')) and isinstance(value,str) and len(value)>=16:secret_tokens.append(value)
            find_secrets(value)
    elif isinstance(obj,list):
        for item in obj:find_secrets(item)
for evidence in GamePlay.objects.values_list('evidence',flat=True):find_secrets(evidence)
connections.close_all()
manifest=read('source-manifest.json')
changed=[f for f,hash in manifest.items() if hashlib.sha256((ROOT/'source'/f).read_bytes()).hexdigest()!=hash]
assert not changed,changed
summary['source_manifest_unchanged']=True
access=[r.split('|') for r in (ROOT/'logs/nginx-access.log').read_text().splitlines()]
summary['gateway']={'502':sum(len(r)>2 and r[2]=='502' for r in access),'504':sum(len(r)>2 and r[2]=='504' for r in access)}
summary['scope']={'accounts':100,'movement_workers':4,'service_workers':4,'jobs':1,'pool_min':1,'pool_max':10,'front_backlog':128,
 'mixed_seconds_each':180,'mixed_protocol_accounts':99,'mixed_chrome_accounts':1,'window_override':True,
 'percentile':'nearest rank over complete started requests; incomplete population yields null'}
(ROOT/'final-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
# Preserve raw data without fixture cookies, interaction proofs or server secrets.
secrets=secret_tokens.copy()
environment=read('environment.json')
secrets.extend(environment[k] for k in ('DJANGO_SECRET_KEY','FRONTEND_SECRET_KEY') if environment.get(k))
clients=read('clients.json')
for u in [*clients['users'],clients['browser']]:secrets.append(u['session_key']);secrets.extend(u['tickets'].values())
metadata=read('metadata.json')
for u in metadata['users']:secrets.extend(u['tickets'].values())
secrets=sorted(set(secrets),key=len,reverse=True)
for p in ROOT.rglob('*'):
    rel=p.relative_to(ROOT)
    if not p.is_file() or rel.parts[0] in ('source','chrome-profile','assets-runtime') or p.name in ('environment.json','clients.json','sessions.json','metadata.json','current-chrome.json','expected-api.json'):continue
    if p.suffix not in ('.json','.jsonl','.log','.py','.conf','.png'):continue
    dest=OUT/rel;dest.parent.mkdir(parents=True,exist_ok=True)
    if p.suffix=='.png':shutil.copy2(p,dest);continue
    text=p.read_text(errors='replace')
    for secret in secrets:text=text.replace(secret,'[REDACTED]')
    if p.suffix=='.log':dest.with_suffix('.log.gz').write_bytes(gzip.compress(text.encode(),mtime=0))
    else:dest.write_text(text)
summary_path=OUT/'final-summary.json'
print(json.dumps({'summary':str(summary_path),'checks':summary['checks'],'integrity':integrity,'gateway':summary['gateway']},ensure_ascii=False))
