"""Main quest v3: durable stage state and independently recorded game plays."""
import json
import random
import uuid
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Max
from django.contrib.auth import get_user_model
from django.utils import timezone
from learning.services import LearningError, learning_write_transaction, active_platform_session, parse_uuid4
from eventing.events import record_play_ended, record_play_started, record_progress_completed, record_story_action
from eventing.models import PlatformSession
from .models import EventDefinition, EventProgress, GameDefinition, GamePlay, StoryProgress, StoryRecord
from .access import verify
from . import topping_order

TZ = ZoneInfo('Asia/Seoul')
# Stable event IDs preserve all existing progress, plays and final completion ranks.
STAGES = [('1', '이상한 수량표 확인'), ('3', 'ROOM01 증거분석실'), ('4', '꾸왕이에게 돌아가기'), ('5', 'ROOM02 빼빼로 만들기'), ('6', '꾸왕이 최종 전달')]
NAMES = [name for _, name in STAGES]
GAMES = [('sql_memory', 'SQL 순서 기억 게임'), ('card_match', '카드 뒤집기'), ('coating', '초콜릿 코팅'), ('topping', '토핑 뿌리기'), ('wrapping', '포장 완료')]
LOCKED = {}
SALT = 'event-main-game-v3'
TOUCH_DURATION_MS = 10000  # Finish the full round to advance STEP 03; hit count is a score only.

def uid(code):
    return uuid.uuid5(uuid.NAMESPACE_URL, 'playdata/pepero-2026/v3/' + code)

def coating_challenge(seed):
    rng=random.Random(seed)
    return dict(rulesVersion=3,leadInMs=300,lateWindowMs=500,maxAverageMs=200,rounds=[dict(fillMs=rng.choice([1000,1200,1400]),targetPct=75,zoneLeftPct=70,zoneRightPct=80) for _ in range(3)])


def coating_timing(c,result,elapsed):
    trials=result.get('trials');rounds=c.get('rounds',[])
    if not isinstance(trials,list) or len(trials)!=3 or len(rounds)!=3:return None
    errors=[];previous=0;in_zone=[]
    for trial,round_spec in zip(trials,rounds):
        if not isinstance(trial,dict):return None
        start=trial.get('startMs');stop=trial.get('stopMs')
        if type(start) is not int or start<previous or start>elapsed+100:return None
        deadline=start+c['leadInMs']+round_spec['fillMs']+c['lateWindowMs']
        if stop is None:
            if deadline>elapsed+100:return None
            errors.append(None);in_zone.append(False);previous=deadline
        else:
            if type(stop) is not int or not start<=stop<=deadline or stop>elapsed+100:return None
            perfect=start+c['leadInMs']+round_spec['fillMs']*round_spec['targetPct']/100
            errors.append(int(abs(stop-perfect)));previous=stop
            fill=(stop-start-c['leadInMs'])/round_spec['fillMs']*100
            in_zone.append(round_spec.get('zoneLeftPct',-float('inf'))<=fill<=round_spec.get('zoneRightPct',float('inf')))
    average=None if None in errors else sum(errors)/3
    return dict(errorsMs=errors,averageMs=None if average is None else round(average,3),limitMs=c['maxAverageMs'],inZone=in_zone,success=average is not None and all(in_zone) and sum(errors)<=c['maxAverageMs']*3)


def catalog():
    codes=['campaign',*[code for code,_ in STAGES],*[code for code,_ in GAMES]]
    definitions=EventDefinition.objects.in_bulk([uid(code) for code in [*codes,'2']])
    games=dict(GameDefinition.objects.filter(pk__in=[uid('game/'+code) for code in [*[c for c,_ in GAMES],'kkuwang_touch']]).values_list('pk','game_name'))
    retired=definitions.get(uid('2'))
    if (all(uid(code) in definitions for code in codes)
            and all(uid('game/'+code) in games for code,_ in GAMES)
            and games.get(uid('game/kkuwang_touch'))=='꾸왕이 빼빼로 타격 챌린지'
            and all(definitions[uid(code)].step_order==order and definitions[uid(code)].event_name==name
                    for order,(code,name) in enumerate(STAGES,1))
            and all(definitions[uid(code)].step_order==(1 if code=='sql_memory' else index) and definitions[uid(code)].event_name==name
                    for index,(code,name) in enumerate(GAMES))
            and definitions[uid('4')].game_id is not None
            and (retired is None or (retired.parent_event_id is None and retired.step_order is None and retired.event_type=='ARCHIVED'))):
        return
    _initialize_catalog()


