"""The seven event-story and learning events (plan 25 §3.3·§3.3a): one event per business row, none on failure."""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core import signing
from django.db import DatabaseError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Profile
from event_story import main, recipe, services as story
from event_story.access import SALT
from event_story.achievement_services import (
    AD_ID, CAMPAIGN_ID, GREETING_ID, TIMER_ID, complete_achievement, manager_ids, record_manager_action,
)
from event_story.game_services import finish_game, start_game
from event_story.interrupt import interrupt_running
from event_story.models import EventProgress, GameDefinition, GamePlay, ManagerInteraction, StoryProgress, StoryRecord
from event_story.test_achievements import seed_definitions
from event_story.test_learning_achievements import lesson
from learning import services as learning
from . import events
from .models import EventOutbox, PlatformSession
from .test_outbox import make_user


def rows(event_type):
    return list(EventOutbox.objects.filter(event_type=event_type).order_by("created_at", "outbox_uuid"))


@override_settings(DEBUG=True, EVENT_STORY_PREVIEW=True)
class StoryEventTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_definitions()
        main.catalog()
        cls.user = make_user("story-owner")
        cls.other = make_user("story-other", "aio-4")
        now = timezone.now()
        cls.session = PlatformSession.objects.create(user=cls.user, started_at=now, last_seen_at=now)
        cls.other_session = PlatformSession.objects.create(user=cls.other, started_at=now, last_seen_at=now)

    def ticket(self, target):
        return signing.dumps({"user": str(self.user.pk), "target": target}, salt=SALT)

    def act(self, action, **data):
        if action == "game_start":
            data.setdefault("request_uuid", str(uuid.uuid4()))
        return main.action(self.user, dict(action=action, **data), str(self.session.pk))

    def assert_owner(self, row, user, session):
        """Common fields come from the row's owner (not the requester)."""
        self.assertEqual(row.topic, "raw.event")
        self.assertEqual(row.raw_data["user_uuid"], str(user.pk))
        self.assertEqual(row.raw_data["session_uuid"], str(session.pk) if session else None)
        self.assertEqual(row.raw_data["class_group_uuid"], str(Profile.objects.get(user=user).cohort_id))

    def finished_play(self):
        """A play that already ended in this session: ending the session or starting a game must leave it alone."""
        return GamePlay.objects.create(user=self.user, session=self.session, game=GameDefinition.objects.get(game_code="timer"),
                                       status="SUCCESS", ended_at=timezone.now() - timedelta(minutes=1), duration_ms=11110)

    def assert_untouched(self, play):
        self.assertEqual(GamePlay.objects.values_list("status", "ended_at", "duration_ms").get(pk=play.pk),
                         (play.status, play.ended_at, play.duration_ms))
        self.assertNotIn(str(play.pk), [row.raw_data["play_uuid"] for row in rows("game_play_ended")])


