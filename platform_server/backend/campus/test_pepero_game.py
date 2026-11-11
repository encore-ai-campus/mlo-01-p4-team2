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
