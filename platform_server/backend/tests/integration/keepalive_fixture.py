"""All mutations are confined to explicitly named disposable keep-alive DB clones."""
import json
import os
import sys
from pathlib import Path
from datetime import timedelta

REPO=Path('/Users/sindong-yeob/projects/platform-server/playdatametaverse')
ROOT=Path('/private/tmp/platform-keepalive-reviewed-20261107')
OUT=Path(os.environ['LOCAL44_OUT'])
os.environ.update(json.loads((OUT/'environment.json').read_text()))
sys.path.insert(0,str(ROOT/'source/backend'))
import django
django.setup()
from django.db import connection,connections
from django.contrib.auth import get_user_model,SESSION_KEY,BACKEND_SESSION_KEY,HASH_SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.utils import timezone
from accounts.models import ActiveLogin
from accounts.session_policy import start_login
from event_story.models import EventProgress,GamePlay,StoryProgress,StoryRecord
from event_story import main
from learning.models import LearningSession,UserAnswer,PointLedger,PointWallet,Question
assert connection.settings_dict['NAME'].startswith('receipt_keepalive_') and str(connection.settings_dict['PORT'])=='55500'
phase=sys.argv[1]
clients=json.loads((OUT/'clients.json').read_text())
ids=[row['user_id'] for row in clients['users']]
if phase=='refresh':
    for row in [*clients['users'],clients['browser']]:
        user=get_user_model().objects.get(pk=row['user_id'])
        store=SessionStore()
        data={SESSION_KEY:str(user.pk),BACKEND_SESSION_KEY:'django.contrib.auth.backends.ModelBackend',
              HASH_SESSION_KEY:user.get_session_auth_hash()}
        Session.objects.update_or_create(session_key=row['session_key'],defaults={
            'session_data':store.encode(data),'expire_date':timezone.now()+timedelta(days=1)})
        store=SessionStore(session_key=row['session_key'])
        start_login(store,user);store.save()
        row['session_uuid']=str(store['platform_session_uuid'])
    (OUT/'clients.json').write_text(json.dumps(clients));(OUT/'clients.json').chmod(0o600)
    (OUT/'sessions.json').write_text(json.dumps([r['session_key'] for r in clients['users']]));(OUT/'sessions.json').chmod(0o600)
    metadata=json.loads((OUT/'metadata.json').read_text())
    metadata['users']=clients['users']
    (OUT/'metadata.json').write_text(json.dumps(metadata));(OUT/'metadata.json').chmod(0o600)
elif phase=='preflight':
    assert Question.objects.filter(is_active=True).count()==3580
    assert len(ids)==100 and get_user_model().objects.filter(pk__in=ids,is_active=True).count()==100
    assert all(ActiveLogin.objects.filter(user_id=r['user_id'],session_key=r['session_key']).exists() for r in clients['users'])
    with connection.cursor() as cursor:
        cursor.execute('SHOW max_connections')
        maximum=int(cursor.fetchone()[0])
    from django.conf import settings
    attest=[json.loads(f.read_text()) for f in OUT.glob('attestation-*.json')]
    assert len(attest)==9,len(attest)
    assert maximum==100 and settings.DATABASES['default']['OPTIONS']['pool']['max_size']==10
    queues=__import__('subprocess').check_output(['netstat','-Lan'],text=True)
    (OUT/'listen-queues.txt').write_text(queues)
    for port in [9601,9602,*range(9751,9755),*range(9761,9765)]:
        lines=[line for line in queues.splitlines() if f'127.0.0.1.{port}' in line]
        assert len(lines)==1 and int(lines[0].split()[0].split('/')[-1])==128,(port,lines)
    expected={*[f'movement-{port}' for port in range(9751,9755)],*[f'service-{port}' for port in range(9761,9765)],'jobs'}
    assert {row['worker'] for row in attest}==expected
    source_manifest=json.loads((ROOT/'source-manifest.json').read_text())
    for row in attest:
        assert row['db']==connection.settings_dict['NAME'] and str(row['port'])=='55500'
        assert row['pool']['min_size']==1 and row['pool']['max_size']==10
        assert row['windowOverride'] is True and row['debug'] is False
        assert all(source_manifest[name]==value for name,value in row['files'].items())
    hooks=[json.loads(f.read_text()) for f in OUT.glob('hook-attestation-*.json')]
    assert {row['worker'] for row in hooks}==expected|{'front-movement','front-service'}
    assert len(hooks)==11
    for row in hooks:
        assert row['debug'] is False
        assert row['files'] and all(source_manifest[name]==value for name,value in row['files'].items())
        if row['backend']:
            assert row['database']==connection.settings_dict['NAME'] and row['port']=='55500'
            assert row['pool']['min_size']==1 and row['pool']['max_size']==10
        else:
            assert row['destination']=='http://127.0.0.1:8118' and row['capacity']==100 and row['idle_timeout']==20
            assert row['keepalive']==OUT.name.startswith('b')
    (OUT/'preflight.json').write_text(json.dumps({'active_questions':3580,'accounts':100,
        'backend_processes':8,'jobs':1,'frontends':2,'pool_max_per_process':10,
        'postgres_max_connections':maximum,'listener_backlog':128,'open_window_override':True},indent=2))
