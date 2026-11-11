import json
import uuid
from collections import Counter
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.http import Http404
from django.utils import timezone

from learning import services
from learning.bank_validation import validate_bank
from learning.grading import answers_match
from learning.management.commands.import_learning_bank import apply_rows, snapshot_existing, digest_snapshot
from learning.models import Question, LearningSession, SessionQuestion, UserAnswer, PointLedger
from learning.tests.legacy_fixtures import prepare_event_context
from event_story import main, services as story
from event_story.models import EventProgress, StoryProgress, StoryRecord
from eventing.models import ContentOpenRequestLog

BASE = Path(__file__).resolve().parents[1]
ROWS = json.loads((BASE/'data/cs_questions.json').read_text())


class CSBankStructureTests(SimpleTestCase):
    def test_six_pools_and_five_fields_each_have_the_promised_counts(self):
        report = validate_bank(ROWS)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['total'], 1200)
        self.assertEqual(report['target'], 1200)
        counts = Counter((r['difficulty'], r['question_type'], r['category']) for r in ROWS)
        self.assertEqual(len(counts), 30)
        self.assertEqual(set(counts.values()), {40})
        for row in ROWS:
            if row['question_type'] == 'fill_blank':
                self.assertEqual(row['code_snippet'].count('____'), 1)
                self.assertTrue(answers_match('cs','fill_blank',' '+row['correct_answer'].swapcase()+' ',row['correct_answer']))

    def test_cs_normalization_does_not_change_python_or_sql(self):
        self.assertTrue(answers_match('cs','fill_blank','  cpu  ','CPU'))
        self.assertTrue(answers_match('cs','fill_blank',' 프로세스 ','프로세스'))
        self.assertFalse(answers_match('cs','fill_blank','쓰레드','스레드'))
        self.assertFalse(answers_match('python','fill_blank','true','True'))
        self.assertFalse(answers_match('sql','fill_blank',"SELECT 'a'","SELECT 'A'"))
        self.assertTrue(answers_match('sql','fill_blank','select','SELECT'))

    def test_empty_file_is_not_a_complete_bank(self):
        self.assertTrue(validate_bank([])['errors'])


class CSBankImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        apply_rows(ROWS)

    def test_repeat_import_preserves_identifiers_and_history(self):
        user = get_user_model().objects.create_user(username='cs-history')
        q = Question.objects.filter(subject='cs',question_type='fill_blank').first()
        session = LearningSession.objects.create(user=user,subject='cs',question_type='fill_blank',difficulty=q.difficulty,total_questions=1)
        item = SessionQuestion.objects.create(session=session,question=q,question_order=1)
        UserAnswer.objects.create(user=user,session_question=item,submitted_answer=q.correct_answer,is_correct=True)
        before = digest_snapshot(snapshot_existing())
        history = list(UserAnswer.objects.values())
        self.assertEqual(apply_rows(ROWS)[0], 0)
        self.assertEqual(digest_snapshot(snapshot_existing()), before)
        self.assertEqual(list(UserAnswer.objects.values()), history)

    def test_python_sql_import_can_follow_cs_and_never_rewrite_cs(self):
        original = list(Question.objects.filter(subject='cs').order_by('pk').values())
        call_command('seed_learning_questions', file=BASE/'sample_questions.json', stdout=StringIO())
        call_command('seed_learning_questions', file=BASE/'data/initial_subject_bank.json', stdout=StringIO())
        expanded = json.loads((BASE/'data/expanded_questions_20261016.json').read_text())
        self.assertEqual(apply_rows(expanded)[0], 2280)
        self.assertEqual(Question.objects.count(), 3600)
        self.assertEqual(list(Question.objects.filter(subject='cs').order_by('pk').values()), original)
        self.assertEqual(apply_rows(expanded)[0], 0)
        self.assertEqual(apply_rows(ROWS)[0], 0)

    def test_changed_existing_code_is_rejected_and_import_is_read_only_by_default(self):
        before = digest_snapshot(snapshot_existing())
        modified = [{**r, 'explanation':'changed'} if i == 0 else r for i,r in enumerate(ROWS)]
        with self.assertRaises(CommandError): apply_rows(modified)
        out = StringIO()
        call_command('import_learning_bank', file=BASE/'data/cs_questions.json', stdout=out)
        self.assertEqual(json.loads(out.getvalue())['target_active'], 1200)
        self.assertEqual(digest_snapshot(snapshot_existing()), before)


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class CSLearningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        apply_rows(ROWS)
        cls.user = get_user_model().objects.create_user(username='cs-student')
        cls.other = get_user_model().objects.create_user(username='cs-other')
        prepare_event_context(cls.user)

    def setUp(self):
        self.client.force_login(self.user)

    def post(self, path, data):
        return self.client.post(path, json.dumps(data), content_type='application/json')

    def test_all_six_pools_select_ten_unique_questions_and_cannot_leak_to_other_account(self):
        for level in ['beginner','intermediate','advanced']:
            for kind in ['multiple_choice','fill_blank']:
                session = services.start_learning_session(self.user,kind,level,'cs')
                items = list(session.session_questions.select_related('question'))
                self.assertEqual(len({i.question_id for i in items}), 10)
                self.assertTrue(all((i.question.subject,i.question.difficulty,i.question.question_type)==('cs',level,kind) for i in items))
                data = self.client.get(f'/api/learning/sessions/{session.pk}/questions/1/').json()
                self.assertNotIn('correct_answer', data['question'])
                with self.assertRaises(Http404):
                    services.owned_session(self.other,session.pk)

    def test_content_open_records_cs_zone_and_owner(self):
        response = self.post('/api/learning/content-open/', {'subject':'cs','question_type':'fill_blank'})
        self.assertEqual(response.status_code, 201, response.content)
        log = ContentOpenRequestLog.objects.get()
        self.assertEqual(log.user_id,self.user.pk)
        self.assertEqual(log.content.code,'cs-fill-blank')
        self.assertEqual(log.zone.code,'cs-classroom')

    def test_scores_six_and_seven_retry_rewards_and_no_story_or_achievement_unlock(self):
        main.catalog()
        main.complete(self.user,'1')
        StoryProgress.objects.create(user=self.user,event_key=story.key(),started_at=timezone.now(),flags={'wrapper':True,'count':True})
        for score in [6,7]:
            session = services.start_learning_session(self.user,'fill_blank','beginner','cs')
            base = f'/api/learning/sessions/{session.pk}/'
            for order in range(1,11):
                item = services.get_current_question(self.user,session.pk,order)
                answer = item.question.correct_answer.swapcase() if order <= score else 'not-a-valid-cs-term'
                payload = {'answer':' '+answer+' ', 'submission_request_uuid':str(uuid.uuid4())}
                path = base+f'questions/{order}/answers/'
                response = self.post(path,payload)
                self.assertEqual(response.status_code,201,response.content)
                if order == 1:
                    self.assertEqual(response.json(),self.post(path,payload).json())
                if order > score:
                    for _ in range(2):
                        payload['submission_request_uuid']=str(uuid.uuid4())
                        self.assertEqual(self.post(path,payload).status_code,201)
            result=self.post(base+'complete/',{}).json()
            self.assertEqual(result['correct_count'],score)
            self.assertEqual(result['passed'],score>=7)
            self.assertEqual(result['points_summary']['granted_total'],score*20)
            self.assertEqual(result.get('newly_completed',[]),[])
            self.assertEqual(self.post(base+'complete/',{}).json()['correct_count'],score)
        self.assertEqual(PointLedger.objects.count(),13)
        self.assertFalse(main.done(self.user,'2'))
        self.assertFalse(EventProgress.objects.filter(user=self.user).exclude(event_id=main.uid('1')).exists())
        self.assertFalse(StoryRecord.objects.filter(progress__user=self.user,kind='learning_result').exists())

    def test_db_subject_constraint_accepts_cs_and_rejects_unknown_subject(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Question.objects.filter(pk=Question.objects.first().pk).update(subject='unknown')
