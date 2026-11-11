from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import connection
from django.test import TestCase,TransactionTestCase,override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from event_story import main,recipe
from event_story.access import SALT
from event_story.achievement_services import TYPING_ID,PYTHON_ID,RECIPE_ID
from event_story.game_services import start_game,finish_game
from event_story.models import EventDefinition,EventProgress,GamePlay,StoryRecord
from event_story.test_achievements import seed_definitions
from event_story.test_learning_achievements import lesson
from eventing.models import PlatformSession
from learning import services as learning
from learning.models import LearningSession
from tests.factories import create_profile


def ticket(user,target):
    return signing.dumps({'user':str(user.pk),'target':target},salt=SALT)


@override_settings(DEBUG=True,EVENT_STORY_PREVIEW=True)
class CommittedResponseTests(TransactionTestCase):
    def setUp(self):
        seed_definitions();main.catalog()
        self.user=get_user_model().objects.create_user(username='committed-response')
        self.session=PlatformSession.objects.create(user=self.user,started_at=timezone.now(),last_seen_at=timezone.now())
        opened=patch('event_story.main.ensure_open');opened.start();self.addCleanup(opened.stop)

    def failed_response(self,*args,**kwargs):
        self.assertFalse(connection.in_atomic_block)
        raise RuntimeError('response unavailable')

    def test_main_commit_precedes_snapshot_and_retry_keeps_rank(self):
        for code in ('1','3','4','5'):main.complete(self.user,code,self.session.pk)
        data={'action':'deliver','ticket':ticket(self.user,'final-guide')}
        with patch('event_story.main.status',side_effect=self.failed_response):
            with self.assertRaises(RuntimeError):main.action(self.user,data,self.session.pk)
        saved=EventProgress.objects.get(user=self.user,event_id=main.uid('6'))
        self.assertEqual((saved.completion_order,saved.session_id),(1,self.session.pk))
        replay=main.action(self.user,data,self.session.pk)
        self.assertEqual(replay['state']['ranking']['myRank'],1)
        self.assertEqual(EventProgress.objects.get(pk=saved.pk).completed_at,saved.completed_at)

    def test_notice_read_failure_does_not_consume_the_daily_prologue(self):
        with patch('event_story.main.status',side_effect=self.failed_response):
            with self.assertRaises(RuntimeError):main.action(self.user,{'action':'notice'},self.session.pk)
        self.assertFalse(StoryRecord.objects.filter(progress__user=self.user,progress__event_key='pepero-2026-ui-notices').exists())
        self.assertTrue(main.action(self.user,{'action':'notice'},self.session.pk)['show_notice'])
        self.assertFalse(main.action(self.user,{'action':'notice'},self.session.pk)['show_notice'])

    def test_game_commit_precedes_snapshot_and_retry_is_idempotent(self):
        play=start_game(self.user,'typing',self.session.pk)
        GamePlay.objects.filter(pk=play['play_uuid']).update(started_at=timezone.now()-timedelta(seconds=2))
        data={'status':'SUCCESS','duration_ms':1000}
        with patch('event_story.game_services.achievement_snapshot',side_effect=self.failed_response):
            with self.assertRaises(RuntimeError):finish_game(self.user,play['play_uuid'],self.session.pk,data)
        saved=GamePlay.objects.get(pk=play['play_uuid'])
        self.assertEqual(saved.status,'SUCCESS')
        self.assertTrue(EventProgress.objects.filter(user=self.user,event_id=TYPING_ID,session=self.session).exists())
        replay=finish_game(self.user,play['play_uuid'],self.session.pk,data)
        self.assertEqual(replay['ended_at'],saved.ended_at.isoformat());self.assertEqual(replay['newly_completed'],[])

    def test_lesson_commit_precedes_snapshot_and_retry_keeps_completion(self):
        item=lesson(self.user)
        with patch('event_story.achievement_services.achievement_snapshot',side_effect=self.failed_response):
            with self.assertRaises(RuntimeError):learning.complete_session(self.user,item.pk,self.session.pk)
        saved=LearningSession.objects.get(pk=item.pk)
        self.assertIsNotNone(saved.completed_at)
        self.assertTrue(EventProgress.objects.filter(user=self.user,event_id=PYTHON_ID,session=self.session).exists())
        replay=learning.complete_session(self.user,item.pk,self.session.pk)
        self.assertEqual(replay.completed_at,saved.completed_at);self.assertEqual(replay.achievement_update['newly_completed'],[])

    def test_recipe_commit_precedes_snapshot_and_retry_keeps_one_log(self):
        main.complete(self.user,'6',self.session.pk)
        def act(action):
            return recipe.action(self.user,{'action':action,'ticket':ticket(self.user,'recipe-note' if action=='acquire' else 'recipe-guide'),
                **({'topping':'thanks'} if action=='topping' else {})},self.session.pk)
        for action in ('acquire','recognize','start','topping'):act(action)
        with patch('event_story.recipe.achievement_snapshot',side_effect=self.failed_response):
            with self.assertRaises(RuntimeError):act('finish')
        saved=EventProgress.objects.get(user=self.user,event_id=RECIPE_ID)
        self.assertEqual(saved.session_id,self.session.pk)
        self.assertTrue(recipe.status(self.user)['secretCompleted'])
        self.assertEqual(act('finish')['newly_completed'],[])
        self.assertEqual(EventProgress.objects.get(pk=saved.pk).completed_at,saved.completed_at)
        self.assertEqual(StoryRecord.objects.filter(progress__user=self.user,key='finish').count(),1)


