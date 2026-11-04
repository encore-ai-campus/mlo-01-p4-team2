from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
import json
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import close_old_connections, connection, connections
from django.test import RequestFactory, TestCase, TransactionTestCase

from .models import EventProgress, ManagerInteraction
from .achievement_services import (
    CAMPAIGN_ID, GREETING_ID, POKING_ID, achievement_snapshot, manager_ids,
    record_manager_action,
)
from .achievement_views import achievements


def seed_definitions():
    import_module("event_story.migrations.0004_seed_easter_eggs").seed(apps, SimpleNamespace(connection=connection))
    import_module("event_story.migrations.0005_learning_achievements").seed(apps, SimpleNamespace(connection=connection))


class ManagerAchievementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_definitions()
        cls.alice = get_user_model().objects.create_user(username="manager-alice")
        cls.bob = get_user_model().objects.create_user(username="manager-bob")

    def setUp(self):
        self.managers = sorted(manager_ids())

    def test_eight_achievements_and_generated_seven_managers(self):
        self.assertEqual(set(self.managers), {
            "manager-suhyeon", "manager-yebin", "manager-songhee", "manager-huiae",
            "manager-yeongseok", "manager-jeonghun", "manager-yuhyeong",
        })
        snapshot = achievement_snapshot(self.alice)
        self.assertEqual([row["name"] for row in snapshot], ["수상한 친화력", "참을성 테스트", "뱀을 길들이는 자", "데이터의 비밀문", "찰나의 기적", "손가락에 불났다", "삼각지대 탈출", "컴퓨터의 속마음"])
        self.assertTrue(all(not row["achieved"] and row["completed_at"] is None for row in snapshot))
        self.assertTrue(all(row["image_url"].startswith("/static/campus/assets/achievements/") for row in snapshot))

    def test_seven_distinct_greetings_are_per_account_and_idempotent(self):
        manager = self.managers[0]
        record_manager_action(self.alice, manager, "greet")
        first_greeting = ManagerInteraction.objects.get(user=self.alice, manager_id=manager).greeted_at
        repeated = record_manager_action(self.alice, manager, "greet")
        self.assertEqual(repeated["newly_completed"], [])
        self.assertEqual(ManagerInteraction.objects.get(user=self.alice, manager_id=manager).greeted_at, first_greeting)
        for manager in self.managers[1:-1]:
            self.assertEqual(record_manager_action(self.alice, manager, "greet")["newly_completed"], [])
        completed = record_manager_action(self.alice, self.managers[-1], "greet")
        self.assertEqual(completed["newly_completed"], [str(GREETING_ID)])
        completed_at = EventProgress.objects.get(user=self.alice, event_id=GREETING_ID).completed_at
        self.assertEqual(record_manager_action(self.alice, self.managers[-1], "greet")["newly_completed"], [])
        self.assertEqual(EventProgress.objects.get(user=self.alice, event_id=GREETING_ID).completed_at, completed_at)
        self.assertFalse(achievement_snapshot(self.bob)[0]["achieved"])
        self.assertFalse(ManagerInteraction.objects.filter(user=self.bob).exists())

    def test_each_manager_requires_eleven_hits_and_counts_keep_growing(self):
        ManagerInteraction.objects.bulk_create([
            ManagerInteraction(user=self.alice, campaign_id=CAMPAIGN_ID, manager_id=manager, poke_count=10)
            for manager in self.managers
        ])
        for manager in self.managers[:-1]:
            self.assertEqual(record_manager_action(self.alice, manager, "poke")["newly_completed"], [])
        result = record_manager_action(self.alice, self.managers[-1], "poke")
        self.assertEqual(result["newly_completed"], [str(POKING_ID)])
        completed_at = EventProgress.objects.get(user=self.alice, event_id=POKING_ID).completed_at
        self.assertEqual(record_manager_action(self.alice, self.managers[0], "poke")["newly_completed"], [])
        self.assertEqual(ManagerInteraction.objects.get(user=self.alice, manager_id=self.managers[0]).poke_count, 12)
        self.assertEqual(EventProgress.objects.get(user=self.alice, event_id=POKING_ID).completed_at, completed_at)
        self.assertFalse(achievement_snapshot(self.alice)[0]["achieved"])
        self.assertFalse(achievement_snapshot(self.bob)[1]["achieved"])

    def test_total_hits_cannot_replace_a_missing_manager(self):
        for manager in self.managers[:-1]:
            ManagerInteraction.objects.create(
                user=self.alice, campaign_id=CAMPAIGN_ID, manager_id=manager, poke_count=100,
            )
        result = record_manager_action(self.alice, self.managers[0], "poke")
        self.assertEqual(result["newly_completed"], [])
        self.assertFalse(result["achievements"][1]["achieved"])

    def test_invalid_actions_do_not_write_progress(self):
        for manager, action in (("classroom-instructor", "poke"), ([], "greet"), (self.managers[0], "reward")):
            with self.assertRaises(ValueError):
                record_manager_action(self.alice, manager, action)
        self.assertFalse(ManagerInteraction.objects.exists())
        self.assertFalse(EventProgress.objects.exists())

    def test_invalid_generated_catalog_is_rejected(self):
        manager_ids.cache_clear()
        self.addCleanup(manager_ids.cache_clear)
        with patch("event_story.achievement_services.geometry", return_value={"worlds": {"campus": {"objects": [
            {"id": "manager-duplicate", "action": "manager_greet"} for _ in range(6)
        ]}}}):
            with self.assertRaises(ImproperlyConfigured):
                manager_ids()

    def test_endpoint_auth_method_and_account_isolation(self):
        response = self.client.get("/api/event-story/achievements/")
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.json())
        record_manager_action(self.alice, self.managers[0], "greet")
        for manager in self.managers[1:]:
            record_manager_action(self.alice, manager, "greet")
        request = RequestFactory().get("/api/event-story/achievements/", {"user_id": self.alice.pk})
        request.user = self.bob
        response = achievements(request)
        self.assertFalse(json.loads(response.content)["achievements"][0]["achieved"])
        request = RequestFactory().post("/api/event-story/achievements/")
        request.user = self.alice
        self.assertEqual(achievements(request).status_code, 405)


class ConcurrentManagerAchievementTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row locks are required for this concurrency check.")
        seed_definitions()
        self.user = get_user_model().objects.create_user(username="manager-concurrent")
        self.managers = sorted(manager_ids())
        ManagerInteraction.objects.bulk_create([
            ManagerInteraction(user=self.user, campaign_id=CAMPAIGN_ID, manager_id=manager,
                               poke_count=10 if index >= len(self.managers)-2 else 11)
            for index, manager in enumerate(self.managers)
        ])

    def test_concurrent_last_hits_on_different_managers_complete_once(self):
        start = Barrier(2)

        def hit(manager_id):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.user.pk)
                start.wait(timeout=5)
                return record_manager_action(user, manager_id, "poke")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(hit, manager) for manager in self.managers[-2:]]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sum(str(POKING_ID) in row["newly_completed"] for row in results), 1)
        self.assertEqual(ManagerInteraction.objects.filter(user=self.user, poke_count=11).count(), 7)
        self.assertEqual(EventProgress.objects.filter(user=self.user, event_id=POKING_ID,
                                                    status=EventProgress.Status.COMPLETED).count(), 1)
