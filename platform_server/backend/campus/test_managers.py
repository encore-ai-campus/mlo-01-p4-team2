"""Manager actions use server positions and persist outside the shared room lock."""
import asyncio
import math
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.db import DatabaseError
from django.test import SimpleTestCase

from . import consumers, poke
from .motion import ControlError, MotionController


class ManagerControlTests(SimpleTestCase):
    def setUp(self):
        self.room = consumers.CampusRoom()
        room_patch = patch.object(consumers, 'room', self.room)
        room_patch.start()
        self.addCleanup(room_patch.stop)
        self.consumer = c = consumers.CampusConsumer()
        c.player_id = 'manager-test-player'
        c.user = SimpleNamespace(pk='server-authenticated-user')
        c.controller = MotionController({'floor': 'campus', 'x': 2150,
                                         'y': 818, 'dir': 'up-left'})
        c.controller.poke_direction = 'up-left'
        c.state = {'id': c.player_id, **c.controller.pose}
        c.pepero_remaining = 10
        c.last_poke = -math.inf
        c.poke_requested = True
        c.poke_started = c.poke_scope = c.poke_stream_id = c.poke_direction = None
        c.poke_watchers = set()
        c.policy_status = {'expiresAt': (time.time() + 3600) * 1000}
        c.controller.last_input = time.monotonic()
        c.send_packet = AsyncMock()
        c.send_poke_to = AsyncMock()
        self.room.players[c.player_id] = c.state.copy()
        self.snapshot = {'achievements': [], 'newly_completed': []}
        self.db = AsyncMock(side_effect=self.saved_action)
        db_patch = patch.object(consumers.socket_db, 'run', self.db)
        db_patch.start()
        self.addCleanup(db_patch.stop)

    async def saved_action(self, *args):
        self.assertFalse(self.room.lock.locked())
        return self.snapshot

    async def test_greeting_uses_server_target_and_exact_dialogue(self):
        result = await self.consumer.greet_manager({'objectId': 'manager-suhyeon'})
        self.assertEqual(result['dialogue'], '이곳에는 숨겨진 이스터에그가 많다던데...')
        self.assertEqual(result['managerName'], '수현 매니저님')
        self.db.assert_awaited_once_with(self.consumer.manager_action_now, 'manager-suhyeon', 'greet')

    async def test_room1_manager_greeting_and_poke_share_the_private_target(self):
        self.consumer.controller.interior = {"subject":"mission1", "missionRoom":1,
            "actor":{"x":355,"y":195,"dir":"up","moving":False}}
        result = await self.consumer.greet_manager({"objectId":"sql_answer"})
        self.assertEqual(result["managerName"],"영석 매니저님")
        self.assertEqual(self.db.await_args.args[-2:],("manager-yeongseok","greet"))
        result = await self.consumer.receive_poke({"context":"classroom","subject":"mission1"})
        self.assertTrue(result["hit"])
        self.assertEqual(self.db.await_args.args[-2:],("manager-yeongseok","poke"))

    async def test_distant_foreign_floor_and_invalid_targets_are_rejected(self):
        for target in ['manager-yebin', 'manager-yeongseok', 'not-a-manager', None, []]:
            with self.subTest(target=target), self.assertRaises(ControlError):
                await self.consumer.greet_manager({'objectId': target})
        self.db.assert_not_awaited()

    async def test_wall_or_transfer_prevents_greeting(self):
        with patch('campus.consumers.World.clear', return_value=False):
            with self.assertRaises(ControlError):
                await self.consumer.greet_manager({'objectId': 'manager-suhyeon'})
        self.consumer.controller.pending_transfer = {'toFloor': 'rooftop'}
        with self.assertRaises(ControlError):
            await self.consumer.greet_manager({'objectId': 'manager-suhyeon'})
        self.db.assert_not_awaited()

    async def test_single_poke_records_the_last_biscuit_hit(self):
        self.consumer.pepero_remaining = 1
        result = await self.consumer.receive_poke({'context': 'world', 'floor': 'campus'})
        self.assertTrue(result['hit'])
        self.db.assert_awaited_once_with(self.consumer.manager_action_now, 'manager-suhyeon', 'poke')
        with self.assertRaises(ControlError):
            await self.consumer.receive_poke({'context': 'world', 'floor': 'campus'})
        self.assertEqual(self.db.await_count, 1)
        self.assertEqual(self.consumer.pepero_remaining, 0)

    async def test_empty_hand_does_not_count_single_or_held_pokes(self):
        self.consumer.pepero_remaining = 0
        result = await self.consumer.receive_poke({'context':'world','floor':'campus'})
        self.assertTrue(result['hit'])
        self.consumer.last_poke -= poke.COOLDOWN
        await self.consumer.tick_poke()
        self.db.assert_not_awaited()

    async def test_held_poke_counts_the_last_biscuit_only_once(self):
        self.consumer.pepero_remaining = 1
        await self.consumer.tick_poke()
        self.assertEqual(self.consumer.pepero_remaining, 0)
        self.assertEqual(self.db.await_count, 1)
        self.consumer.last_poke -= poke.COOLDOWN
        await self.consumer.tick_poke()
        self.assertEqual(self.db.await_count, 1)

    async def test_held_poke_records_each_valid_hit_and_obeys_cooldown(self):
        await self.consumer.tick_poke()
        await self.consumer.tick_poke()
        self.assertEqual(self.db.await_count, 1)
        self.consumer.last_poke -= poke.COOLDOWN
        await self.consumer.tick_poke()
        self.assertEqual(self.db.await_count, 2)
        self.assertEqual(self.db.await_args.args[-2:], ('manager-suhyeon', 'poke'))

    async def test_miss_does_not_write_manager_progress(self):
        self.consumer.controller.pose.update(x=1950, y=580)
        result = await self.consumer.receive_poke({'context': 'world', 'floor': 'campus'})
        self.assertFalse(result['hit'])
        self.db.assert_not_awaited()

    async def test_only_new_achievement_is_notified_privately(self):
        await self.consumer.record_manager_poke('manager-suhyeon')
        self.consumer.send_packet.assert_not_awaited()
        self.snapshot = {'achievements': [{'name': '참을성 테스트', 'achieved': True}],
                         'newly_completed': ['new-achievement']}
        await self.consumer.record_manager_poke('manager-suhyeon')
        self.consumer.send_packet.assert_awaited_once_with({
            'type': 'achievement_update', **self.snapshot,
        })
        self.consumer.send_poke_to.assert_not_awaited()

    async def test_save_failure_is_reported_without_marking_an_achievement(self):
        self.db.side_effect = DatabaseError('unavailable')
        with self.assertLogs(consumers.__name__, level='ERROR'):
            await self.consumer.record_manager_poke('manager-suhyeon')
        self.assertEqual(self.consumer.send_packet.await_args.args[0]['type'], 'control_error')

    def test_revoked_session_never_writes_manager_progress(self):
        with patch.object(self.consumer, 'session_user', return_value=None), patch.object(consumers, 'record_manager_action') as record:
            self.assertIsNone(self.consumer.manager_action_now('manager-suhyeon', 'poke'))
            record.assert_not_called()

    async def test_held_poke_closes_revoked_session_without_achievement_update(self):
        self.snapshot = None
        self.consumer.session_expired = AsyncMock()
        await self.consumer.tick_poke()
        self.consumer.session_expired.assert_awaited_once()
        self.assertFalse(self.consumer.poke_requested)
        self.assertFalse(any(call.args[0].get('type') == 'achievement_update'
                             for call in self.consumer.send_packet.await_args_list))

    def test_managers_are_normal_targets_and_sandbags_still_guard(self):
        targets = poke.world_targets(self.consumer.controller.world)
        manager = next(row for row in targets if row['id'] == 'manager-suhyeon')
        self.assertFalse(manager['guarding'])
        for target in targets:
            if self.consumer.controller.world.objects[target['id']].get('action') == 'guard_sandbag':
                self.assertTrue(target['guarding'])
