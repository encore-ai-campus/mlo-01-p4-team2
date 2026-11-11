from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase, SimpleTestCase, override_settings
from django.db import transaction
from django.utils import timezone
from learning.models import Question, LearningSession, UserAnswer
from learning import services as learning
from learning.tests.legacy_fixtures import prepare_event_context
from campus.motion import MotionController, ControlError, world
from . import services, access
from .models import StoryProgress, StoryRecord


@override_settings(EVENT_STORY_DATE='2026-11-11', EVENT_STORY_PREVIEW=True, DEBUG=True)
class StoryTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='story-test')
        self.other=get_user_model().objects.create_user(username='story-other')

    def act(self, action, **data):
        if action in access.TARGETS:
            data.setdefault('ticket', signing.dumps({'user': self.user.pk, 'target': action}, salt=access.SALT))
        return services.act(self.user, {'action':action, **data})

    def clues(self):
        self.act('start'); self.act('board'); self.act('wrapper'); self.act('count')

    def pool(self):
        for i in range(10):
            Question.objects.create(subject='python', question_type='fill_blank', difficulty='beginner', category='test', question_text=f'{i}: fill append', correct_answer='append')

    def lesson(self, score=7):
        lesson=learning.start_learning_session(self.user, 'fill_blank', 'beginner', 'python')
        for item in lesson.session_questions.all():
            correct=item.question_order<=score
            for attempt in range(1,2 if correct else 4):
                UserAnswer.objects.create(user=self.user, session_question=item, submitted_answer='append' if correct else 'bad', is_correct=correct, attempt_no=attempt)
        return learning.complete_session(self.user, lesson.pk)

    def test_status_does_not_start_or_complete_clues(self):
        state=services.status(self.user)
        self.assertFalse(state['started']); self.assertFalse(StoryProgress.objects.exists())
        self.act('start'); self.act('board')
        self.assertEqual(services.status(self.user)['stage'], 2)
        self.assertNotIn('wrapper', services.status(self.user)['flags'])

    def test_clues_any_order_and_idempotent_retries(self):
        self.act('start'); self.act('start'); self.act('board')
        self.act('count'); self.act('count')
        self.assertEqual(services.status(self.user)['stage'], 2)
        self.act('wrapper')
        self.assertEqual(services.status(self.user)['stage'], 3)
        self.assertEqual(StoryRecord.objects.filter(key='count').count(),1)
        self.assertEqual(StoryRecord.objects.filter(key='start').count(),1)

    def test_prerequisites_and_forged_ticket_rejected(self):
        self.act('start')
        with self.assertRaises(learning.LearningError):self.act('quantity', answer=2)
        with self.assertRaises(learning.LearningError):self.act('board', ticket='fake')
        other=signing.dumps({'user':self.other.pk,'target':'board'},salt=access.SALT)
        with self.assertRaises(learning.LearningError):self.act('board',ticket=other)
        self.assertEqual(services.status(self.user)['stage'],1)

    def test_existing_learning_pass_advances_fail_does_not(self):
        self.clues();self.pool()
        self.lesson(6);self.assertEqual(services.status(self.user)['stage'],3)
        self.lesson(7);self.assertEqual(services.status(self.user)['stage'],4)
        self.assertEqual(services.status(self.user)['pending'],'memo')
        self.assertEqual(StoryRecord.objects.filter(kind='learning_result').count(),2)

    def test_pre_clue_lesson_cannot_advance(self):
        self.pool()
        lesson=learning.start_learning_session(self.user,'fill_blank','beginner','python')
        self.clues()
        lesson.completed_at=timezone.now();lesson.correct_count=10;lesson.save()
        with transaction.atomic():services.learning_completed(lesson)
        self.assertEqual(services.status(self.user)['stage'],3)

    def test_full_story_reconnect_wrong_answers_and_no_extra_rewards(self):
        self.clues();self.pool();self.lesson()
        self.act('memo')
        self.assertFalse(self.act('quantity',answer=1)['correct'])
        self.assertEqual(services.status(self.user)['stage'],4)
        self.act('quantity',answer=2)
        self.assertFalse(self.act('ethics',answer=3)['correct'])
        self.act('ethics',answer=2)
        self.assertEqual(services.status(self.user)['pending'],'apology')
        self.act('apology')
        self.assertFalse(services.status(self.user)['completed'])
        self.act('finish');self.act('finish')
        self.assertTrue(services.status(self.user)['completed'])
        self.assertEqual(StoryRecord.objects.filter(kind='story_finish').count(),1)
        self.assertFalse(services.status(self.other)['started'])
        from learning.models import PointLedger
        self.assertEqual(PointLedger.objects.count(),0)

    def test_transaction_failure_never_advances_progress(self):
        self.act('start')
        with patch('event_story.services.log',side_effect=RuntimeError('DB failure')):
            with self.assertRaises(RuntimeError):self.act('board')
        self.assertFalse(services.status(self.user)['flags'].get('board'))

    @override_settings(EVENT_STORY_PREVIEW=False)
    def test_kst_window_is_half_open(self):
        kst=ZoneInfo('Asia/Seoul')
        self.assertEqual(services.window(datetime(2026,11,10,23,59,59,tzinfo=kst))['phase'],'before')
        self.assertEqual(services.window(datetime(2026,11,11,tzinfo=kst))['phase'],'open')
        self.assertEqual(services.window(datetime(2026,11,12,tzinfo=kst))['phase'],'closed')
        with patch('event_story.services.timezone.now',return_value=datetime(2026,11,12,tzinfo=kst)):
            with self.assertRaises(learning.LearningError):self.act('start')

    @override_settings(DEBUG=False)
    def test_preview_cannot_open_production(self):
        self.assertFalse(services.window()['preview'])

    def test_http_authentication_and_account_isolation(self):
        self.assertEqual(self.client.get('/api/event-story/status/').status_code,401)
        self.clues()
        self.client.force_login(self.other)
        response=self.client.get('/api/event-story/status/')
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['started'])


class StoryGeometryTests(SimpleTestCase):
    def test_server_proximity_proof_and_walkable_clue_approaches(self):
        c=MotionController(dict(floor='campus',x=590,y=445,dir='up'))
        for target, point in [('wrapper',(590,445)),('count',(700,445)),('board',(1250,185))]:
            c.pose.update(x=point[0],y=point[1]);self.assertTrue(c.world.walkable(*point))
            proof=access.issue(7,c,{'target':target})
            self.assertEqual(signing.loads(proof['ticket'],salt=access.SALT)['target'],target)
        c.pose.update(x=2000,y=900)
        with self.assertRaises(ControlError):access.issue(7,c,{'target':'wrapper'})

    def test_table_only_blocks_its_base_and_booth_remains_accessible(self):
        floor=world('campus')
        self.assertFalse(floor.walkable(600,380))
        self.assertTrue(floor.walkable(600,340))
        self.assertTrue(floor.walkable(750,400))
        c=MotionController(dict(floor='rooftop',x=692,y=897,dir='up'))
        self.assertEqual(access.issue(7,c,{'target':'ethics'})['target'],'ethics')
