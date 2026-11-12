import concurrent.futures
import json
import threading
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.db import connection, connections
from django.test import Client, TransactionTestCase
from django.utils import timezone

from accounts.models import ActiveLogin, Position, Profile
from campus.positions import DEFAULT_POSITION, get_position, save_position
from community.models import Comment, Post, PostReadLog
from eventing.catalog import CLASS_GROUPS, FLOORS, ZONES, CATEGORIES, CONTENTS, stable_uuid
from eventing.models import ClassGroup, ContentCategory, ContentItem, ContentOpenRequestLog, MapFloor, MapZone, PlatformSession
from learning import services
from learning.models import PointLedger, PointWallet, Question, QuestionChoice, UserAnswer


def seed_catalog():
    for code, name in CLASS_GROUPS:
        ClassGroup.objects.get_or_create(class_group_uuid=stable_uuid('class-group', code), defaults={'code': code, 'name': name})
    for code, name in FLOORS:
        MapFloor.objects.get_or_create(floor_uuid=stable_uuid('floor', code), defaults={'code': code, 'name': name})
    for floor, code, kind, name in ZONES:
        MapZone.objects.get_or_create(zone_uuid=stable_uuid('zone', code), defaults={
            'floor_id': stable_uuid('floor', floor), 'code': code, 'zone_type': kind, 'name': name})
    for code, domain, name in CATEGORIES:
        ContentCategory.objects.get_or_create(category_uuid=stable_uuid('category', code), defaults={'code': code, 'domain': domain, 'name': name})
    for code, category, title, kind, url, zone in CONTENTS:
        ContentItem.objects.get_or_create(content_uuid=stable_uuid('content', code), defaults={
            'code': code, 'category_id': stable_uuid('category', category), 'title': title,
            'content_kind': kind, 'external_url': url, 'zone_id': stable_uuid('zone', zone)})

    from django.apps import apps
    from importlib import import_module
    import_module('eventing.migrations.0013_cs_learning_catalog').add_cs(apps, None)


