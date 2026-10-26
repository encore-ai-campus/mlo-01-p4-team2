import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase
from .interest import InterestView, SpatialIndex
from . import consumers


def state(pid, x=0, floor='campus', **extra):
    return dict(id=pid, nickname=pid, avatar='male-1', color='#dfa34d', x=x, y=0,
                floor=floor, dir='right', moving=False, running=False, guarding=False,
                inClassroom=False, **extra)


class NearbyInterestTests(SimpleTestCase):
    def test_only_same_floor_and_nearby_receive_running_guard_and_inventory(self):
        me = state('me')
        near = {**state('near', 150), 'running': True, 'guarding': True, 'peperoRemaining': 7}
        view = InterestView()
        packet = view.packet(SpatialIndex([me, near, state('far', 1800), state('roof', 10, 'rooftop')]),
                             me, {'near', 'far', 'roof'})
        self.assertEqual(packet['players'], [{**near, 'inView': True}])
        self.assertEqual(view.packet(SpatialIndex([me, near]), me, set())['players'], [])

    def test_moving_viewer_discovers_stationary_actor_and_hysteresis_prevents_flicker(self):
        me, other = state('me'), state('other', 1600)
        view = InterestView()
        self.assertEqual(view.packet(SpatialIndex([me, other]), me, set())['players'], [])
        me['x'] = 500
        self.assertEqual(view.packet(SpatialIndex([me, other]), me, {'me'})['players'][0]['nickname'], 'other')
        me['x'] = 250  # 1350 away, still visible after entering at 1200.
        self.assertNotIn('hidden', view.packet(SpatialIndex([me, other]), me, {'me'}))
        me['x'] = 0
        self.assertEqual(view.packet(SpatialIndex([me, other]), me, {'me'})['hidden'], ['other'])
        other.update(x=100, guarding=True, peperoRemaining=3)
        restored = view.packet(SpatialIndex([me, other]), me, {'other'})['players'][0]
        self.assertEqual((restored['x'], restored['nickname'], restored['guarding'], restored['peperoRemaining']),
                         (100, 'other', True, 3))

    def test_floor_teleport_and_classroom_clear_old_actors(self):
        me, old, roof = state('me'), state('old', 50), state('roof', 50, 'rooftop')
        index = SpatialIndex([me, old, roof]); view = InterestView()
        view.packet(index, me, set())
        me['floor'] = 'rooftop'
        packet = view.packet(SpatialIndex([me, old, roof]), me, {'me'})
        self.assertEqual(packet['hidden'], ['old'])
        self.assertEqual([row['id'] for row in packet['players']], ['roof'])
        me['inClassroom'] = True
        self.assertEqual(view.packet(SpatialIndex([me, old, roof]), me, {'me'})['hidden'], ['roof'])

    def test_admin_overview_still_excludes_other_floors_and_private_classrooms(self):
        me = state('me'); view = InterestView()
        hidden = {**state('private', 5), 'inClassroom': True}
        packet = view.packet(SpatialIndex([me, state('far', 9000), state('roof', 0, 'rooftop'), hidden]),
                             me, set(), overview=True)
        self.assertEqual([row['id'] for row in packet['players']], ['far'])

    def test_compaction_preserves_stop_and_changed_identity(self):
        me, other = state('me'), state('other', 30); view = InterestView()
        view.packet(SpatialIndex([me, other]), me, set())
        other['running'] = False
        packet = view.packet(SpatialIndex([me, other]), me, {'other'})
        self.assertNotIn('nickname', packet['players'][0])
        self.assertFalse(packet['players'][0]['running'])
        other['nickname'] = 'new name'
        self.assertEqual(view.packet(SpatialIndex([me, other]), me, {'other'})['players'][0]['nickname'], 'new name')

    async def test_flush_routes_remote_and_local_updates_and_reconciles_without_movement(self):
        campus = consumers.CampusRoom()
        me, other, remote = state('me'), state('other', 50), state('remote', 200)
        campus.players = {'me': me, 'other': other}
        campus.cluster.remote = {'remote': {'state': remote}}
        socket = SimpleNamespace(nearby_moves=True, compact_moves=True, view_overview=False,
                                 interest=InterestView(), send=AsyncMock())
        campus.sockets = {'me': socket}
        frames = 0
        async def frame(_):
            nonlocal frames
            frames += 1
            if frames == 1:
                campus.pending_moves = {'other': other}
            elif frames == 2:
                me['floor'] = 'rooftop'
                campus.pending_moves = {'me': me}
            elif frames == 3:
                campus.interest_dirty = True  # New participant arrives without any movement.
                campus.cluster.remote['remote']['state'] = {**remote, 'floor': 'rooftop'}
            else:
                raise asyncio.CancelledError
        with patch.object(consumers.cluster, 'enabled', return_value=False), patch.object(consumers.asyncio, 'sleep', side_effect=frame):
            with self.assertRaises(asyncio.CancelledError):
                await consumers.flush_moves(campus)
        packets = [json.loads(call.kwargs['text_data']) for call in socket.send.await_args_list]
        self.assertEqual({p['id'] for p in packets[0]['players']}, {'other', 'remote'})
        self.assertEqual(set(packets[1]['hidden']), {'other', 'remote'})
        self.assertEqual([p['id'] for p in packets[2]['players']], ['remote'])

    def test_spread_out_users_reduce_state_fanout(self):
        people = [state(str(i), x=i * 100) for i in range(100)]
        index = SpatialIndex(people); changed = {row['id'] for row in people}
        delivered = sum(len(InterestView().packet(index, person, changed)['players']) for person in people)
        self.assertLess(delivered, 100 * 99 / 3)


