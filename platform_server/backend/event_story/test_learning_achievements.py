from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import DatabaseError, close_old_connections, connections
from django.http import Http404
from django.test import TestCase, TransactionTestCase
from learning import services
from learning.models import LearningSession, Question, QuestionChoice, SessionQuestion, UserAnswer
from .models import EventProgress
from .achievement_services import PYTHON_ID, SQL_ID, CS_ID, ALL_SUBJECTS_ID, achievement_snapshot
from .test_achievements import seed_definitions

def lesson(user, subject='python', difficulty='intermediate', correct=7, total=10, pending=False, kind='fill_blank'):
    session=LearningSession.objects.create(user=user,subject=subject,question_type=kind,difficulty=difficulty,total_questions=total)
    for index in range(total):
        q=Question.objects.create(subject=subject,question_type=kind,difficulty=difficulty,
            category='achievement',question_text='Question '+str(index),correct_answer='right',explanation='answer')
        choices=[]
        if kind=='multiple_choice':
            choices=[QuestionChoice.objects.create(question=q,choice_no=n+1,choice_text=text)
                for n,text in enumerate(['right','wrong','other','last'])]
        item=SessionQuestion.objects.create(session=session,question=q,question_order=index+1)
        if pending and index==total-1: continue
        passed=index<correct
        UserAnswer.objects.bulk_create([UserAnswer(user=user,session_question=item,
            submitted_answer='right' if passed else 'wrong',is_correct=passed,attempt_no=attempt,
            selected_choice=(choices[0] if passed else choices[1]) if choices else None)
            for attempt in ([1] if passed else [1,2,3])])
    return session

