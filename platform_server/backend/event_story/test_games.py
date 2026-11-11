from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.http import Http404
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from eventing.events import end_platform_session, SESSION_KEY
from eventing.models import PlatformSession
from .game_services import GameError, start_game, finish_game
from .models import GamePlay, GameDefinition
from .test_achievements import seed_definitions


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class GamePlayTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_definitions()
        cls.user = get_user_model().objects.create_user(username="game-alice")
        cls.other = get_user_model().objects.create_user(username="game-bob")
        now = timezone.now()
        cls.session = PlatformSession.objects.create(user=cls.user, started_at=now, last_seen_at=now)
        cls.other_session = PlatformSession.objects.create(user=cls.other, started_at=now, last_seen_at=now)

    def result(self, play, **overrides):
        return finish_game(self.user, play["play_uuid"], str(self.session.pk),
                           {"status": "FAILED", "duration_ms": 0, **overrides})

    def test_start_binds_server_account_session_and_game_without_event(self):
        play = start_game(self.user, "timer", str(self.session.pk))
        row = GamePlay.objects.get(pk=play["play_uuid"])
        self.assertEqual((row.user_id, row.session_id, row.game.game_code), (self.user.pk, self.session.pk, "timer"))
        self.assertEqual((row.status, row.duration_ms, row.ended_at), ("RUNNING", None, None))
        self.assertIsNone(row.event_id)
        self.assertEqual(row.evidence, {})
        self.assertNotIn("difficulty", play)
        self.assertEqual(play["config"], {"targetMs": 11110, "toleranceMs": 0, "overtimeMs": 2000})

    def test_exact_result_retry_preserves_time_and_rejects_conflicting_result(self):
        play = start_game(self.user, "timer", self.session.pk)
        first = self.result(play)
        self.assertEqual(self.result(play), first)
        with self.assertRaises(GameError) as conflict:
            self.result(play, status="INTERRUPTED", duration_ms=None)
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(GamePlay.objects.count(), 1)

    def test_timer_success_requires_11110_ms(self):
        play = start_game(self.user, "timer", self.session.pk)
        GamePlay.objects.filter(pk=play["play_uuid"]).update(started_at=timezone.now()-timedelta(seconds=12))
        for duration in (11100, 11120):
            with self.subTest(duration=duration), self.assertRaises(GameError):
                self.result(play, status="SUCCESS", duration_ms=duration)
        self.assertEqual(self.result(play, status="SUCCESS", duration_ms=11110)["status"], "SUCCESS")

    def test_new_round_interrupts_old_round_and_cannot_revive_it(self):
        old = start_game(self.user, "timer", self.session.pk)
        new = start_game(self.user, "typing", self.session.pk)
        with self.assertRaises(GameError):
            self.result(old)
        row = GamePlay.objects.get(pk=old["play_uuid"])
        self.assertEqual(row.status, "INTERRUPTED")
        self.assertIsNotNone(row.ended_at)
        self.assertNotEqual(old["play_uuid"], new["play_uuid"])
        self.assertEqual(GamePlay.objects.filter(status="RUNNING").count(), 1)

    def test_result_account_and_session_are_checked(self):
        play = start_game(self.user, "timer", self.session.pk)
        with self.assertRaises(Http404):
            finish_game(self.other, play["play_uuid"], self.other_session.pk,
                        {"status": "FAILED", "duration_ms": 0})
        with self.assertRaises(GameError):
            start_game(self.user, "timer", self.other_session.pk)
        session = PlatformSession.objects.create(user=self.user, started_at=timezone.now(), last_seen_at=timezone.now())
        with self.assertRaises(GameError):
            finish_game(self.user, play["play_uuid"], session.pk,
                        {"status": "FAILED", "duration_ms": 0})
        with self.assertRaises(GameError):
            start_game(self.user, "timer", None)

    def test_invalid_payloads_leave_running_record_unchanged(self):
        play = start_game(self.user, "timer", self.session.pk)
        for overrides in ({"status": []}, {"duration_ms": True}, {"duration_ms": -1},
                          {"duration_ms": 100000}, {"duration_ms": None}, {"difficulty": "expert"},
                          {"difficulty": "beginner"}, {"user_uuid": str(self.other.pk)}):
            with self.subTest(overrides=overrides), self.assertRaises(GameError):
                self.result(play, **overrides)
        self.assertEqual(GamePlay.objects.get(pk=play["play_uuid"]).status, "RUNNING")

    def test_typing_accepts_two_fields_and_close_is_idempotent(self):
        play = start_game(self.user, "typing", self.session.pk)
        saved = self.result(play)
        self.assertEqual(saved["status"], "FAILED")
        self.assertNotIn("difficulty", saved)
        play = start_game(self.user, "typing", self.session.pk)
        saved = self.result(play, duration_ms=None, status="INTERRUPTED")
        self.assertEqual(self.result(play, duration_ms=None, status="INTERRUPTED"), saved)

    def test_standalone_endpoint_cannot_settle_a_main_quest_game(self):
        game = GameDefinition.objects.create(game_code="sql_memory", game_name="SQL")
        play = GamePlay.objects.create(user=self.user,session=self.session,game=game)
        with self.assertRaises(GameError):
            self.result({"play_uuid":str(play.pk)})
        play.refresh_from_db()
        self.assertEqual(play.status,"RUNNING")

    def test_disabled_or_unplaced_game_cannot_start(self):
        for column in ("is_enabled", "is_placed"):
            GameDefinition.objects.filter(game_code="timer").update(**{column: False})
            with self.assertRaises(GameError):
                start_game(self.user, "timer", self.session.pk)
            GameDefinition.objects.filter(game_code="timer").update(**{column: True})
        self.assertFalse(GamePlay.objects.exists())

    def test_logout_interrupts_running_game(self):
        play = start_game(self.user, "timer", self.session.pk)
        end_platform_session(self.user, {SESSION_KEY: str(self.session.pk)}, "LOGOUT")
        self.assertEqual(GamePlay.objects.get(pk=play["play_uuid"]).status, "INTERRUPTED")
        with self.assertRaises(GameError):
            self.result(play)

    def test_result_endpoint_requires_auth_post_csrf_and_valid_json(self):
        url = "/api/event-story/plays/00000000-0000-0000-0000-000000000000/result/"
        self.assertEqual(self.client.post(url, {}, content_type="application/json").status_code, 401)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, "{", content_type="application/json").status_code, 400)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(url, {}, content_type="application/json").status_code, 403)

    def test_success_keeps_game_results_but_only_one_egg_per_account(self):
        from .models import EventProgress
        from .achievement_services import TIMER_ID, TYPING_ID, achievement_snapshot
        for index, code in enumerate(['timer', 'typing', 'timer']):
            play = start_game(self.user, code, self.session.pk)
            GamePlay.objects.filter(pk=play['play_uuid']).update(started_at=timezone.now()-timedelta(seconds=12))
            result = self.result(play, status='SUCCESS', duration_ms=11110)
            self.assertEqual(result['status'], 'SUCCESS')
            self.assertEqual(result['newly_completed'], [str(TIMER_ID)] if index == 0 else [])
            retry = self.result(play, status='SUCCESS', duration_ms=11110)
            self.assertEqual((retry['ended_at'], retry['newly_completed']), (result['ended_at'], []))
            self.assertEqual(GamePlay.objects.get(pk=play['play_uuid']).status, 'SUCCESS')
        self.assertEqual(list(EventProgress.objects.filter(user=self.user).values_list('event_id',flat=True)), [TIMER_ID])
        self.assertFalse(EventProgress.objects.filter(event_id=TYPING_ID).exists())
        self.assertTrue(all(not row['achieved'] for row in achievement_snapshot(self.other)))

    def test_failure_and_interruption_do_not_unlock_games(self):
        from .models import EventProgress
        for status, duration in (("FAILED", 0), ("INTERRUPTED", None)):
            for code in ("timer", "typing"):
                play = start_game(self.user, code, self.session.pk)
                result = self.result(play, status=status, duration_ms=duration)
                self.assertEqual(result["newly_completed"], [])
                self.assertTrue(all(not row["achieved"] for row in result["achievements"]))
        self.assertFalse(EventProgress.objects.exists())

    def test_achievement_save_failure_rolls_back_game_result(self):
        from unittest.mock import patch
        from django.db import DatabaseError
        from .models import EventProgress
        play = start_game(self.user, "typing", self.session.pk)
        with patch("event_story.achievement_services.EventProgress.objects.get_or_create", side_effect=DatabaseError("save failure")):
            with self.assertRaises(DatabaseError):
                self.result(play, status="SUCCESS")
        row = GamePlay.objects.get(pk=play["play_uuid"])
        self.assertEqual((row.status, row.ended_at, row.duration_ms), ("RUNNING", None, None))
        self.assertFalse(EventProgress.objects.exists())
        self.assertEqual(len(self.result(play, status="SUCCESS")["newly_completed"]), 1)

    def test_replay_preserves_first_achievement_completion(self):
        from .models import EventProgress
        from .achievement_services import TYPING_ID
        first = start_game(self.user, "typing", self.session.pk)
        self.result(first, status="SUCCESS")
        stamp = EventProgress.objects.get(user=self.user, event_id=TYPING_ID).completed_at
        replay = start_game(self.user, "typing", self.session.pk)
        self.assertEqual(self.result(replay, status="SUCCESS")["newly_completed"], [])
        self.assertEqual(EventProgress.objects.get(user=self.user, event_id=TYPING_ID).completed_at, stamp)

    def test_http_result_persists_achievement_and_returns_collection(self):
        from .achievement_services import TYPING_ID
        self.client.force_login(self.user)
        session = self.client.session
        session[SESSION_KEY] = str(self.session.pk)
        session.save()
        play = start_game(self.user, "typing", self.session.pk)
        url = "/api/event-story/plays/"+play["play_uuid"]+"/result/"
        payload = {"status": "SUCCESS", "duration_ms": 0}
        first = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["newly_completed"], [str(TYPING_ID)])
        self.assertEqual(self.client.post(url, payload, content_type="application/json").json()["newly_completed"], [])
        saved = self.client.get("/api/event-story/achievements/").json()["achievements"]
        self.assertTrue(next(row for row in saved if row["event_uuid"] == str(TYPING_ID))["achieved"])