@learning_write_transaction
def _initialize_catalog():
    campaign, _ = EventDefinition.objects.get_or_create(event_uuid=uid('campaign'), defaults=dict(event_name='사라진 11번째 빼빼로', event_type='CAMPAIGN', starts_at=datetime(2026,11,9,8,30,tzinfo=TZ), ends_at=datetime(2026,11,11,22,tzinfo=TZ)))
    for code, name in STAGES:
        EventDefinition.objects.get_or_create(event_uuid=uid(code), defaults=dict(parent_event=campaign,event_type='MAIN',event_name=name))
    definitions = EventDefinition.objects.in_bulk([uid(code) for code, _ in STAGES] + [uid('2')])
    retired = definitions.get(uid('2'))
    if ((retired and (retired.parent_event_id is not None or retired.step_order is not None or retired.event_type != 'ARCHIVED'))
            or any(definitions[uid(code)].step_order != order or definitions[uid(code)].event_name != name
                   for order, (code, name) in enumerate(STAGES, 1))):
        with transaction.atomic():
            EventDefinition.objects.select_for_update().get(pk=campaign.pk)
            EventDefinition.objects.filter(pk__in=definitions).update(step_order=None)
            EventDefinition.objects.filter(pk=uid('2')).update(parent_event=None, event_type='ARCHIVED')
            for order, (code, name) in enumerate(STAGES, 1):
                EventDefinition.objects.filter(pk=uid(code)).update(parent_event=campaign, event_type='MAIN', event_name=name, step_order=order)
    desired=[]
    for i,(code,name) in enumerate(GAMES):
        game,_=GameDefinition.objects.get_or_create(game_uuid=uid('game/'+code),defaults=dict(game_code=code,game_name=name))
        event,_=EventDefinition.objects.get_or_create(event_uuid=uid(code),defaults=dict(parent_event_id=uid('3' if code=='sql_memory' else '5'),event_type='MAIN',event_name=name,game=game))
        desired.append((event,game,code,name,1 if code=='sql_memory' else i))
    # Reorder existing definitions without deleting progress/plays or violating sibling uniqueness.
    if any(e.step_order!=order or e.event_name!=name for e,g,c,name,order in desired):
        with transaction.atomic():
            EventDefinition.objects.select_for_update().get(pk=campaign.pk)
            EventDefinition.objects.filter(parent_event_id=uid('5')).update(step_order=None)
            EventDefinition.objects.filter(pk=uid('typing')).update(parent_event=None,step_order=None,event_type='ARCHIVED')
            GameDefinition.objects.filter(pk=uid('game/typing')).update(is_placed=False)
            for event,game,code,name,order in desired:
                EventDefinition.objects.filter(pk=event.pk).update(parent_event_id=uid('3' if code=='sql_memory' else '5'),step_order=order,event_name=name)
                GameDefinition.objects.filter(pk=game.pk).update(game_name=name,is_placed=True)
            GameDefinition.objects.filter(pk=uid('game/wrapping')).update(is_enabled=True)
    touch,_=GameDefinition.objects.get_or_create(game_uuid=uid('game/kkuwang_touch'),defaults=dict(game_code='kkuwang_touch',game_name='꾸왕이 빼빼로 타격 챌린지'))
    if touch.game_name!='꾸왕이 빼빼로 타격 챌린지':GameDefinition.objects.filter(pk=touch.pk).update(game_name='꾸왕이 빼빼로 타격 챌린지')
    EventDefinition.objects.filter(pk=uid('4'),game__isnull=True).update(game=touch)