class MainQuestEventTests(StoryEventTestCase):
    def test_a_stage_completes_once_with_its_final_values(self):
        for _ in range(2):
            self.act("investigate", ticket=self.ticket("investigate"))
        [row] = rows("event_progress_completed")
        progress = EventProgress.objects.get(user=self.user, event_id=main.uid("1"))
        self.assert_owner(row, self.user, self.session)
        self.assertEqual(
            {key: row.raw_data[key] for key in events.EXTRA_FIELDS["event_progress_completed"]},
            {"progress_uuid": str(progress.pk), "event_definition_uuid": str(main.uid("1")),
             "event_kind": "MAIN", "completion_order": None},
        )
        self.assertEqual(row.occurred_at, progress.completed_at)

    def test_a_failure_after_complete_leaves_no_event_now_or_on_the_next_request(self):
        real, calls = main.recover_finished_challenge, []

        def fail_on_second_call(user):  # _write_action calls it before and after the action
            calls.append(user)
            if len(calls) == 2:
                raise RuntimeError("after complete")
            return real(user)

        with patch("event_story.main.recover_finished_challenge", side_effect=fail_on_second_call):
            with self.assertRaises(RuntimeError):
                self.act("investigate", ticket=self.ticket("investigate"))
        self.assertFalse(main.done(self.user, "1"))
        self.act("notice")
        self.assertFalse(EventOutbox.objects.exists())
        self.act("investigate", ticket=self.ticket("investigate"))
        self.assertEqual(len(rows("event_progress_completed")), 1)

    def test_deliver_carries_the_rank_and_a_repeat_succeeds_without_an_event(self):
        for code in ("1", "3", "4", "5"):
            main.complete(self.user, code)
        self.act("deliver", ticket=self.ticket("final-guide"))
        self.assertTrue(self.act("deliver", ticket=self.ticket("final-guide"))["correct"])
        [row] = rows("event_progress_completed")
        progress = EventProgress.objects.get(user=self.user, event_id=main.uid("6"))
        self.assertIsNotNone(progress.completion_order)
        self.assertEqual(row.raw_data["completion_order"], progress.completion_order)

    def test_the_recovered_fourth_stage_carries_the_play_time_and_session(self):
        for code in ("1", "3"):
            main.complete(self.user, code)
        ended = timezone.now() - timedelta(minutes=5)
        played = PlatformSession.objects.create(user=self.user, started_at=ended - timedelta(minutes=1), last_seen_at=ended)
        GamePlay.objects.create(user=self.user, session=played, game=GameDefinition.objects.get(game_code="kkuwang_touch"),
                                event_id=main.uid("4"), status="FINISHED", ended_at=ended, duration_ms=main.TOUCH_DURATION_MS)
        main.prepare_status(self.user)
        [row] = rows("event_progress_completed")
        self.assertEqual(row.raw_data["event_definition_uuid"], str(main.uid("4")))
        self.assertEqual(row.raw_data["session_uuid"], str(played.pk))
        self.assertEqual(row.occurred_at, ended)

    def test_main_game_plays_start_and_end_once_per_row(self):
        for code in ("1", "3"):
            main.complete(self.user, code)
        request = str(uuid.uuid4())
        first = self.act("game_start", game="kkuwang_touch", ticket=self.ticket("roof-guide"), request_uuid=request)
        self.act("game_start", game="kkuwang_touch", ticket=self.ticket("roof-guide"), request_uuid=request)
        self.assertEqual(len(rows("game_play_started")), 1)  # same request_uuid returns the same play
        second = self.act("game_start", game="kkuwang_touch", ticket=self.ticket("roof-guide"))
        GamePlay.objects.filter(pk=second["play"]).update(started_at=timezone.now() - timedelta(seconds=11))
        for _ in range(2):  # a resent result returns the stored one
            self.act("game_finish", play=second["play"], token=second["token"], proof={"taps": [100, 200]})
        started, ended = rows("game_play_started"), rows("game_play_ended")
        self.assertEqual([row.raw_data["play_uuid"] for row in started], [first["play"], second["play"]])
        self.assertEqual([(row.raw_data["play_uuid"], row.raw_data["status"]) for row in ended],
                         [(first["play"], "INTERRUPTED"), (second["play"], "FINISHED")])
        finished = GamePlay.objects.get(pk=second["play"])
        self.assertEqual((ended[1].raw_data["duration_ms"], ended[1].occurred_at), (finished.duration_ms, finished.ended_at))
        for row in started + ended:
            self.assert_owner(row, self.user, self.session)
            self.assertEqual(row.raw_data["game_uuid"], str(main.uid("game/kkuwang_touch")))
            self.assertEqual(row.raw_data["event_definition_uuid"], str(main.uid("4")))
        self.assertEqual(len(rows("event_progress_completed")), 1)  # stage 4 by the finished round


class TerminalGameEventTests(StoryEventTestCase):
    def test_starting_twice_interrupts_the_first_play_and_a_result_is_reported_once(self):
        done = self.finished_play()
        first = start_game(self.user, "timer", str(self.session.pk))
        second = start_game(self.user, "timer", str(self.session.pk))
        self.assertEqual([row.raw_data["play_uuid"] for row in rows("game_play_started")],
                         [first["play_uuid"], second["play_uuid"]])
        GamePlay.objects.filter(pk=second["play_uuid"]).update(started_at=timezone.now() - timedelta(seconds=12))
        for _ in range(2):
            finish_game(self.user, second["play_uuid"], str(self.session.pk), {"status": "SUCCESS", "duration_ms": 11110})
        ended = rows("game_play_ended")
        self.assertEqual(
            [(row.raw_data["play_uuid"], row.raw_data["status"], row.raw_data["duration_ms"], row.raw_data["event_definition_uuid"])
             for row in ended],
            [(first["play_uuid"], "INTERRUPTED", None, None), (second["play_uuid"], "SUCCESS", 11110, None)],
        )
        self.assert_untouched(done)
        [egg] = rows("event_progress_completed")
        self.assertEqual((egg.raw_data["event_definition_uuid"], egg.raw_data["event_kind"], egg.raw_data["completion_order"]),
                         (str(TIMER_ID), "EASTER_EGG", 1))
        self.assert_owner(egg, self.user, self.session)