from django.test import TransactionTestCase


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class ConcurrentGameAchievementTests(TransactionTestCase):
    def test_duplicate_success_is_confirmed_once_across_connections(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row locks are required for this check.")
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections, connections
        from .models import EventProgress
        from .achievement_services import TYPING_ID
        from .test_achievements import seed_definitions
        seed_definitions()
        import_module("event_story.migrations.0004_seed_easter_eggs").seed(apps, SimpleNamespace(connection=connection))
        user = get_user_model().objects.create_user(username="concurrent-game-achievement")
        now = timezone.now()
        session = PlatformSession.objects.create(user=user, started_at=now, last_seen_at=now)
        play = start_game(user, "typing", session.pk)
        barrier = Barrier(2)

        def complete():
            close_old_connections()
            try:
                owner = get_user_model().objects.get(pk=user.pk)
                barrier.wait(timeout=8)
                return finish_game(owner, play["play_uuid"], session.pk,
                                   {"status": "SUCCESS", "duration_ms": 0})
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(complete) for _ in range(2)]
            results = [task.result(timeout=15) for task in tasks]
        self.assertEqual(sum(str(TYPING_ID) in row["newly_completed"] for row in results), 1)
        self.assertEqual(results[0]["ended_at"], results[1]["ended_at"])
        self.assertEqual(EventProgress.objects.filter(user=user, event_id=TYPING_ID).count(), 1)
        self.assertEqual(GamePlay.objects.filter(user=user, status="SUCCESS").count(), 1)
