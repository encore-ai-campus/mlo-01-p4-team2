import json
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import connections, OperationalError
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from tests.factories import create_profile

from .models import Comment, Post, PostReadLog
from .read_db import ALIAS, COOKIE


@skipUnless(ALIAS in settings.DATABASES, "Configure community_read test mirror for replica routing tests")
class CommunityReadDatabaseTests(TransactionTestCase):
    databases = {"default", ALIAS}

    @classmethod
    def tearDownClass(cls):
        # A mirror has its own pool; release it before Django drops the test DB.
        connections[ALIAS].close()
        close_pool = getattr(connections[ALIAS], "close_pool", None)
        if close_pool:
            close_pool()
        super().tearDownClass()

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="read-db-author")
        create_profile(user=self.user, nickname="조회 분리 검사")
        self.client.force_login(self.user)
        self.post = Post.objects.create(author=self.user, category="free", title="조회 분리", body="본문")
        parent = Comment.objects.create(post=self.post, author=self.user, body="댓글")
        Comment.objects.create(post=self.post, author=self.user, parent=parent, body="답글")

    def url(self, name, *args):
        return reverse("community:" + name, args=args)

    def test_list_search_metadata_and_comment_threads_use_replica(self):
        for url in (self.url("metadata"), self.url("posts") + "?q=조회",
                    self.url("comments", self.post.id)):
            with self.subTest(url=url), CaptureQueriesContext(connections[ALIAS]) as replica:
                response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(replica.captured_queries)
            self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in replica))
        self.assertEqual(response.json()["comments"][0]["replies"][0]["body"], "답글")

    def test_successful_write_pins_browser_reads_to_primary(self):
        response = self.client.post(self.url("posts"), json.dumps({"category": "free", "title": "새 글", "body": "즉시 확인"}), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertIn(COOKIE, response.cookies)
        self.assertTrue(response.cookies[COOKIE]["httponly"])
        with CaptureQueriesContext(connections[ALIAS]) as replica:
            data = self.client.get(self.url("posts")).json()
        self.assertEqual(len(replica), 0)
        self.assertIn("새 글", [p["title"] for p in data["posts"]])

    def test_detail_read_log_and_outbox_remain_on_primary(self):
        with CaptureQueriesContext(connections[ALIAS]) as replica:
            response = self.client.get(self.url("post-detail", self.post.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(replica), 0)
        self.assertEqual(PostReadLog.objects.filter(post=self.post).count(), 1)

    def test_reply_creation_remains_on_primary(self):
        parent = Comment.objects.get(post=self.post, parent=None)
        with CaptureQueriesContext(connections[ALIAS]) as replica:
            response = self.client.post(self.url("comments", self.post.id), json.dumps({"body": "새 답글", "parentCommentUuid": str(parent.pk)}), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(replica), 0)

    def test_replica_connection_failure_retries_read_on_primary(self):
        def unavailable(*args, **kwargs):
            raise OperationalError("test replica unavailable")
        with connections[ALIAS].execute_wrapper(unavailable):
            with self.assertLogs("community.read_db", level="WARNING"):
                response = self.client.get(self.url("posts"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["posts"][0]["id"], self.post.id)

    def test_tampered_or_other_user_cookie_does_not_pin_reads(self):
        self.client.cookies[COOKIE] = "tampered"
        with CaptureQueriesContext(connections[ALIAS]) as replica:
            response = self.client.get(self.url("posts"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(replica.captured_queries)