class InterruptedPlayEventTests(StoryEventTestCase):
    def play(self, user, session):
        return GamePlay.objects.create(user=user, session=session, game=GameDefinition.objects.get(game_code="timer"))

    def test_session_end_ends_each_running_play_of_that_session_once(self):
        mine = [self.play(self.user, self.session) for _ in range(2)]
        theirs, done = self.play(self.other, self.other_session), self.finished_play()
        for _ in range(2):
            events.end_platform_session(self.user, {events.SESSION_KEY: str(self.session.pk)}, "LOGOUT")
        ended = rows("game_play_ended")
        self.assertEqual(sorted(row.raw_data["play_uuid"] for row in ended), sorted(str(play.pk) for play in mine))
        for row in ended:
            self.assert_owner(row, self.user, self.session)
            self.assertEqual((row.raw_data["status"], row.raw_data["duration_ms"]), ("INTERRUPTED", None))
        self.assertEqual(GamePlay.objects.get(pk=theirs.pk).status, "RUNNING")
        self.assert_untouched(done)

    def test_an_operator_stop_ends_every_running_play_as_its_owner(self):
        mine, theirs = self.play(self.user, self.session), self.play(self.other, self.other_session)
        for _ in range(2):
            with transaction.atomic():
                interrupt_running()
        ended = {row.raw_data["play_uuid"]: row for row in rows("game_play_ended")}
        self.assertEqual(set(ended), {str(mine.pk), str(theirs.pk)})
        self.assert_owner(ended[str(mine.pk)], self.user, self.session)
        self.assert_owner(ended[str(theirs.pk)], self.other, self.other_session)
        stopped = GamePlay.objects.get(pk=theirs.pk)
        self.assertEqual((ended[str(theirs.pk)].raw_data["duration_ms"], ended[str(theirs.pk)].occurred_at),
                         (stopped.duration_ms, stopped.ended_at))


class ManagerAndEggEventTests(StoryEventTestCase):
    def setUp(self):
        self.managers = sorted(manager_ids())

    def test_a_greeting_once_per_manager_then_the_egg(self):
        for manager in self.managers + self.managers[:1]:
            record_manager_action(self.user, manager, "greet", self.session.pk)
        greeted = rows("manager_greeted")
        self.assertEqual([row.raw_data["manager_code"] for row in greeted], self.managers)
        for row in greeted:
            interaction = ManagerInteraction.objects.get(user=self.user, manager_id=row.raw_data["manager_code"])
            self.assertEqual((row.raw_data["interaction_uuid"], row.raw_data["event_definition_uuid"], row.occurred_at),
                             (str(interaction.pk), str(CAMPAIGN_ID), interaction.greeted_at))
            self.assert_owner(row, self.user, self.session)
        [egg] = rows("event_progress_completed")
        self.assertEqual(egg.raw_data["event_definition_uuid"], str(GREETING_ID))

    def test_pokes_count_up_by_one_per_event(self):
        for manager in [self.managers[0]] * 3 + [self.managers[1]]:
            record_manager_action(self.user, manager, "poke", self.session.pk)
        poked = rows("manager_poked")
        self.assertEqual(sorted((row.raw_data["manager_code"], row.raw_data["poke_count"]) for row in poked),
                         [(self.managers[0], 1), (self.managers[0], 2), (self.managers[0], 3), (self.managers[1], 1)])
        first = ManagerInteraction.objects.get(user=self.user, manager_id=self.managers[0])
        self.assertEqual({row.raw_data["interaction_uuid"] for row in poked if row.raw_data["manager_code"] == self.managers[0]},
                         {str(first.pk)})

    def test_a_call_without_a_platform_session_records_no_manager_event(self):
        record_manager_action(self.user, self.managers[0], "poke")
        self.assertEqual(ManagerInteraction.objects.get(user=self.user).poke_count, 1)
        self.assertFalse(EventOutbox.objects.exists())

    def test_an_egg_without_a_known_session_is_reported_with_a_null_session(self):
        self.assertTrue(complete_achievement(self.other, AD_ID, timezone.now()))
        [row] = rows("event_progress_completed")
        self.assert_owner(row, self.other, None)
        self.assertEqual((row.raw_data["event_kind"], row.raw_data["completion_order"]), ("EASTER_EGG", 1))


