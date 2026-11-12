from uuid import uuid4
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.utils import timezone
from eventing.models import PlatformSession
from learning.services import LearningError
from . import main
from .access import SALT
from .models import EventProgress, GamePlay

@override_settings(DEBUG=True,EVENT_STORY_PREVIEW=True)
class MainFlowTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='main-flow-test')
        self.session=PlatformSession.objects.create(user=self.user,started_at=timezone.now(),last_seen_at=timezone.now())
        main.catalog()
    def ticket(self,target):
        return signing.dumps({'user':str(self.user.pk),'target':target},salt=SALT)
    def send(self,action,**kwargs):
        if action=='game_start':kwargs.setdefault('request_uuid',str(uuid4()))
        return main.action(self.user,dict(action=action,**kwargs),self.session.pk)
    def test_notice_does_not_register_and_investigation_is_idempotent(self):
        self.assertTrue(self.send('notice')['show_notice'])
        self.assertFalse(self.send('notice')['show_notice'])
        self.assertFalse(EventProgress.objects.filter(user=self.user).exists())
        self.assertTrue(self.send('investigate',ticket=self.ticket('investigate'))['first'])
        self.assertFalse(self.send('investigate',ticket=self.ticket('investigate'))['first'])
        self.assertEqual(EventProgress.objects.filter(user=self.user).count(),1)
    def test_order_and_pending_policies_cannot_be_bypassed(self):
        with self.assertRaises(LearningError):self.send('game_start',game='sql_memory',ticket=self.ticket('room1'))
        for code in ('1','3'):main.complete(self.user,code)
        self.assertTrue(self.send('confession',ticket=self.ticket('roof-guide'))['correct'])
        self.assertFalse(main.done(self.user,'4'))
        with self.assertRaises(LearningError):self.send('game_start',game='typing',ticket=self.ticket('room2'))
    def test_sql_replay_and_idempotent_result(self):
        for code in ('1',):main.complete(self.user,code)
        self.send('room1_briefing',ticket=self.ticket('room1'))
        started=self.send('game_start',game='sql_memory',ticket=self.ticket('room1'))
        import json
        questions=json.loads(main.Path(main.__file__).with_name('sql_questions.json').read_text(encoding='utf-8'))
        seq=questions[started['challenge']['problem']]['sequence']
        GamePlay.objects.filter(pk=started['play']).update(started_at=timezone.now()-timedelta(seconds=25))
        proof={'success':True,'rounds':[seq[:i] for i in range(1,len(seq)+1)]}
        self.assertTrue(self.send('game_finish',play=started['play'],token=started['token'],proof=proof)['success'])
        self.assertTrue(self.send('game_finish',play=started['play'],token=started['token'],proof=proof)['success'])
        self.assertEqual(GamePlay.objects.count(),1)
        with self.assertRaises(LearningError):self.send('game_finish',play=started['play'],token=started['token'],proof={'success':False})
        self.assertFalse(self.send('sql_answer',ticket=self.ticket('room1'),answer='B')['correct'])
        self.assertTrue(self.send('sql_answer',ticket=self.ticket('room1'),answer='C')['correct'])
        self.assertTrue(main.done(self.user,'3'))
    def test_client_success_alone_does_not_complete(self):
        for code in ('1',):main.complete(self.user,code)
        self.send('room1_briefing',ticket=self.ticket('room1'))
        started=self.send('game_start',game='sql_memory',ticket=self.ticket('room1'))
        self.assertFalse(self.send('game_finish',play=started['play'],token=started['token'],proof={'success':True})['success'])
        self.assertFalse(main.done(self.user,'sql_memory'))

    def test_challenge_records_score_completes_stage_and_unlocks_room2(self):
        for code in ('1','3'):main.complete(self.user,code)
        started=self.send('game_start',game='kkuwang_touch',ticket=self.ticket('roof-guide'))
        GamePlay.objects.filter(pk=started['play']).update(started_at=timezone.now()-timedelta(seconds=11))
        result=self.send('game_finish',play=started['play'],token=started['token'],proof={'taps':[100,300,500]})
        self.assertIsNone(result['success'])
        self.assertEqual(result['score'],3)
        self.assertEqual(GamePlay.objects.get(pk=started['play']).status,'FINISHED')
        self.assertTrue(main.done(self.user,'4'))
        self.assertEqual(result['state']['stage'],4)
        self.send('game_finish',play=started['play'],token=started['token'],proof={'taps':[100,300,500]})
        self.assertEqual(EventProgress.objects.filter(user=self.user,event_id=main.uid('4')).count(),1)
        self.send('game_start',game='card_match',ticket=self.ticket('room2'))

    def test_practice_does_not_register_main_progress(self):
        self.user.is_staff=True;self.user.save(update_fields=['is_staff'])
        started=self.send('game_start',game='card_match',ticket=self.ticket('room2'),practice=True)
        deck=started['challenge']['deck'];picks=[[i for i,v in enumerate(deck) if v==term] for term in range(6)]
        GamePlay.objects.filter(pk=started['play']).update(started_at=timezone.now()-timedelta(seconds=2))
        result=self.send('game_finish',play=started['play'],token=started['token'],proof={'success':True,'picks':picks})
        self.assertTrue(result['success']);self.assertIsNone(GamePlay.objects.get(pk=started['play']).event_id)
        self.assertFalse(EventProgress.objects.filter(user=self.user).exists())

    def test_freeze_interrupts_running_game_without_resetting_progress(self):
        for code in ('1',):main.complete(self.user,code)
        self.send('room1_briefing',ticket=self.ticket('room1'))
        started=self.send('game_start',game='sql_memory',ticket=self.ticket('room1'))
        from campus_control.service import change, read_state
        change(self.user,True,'강의 중',read_state(force=True)['revision'])
        self.assertEqual(GamePlay.objects.get(pk=started['play']).status,'INTERRUPTED')
        self.assertTrue(main.done(self.user,'1'))

    def test_topping_and_coating_replay(self):
        challenge={'seed':1234}
        caught=[{'id':i,'at':int((i*.7+2.4)*1000),'x':60+(1234*(i+17)*97)%680} for i in range(10)]
        self.assertTrue(main.judge('topping',challenge,{'success':True,'caught':caught},31000))
        caught[0]['x']+=200
        self.assertFalse(main.judge('topping',challenge,{'success':True,'caught':caught},31000))
        taps=[int(900+i*2400-challenge['seed']%2400) for i in range(1,11)]
        self.assertTrue(main.judge('coating',challenge,{'success':True,'taps':taps},26000))
        self.assertFalse(main.judge('coating',challenge,{'success':True,'taps':[1]*10},26000))

    def test_workshop_new_order_and_wrapping_completion(self):
        for code in ('1','3','4','card_match'):main.complete(self.user,code)
        with self.assertRaises(LearningError):self.send('game_start',game='topping',ticket=self.ticket('room2'))
        started=self.send('game_start',game='coating',ticket=self.ticket('room2'))
        c=started['challenge'];trials=[{'startMs':i*4000,'stopMs':int(i*4000+c['leadInMs']+v['fillMs']*v['targetPct']/100)+5} for i,v in enumerate(c['rounds'])]
        GamePlay.objects.filter(pk=started['play']).update(started_at=timezone.now()-timedelta(seconds=20))
        result=self.send('game_finish',play=started['play'],token=started['token'],proof={'success':True,'trials':trials})
        self.assertTrue(result['success']);self.assertEqual(result['coatingTiming']['averageMs'],5)
        c=main.topping_order.challenge(1234)
        c['order']={'strawberry':3,'almond':3}
        c['drops']=[dict(id=i,kind='strawberry' if i%2==0 else 'almond',x=400,atMs=0,travelMs=(i+1)*500,landTick=(i+1)*10) for i in range(10)]
        c['order']={'strawberry':5,'almond':5}
        with patch.object(main.topping_order,'challenge',return_value=c):
            started=self.send('game_start',game='topping',ticket=self.ticket('room2'))
        self.assertEqual(started['challenge']['order'],{'strawberry':5,'almond':5})
        GamePlay.objects.filter(pk=started['play']).update(started_at=timezone.now()-timedelta(seconds=6))
        self.assertTrue(self.send('game_finish',play=started['play'],token=started['token'],proof={'positions':[400]*101})['success'])
        started=self.send('game_start',game='wrapping',ticket=self.ticket('room2'))
        proof={'success':True,'animationMs':4500,'sequence':started['challenge']['sequence']}
        self.assertFalse(self.send('game_finish',play=started['play'],token=started['token'],proof=proof)['success'])
        self.assertFalse(main.done(self.user,'5'))
        started=self.send('game_start',game='wrapping',ticket=self.ticket('room2'))
        GamePlay.objects.filter(pk=started['play']).update(started_at=timezone.now()-timedelta(seconds=5))
        self.assertTrue(self.send('game_finish',play=started['play'],token=started['token'],proof=proof)['success'])
        self.assertTrue(main.done(self.user,'5'))
        self.assertFalse(main.done(self.user,'typing'))
        self.send('deliver',ticket=self.ticket('final-guide'))
        self.send('deliver',ticket=self.ticket('final-guide'))
        self.assertEqual(EventProgress.objects.get(user=self.user,event_id=main.uid('6')).completion_order,1)

    def test_workshop_definition_reorder_preserves_existing_progress(self):
        from .models import EventDefinition,GameDefinition
        main.complete(self.user,'card_match')
        EventDefinition.objects.filter(parent_event_id=main.uid('5')).update(step_order=None)
        for code,order in [('card_match',1),('topping',2),('coating',3),('wrapping',5)]:
            EventDefinition.objects.filter(pk=main.uid(code)).update(step_order=order)
        GameDefinition.objects.filter(game_code='wrapping').update(is_enabled=False)
        main.catalog()
        self.assertTrue(main.done(self.user,'card_match'))
        self.assertEqual(list(EventDefinition.objects.filter(parent_event_id=main.uid('5'),step_order__isnull=False).order_by('step_order').values_list('event_name',flat=True)),['카드 뒤집기','초콜릿 코팅','토핑 뿌리기','포장 완료'])
        self.assertTrue(GameDefinition.objects.get(game_code='wrapping').is_enabled)


    def test_room1_requires_manager_before_game_and_persists_dialogue(self):
        for code in ('1',):main.complete(self.user,code)
        with self.assertRaises(LearningError):self.send('game_start',game='sql_memory',ticket=self.ticket('room1'))
        with self.assertRaises(LearningError):self.send('room1_briefing',ticket=self.ticket('room2'))
        self.assertTrue(self.send('room1_briefing',ticket=self.ticket('room1'))['first'])
        self.assertFalse(self.send('room1_briefing',ticket=self.ticket('room1'))['first'])
        self.assertTrue(main.status(self.user)['room1Briefed'])
        self.send('game_start',game='sql_memory',ticket=self.ticket('room1'))
        other=get_user_model().objects.create_user(username='other-room1-user')
        self.assertFalse(main.room1_briefed(other))
        self.assertFalse(main.done(self.user,'3'))

    def test_practice_also_requires_manager_dialogue(self):
        self.user.is_staff=True;self.user.save(update_fields=['is_staff'])
        with self.assertRaises(LearningError):self.send('game_start',game='sql_memory',ticket=self.ticket('room1'),practice=True)
        self.send('room1_briefing',ticket=self.ticket('room1'))
        self.send('game_start',game='sql_memory',ticket=self.ticket('room1'),practice=True)
        self.assertFalse(EventProgress.objects.filter(user=self.user).exists())

    def test_single_campus_guide_issues_every_main_interaction(self):
        from campus.motion import MotionController,ControlError,world
        from .access import issue
        c=MotionController({'floor':'campus','x':1250.25,'y':195,'dir':'up'})
        self.assertEqual(c.hints()['nearbyId'],'campus-guide-kkuwang')
        for target in ('investigate','roof-guide','final-guide'):
            self.assertIn('ticket',issue(str(self.user.pk),c,{'target':target}))
        for code in ('1','3'):main.complete(self.user,code)
        token=issue(str(self.user.pk),c,{'target':'roof-guide'})['ticket']
        self.send('game_start',game='kkuwang_touch',ticket=token)
        old=MotionController({'floor':'rooftop','x':692,'y':897,'dir':'up'})
        with self.assertRaises(ControlError):issue(str(self.user.pk),old,{'target':'roof-guide'})
        old=MotionController({'floor':'campus','x':650,'y':445,'dir':'up'})
        with self.assertRaises(ControlError):issue(str(self.user.pk),old,{'target':'investigate'})
        self.assertNotIn('story-evidence-table',world('campus').objects)
        self.assertNotIn('story-wrapper',world('campus').objects)
        self.assertNotIn('story-count',world('campus').objects)
        self.assertTrue(world('campus').walkable(650,400))

    def test_challenge_interrupt_and_early_finish_do_not_unlock(self):
        for code in ('1','3'):main.complete(self.user,code)
        p=self.send('game_start',game='kkuwang_touch',ticket=self.ticket('roof-guide'))
        with self.assertRaises(LearningError):self.send('game_finish',play=p['play'],token=p['token'],proof={'taps':[100]})
        self.send('game_interrupt',play=p['play'],token=p['token'],proof={})
        self.assertFalse(main.done(self.user,'4'))

    def test_old_finished_round_recovers_progress_but_practice_does_not(self):
        for code in ('1','3'):main.complete(self.user,code)
        from .models import GameDefinition
        game=GameDefinition.objects.get(game_code='kkuwang_touch');ended=timezone.now()-timedelta(hours=1)
        GamePlay.objects.create(user=self.user,session=self.session,game=game,status='FINISHED',duration_ms=11000,ended_at=ended,evidence={'score':3})
        main.prepare_status(self.user)
        self.assertEqual(main.status(self.user)['stage'],3)
        GamePlay.objects.create(user=self.user,session=self.session,game=game,event_id=main.uid('4'),status='FINISHED',duration_ms=11000,ended_at=ended,evidence={'score':3})
        main.prepare_status(self.user)
        self.assertEqual(main.status(self.user)['stage'],4)
        self.assertEqual(EventProgress.objects.get(user=self.user,event_id=main.uid('4')).completed_at,ended)

    def test_complete_round_has_no_minimum_score_and_practice_never_unlocks(self):
        self.user.is_staff=True;self.user.save(update_fields=['is_staff'])
        p=self.send('game_start',game='kkuwang_touch',ticket=self.ticket('roof-guide'),practice=True)
        GamePlay.objects.filter(pk=p['play']).update(started_at=timezone.now()-timedelta(seconds=11))
        self.send('game_finish',play=p['play'],token=p['token'],proof={'taps':[]})
        self.assertFalse(main.done(self.user,'4'))
        for code in ('1','3'):main.complete(self.user,code)
        p=self.send('game_start',game='kkuwang_touch',ticket=self.ticket('roof-guide'))
        GamePlay.objects.filter(pk=p['play']).update(started_at=timezone.now()-timedelta(seconds=11))
        result=self.send('game_finish',play=p['play'],token=p['token'],proof={'taps':[]})
        self.assertEqual(result['score'],0);self.assertTrue(main.done(self.user,'4'))

    def test_coating_v3_average_boundary_and_server_calculation(self):
        c=main.coating_challenge(1234)
        self.assertEqual(c['maxAverageMs'],200)
        self.assertTrue(all(v['targetPct']==75 and v['zoneLeftPct']==70 and v['zoneRightPct']==80 for v in c['rounds']))
        # Existing signed games with no explicit bounds keep their average-only policy.
        for v in c['rounds']:v.pop('zoneLeftPct');v.pop('zoneRightPct')
        def proof(errors):return {'success':False,'averageMs':0,'trials':[{'startMs':i*4000,'stopMs':int(i*4000+c['leadInMs']+v['fillMs']*v['targetPct']/100)+e} for i,(v,e) in enumerate(zip(c['rounds'],errors))]}
        self.assertTrue(main.judge('coating',c,proof([-100,300,200]),15000))
        self.assertTrue(main.judge('coating',c,proof([200,200,200]),15000))
        self.assertFalse(main.judge('coating',c,proof([201,200,200]),15000))
        self.assertEqual(main.coating_timing(c,proof([-100,300,200]),15000)['errorsMs'],[100,300,200])

    def test_coating_v3_rejects_missing_overlapping_future_and_timeout_rounds(self):
        c=main.coating_challenge(1234)
        trials=[{'startMs':i*4000,'stopMs':int(i*4000+c['leadInMs']+v['fillMs']*v['targetPct']/100)} for i,v in enumerate(c['rounds'])]
        self.assertFalse(main.judge('coating',c,{'success':True,'trials':trials[:2]},15000))
        self.assertFalse(main.judge('coating',c,{'success':True,'trials':trials},100))
        wrong=[dict(t) for t in trials];wrong[1]['startMs']=0
        self.assertFalse(main.judge('coating',c,{'success':True,'trials':wrong},15000))
        missed=[dict(t) for t in trials];missed[0]['stopMs']=None
        self.assertFalse(main.judge('coating',c,{'success':True,'trials':missed},15000))

    def test_coating_timing_is_saved_and_retry_is_idempotent(self):
        for code in ('1','3','4','card_match'):main.complete(self.user,code)
        p=self.send('game_start',game='coating',ticket=self.ticket('room2'));c=p['challenge']
        proof={'success':True,'trials':[{'startMs':i*4000,'stopMs':int(i*4000+c['leadInMs']+v['fillMs']*v['targetPct']/100)+5} for i,v in enumerate(c['rounds'])]}
        GamePlay.objects.filter(pk=p['play']).update(started_at=timezone.now()-timedelta(seconds=20))
        r=self.send('game_finish',play=p['play'],token=p['token'],proof=proof)
        self.assertTrue(r['success']);self.assertTrue(main.done(self.user,'coating'))
        self.assertEqual(GamePlay.objects.get(pk=p['play']).evidence['coatingTiming']['averageMs'],5)
        replay=self.send('game_finish',play=p['play'],token=p['token'],proof=proof)
        self.assertEqual(replay['coatingTiming'],r['coatingTiming'])

    def test_topping_order_replay_server_decides_and_rejects_fake_movement(self):
        c=main.topping_order.challenge(1234)
        c['order']={'strawberry':3,'almond':3}
        c['drops']=[dict(id=i,kind='strawberry' if i%2==0 else 'almond',x=400,landTick=(i+1)*10) for i in range(6)]
        proof={'success':False,'positions':[400]*61}
        self.assertTrue(main.judge('topping',c,proof,3100))
        self.assertFalse(main.judge('topping',c,proof,1000))
        self.assertFalse(main.judge('topping',c,{'success':True,'positions':[400]*60},3100))
        self.assertFalse(main.judge('topping',c,{'positions':[400,700]+[700]*59},3100))
        self.assertFalse(main.judge('topping',c,{'positions':[400,float('nan')]},3100))
        self.assertFalse(main.judge('topping',c,{'positions':[400]*62},3100))

    def test_topping_order_burns_timeout_excess_and_non_order_toppings(self):
        c=main.topping_order.challenge(88)
        c['order']={'strawberry':3,'almond':3}
        c['drops']=[dict(id=i,kind='burnt',x=400,landTick=(i+1)*10) for i in range(3)]
        r=main.topping_order.replay(c,{'positions':[400]*31},1600)
        self.assertEqual(r['burnt'],3);self.assertFalse(r['success'])
        c['drops']=[]
        self.assertFalse(main.topping_order.replay(c,{'positions':[400]*601},30000)['success'])
        kinds=['strawberry']*4+['sprinkle']+['almond']*3
        c['drops']=[dict(id=i,kind=k,x=400,landTick=(i+1)*10) for i,k in enumerate(kinds)]
        r=main.topping_order.replay(c,{'positions':[400]*81},4100)
        self.assertTrue(r['success']);self.assertEqual(r['counts'],{'strawberry':4,'almond':3})
        self.assertEqual(r['bestCombo'],6)

    def test_coating_drawn_success_zone_is_enforced(self):
        c=main.coating_challenge(22)
        def proof(fill):return {'success':True,'trials':[{'startMs':i*3000,'stopMs':int(i*3000+c['leadInMs']+v['fillMs']*fill/100)} for i,v in enumerate(c['rounds'])]}
        for fill in (70,75,80):self.assertTrue(main.judge('coating',c,proof(fill),12000))
        for fill in (69,81):
            timing=main.coating_timing(c,proof(fill),12000)
            self.assertLess(timing['averageMs'],200)
            self.assertFalse(timing['success'])

    def test_topping_new_balance_has_narrower_faster_diagonal_drops(self):
        c=main.topping_order.challenge(55)
        self.assertEqual(c['rulesVersion'],5)
        self.assertEqual(c['catchRadius'],44)
        self.assertEqual(c['order'],{'strawberry':10,'almond':10})
        self.assertEqual(len(c['drops']),96)
        self.assertTrue(all(900<=d['travelMs']<=1500 and d['startX']!=d['x'] and d['landTick']*50<=30000 for d in c['drops']))
        self.assertGreater(sum(d['kind']=='burnt' for d in c['drops']),15)

    def test_topping_shrink_changes_actual_collision_radius_and_keeps_old_games(self):
        c=main.topping_order.challenge(123)
        c['order']={'strawberry':1,'almond':1}
        c['drops']=[dict(id=0,kind='burnt',x=400,landTick=10),dict(id=1,kind='strawberry',x=435,landTick=20),dict(id=2,kind='almond',x=400,landTick=30)]
        old={**c,'shrinkPx':0}
        self.assertTrue(main.topping_order.replay(old,{'positions':[400]*31},1600)['success'])
        self.assertIsNone(main.topping_order.replay(c,{'positions':[400]*31},1600))
        c['drops'].append(dict(id=3,kind='strawberry',x=430,landTick=40))
        r=main.topping_order.replay(c,{'positions':[400]*41},2100)
        self.assertTrue(r['success']);self.assertEqual(r['burnt'],1)
        self.assertEqual(r['caught'],[0,2,3])

    def test_topping_ten_each_is_required(self):
        c=main.topping_order.challenge(44)
        c['drops']=[dict(id=i,kind='strawberry' if i%2==0 else 'almond',x=400,landTick=(i+1)*10) for i in range(20)]
        self.assertIsNone(main.topping_order.replay(c,{'positions':[400]*101},6000))
        r=main.topping_order.replay(c,{'positions':[400]*201},11000)
        self.assertTrue(r['success']);self.assertEqual(r['counts'],{'strawberry':10,'almond':10})

    def test_investigation_unlocks_room1_without_any_learning_record(self):
        result = self.send('investigate', ticket=self.ticket('investigate'))
        result['state']=main.status(self.user)
        self.assertEqual(len(result['state']['steps']), 5)
        self.assertEqual(result['state']['stage'], 2)
        self.assertEqual(result['state']['steps'][1]['name'], 'ROOM01 증거분석실')
        self.assertFalse(main.done(self.user, '2'))
        self.send('room1_briefing', ticket=self.ticket('room1'))
        self.send('game_start', game='sql_memory', ticket=self.ticket('room1'))

    def test_learning_hooks_do_not_create_or_advance_main_progress(self):
        from types import SimpleNamespace
        main.complete(self.user, '1')
        session = SimpleNamespace(user=self.user, subject='python', difficulty='beginner', question_type='multiple_choice', total_questions=10, correct_count=10, completed_at=timezone.now())
        main.learning_started(session)
        main.learning_completed(session)
        self.assertFalse(main.done(self.user, '2'))
        self.assertEqual(main.status(self.user)['stage'], 2)

    def test_old_learning_progress_is_preserved_and_excluded_from_steps(self):
        from .models import EventDefinition
        main.complete(self.user, '1')
        EventDefinition.objects.get_or_create(event_uuid=main.uid('2'), defaults={'event_name':'Python 학습교실', 'event_type':'MAIN'})
        old, _ = main.complete(self.user, '2')
        completed_at = old.completed_at
        main.prepare_status(self.user)
        result = main.status(self.user)
        self.assertEqual(result['stage'], 2)
        self.assertEqual(len(result['steps']), 5)
        old.refresh_from_db()
        self.assertEqual(old.completed_at, completed_at)
        retired = EventDefinition.objects.get(pk=main.uid('2'))
        self.assertEqual(retired.event_type, 'ARCHIVED')
        self.assertIsNone(retired.parent_event_id)
        self.assertIsNone(retired.step_order)
        self.assertEqual(list(EventDefinition.objects.filter(parent_event_id=main.uid('campaign'),event_type='MAIN').order_by('step_order').values_list('event_uuid', flat=True)), [main.uid(code) for code, _ in main.STAGES])

    def test_resuming_after_room1_and_final_completion_keeps_history(self):
        for code in ('1', '3'):
            main.complete(self.user, code)
        self.assertEqual(main.status(self.user)['stage'], 3)
        for code in ('4', '5', '6'):
            main.complete(self.user, code)
        final = EventProgress.objects.get(user=self.user, event_id=main.uid('6'))
        final.completion_order = 17
        final.save(update_fields=['completion_order'])
        result = main.status(self.user)
        self.assertTrue(result['completed'])
        self.assertEqual(result['stage'], 6)
        self.assertTrue(all(step['complete'] for step in result['steps']))
        self.assertEqual(result['ranking']['myRank'], 17)
