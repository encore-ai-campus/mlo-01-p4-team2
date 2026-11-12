import math
import time
from unittest.mock import AsyncMock, patch
from django.test import SimpleTestCase
from .consumers import CampusConsumer
from . import consumers
from .motion import MotionController, ControlError, world

class PeperoGameTests(SimpleTestCase):
    def setUp(self):
        self.room = consumers.CampusRoom()
        replacement = patch.object(consumers, "room", self.room)
        replacement.start(); self.addCleanup(replacement.stop)
        self.c = CampusConsumer()
        self.c.player_id = 'pepero-test'
        self.c.controller = MotionController(dict(floor='campus', x=2000, y=900, dir='right'))
        self.c.state = {'id': self.c.player_id, **self.c.controller.pose}
        self.c.pepero_remaining = 10
        self.c.last_poke = -math.inf
        self.c.send_packet = AsyncMock()
        self.c.send_control_state = AsyncMock()
        self.c.send_poke_to = AsyncMock()
        self.c.everyone = lambda: iter([])
        self.c.poke_audience = lambda scope: {self.c.player_id}
        self.c.poke_started = None
        self.c.poke_scope = None
        self.c.poke_direction = None
        self.c.poke_watchers = set()
        self.c.poke_requested = True
        self.c.controller.last_input = time.monotonic()
        self.c.policy_status = {'expiresAt': time.time()*1000+60000}
        self.room.players[self.c.player_id] = self.c.state

    def tearDown(self):
        self.room.players.pop(self.c.player_id, None)
        self.room.pending_moves.pop(self.c.player_id, None)

    async def test_inventory_does_not_schedule_or_trigger_global_movement(self):
        self.c.seen_by_peers = self.c.state.copy()
        self.assertFalse(self.c.pose_changed())
        await self.c.spend_pepero('opponent', False)
        self.assertEqual(self.room.players[self.c.player_id]['peperoRemaining'], 9)
        self.assertNotIn(self.c.player_id, self.room.pending_moves)
        self.assertFalse(self.c.pose_changed())
        self.assertEqual(self.c.send_packet.await_args.args[0],
                         {'type': 'pepero_status', 'remaining': 9})

    async def test_refill_sends_inventory_through_poke_audience(self):
        w = world('campus')
        box = next(o for o in w.objects.values() if o.get('action') == 'pepero_refill')
        self.c.controller.world = w
        x, y = box['interactionPoint']
        self.c.controller.pose.update(x=x, y=y)
        await self.c.refill_pepero({'objectId': box['id']})
        audience, packet = self.c.send_poke_to.await_args.args
        self.assertEqual(audience, {self.c.player_id})
        self.assertEqual(packet['type'], 'pepero_inventory')
        self.assertEqual(packet['remaining'], 10)
        self.assertNotIn(self.c.player_id, self.room.pending_moves)

    async def test_ten_hits_then_empty_hand_can_still_swing(self):
        with patch('campus.poke.find_target', return_value='opponent'):
            for expected in range(9, -1, -1):
                self.c.last_poke = -math.inf
                result = await self.c.receive_poke({'context':'world','floor':'campus'})
                self.assertTrue(result['hit'])
                self.assertEqual(self.c.pepero_remaining, expected)
            self.c.last_poke = -math.inf
            await self.c.receive_poke({'context':'world','floor':'campus'})
            self.assertEqual(self.c.pepero_remaining, 0)

    async def test_miss_preserves_biscuit_and_block_breaks_it(self):
        self.c.everyone = lambda: iter([('opponent', {'id':'opponent','floor':'campus','guarding':True})])
        for target in [None, 'opponent']:
            self.c.last_poke = -math.inf
            with patch('campus.poke.find_target', return_value=target):
                result = await self.c.receive_poke({'context':'world','floor':'campus'})
            self.assertFalse(result['hit'])
            self.assertEqual(self.c.pepero_remaining, 10 if target is None else 0)
            payload = self.c.send_poke_to.await_args.args[1]
            self.assertEqual(payload['peperoBroken'], target is not None)

    async def test_held_swing_continues_after_biscuit_is_spent(self):
        self.c.pepero_remaining = 1
        with patch('campus.poke.find_target', return_value='opponent'):
            await self.c.tick_poke()
            self.assertEqual(self.c.pepero_remaining, 0)
            self.assertIsNotNone(self.c.poke_started)
            calls = self.c.send_poke_to.await_count
            self.c.last_poke = -math.inf
            await self.c.tick_poke()
            self.assertGreater(self.c.send_poke_to.await_count, calls)

    async def test_kkuwang_hit_uses_inventory_before_spending_in_both_paths(self):
        self.c.kkuwang_hit_ui_ready = True
        for held in (False, True):
            self.c.pepero_remaining = 1
            self.c.last_poke = -math.inf
            self.c.kkuwang_hit_resolved = False
            self.c.controller.last_input = time.monotonic()
            with patch('campus.poke.find_target', return_value='campus-guide-kkuwang'), patch(
                'campus.consumers.socket_db.run', new=AsyncMock(return_value={'resolved': True})
            ) as save:
                if held:
                    await self.c.tick_poke()
                else:
                    await self.c.receive_poke({'context': 'world', 'floor': 'campus'})
                save.assert_awaited_once_with(self.c.kkuwang_hit_now)
                self.assertEqual(self.c.pepero_remaining, 0)
                await self.c.record_kkuwang_hit(True)
                save.assert_awaited_once()

    async def test_empty_missed_other_and_guarded_hits_do_not_award(self):
        self.c.kkuwang_hit_ui_ready = True
        for target, remaining, blocked in [(None, 10, False), ('opponent', 10, False),
                                            ('campus-guide-kkuwang', 0, False), ('campus-guide-kkuwang', 10, True)]:
            self.c.pepero_remaining = remaining
            self.c.last_poke = -math.inf
            self.c.everyone = lambda: iter([('campus-guide-kkuwang', {'id': 'campus-guide-kkuwang', 'floor': 'campus', 'guarding': blocked})])
            with patch('campus.poke.find_target', return_value=target), patch('campus.consumers.socket_db.run', new=AsyncMock()) as save:
                await self.c.receive_poke({'context': 'world', 'floor': 'campus'})
                save.assert_not_awaited()

    async def test_old_ui_requires_refresh_without_consuming_first_award(self):
        with patch('campus.consumers.socket_db.run', new=AsyncMock()) as save:
            await self.c.record_kkuwang_hit(True)
            await self.c.record_kkuwang_hit(True)
            save.assert_not_awaited()
        self.assertEqual(self.c.send_packet.await_count, 1)
        self.assertIn('새로고침', self.c.send_packet.await_args.args[0]['message'])

    async def test_transient_save_failure_does_not_resolve_the_hit(self):
        from django.db import DatabaseError
        self.c.kkuwang_hit_ui_ready = True
        with patch('campus.consumers.socket_db.run', new=AsyncMock(side_effect=[DatabaseError('failed'), {'resolved': False}, {'resolved': True}])) as save:
            for _ in range(3):
                await self.c.record_kkuwang_hit(True)
            self.assertEqual(save.await_count, 3)
        self.assertTrue(self.c.kkuwang_hit_resolved)

    async def test_ui_preparation_is_authenticated_and_allowed_while_frozen(self):
        import asyncio
        import json
        self.c.sync_freeze = AsyncMock(return_value={'paused': True, 'blocked': True, 'revision': 4, 'message': '정지'})
        self.c.current_identity = AsyncMock(return_value={'id': 'user'})
        self.c.control_lock = asyncio.Lock()
        self.c.server_control = True
        self.c.logout_prepared = False
        packet = {'type': 'control', 'requestId': 'ui', 'action': 'achievement_ui_ready',
                  'freezeRevision': 0, 'data': {'version': 'kkuwang-hit-v1'}}
        await self.c.handle_message(json.dumps(packet))
        self.assertTrue(self.c.kkuwang_hit_ui_ready)
        self.c.current_identity.assert_awaited_once()
        self.assertEqual(self.c.send_packet.await_args.args[0]['result'], {'ready': True})
        self.c.kkuwang_hit_ui_ready = False
        self.c.current_identity.return_value = None
        self.c.session_expired = AsyncMock()
        await self.c.handle_message(json.dumps(packet))
        self.assertFalse(self.c.kkuwang_hit_ui_ready)
        self.c.session_expired.assert_awaited_once()

    async def test_session_expiring_after_hit_uses_the_normal_logout_path(self):
        from learning.services import LearningError
        self.c.kkuwang_hit_ui_ready = True
        self.c.session_expired = AsyncMock()
        with patch('campus.consumers.socket_db.run', new=AsyncMock(side_effect=LearningError('다시 로그인해 주세요.', 'authentication_required', 401))):
            await self.c.record_kkuwang_hit(True)
        self.c.session_expired.assert_awaited_once_with({'reason': 'signed_out'})
        self.assertFalse(getattr(self.c, 'kkuwang_hit_resolved', False))

    async def test_refill_is_nearby_only_and_capped(self):
        for floor in ['campus', 'rooftop']:
            w = world(floor)
            boxes = [o for o in w.objects.values() if o.get('action')=='pepero_refill']
            self.assertEqual(len(boxes), 2 if floor == 'campus' else 2)
            for box in boxes:
                self.c.controller.world = w
                x, y = box['interactionPoint']
                self.c.controller.pose.update(floor=floor, x=x, y=y)
                for _ in range(2):
                    result = await self.c.refill_pepero({'objectId':box['id']})
                    self.assertEqual(result['remaining'], 10)
                    self.c.send_control_state.assert_awaited()
                    self.assertEqual(self.c.state['peperoRemaining'],10)
                self.assertEqual(box['interactionRadius'], 120)
                self.c.controller.pose.update(x=x, y=y+50)
                self.assertTrue(w.walkable(x, y+50))
                self.assertEqual(self.c.controller.hints()['nearbyId'], box['id'])
                self.assertEqual((await self.c.refill_pepero({'objectId':box['id']}))['remaining'], 10)
                self.c.controller.pose.update(x=x+100, y=y+100)
                with self.assertRaises(ControlError):
                    await self.c.refill_pepero({'objectId':box['id']})
                self.c.controller.pose.update(x=x, y=y)
                self.c.controller.interior = {'subject':'python'}
                with self.assertRaises(ControlError):
                    await self.c.refill_pepero({'objectId':box['id']})
                self.c.controller.interior = None
                self.assertTrue(w.walkable(x,y))
                self.assertIsNotNone(w.route(dict(zip(('x','y'),w.data['spawn'])), {'x':x,'y':y}))
        with self.assertRaises(ControlError):
            await self.c.refill_pepero({'objectId':[]})

    async def test_cooldown_and_guard_cannot_bypass_consumption(self):
        self.c.last_poke = time.monotonic()
        with self.assertRaises(ControlError):
            await self.c.receive_poke({'context':'world','floor':'campus'})
        self.c.last_poke = -math.inf
        self.c.controller.pose['guarding'] = True
        with self.assertRaises(ControlError):
            await self.c.receive_poke({'context':'world','floor':'campus'})
        self.assertEqual(self.c.pepero_remaining, 10)