class StoryAndLearningEventTests(StoryEventTestCase):
    def test_the_room1_dialogue_is_reported_once_and_screen_notices_never(self):
        self.act("notice")
        for _ in range(2):
            self.act("room1_briefing", ticket=self.ticket("room1"))
        [row] = rows("story_action_recorded")
        record = StoryRecord.objects.get(key="yeongseok-intro")
        self.assert_owner(row, self.user, self.session)
        self.assertEqual(
            {key: row.raw_data[key] for key in events.EXTRA_FIELDS["story_action_recorded"]},
            {"story_record_uuid": str(record.event_id), "story_key": "pepero-2026-room1-dialogue",
             "action_key": "yeongseok-intro", "action_kind": "npc_dialogue"},
        )
        self.assertEqual(row.occurred_at, record.created_at)

    def test_the_story_log_reports_only_new_rows_of_reported_kinds_with_a_session(self):
        progress = StoryProgress.objects.create(user=self.user, event_key=recipe.EVENT_KEY)
        record, created = story.log(progress, "acquire", "recipe_note_acquired", {"objectId": "note"}, self.session.pk)
        self.assertTrue(created)
        self.assertEqual(story.log(progress, "acquire", "recipe_note_acquired", {}, self.session.pk), (record, False))
        story.log(progress, "popup_view", "popup_view", session_uuid=self.session.pk)  # not a reported kind
        story.log(progress, "recognize", "recipe_recognize", {}, "test-session")  # not a session UUID
        [row] = rows("story_action_recorded")
        self.assertEqual((row.raw_data["story_record_uuid"], row.raw_data["story_key"], row.raw_data["action_kind"]),
                         (str(record.event_id), recipe.EVENT_KEY, "recipe_note_acquired"))

    def test_a_lesson_completes_once_with_upper_case_codes(self):
        lesson_row = lesson(self.user, "sql", "beginner", correct=8)
        for _ in range(2):
            learning.complete_session(self.user, lesson_row.pk, str(self.session.pk))
        [row] = rows("learning_session_completed")
        lesson_row.refresh_from_db()
        self.assert_owner(row, self.user, self.session)
        self.assertEqual(
            {key: row.raw_data[key] for key in events.EXTRA_FIELDS["learning_session_completed"]},
            {"learning_session_id": lesson_row.pk, "subject": "SQL", "question_type": "FILL_BLANK",
             "difficulty": "BEGINNER", "total_questions": 10, "correct_count": 8},
        )
        self.assertEqual(row.occurred_at, lesson_row.completed_at)
        learning.complete_session(self.user, lesson(self.user, "python", "beginner").pk)  # no platform session
        self.assertEqual(len(rows("learning_session_completed")), 1)


class FailedEventTests(StoryEventTestCase):
    def test_no_business_change_is_kept_when_its_event_cannot_be_written(self):
        managers = sorted(manager_ids())
        progress = StoryProgress.objects.create(user=self.user, event_key=recipe.EVENT_KEY)
        lesson_row = lesson(self.user, "python", "beginner")
        running = GamePlay.objects.create(user=self.other, session=self.other_session, game=GameDefinition.objects.get(game_code="timer"))
        cases = {
            "event_progress_completed": lambda: self.act("investigate", ticket=self.ticket("investigate")),
            "game_play_started": lambda: start_game(self.user, "timer", str(self.session.pk)),
            "game_play_ended": lambda: events.end_platform_session(
                self.other, {events.SESSION_KEY: str(self.other_session.pk)}, "LOGOUT"),
            "manager_greeted": lambda: record_manager_action(self.user, managers[1], "greet", self.session.pk),
            "manager_poked": lambda: record_manager_action(self.user, managers[0], "poke", self.session.pk),
            "story_action_recorded": lambda: story.log(progress, "acquire", "recipe_note_acquired", {}, self.session.pk),
            "learning_session_completed": lambda: learning.complete_session(self.user, lesson_row.pk, str(self.session.pk)),
        }
        self.assertEqual(set(cases), set(events.EXTRA_FIELDS) - set(list(events.EXTRA_FIELDS)[:15]))
        create = EventOutbox.objects.create

        def failing(event_type):
            def create_or_fail(**fields):
                if fields["event_type"] == event_type:
                    raise DatabaseError("outbox unavailable")
                return create(**fields)
            return create_or_fail

        for event_type, call in cases.items():
            with self.subTest(event_type=event_type), self.assertRaises(DatabaseError):
                with patch.object(EventOutbox.objects, "create", side_effect=failing(event_type)):
                    call()
        self.assertFalse(main.done(self.user, "1"))
        self.assertFalse(GamePlay.objects.filter(user=self.user).exists())
        self.assertEqual(GamePlay.objects.get(pk=running.pk).status, "RUNNING")
        self.assertIsNone(PlatformSession.objects.get(pk=self.other_session.pk).ended_at)
        self.assertFalse(ManagerInteraction.objects.filter(user=self.user).exists())
        self.assertFalse(StoryRecord.objects.filter(progress=progress).exists())
        lesson_row.refresh_from_db()
        self.assertIsNone(lesson_row.completed_at)
        self.assertFalse(EventOutbox.objects.exists())


