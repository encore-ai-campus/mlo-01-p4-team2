"""Real HTTP and movement WebSocket checks against an isolated Daphne/test DB."""
from http.cookiejar import Cookie, CookieJar
import json
import time
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

from channels.testing import ChannelsLiveServerTestCase
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from websockets.sync.client import connect

from accounts.models import Position, Profile
from event_story import main
from event_story.achievement_services import RECIPE_ID
from event_story.test_achievements import seed_definitions
from eventing.models import ClassGroup
from tests.test_postgres_platform import seed_catalog
from tests.test_recipe_story import act


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class RecipeLiveTests(ChannelsLiveServerTestCase):
    serve_static = False
    host = '127.0.0.1'

    def setUp(self):
        seed_catalog();seed_definitions();main.catalog()

    def request(self, http, path, data=None, csrf=True):
        opener, cookies = http
        headers = {}
        if data is not None:
            headers['Content-Type'] = 'application/json'
            if csrf:
                headers['X-CSRFToken'] = next(cookie.value for cookie in cookies if cookie.name == settings.CSRF_COOKIE_NAME)
        request = Request(self.live_server_url + path, headers=headers,
                          data=json.dumps(data).encode() if data is not None else None)
        try:
            response = opener.open(request, timeout=8)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            return response.status, json.loads(body) if body and response.headers.get_content_type() == 'application/json' else body.decode()

    def account(self, name, x, y):
        user = get_user_model().objects.create_user(username=name)
        profile = Profile.objects.create(user=user, nickname=name, cohort=ClassGroup.objects.get(code='unassigned'), guide_completed=True)
        Position.objects.create(profile=profile, last_position={'floor': 'campus', 'x': x, 'y': y, 'dir': 'up'})
        main.complete(user, '6')
        client = Client();client.force_login(user)
        cookies = CookieJar()
        cookies.set_cookie(Cookie(version=0, name=settings.SESSION_COOKIE_NAME, value=client.session.session_key,
            port=None, port_specified=False, domain=self.host, domain_specified=False, domain_initial_dot=False,
            path='/', path_specified=True, secure=False, expires=None, discard=True, comment=None, comment_url=None, rest={}))
        http = (build_opener(ProxyHandler({}), HTTPCookieProcessor(cookies)), cookies)
        status, response = self.request(http, '/api/event-story/recipe/status/')
        self.assertEqual(status, 200, response)
        return user, http

    def proof(self, http, target):
        cookies = '; '.join(f'{cookie.name}={cookie.value}' for cookie in http[1])
        with connect(self.live_server_ws_url + '/ws/movement/?control=server', origin=self.live_server_url,
                     additional_headers={'Cookie': cookies}, proxy=None, open_timeout=5, close_timeout=2) as ws:
            request_id = 'recipe-live'
            ws.send(json.dumps({'type': 'control', 'requestId': request_id, 'action': 'story_access', 'data': {'target': target}}))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                message = json.loads(ws.recv(timeout=max(.1, deadline-time.monotonic())))
                if message.get('type') == 'control_result' and message.get('requestId') == request_id:
                    self.assertNotIn('error', message, message)
                    return message['result']['ticket']
        self.fail('Movement server did not provide a proximity proof.')

    def post(self, http, action, proof, **extra):
        status, response = self.request(http, '/api/event-story/recipe/action/',
                                       {'action': action, 'ticket': proof, **extra})
        self.assertEqual(status, 200, response)
        return response

    def test_real_socket_note_proof_and_http_acquisition(self):
        _, http = self.account('live-recipe-note', 1552, 747)
        proof = self.proof(http, 'recipe-note')
        response = self.post(http, 'acquire', proof)
        self.assertTrue(response['state']['hasNote'])
        self.assertEqual(self.post(http, 'acquire', proof), response)

    def test_real_guide_proof_csrf_both_endings_and_persistent_single_reward(self):
        for topping in ('thanks', 'promise'):
            with self.subTest(topping=topping):
                user, http = self.account('live-recipe-' + topping, 1250, 185)
                act(user, 'acquire')
                proof = self.proof(http, 'recipe-guide')
                status, _ = self.request(http, '/api/event-story/recipe/action/', {'action': 'recognize', 'ticket': proof}, csrf=False)
                self.assertEqual(status, 403)
                for action in ('recognize', 'defer', 'start'):
                    self.post(http, action, proof)
                response = self.post(http, 'topping', proof, topping=topping)
                self.assertFalse(response['state']['secretCompleted'])
                response = self.post(http, 'finish', proof)
                self.assertEqual(response['newly_completed'], [str(RECIPE_ID)])
                self.assertTrue(response['state']['secretCompleted'])
                self.assertEqual(self.post(http, 'finish', proof)['newly_completed'], [])
                self.assertEqual(self.request(http, '/api/event-story/recipe/status/')[1], response['state'])
