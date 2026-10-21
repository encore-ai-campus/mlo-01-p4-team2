import json
from datetime import timedelta
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import MealComment


class MealCommentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="meal-author", password="test-password")
        self.other = get_user_model().objects.create_user(username="meal-other", password="test-password")
        self.client.force_login(self.user)
        self.url = "/api/community/meal-comments/"

    def test_daily_comments_are_persistent_and_isolated(self):
        result = self.client.post(self.url, json.dumps({"body": "맛있어요"}), content_type="application/json")
        self.assertEqual(result.status_code, 201)
        self.assertEqual(MealComment.objects.count(), 1)
        MealComment.objects.create(author=self.user, meal_date=timezone.localdate()-timedelta(days=1), body="어제 식단")
        result = self.client.get(self.url).json()
        self.assertEqual([row["body"] for row in result["comments"]], ["맛있어요"])

    def test_only_author_can_change_comment(self):
        item = MealComment.objects.create(author=self.user, meal_date=timezone.localdate(), body="원문")
        url = self.url+str(item.pk)+"/"
        self.client.force_login(self.other)
        self.assertEqual(self.client.delete(url).status_code, 403)
        self.assertEqual(self.client.patch(url, json.dumps({"body": "변경", "version": item.updated_at.isoformat()}), content_type="application/json").status_code, 403)
        self.client.force_login(self.user)
        self.assertEqual(self.client.patch(url, json.dumps({"body": "변경", "version": item.updated_at.isoformat()}), content_type="application/json").status_code, 200)
        self.assertEqual(self.client.patch(url, json.dumps({"body": "오래된 변경", "version": item.updated_at.isoformat()}), content_type="application/json").status_code, 409)
        self.assertEqual(self.client.delete(url).status_code, 200)

    def test_requires_login_and_csrf(self):
        self.assertEqual(Client().get(self.url).status_code, 401)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url, json.dumps({"body": "무단요청"}), content_type="application/json").status_code, 403)

    def test_validation_and_pagination(self):
        self.assertEqual(self.client.post(self.url, json.dumps({"body": " "}), content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(self.url, json.dumps({"body": "a"*2001}), content_type="application/json").status_code, 400)
        self.assertEqual(self.client.get(self.url+"?date=invalid").status_code, 400)
        MealComment.objects.bulk_create([MealComment(author=self.user, meal_date=timezone.localdate(), body=str(i)) for i in range(21)])
        result = self.client.get(self.url+"?page=2").json()
        self.assertEqual(len(result["comments"]), 1)
        self.assertEqual(result["pagination"]["total"], 21)