def window():
    now=timezone.now().astimezone(TZ)
    preview=bool(settings.DEBUG and settings.EVENT_STORY_PREVIEW)
    phase='before' if now<datetime(2026,11,9,8,30,tzinfo=TZ) else 'closed' if now>=datetime(2026,11,11,22,tzinfo=TZ) else 'open'
    slots=[('08:30','09:00'),('09:50','10:00'),('10:50','11:00'),('11:50','12:00'),('12:50','14:00'),('14:50','15:00'),('15:50','16:00'),('16:50','17:00'),('17:50','22:00')]
    if phase=='open' and not any(time.fromisoformat(a)<=now.time().replace(tzinfo=None)<time.fromisoformat(b) for a,b in slots): phase='class'
    return dict(phase='open' if preview else phase,preview=preview,end=datetime(2026,11,11,22,tzinfo=TZ).isoformat())

def ensure_open():
    from campus_control.service import read_state
    if read_state(force=True).get('paused'):
        raise LearningError('강의 중에는 미니게임을 진행할 수 없습니다.','campus_frozen',423)
    if window()['phase']!='open':
        raise LearningError('현재 이벤트 이용 시간이 아닙니다. 저장된 진행은 보존됩니다.', 'event_blocked', 423)

def done(user, code):
    return EventProgress.objects.filter(user=user,event_id=uid(code),status='COMPLETED').exists()

def complete(user, code, session_uuid=None):
    """Returns (progress, newly completed). Callers write the completion event once the row's values are final."""
    p, _=EventProgress.objects.get_or_create(user=user,event_id=uid(code))
    newly=p.status!='COMPLETED'
    if newly:
        p.status='COMPLETED';p.completed_at=timezone.now()
        p.session=active_platform_session(user,session_uuid) if session_uuid is not None else None
        p.save()
    return p, newly

def complete_now(user, code, session_uuid=None):
    """complete() for callers that do not change the row afterwards."""
    p, newly=complete(user,code,session_uuid)
    if newly:record_progress_completed(p)
    return p

def require(user, code):
    if not done(user,code): raise LearningError('이전 미션을 먼저 완료해 주세요.', 'prerequisite',409)

def room1_briefed(user):
    return StoryRecord.objects.filter(progress__user=user,progress__event_key='pepero-2026-room1-dialogue',key='yeongseok-intro').exists()


def recover_finished_challenge(user):
    """Existing validated full rounds also satisfy the now-enabled fourth stage."""
    completed=set(EventProgress.objects.filter(user=user,event_id__in=[uid('3'),uid('4')],status='COMPLETED').values_list('event_id',flat=True))
    if uid('3') not in completed or uid('4') in completed:return
    _recover_finished_challenge(user)


@learning_write_transaction
def _recover_finished_challenge(user):
    get_user_model().objects.select_for_update().get(pk=user.pk)
    if not done(user,'3') or done(user,'4'):return
    play=GamePlay.objects.filter(user=user,game__game_code='kkuwang_touch',event_id=uid('4'),status='FINISHED',ended_at__isnull=False,duration_ms__gte=TOUCH_DURATION_MS-100).order_by('ended_at').first()
    if play:
        progress,newly=complete(user,'4')
        progress.completed_at=play.ended_at;progress.session_id=play.session_id
        progress.save(update_fields=['completed_at','session'])
        if newly:record_progress_completed(progress)


def prepare_status(user):
    catalog()
    recover_finished_challenge(user)


def status(user):
    completed=set(EventProgress.objects.filter(user=user,status='COMPLETED').values_list('event_id',flat=True))
    steps=[dict(name=name,complete=uid(code) in completed) for code,name in STAGES]
    current=next((i+1 for i,x in enumerate(steps) if not x['complete']),len(STAGES)+1)
    mine,top3=_ranking(user,0,3)
    return dict(room1Briefed=room1_briefed(user),window=window(),steps=steps,stage=current,started=steps[0]['complete'],completed=steps[-1]['complete'],games={code:uid(code) in completed for code,_ in GAMES},locked=LOCKED,canPractice=bool(user.is_staff and settings.DEBUG and settings.EVENT_STORY_PREVIEW),ranking=dict(myRank=mine,top3=top3))

def _ranking(user,offset,limit):
    from accounts.models import get_nickname
    q=EventProgress.objects.filter(event_id=uid('6'),status='COMPLETED',completion_order__isnull=False).select_related('user__campus_profile').order_by('completion_order')
    mine=q.filter(user=user).values_list('completion_order',flat=True).first()
    return mine,[dict(rank=p.completion_order,nickname=get_nickname(p.user)) for p in q[offset:offset+limit]]