@override_settings(DEBUG=True,EVENT_STORY_PREVIEW=True)
class ReadOnlyStateTests(TestCase):
    def setUp(self):
        main.catalog();self.user=get_user_model().objects.create_user(username='query-state')

    def test_clean_catalog_has_no_update_and_status_is_pure(self):
        with CaptureQueriesContext(connection) as queries:main.catalog()
        self.assertEqual(len(queries),2)
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
        EventProgress.objects.create(user=self.user,event_id=main.uid('1'),status='IN_PROGRESS')
        with CaptureQueriesContext(connection) as queries:state=main.status(self.user)
        self.assertEqual(len(queries),4)
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
        self.assertFalse(state['steps'][0]['complete'])

    def test_ranking_top3_pages_and_missing_profile_have_constant_queries(self):
        users=[self.user]+[get_user_model().objects.create_user(username=f'rank-{i}') for i in range(2,27)]
        create_profile(user=users[1],nickname='두 번째')
        EventProgress.objects.bulk_create([EventProgress(user=user,event_id=main.uid('6'),status='COMPLETED',
            completed_at=timezone.now(),completion_order=i) for i,user in enumerate(users,1)])
        with self.assertNumQueries(4):state=main.status(self.user)
        self.assertEqual(len(state['ranking']['top3']),3)
        self.assertEqual(state['ranking']['top3'][0]['nickname'],self.user.username)
        self.assertEqual(state['ranking']['top3'][1]['nickname'],'두 번째')
        with self.assertNumQueries(2):first=main.ranking(self.user)
        self.assertEqual(len(first['entries']),25);self.assertTrue(first['hasMore'])
        with self.assertNumQueries(2):last=main.ranking(self.user,2)
        self.assertEqual([row['rank'] for row in last['entries']],[26]);self.assertFalse(last['hasMore'])
        EventProgress.objects.filter(user=users[-1],event_id=main.uid('6')).delete()
        self.assertFalse(main.ranking(self.user)['hasMore'])

    def test_http_preparation_recovers_legacy_play_without_hidden_status_write(self):
        for code in ('1','3'):main.complete(self.user,code)
        session=PlatformSession.objects.create(user=self.user,started_at=timezone.now(),last_seen_at=timezone.now())
        ended=timezone.now()-timedelta(hours=1)
        GamePlay.objects.create(user=self.user,session=session,game_id=main.uid('game/kkuwang_touch'),event_id=main.uid('4'),
            status='FINISHED',duration_ms=11000,ended_at=ended)
        self.assertFalse(main.status(self.user)['steps'][2]['complete'])
        self.client.force_login(self.user)
        response=self.client.get('/api/event-main/status/')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['steps'][2]['complete'])
        saved=EventProgress.objects.get(user=self.user,event_id=main.uid('4'))
        self.assertEqual((saved.completed_at,saved.session_id),(ended,session.pk))
        main.prepare_status(self.user)
        self.assertEqual(EventProgress.objects.get(pk=saved.pk).completed_at,ended)