class StoryContractTests(TestCase):
    identity = {"user_uuid": str(uuid.uuid4()), "session_uuid": str(uuid.uuid4()), "class_group_uuid": str(uuid.uuid4())}

    def valid(self, event_type, **fields):
        events.validate_raw_data(event_type, {**self.identity, **fields})

    def invalid(self, event_type, **fields):
        with self.assertLogs("eventing.events", level="ERROR"), self.assertRaises(ValueError):
            self.valid(event_type, **fields)

    def test_every_code_the_app_can_send_fits_the_contract(self):
        greeted = {"interaction_uuid": str(uuid.uuid4()), "event_definition_uuid": str(CAMPAIGN_ID)}
        for manager in sorted(manager_ids()):
            with self.subTest(manager=manager):
                self.valid("manager_greeted", **greeted, manager_code=manager)
        # recipe._write_action's six actions and their log kinds, plus the room1 dialogue (main.py)
        actions = ("acquire", "recognize", "defer", "start", "topping", "finish")
        stories = [(recipe.EVENT_KEY, action, "recipe_note_acquired" if action == "acquire" else "recipe_" + action)
                   for action in actions] + [("pepero-2026-room1-dialogue", "yeongseok-intro", "npc_dialogue")]
        self.assertEqual({kind for _, _, kind in stories}, events.STORY_ACTION_KINDS)
        for story_key, action_key, kind in stories + [(story.key(), "start", "event_start")]:
            with self.subTest(story_key=story_key, action_key=action_key):
                self.valid("story_action_recorded", story_record_uuid=str(uuid.uuid4()), story_key=story_key,
                           action_key=action_key, action_kind=kind)

    def test_codes_counts_and_nulls_outside_the_contract_are_rejected(self):
        greeted = {"interaction_uuid": str(uuid.uuid4()), "event_definition_uuid": str(CAMPAIGN_ID)}
        for code in ("manager-", "Manager-a", "manager-a\n", "npc-a", "manager-" + "a" * 57, "manager-a b"):
            with self.subTest(manager_code=code):
                self.invalid("manager_greeted", **greeted, manager_code=code)
        self.invalid("manager_poked", **greeted, manager_code="manager-a", poke_count=0)
        self.invalid("manager_poked", **greeted, manager_code="manager-a", poke_count=True)
        completed = {"progress_uuid": str(uuid.uuid4()), "event_definition_uuid": str(CAMPAIGN_ID)}
        self.valid("event_progress_completed", **completed, event_kind="MAIN", completion_order=None)
        self.invalid("event_progress_completed", **completed, event_kind="CAMPAIGN", completion_order=1)
        self.invalid("event_progress_completed", **completed, event_kind="MAIN", completion_order=0)
        events.validate_raw_data("event_progress_completed", {**self.identity, "session_uuid": None, **completed,
                                                              "event_kind": "EASTER_EGG", "completion_order": 1})
        with self.assertLogs("eventing.events", level="ERROR"), self.assertRaises(ValueError):
            events.validate_raw_data("manager_greeted", {**self.identity, "session_uuid": None, **greeted,
                                                         "manager_code": "manager-a"})
        play = {"play_uuid": str(uuid.uuid4()), "game_uuid": str(main.uid("game/coating")), "event_definition_uuid": None}
        self.valid("game_play_ended", **play, status="FINISHED", duration_ms=None)
        self.invalid("game_play_ended", **play, status="RUNNING", duration_ms=0)
        self.invalid("game_play_ended", **play, status="FAILED", duration_ms=-1)
        lesson_fields = {"learning_session_id": 1, "subject": "CS", "question_type": "MULTIPLE_CHOICE", "difficulty": "ADVANCED"}
        self.valid("learning_session_completed", **lesson_fields, total_questions=10, correct_count=10)
        self.invalid("learning_session_completed", **lesson_fields, total_questions=10, correct_count=11)
        self.invalid("learning_session_completed", **{**lesson_fields, "subject": "cs"}, total_questions=10, correct_count=1)
        # Each count one below its lower bound (0 of 0 also passes correct <= total, so only the bound stops it).
        self.invalid("learning_session_completed", **{**lesson_fields, "learning_session_id": 0}, total_questions=1, correct_count=0)
        self.invalid("learning_session_completed", **lesson_fields, total_questions=0, correct_count=0)
        self.invalid("learning_session_completed", **lesson_fields, total_questions=1, correct_count=-1)
        self.invalid("story_action_recorded", story_record_uuid=str(uuid.uuid4()), story_key="a" * 41,
                     action_key="start", action_kind="recipe_start")