elif phase=='learning_baseline':
    (OUT/'learning-before.json').write_text(json.dumps({
        'session_id':LearningSession.objects.order_by('-pk').values_list('pk',flat=True).first() or 0,
        'wallets':{str(user):balance for user,balance in PointWallet.objects.filter(profile__user_id__in=ids).values_list('profile__user_id','balance')}}))
elif phase=='audit':
    from django.db.models import Count,Sum,F
    before=json.loads((OUT/'learning-before.json').read_text());baseline=before['session_id']
    lessons=LearningSession.objects.filter(pk__gt=baseline,user_id__in=ids)
    answers=UserAnswer.objects.filter(session_question__session__in=lessons)
    ledger=PointLedger.objects.filter(answer__in=answers)
    duplicate=list(answers.values('user_id','submission_request_uuid').annotate(n=Count('pk')).filter(n__gt=1))
    wrong_owner=answers.exclude(user_id=F('session_question__session__user_id')).count()
    wrong_ledger=ledger.exclude(profile__user_id=F('answer__user_id')).count()
    double_reward=list(ledger.values('answer_id').annotate(n=Count('pk')).filter(n__gt=1))
    deltas={str(user):total for user,total in ledger.values('profile__user_id').annotate(total=Sum('delta')).values_list('profile__user_id','total')}
    balances={str(user):balance for user,balance in PointWallet.objects.filter(profile__user_id__in=ids).values_list('profile__user_id','balance')}
    wrong_balance=sum(balances.get(user,0)-before['wallets'].get(user,0)!=deltas.get(user,0) for user in ids)
    bad_session=answers.filter(platform_session__isnull=True).count()+answers.exclude(platform_session__user_id=F('user_id')).count()
    event_owner=EventProgress.objects.filter(user_id__in=ids,session__isnull=False).exclude(session__user_id=F('user_id')).count()
    play_owner=GamePlay.objects.filter(user_id__in=ids).exclude(session__user_id=F('user_id')).count()
    result={'wrong_wallet_balance':wrong_balance,'wrong_answer_session':bad_session,
            'wrong_event_session_owner':event_owner,'wrong_play_session_owner':play_owner,'started_lessons':lessons.count(),'completed_lessons':lessons.filter(completed_at__isnull=False).count(),
            'new_answers':answers.count(),'new_ledger':ledger.count(),'duplicate_answer_groups':len(duplicate),
            'wrong_owner':wrong_owner,'wrong_ledger':wrong_ledger,'duplicate_reward_groups':len(double_reward)}
    assert not any(result[k] for k in ('duplicate_answer_groups','wrong_owner','wrong_ledger','duplicate_reward_groups','wrong_wallet_balance','wrong_answer_session','wrong_event_session_owner','wrong_play_session_owner')),result
    (OUT/'learning-audit.json').write_text(json.dumps(result,indent=2))
elif phase=='receipt_reset':
    GamePlay.objects.filter(user_id__in=ids).delete()
    EventProgress.objects.filter(user_id__in=ids,event_id=main.uid('6')).delete()
elif phase=='browser_reset':
    user_id=clients['browser']['user_id']
    GamePlay.objects.filter(user_id=user_id).delete()
    EventProgress.objects.filter(user_id=user_id).delete()
if phase in ('refresh','tickets'):
    from django.core import signing
    from event_story.access import SALT
    for row in [*clients['users'],clients['browser']]:
        row['tickets']={target:signing.dumps({'user':row['user_id'],'target':target},salt=SALT)
            for target in ('investigate','room1','room2','roof-guide','final-guide','recipe-note','recipe-guide')}
    (OUT/'clients.json').write_text(json.dumps(clients));(OUT/'clients.json').chmod(0o600)
    metadata=json.loads((OUT/'metadata.json').read_text());metadata['users']=clients['users']
    (OUT/'metadata.json').write_text(json.dumps(metadata));(OUT/'metadata.json').chmod(0o600)
connections.close_all()
print('Owned fixture phase '+phase,flush=True)