def ranking(user,page=1):
    mine,entries=_ranking(user,(page-1)*25,26)
    return dict(myRank=mine,entries=entries[:25],page=page,hasMore=len(entries)>25)

def check_game(user, code):
    if code=='kkuwang_touch':
        require(user,'3');return
    if code in LOCKED: raise LearningError(LOCKED[code]+' — 완료 처리가 잠겨 있습니다.','policy_pending',409)
    if code=='sql_memory': require(user,'1')
    else:
        require(user,'4')
        codes=[c for c,_ in GAMES][1:];index=codes.index(code)
        if index: require(user,codes[index-1])

def action(user, data, session_uuid):
    if data.get('action')=='notice':
        prepare_status(user)
        # Build the one-time notice before consuming it, so a read failure is retryable.
        state=status(user)
        return dict(state=state,**_write_action(user,data,session_uuid))
    catalog()
    result=_write_action(user,data,session_uuid)
    if data.get('action') in ('game_start','investigate'):
        return result
    return dict(state=status(user),**result)


@learning_write_transaction
def _write_action(user,data,session_uuid):
    get_user_model().objects.select_for_update().get(pk=user.pk)
    recover_finished_challenge(user)
    result=_action(user,data,session_uuid)
    recover_finished_challenge(user)
    return result