class PostgreSQLPlatformTests(TransactionTestCase):
    def setUp(self):
        seed_catalog()
        self.user = get_user_model().objects.create_user(username='student', password='TestPassword!4820')
        self.profile = Profile.objects.create(user=self.user, nickname='학생', cohort=ClassGroup.objects.get(code='mlo-1'))
        self.client = Client()
        self.assertTrue(self.client.login(username='student', password='TestPassword!4820'))
        self.client.get('/api/accounts/session/status/')

    def post(self, path, data):
        return self.client.post(path, data=json.dumps(data), content_type='application/json')

    def quiz(self):
        for i in range(10):
            question = Question.objects.create(subject='python', question_type='multiple_choice', difficulty='beginner',
                category='syntax', question_text=f'Question {i}', correct_answer='one', explanation='first choice')
            for n, text in enumerate(['one','two','three','four'], 1):
                QuestionChoice.objects.create(question=question, choice_no=n, choice_text=text)
        response = self.post('/api/learning/sessions/', {'subject':'python','question_type':'multiple_choice','difficulty':'beginner'})
        self.assertEqual(response.status_code, 201, response.content)
        session = response.json()['session_id']
        response = self.client.get(f'/api/learning/sessions/{session}/questions/1/')
        self.assertEqual(response.status_code, 200, response.content)
        return session, response.json()['question']

    def test_actual_postgresql_and_shared_account_key(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertIsInstance(self.user.pk, uuid.UUID)
        self.assertEqual(self.profile.pk, self.user.pk)
        self.assertEqual(self.profile.cohort.code, 'mlo-1')
        for name in ['id','user_uuid','last_position','last_movement_event_at','movement_event_pending','created_at']:
            with self.assertRaises(FieldDoesNotExist): Profile._meta.get_field(name)
        self.assertIn('eventing_eventoutbox', connection.introspection.table_names())  # 사건 outbox(CDC가 읽음) — 열·키는 eventing.test_outbox
        self.assertNotIn('community_mealcomment', connection.introspection.table_names())
        self.assertIn('content_open_request_log', connection.introspection.table_names())
        with connection.cursor() as cursor:
            cursor.execute("""SELECT t.typname FROM pg_constraint c
                JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey)
                JOIN pg_type t ON t.oid=a.atttypid
                WHERE c.contype='f' AND c.confrelid='auth_user'::regclass""")
            types = [row[0] for row in cursor.fetchall()]
        self.assertGreaterEqual(len(types), 14)
        self.assertEqual(set(types), {'uuid'})

    def test_signup_and_profile_setup_use_real_cohort_relation(self):
        client = Client()
        response = client.post('/accounts/signup/', {'username':'new-student', 'password1':'BetterPassword!4820',
            'password2':'BetterPassword!4820', 'cohort':'mlo-1'})
        self.assertEqual(response.status_code, 302, response.content)
        user = get_user_model().objects.get(username='new-student')
        self.assertEqual(user.campus_profile.cohort.code, 'mlo-1')
        self.assertFalse(user.campus_profile.setup_complete)
        response = client.post('/accounts/setup/', {'nickname':'새학생','avatar':'male-1'})
        self.assertEqual(response.status_code, 302, response.content)
        user.campus_profile.refresh_from_db()
        self.assertTrue(user.campus_profile.setup_complete)
        self.assertTrue(PointWallet.objects.filter(profile_id=user.pk).exists())

    def test_missing_profile_nickname_creation_uses_insert(self):
        user = get_user_model().objects.create_user(username='no-profile', password='AnotherPassword!4820')
        client = Client(); client.force_login(user)
        response = client.post('/accounts/nickname/', {'nickname':'새닉네임'})
        self.assertEqual(response.status_code, 302, response.content)
        self.assertEqual(Profile.objects.get(user=user).nickname, '새닉네임')

    def test_manual_uuid_login_and_latest_session(self):
        client = Client()
        client.post('/accounts/login/', {'username':'student', 'password':'TestPassword!4820'})
        first_key = client.session.session_key
        other = Client(); other.post('/accounts/login/', {'username':'student', 'password':'TestPassword!4820'})
        self.assertNotEqual(first_key, other.session.session_key)
        self.assertEqual(ActiveLogin.objects.get(pk=self.user.pk).session_key, other.session.session_key)
        self.assertFalse(client.get('/api/accounts/session/status/').json()['authenticated'])
        other.post('/accounts/logout/')
        self.assertTrue(PlatformSession.objects.filter(user=self.user, end_reason='LOGOUT').exists())
        custom = uuid.uuid4()
        get_user_model().objects.create_user(id=custom, username='manual', password='ManualPassword!4820')
        self.assertTrue(Client().login(username='manual', password='ManualPassword!4820'))

    def test_latest_position_and_lossless_json_survive_postgresql(self):
        position={'floor':'rooftop','x':-0.0,'y':100.125,'dir':'left'}
        self.assertTrue(save_position(self.user, position))
        self.assertFalse(save_position(self.user, DEFAULT_POSITION, timezone.now()-timedelta(minutes=1)))
        self.assertEqual(get_position(self.user), position)
        self.assertEqual(Position.objects.filter(profile_id=self.user.pk).count(), 1)
        with connection.cursor() as cursor:
            cursor.execute('SELECT last_position::text FROM accounts_position WHERE user_uuid=%s', [self.user.pk])
            self.assertIn('-0.0', cursor.fetchone()[0])

    def test_board_uuid_keys_numeric_routes_replies_and_read_idempotency(self):
        response=self.post('/api/community/posts/', {'category':'free','title':'제목','body':'본문'})
        self.assertEqual(response.status_code, 201, response.content)
        post_id=response.json()['post']['id']
        self.assertIsInstance(post_id, int)
        item=Post.objects.get(id=post_id); self.assertEqual(item.pk, item.post_uuid)
        key=uuid.uuid4(); url=f'/api/community/posts/{post_id}/?read_uuid={key}'
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(PostReadLog.objects.filter(read_uuid=key).count(), 1)
        self.assertEqual(self.client.get(f'/api/community/posts/{post_id}/?read_uuid=bad').status_code, 400)
        comment=self.post(f'/api/community/posts/{post_id}/comments/', {'body':'댓글'})
        self.assertEqual(comment.status_code, 201, comment.content)
        data=comment.json()['comment']; obj=Comment.objects.get(id=data['id'])
        self.assertEqual(obj.pk, obj.comment_uuid); self.assertEqual(obj.post_id, item.pk)
        reply=self.post(f'/api/community/posts/{post_id}/comments/', {'body':'답글','parentCommentUuid':data['commentUuid']})
        self.assertEqual(reply.status_code, 201, reply.content)
        self.assertEqual(Comment.objects.get(id=reply.json()['comment']['id']).parent_id, obj.pk)
        response=self.client.delete(f'/api/community/posts/{post_id}/')
        self.assertEqual(response.status_code, 200, response.content)
        item.refresh_from_db(); self.assertIsNotNone(item.deleted_at)
        self.assertTrue(PostReadLog.objects.filter(post=item).exists())

    def test_concurrent_read_retries_store_one_row_and_reject_uuid_reuse(self):
        post = Post.objects.create(author=self.user, category='free', title='읽기', body='본문')
        key = uuid.uuid4()
        path = f'/api/community/posts/{post.id}/?read_uuid={key}'
        cookie = self.client.cookies.copy()
        ready = threading.Barrier(2)

        def read():
            connections.close_all()
            client = Client(); client.cookies = cookie.copy()
            try:
                ready.wait(timeout=5)
                return client.get(path).status_code
            finally:
                connections.close_all()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(lambda _: read(), range(2))), [200, 200])
        record = PostReadLog.objects.get(read_uuid=key)
        self.assertEqual(PostReadLog.objects.count(), 1)
        Post.objects.filter(pk=post.pk).update(revision=2)
        self.assertEqual(self.client.get(path).status_code, 200)
        record.refresh_from_db(); self.assertEqual(record.revision, 1)
        other = Post.objects.create(author=self.user, category='free', title='다른 글', body='본문')
        self.assertEqual(self.client.get(f'/api/community/posts/{other.id}/?read_uuid={key}').status_code, 409)
        user = get_user_model().objects.create_user(username='other-reader')
        client = Client(); client.force_login(user)
        self.assertEqual(client.get(path).status_code, 409)
        self.assertEqual(PostReadLog.objects.count(), 1)

    def test_content_open_requests_are_append_only_with_session_snapshot(self):
        for _ in range(2):
            response=self.client.get('/content/gomoku/open/')
            self.assertEqual(response.status_code, 302, response.content)
        logs=ContentOpenRequestLog.objects.filter(user=self.user)
        self.assertEqual(logs.count(), 2)
        row=logs.first(); self.assertEqual(row.class_group_id, self.profile.cohort_id)
        self.assertEqual(row.category.code, 'game'); self.assertEqual(row.domain, 'REST')
        self.assertEqual(row.platform_session.user_id, self.user.pk)

    def test_answer_retry_wrong_then_correct_and_points_remain_atomic(self):
        session, question=self.quiz()
        path=f'/api/learning/sessions/{session}/questions/1/answers/'
        wrong=next(c for c in question['choices'] if c['choice_text']=='two')
        right=next(c for c in question['choices'] if c['choice_text']=='one')
        data={'answer':'two','choice_uuid':wrong['choice_uuid'],'submission_request_uuid':str(uuid.uuid4())}
        first=self.post(path,data); self.assertEqual(first.status_code,201,first.content)
        again=self.post(path,data); self.assertEqual(first.json()['attempt_uuid'],again.json()['attempt_uuid'])
        self.assertEqual(UserAnswer.objects.count(),1); self.assertEqual(PointLedger.objects.count(),0)
        data={'answer':'one','choice_uuid':right['choice_uuid'],'submission_request_uuid':str(uuid.uuid4())}
        for _ in range(2): self.assertEqual(self.post(path,data).status_code,201)
        self.assertEqual(UserAnswer.objects.count(),2); self.assertEqual(PointLedger.objects.count(),1)
        self.assertEqual(PointWallet.objects.get(profile=self.profile).balance,20)
        data['answer']='two';data['choice_uuid']=wrong['choice_uuid']
        self.assertEqual(self.post(path,data).status_code,409)
        self.assertEqual(UserAnswer.objects.order_by('attempt_no').last().platform_session.user_id,self.user.pk)

    def test_concurrent_same_answer_does_not_duplicate_award(self):
        session, question=self.quiz()
        choice=next(c for c in question['choices'] if c['choice_text']=='one')
        key=str(uuid.uuid4()); platform_session=self.client.session['platform_session_uuid']
        def submit():
            connections.close_all()
            try:
                user=get_user_model().objects.get(pk=self.user.pk)
                return str(services.submit_answer(user,session,1,'one',key,choice_uuid=choice['choice_uuid'],platform_session_uuid=platform_session).attempt_uuid)
            finally: connections.close_all()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:submit(),range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(UserAnswer.objects.count(),1)
        self.assertEqual(PointLedger.objects.count(),1);self.assertEqual(PointWallet.objects.get(profile=self.profile).balance,20)

    def test_meal_comments_removed_but_photo_api_retained(self):
        self.assertEqual(self.client.get('/api/community/meal-comments/').status_code,404)
        with patch('campus.views.latest_menu',return_value=None):
            response=self.client.get('/api/board/menu/')
        self.assertEqual(response.status_code,200);self.assertIsNone(response.json()['imageUrl'])
