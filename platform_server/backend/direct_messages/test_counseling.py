from tests.factories import create_profile
import json
import uuid
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, override_settings
from accounts.models import Profile
from . import views

@override_settings(CAMPUS_DM_ENABLED=True)
class CounselingTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.student = self.user('student')
        self.manager = self.user('manager', is_staff=True)
        self.other = self.user('other')
        self.user('inactive', is_staff=True, is_active=False)
        self.user('incomplete', is_staff=True, complete=False)
    def user(self, name, complete=True, **kwargs):
        user = get_user_model().objects.create_user(username=name, **kwargs)
        create_profile(user=user, nickname=name, setup_complete=complete)
        return user
    def request(self, user, body=None):
        request = self.factory.get('/api/messages/counselors/') if body is None else self.factory.post('/api/messages/send/',data=json.dumps(body),content_type='application/json')
        request.user = user
        return request
    def test_only_active_ready_managers(self):
        response = views.counselors(self.request(self.student))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['id'] for r in json.loads(response.content)['counselors']], [str(self.manager.campus_profile.user_uuid)])
    def test_self_excluded(self):
        self.assertEqual(json.loads(views.counselors(self.request(self.manager)).content)['counselors'], [])
    def test_login_required(self):
        self.assertEqual(views.counselors(self.request(AnonymousUser())).status_code, 401)
    def test_get_only(self):
        self.assertEqual(views.counselors(self.request(self.student, {})).status_code, 405)
    @override_settings(CAMPUS_DM_ENABLED=False)
    def test_disabled(self):
        self.assertEqual(views.counselors(self.request(self.student)).status_code, 404)
    def test_message_saved_and_private(self):
        response = views.send(self.request(self.student, {'recipient':str(self.manager.campus_profile.user_uuid),'requestId':str(uuid.uuid4()),'body':'상담을 요청합니다.'}))
        self.assertEqual(response.status_code,201,response.content)
        conversation = json.loads(response.content)['conversationId']
        self.assertEqual(views.history(self.request(self.manager),conversation).status_code,200)
        self.assertEqual(views.history(self.request(self.other),conversation).status_code,404)