def _action(user,data,session_uuid):
    a=data.get('action')
    if a=='notice':
        p,_=StoryProgress.objects.get_or_create(user=user,event_key='pepero-2026-ui-notices')
        _,created=StoryRecord.objects.get_or_create(progress=p,key=timezone.now().astimezone(TZ).date().isoformat(),defaults={'kind':'daily_notice'})
        return dict(show_notice=created)
    ensure_open()
    if a=='investigate':
        verify(user,data.get('ticket'),'investigate')
        first=not done(user,'1');complete_now(user,'1',session_uuid)
        return dict(first=first,correct=True)
    elif a=='room1_briefing':
        verify(user,data.get('ticket'),'room1')
        progress,_=StoryProgress.objects.get_or_create(user=user,event_key='pepero-2026-room1-dialogue')
        record,created=StoryRecord.objects.get_or_create(progress=progress,key='yeongseok-intro',defaults={'kind':'npc_dialogue','session_uuid':str(session_uuid),'payload':{'npc_id':'manager-yeongseok','dialogue':'room1-intro-v1'}})
        if created:record_story_action(record,session_uuid)
        return dict(first=created)
    elif a=='sql_answer':
        require(user,'sql_memory')
        verify(user,data.get('ticket'),'room1')
        if data.get('answer')!='C':return dict(correct=False)
        complete_now(user,'3',session_uuid)
    elif a=='confession':
        require(user,'3');verify(user,data.get('ticket'),'roof-guide')
        return dict(correct=True)
    elif a=='game_start':
        request_uuid=str(parse_uuid4(data.get('request_uuid'),'invalid_request_uuid'))
        code=data.get('game')
        if code not in dict(GAMES) and code!='kkuwang_touch':raise LearningError('게임을 확인해 주세요.')
        practice=data.get('practice') is True
        if practice and not (user.is_staff and settings.DEBUG and settings.EVENT_STORY_PREVIEW):raise LearningError('연습 모드 권한이 없습니다.','forbidden',403)
        if not practice or code in LOCKED:check_game(user,code)
        if code=='sql_memory' and not room1_briefed(user):
            raise LearningError('먼저 ROOM 01 영석 매니저님에게 말을 걸어 주세요.','manager_briefing_required',409)
        game=GameDefinition.objects.filter(game_code=code).first()
        if game is None or not game.is_enabled: raise LearningError('이 게임은 현재 이용할 수 없습니다.')
        verify(user,data.get('ticket'),'roof-guide' if code=='kkuwang_touch' else 'room1' if code=='sql_memory' else 'room2')
        session=PlatformSession.objects.select_for_update().filter(pk=parse_uuid4(session_uuid,'invalid_platform_session'),user=user,ended_at__isnull=True).first()
        if session is None: raise LearningError('접속이 종료됐어요. 다시 로그인해 주세요.','session_expired',409)
        previous=GamePlay.objects.filter(user=user,evidence__request_uuid=request_uuid).first()
        if previous is not None:
            receipt=previous.evidence['start_receipt']
            if previous.game_id!=game.pk or receipt['practice']!=practice:
                raise LearningError('같은 시작 요청 번호로 다른 게임을 시작할 수 없습니다.','request_conflict',409)
            if previous.session_id!=session.pk:
                raise LearningError('게임을 시작한 접속이 달라졌어요. 새 판을 시작해 주세요.','session_conflict',409)
            return dict(receipt,status=previous.status)
        # Only a new request interrupts an unfinished attempt.
        for old in GamePlay.objects.select_for_update().filter(user=user,status='RUNNING'):
            old.status='INTERRUPTED';old.ended_at=timezone.now();old.duration_ms=max(0,int((old.ended_at-old.started_at).total_seconds()*1000));old.save()
            record_play_ended(old)
        challenge={'seed':random.SystemRandom().randrange(1,1000000)}
        if code=='kkuwang_touch':challenge['durationMs']=TOUCH_DURATION_MS
        if code=='sql_memory':challenge['problem']=random.SystemRandom().randrange(8)
        if code=='coating':challenge.update(coating_challenge(challenge['seed']))
        if code=='topping':challenge.update(topping_order.challenge(challenge['seed']))
        if code=='wrapping':challenge.update(durationMs=4500,sequence=['봉투 펼치기','빼빼로 담기','포장지 접기','리본 묶기'],rulesVersion=2)
        if code=='card_match':
            deck=list(range(6))*2;random.SystemRandom().shuffle(deck);challenge['deck']=deck
        play=GamePlay.objects.create(user=user,session=session,game=game,event_id=None if practice else uid('4' if code=='kkuwang_touch' else code),evidence={'challenge':challenge})
        record_play_started(play)
        token=signing.dumps({'play':str(play.pk),'user':str(user.pk),'challenge':challenge},salt=SALT)
        receipt=dict(play=str(play.pk),token=token,challenge=challenge,practice=practice)
        play.evidence.update(request_uuid=request_uuid,start_receipt=receipt)
        play.save(update_fields=['evidence'])
        return dict(receipt,status=play.status)
    elif a in ('game_finish','game_interrupt'):
        try:play=GamePlay.objects.select_for_update().get(pk=data.get('play'),user=user)
        except (GamePlay.DoesNotExist,ValueError):raise LearningError('게임 기록을 찾을 수 없습니다.')
        try:
            signed=signing.loads(data.get('token'),salt=SALT,max_age=7200)
            if signed['play']!=str(play.pk) or signed['user']!=str(user.pk):raise signing.BadSignature()
        except (signing.BadSignature,TypeError,KeyError):raise LearningError('게임 시작 승인을 다시 받아 주세요.')
        result=data.get('proof',{})
        if not isinstance(result,dict) or len(json.dumps(result))>50000:raise LearningError('게임 결과 형식을 확인하세요.')
        if play.status!='RUNNING':
            if play.evidence.get('result')!=result:raise LearningError('이미 다른 결과로 확정된 판입니다.','result_conflict',409)
            return dict(success=None if play.status=='FINISHED' else play.status=='SUCCESS',score=play.evidence.get('score'),coatingTiming=play.evidence.get('coatingTiming'))
        elapsed=max(0,int((timezone.now()-play.started_at).total_seconds()*1000))
        if play.game.game_code=='kkuwang_touch' and a=='game_finish':
            taps=result.get('taps',[]);duration=signed['challenge']['durationMs']
            if not isinstance(taps,list) or len(taps)>1000 or any(type(t)!=int or not 0<=t<=duration for t in taps) or taps!=sorted(taps) or elapsed<duration-100:
                raise LearningError('터치 기록을 확인할 수 없습니다. 다시 시도해 주세요.')
            if play.event_id is not None:require(user,'3')
            play.status='FINISHED';play.ended_at=timezone.now();play.duration_ms=elapsed;play.evidence={**play.evidence,'result':result,'score':len(taps)};play.save()
            record_play_ended(play)
            if play.event_id is not None:complete_now(user,'4',session_uuid)
            return dict(success=None,score=len(taps))
        success=False if a=='game_interrupt' else judge(play.game.game_code,signed['challenge'],result,elapsed)
        play.status='INTERRUPTED' if a=='game_interrupt' else 'SUCCESS' if success else 'FAILED'
        timing=coating_timing(signed['challenge'],result,elapsed) if play.game.game_code=='coating' and signed['challenge'].get('rulesVersion')==3 else None
        play.ended_at=timezone.now();play.duration_ms=elapsed;play.evidence={**play.evidence,'result':result,**({'coatingTiming':timing} if timing is not None else {})};play.save()
        record_play_ended(play)
        if success and play.event_id is not None:
            check_game(user,play.game.game_code);complete_now(user,play.game.game_code,session_uuid)
            if all(done(user,c) for c,_ in GAMES[1:]):complete_now(user,'5',session_uuid)
        return dict(success=success,coatingTiming=timing)
    elif a=='deliver':
        require(user,'5');verify(user,data.get('ticket'),'final-guide')
        EventDefinition.objects.select_for_update().get(pk=uid('6'))
        p,newly=complete(user,'6',session_uuid)
        if p.completion_order is None:
            p.completion_order=(EventProgress.objects.filter(event_id=uid('6')).aggregate(n=Max('completion_order'))['n'] or 0)+1;p.save()
        if newly:record_progress_completed(p)
    else: raise LearningError('지원하지 않는 이벤트 요청입니다.')
    return dict(correct=True)