from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import TransactionTestCase, override_settings
from . import test_load_reduction as fixtures


@override_settings(CAMPUS_TEST_LEGACY_MOVEMENT=False, KAFKA_BOOTSTRAP_SERVERS='')
class NearbySocketTests(TransactionTestCase):
    setUp = fixtures.IdleTrafficTests.setUp
    login = fixtures.IdleTrafficTests.login
    drain = fixtures.IdleTrafficTests.drain
    disconnect_all = fixtures.IdleTrafficTests.disconnect_all

    async def connect_nearby(self, cookie):
        from config.asgi import application
        socket = WebsocketCommunicator(application, '/ws/campus/?control=server&moves=compact&view=nearby',
            headers=[(b'origin', b'http://127.0.0.1'), (b'host', b'127.0.0.1'), (b'cookie', cookie)])
        self.assertTrue((await socket.connect(timeout=5))[0])
        self.sockets.append(socket)
        while (packet := await socket.receive_json_from(timeout=2))['type'] != 'welcome':
            pass
        socket.player_id = packet['selfId']
        socket.welcome = packet
        return socket

    async def test_real_connections_hide_far_actor_restore_it_and_preserve_roster(self):
        try:
            a = await self.connect_nearby(self.cookies[0])
            b = await self.connect_nearby(self.cookies[1])
            self.assertTrue(self.room.sockets[a.player_id].nearby_moves)
            await self.drain(a, .25); await self.drain(b)
            observer = self.room.players[a.player_id]
            target = self.room.sockets[b.player_id]
            # A valid distant point on the same floor, independent of event furniture.
            w = target.controller.world
            point = next((x, y) for x in range(60, 2400, 60) for y in range(60, 1200, 60)
                         if w.walkable(x, y) and ((x-observer['x'])**2+(y-observer['y'])**2)**.5 > 1500)
            target.controller.pose.update(x=point[0], y=point[1])
            await target.receive_movement({**target.controller.pose, 'color': target.state['color']}, authoritative=True)
            packets = await self.drain(a, .25)
            self.assertTrue(any(b.player_id in packet.get('hidden', []) for packet in packets))
            self.assertFalse(any(row['id'] == b.player_id for packet in packets for row in packet.get('players', [])))
            self.assertFalse(any(packet['type'] == 'leave' for packet in packets))
            target.controller.pose.update(x=observer['x']+20, y=observer['y'])
            await target.receive_movement({**target.controller.pose, 'color': target.state['color']}, authoritative=True)
            packets = await self.drain(a, .25)
            restored = next(row for packet in packets for row in packet.get('players', []) if row['id'] == b.player_id)
            self.assertTrue(restored['inView'])
            self.assertIn('nickname', restored)
            self.assertIn('guarding', restored)
        finally:
            await self.disconnect_all()