class LearningAchievementTests(TestCase):
    def setUp(self):
        seed_definitions()
        self.user=get_user_model().objects.create_user(username='achievement-learner')
        self.other=get_user_model().objects.create_user(username='other-learner')

    def test_intermediate_and_advanced_passes_use_single_set_and_both_question_types(self):
        for subject,difficulty,kind in [('python','intermediate','fill_blank'),('python','advanced','multiple_choice'),
                                         ('sql','intermediate','multiple_choice'),('sql','advanced','fill_blank')]:
            with self.subTest(subject=subject,difficulty=difficulty,kind=kind):
                session=lesson(self.user,subject,difficulty,kind=kind)
                completed=services.complete_session(self.user,session.pk)
                self.assertEqual(completed.correct_count,7)
                event=PYTHON_ID if subject=='python' else SQL_ID
                self.assertTrue(EventProgress.objects.filter(user=self.user,event_id=event,status='COMPLETED').exists())
        self.assertEqual(EventProgress.objects.filter(user=self.user).count(),2)
        self.assertFalse(EventProgress.objects.filter(user=self.other).exists())

    def test_beginner_and_two_failed_sets_never_count_as_a_pass(self):
        for difficulty,correct in [('beginner',10),('intermediate',6),('intermediate',6)]:
            services.complete_session(self.user,lesson(self.user,difficulty=difficulty,correct=correct).pk)
        self.assertFalse(EventProgress.objects.filter(user=self.user).exists())

    def test_unfinished_or_short_legacy_set_cannot_unlock(self):
        incomplete=lesson(self.user,pending=True)
        with self.assertRaises(services.LearningError):
            services.complete_session(self.user,incomplete.pk)
        incomplete.refresh_from_db();self.assertIsNone(incomplete.completed_at)
        services.complete_session(self.user,lesson(self.user,correct=7,total=7).pk)
        self.assertFalse(EventProgress.objects.filter(user=self.user).exists())

    def test_retry_preserves_first_completion_and_does_not_repeat_notice(self):
        session=lesson(self.user)
        first=services.complete_session(self.user,session.pk)
        self.assertEqual(first.achievement_update['newly_completed'],[str(PYTHON_ID)])
        stamp=EventProgress.objects.get(user=self.user,event_id=PYTHON_ID).completed_at
        second=services.complete_session(self.user,session.pk)
        self.assertEqual(second.achievement_update['newly_completed'],[])
        self.assertEqual(first.completed_at,second.completed_at)
        self.assertEqual(EventProgress.objects.get(user=self.user,event_id=PYTHON_ID).completed_at,stamp)
        self.assertEqual(len(second.achievement_update['achievements']),11)

    def test_wrong_owner_and_forged_score_do_not_unlock(self):
        session=lesson(self.user,correct=6)
        with self.assertRaises(Http404): services.complete_session(self.other,session.pk)
        LearningSession.objects.filter(pk=session.pk).update(correct_count=10)
        completed=services.complete_session(self.user,session.pk)
        self.assertEqual(completed.correct_count,6)
        self.assertFalse(EventProgress.objects.exists())

    def test_failed_achievement_save_rolls_back_lesson_completion(self):
        session=lesson(self.user)
        with patch('event_story.achievement_services.EventProgress.objects.get_or_create',side_effect=DatabaseError('save failure')):
            with self.assertRaises(DatabaseError): services.complete_session(self.user,session.pk)
        session.refresh_from_db()
        self.assertIsNone(session.completed_at)
        self.assertFalse(EventProgress.objects.exists())

    def test_http_completion_returns_server_achievement_and_retry_is_safe(self):
        session=lesson(self.user,subject='sql')
        self.client.force_login(self.user)
        url='/api/learning/sessions/'+str(session.pk)+'/complete/'
        first=self.client.post(url,data='{"correct_count":0}',content_type='application/json')
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json()['newly_completed'],[str(SQL_ID)])
        self.assertEqual(first.json()['correct_count'],7)
        self.assertEqual(self.client.post(url).json()['newly_completed'],[])
        self.assertEqual(len(self.client.get('/api/event-story/achievements/').json()['achievements']),11)

    def test_three_subjects_require_separate_passes_and_allow_mixed_levels_and_types(self):
        for subject,level,kind in [('python','beginner','multiple_choice'),('python','beginner','fill_blank'),('sql','advanced','fill_blank')]:
            services.complete_session(self.user,lesson(self.user,subject,level,kind=kind).pk)
        self.assertFalse(EventProgress.objects.filter(user=self.user,event_id=ALL_SUBJECTS_ID).exists())
        # Another account's CS pass cannot supply the missing subject.
        services.complete_session(self.other,lesson(self.other,'cs','beginner').pk)
        self.assertFalse(EventProgress.objects.filter(user=self.user,event_id=ALL_SUBJECTS_ID).exists())
        failed=services.complete_session(self.user,lesson(self.user,'cs','beginner',correct=6).pk)
        self.assertNotIn(str(ALL_SUBJECTS_ID),(failed.achievement_update or {}).get('newly_completed',[]))
        passed=services.complete_session(self.user,lesson(self.user,'cs','beginner').pk)
        self.assertEqual(passed.achievement_update['newly_completed'],[str(ALL_SUBJECTS_ID)])
        self.assertFalse(EventProgress.objects.filter(user=self.user,event_id=CS_ID).exists())

    def test_cs_intermediate_and_advanced_passes_award_once_and_reject_six_or_short_sets(self):
        for level,score,total in [('beginner',10,10),('intermediate',6,10),('advanced',7,7)]:
            services.complete_session(self.user,lesson(self.user,'cs',level,correct=score,total=total).pk)
        self.assertFalse(EventProgress.objects.filter(user=self.user,event_id=CS_ID).exists())
        passed=services.complete_session(self.user,lesson(self.user,'cs','intermediate',kind='multiple_choice').pk)
        self.assertEqual(passed.achievement_update['newly_completed'],[str(CS_ID)])
        stamp=EventProgress.objects.get(user=self.user,event_id=CS_ID).completed_at
        replay=services.complete_session(self.user,lesson(self.user,'cs','advanced').pk)
        self.assertEqual(replay.achievement_update['newly_completed'],[])
        self.assertEqual(EventProgress.objects.get(user=self.user,event_id=CS_ID).completed_at,stamp)

    def test_final_cs_pass_awards_both_and_retries_preserve_timestamps(self):
        for subject in ['python','sql']:
            services.complete_session(self.user,lesson(self.user,subject,'beginner').pk)
        session=lesson(self.user,'cs','intermediate')
        done=services.complete_session(self.user,session.pk)
        self.assertEqual(done.achievement_update['newly_completed'],[str(CS_ID),str(ALL_SUBJECTS_ID)])
        stamps=list(EventProgress.objects.filter(user=self.user).order_by('event_id').values_list('event_id','completed_at'))
        self.assertEqual(services.complete_session(self.user,session.pk).achievement_update['newly_completed'],[])
        self.assertEqual(list(EventProgress.objects.filter(user=self.user).order_by('event_id').values_list('event_id','completed_at')),stamps)
        self.assertEqual(len(done.achievement_update['achievements']),11)

    def test_existing_completed_lessons_restore_without_changing_lesson_or_reward_records(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        from django.utils import timezone
        from types import SimpleNamespace
        from learning.models import PointLedger
        for subject in ['python','sql','cs']:
            session=lesson(self.user,subject)
            session.completed_at=timezone.now();session.correct_count=7;session.save()
        before=list(LearningSession.objects.values())
        restore=import_module('event_story.migrations.0005_learning_achievements').restore
        restore(apps,SimpleNamespace(connection=connection))
        self.assertEqual(set(EventProgress.objects.filter(user=self.user).values_list('event_id',flat=True)),{CS_ID,ALL_SUBJECTS_ID})
        self.assertTrue(all(p.started_at<=p.completed_at for p in EventProgress.objects.all()))
        self.assertEqual(EventProgress.objects.get(event_id=ALL_SUBJECTS_ID).started_at,min(s['started_at'] for s in before))
        stamps=list(EventProgress.objects.order_by('event_id').values_list('event_id','completed_at'))
        restore(apps,SimpleNamespace(connection=connection))
        self.assertEqual(list(EventProgress.objects.order_by('event_id').values_list('event_id','completed_at')),stamps)
        self.assertEqual(list(LearningSession.objects.values()),before)
        self.assertFalse(PointLedger.objects.exists())


class ConcurrentLearningAchievementTests(TransactionTestCase):
    def test_different_lessons_share_owner_lock_and_unlock_both_subjects(self):
        seed_definitions()
        user=get_user_model().objects.create_user(username='concurrent-achievement')
        lessons=[lesson(user,'python'),lesson(user,'sql')]
        barrier=Barrier(2)
        def complete(pk):
            close_old_connections()
            try:
                owner=get_user_model().objects.get(pk=user.pk)
                barrier.wait(timeout=8)
                return services.complete_session(owner,pk).achievement_update['newly_completed']
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks=[pool.submit(complete,item.pk) for item in lessons]
            results=[task.result(timeout=15) for task in tasks]
        self.assertEqual(sum(len(value) for value in results),2)
        self.assertEqual(EventProgress.objects.filter(user=user,status='COMPLETED').count(),2)

    def test_concurrent_sql_and_cs_passes_complete_the_trio_once(self):
        seed_definitions()
        user=get_user_model().objects.create_user(username='trio-concurrent')
        services.complete_session(user,lesson(user,'python','beginner').pk)
        lessons=[lesson(user,'sql','beginner'),lesson(user,'cs','intermediate')]
        barrier=Barrier(2)
        def complete(pk):
            close_old_connections()
            try:
                owner=get_user_model().objects.get(pk=user.pk);barrier.wait(timeout=8)
                return services.complete_session(owner,pk).achievement_update['newly_completed']
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(complete,[s.pk for s in lessons]))
        self.assertEqual(sum(str(ALL_SUBJECTS_ID) in ids for ids in results),1)
        self.assertEqual(sum(str(CS_ID) in ids for ids in results),1)
        self.assertEqual(EventProgress.objects.filter(user=user,status='COMPLETED').count(),2)