def judge(code,c,r,elapsed):
    if code=='topping' and c.get('rulesVersion',0)>=3:
        summary=topping_order.replay(c,r,elapsed)
        return bool(summary and summary['success'])
    if code=='coating' and c.get('rulesVersion')==3:
        timing=coating_timing(c,r,elapsed)
        return bool(timing and timing['success'])
    if r.get('success') is not True:return False
    if code=='sql_memory':
        questions=json.loads(Path(__file__).with_name('sql_questions.json').read_text(encoding='utf-8'))
        seq=questions[c['problem']]['sequence']
        return r.get('rounds')==[seq[:i] for i in range(1,len(seq)+1)] and elapsed>=len(seq)*300
    if code=='card_match':
        picks=r.get('picks',[]);deck=c['deck'];matched=set()
        if not isinstance(picks,list) or len(picks)>600:return False
        for pair in picks:
            if not isinstance(pair,list) or len(pair)!=2 or any(type(i)!=int or not 0<=i<12 for i in pair):return False
            a,b=pair
            if a==b or a in matched or b in matched:return False
            if deck[a]==deck[b]:matched.update(pair)
        return len(matched)==12 and 1000<=elapsed<=65000
    if code=='coating':
        taps=r.get('taps',[])
        if len(taps)!=10 or any(type(v)!=int or v<0 or v>elapsed+100 for v in taps):return False
        if any(b-a<140 for a,b in zip(taps,taps[1:])):return False
        hits=[((v+c['seed']%2400)/12)%200 for v in taps]
        return sum(65<=min(v,200-v)<=80 for v in hits)>=7
    if code=='topping':
        caught=r.get('caught',[])
        if not isinstance(caught,list) or not c.get('minCatch',10)<=len(caught)<=40 or not 29000<=elapsed<=38000:return False
        seen=set()
        for hit in caught:
            if not isinstance(hit,dict):return False
            i,at,x=hit.get('id'),hit.get('at'),hit.get('x')
            if type(i)!=int or not 0<=i<40 or i in seen or type(at)!=int or type(x) not in (int,float):return False
            if not 0<=at<=min(elapsed+100,30000) or not 281.8<=((at/1000-i*.7)*125)<=314.2:return False
            if abs((60+(c['seed']*(i+17)*97)%680)-x)>=c.get('catchRadius',52):return False
            seen.add(i)
        return True
    if code=='wrapping':
        return type(r.get('animationMs')) is int and r['animationMs']>=c['durationMs'] and elapsed>=c['durationMs'] and r.get('sequence')==c['sequence']
    return False

def learning_started(session):
    """Learning remains optional and no longer creates main-quest progress."""
    return None


def learning_completed(session):
    """Learning results and learning achievements are handled independently."""
    return None
