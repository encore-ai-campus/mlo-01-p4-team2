import random
from unittest.mock import patch

from django.test import SimpleTestCase

from .interest import InterestView, SpatialIndex
from .motion import MotionController, world


def actor(pid, x=0, **extra):
    return dict(id=pid, x=x, y=0, floor='campus', nickname=pid,
                avatar='male-1', color='#dfa34d', inClassroom=False, **extra)


class MovementWorkTests(SimpleTestCase):
    def test_stationary_observer_does_not_rescan_neighbours(self):
        me, other = actor('me'), actor('other', 30)
        view = InterestView()
        view.packet(SpatialIndex([me, other]), me, set())
        other['x'] = 40
        index = SpatialIndex([me, other])
        with patch.object(index, 'candidates', side_effect=AssertionError('full rescan')):
            self.assertEqual(view.packet(index, me, {'other'})['players'][0]['x'], 40)
            self.assertEqual(view.packet(index, me, set())['players'], [])

    def test_unchanged_observer_hides_departed_and_private_actors(self):
        me, other = actor('me'), actor('other', 30)
        view = InterestView()
        view.packet(SpatialIndex([me, other]), me, set())
        other['inClassroom'] = True
        self.assertEqual(view.packet(SpatialIndex([me, other]), me, {'other'})['hidden'], ['other'])
        other['inClassroom'] = False
        view.packet(SpatialIndex([me, other]), me, {'other'})
        self.assertEqual(view.packet(SpatialIndex([me]), me, set())['hidden'], ['other'])

    def test_incremental_packets_match_full_recalculation(self):
        rng = random.Random(921)
        people = {str(i): actor(str(i), i * 90) for i in range(60)}
        incremental, reference = InterestView(), InterestView()
        for frame in range(400):
            changed = set(rng.sample(list(people), 3))
            for pid in changed:
                people[pid].update(x=rng.randint(-2000, 4000), y=rng.randint(-1000, 1000),
                                   floor=rng.choice(['campus', 'rooftop']),
                                   inClassroom=rng.random() < .1, nickname=f'{pid}-{frame}')
            if frame % 13 == 0:
                people['0']['x'] += 100
                changed.add('0')
            index = SpatialIndex(people.values())
            overview = frame % 51 < 4
            args = dict(compact=frame % 2 == 0, overview=overview)
            self.assertEqual(incremental.packet(index, people['0'], changed, **args),
                             reference.packet(index, people['0'], changed, reconcile=True, **args))

    def test_roster_reconciliation_discovers_idle_arrival(self):
        me = actor('me'); view = InterestView()
        view.packet(SpatialIndex([me]), me, set())
        packet = view.packet(SpatialIndex([me, actor('new', 30)]), me, set(), reconcile=True)
        self.assertEqual(packet['players'][0]['id'], 'new')

    def controller(self):
        w = world('rooftop')
        return MotionController(dict(floor='rooftop', x=w.data['spawn'][0], y=w.data['spawn'][1], dir='up'))

    def test_idle_hint_cache_and_coordinate_invalidation(self):
        c = self.controller()
        expected = c.hints()
        with patch.object(c.world, 'distance', wraps=c.world.distance) as distance:
            returned = c.hints()
            returned['nearbyId'] = 'corrupted'
            self.assertEqual(c.hints(), expected)
            distance.assert_not_called()
            c.pose['x'] += .01
            c.hints()
            self.assertEqual(distance.call_count, len(c.world.hint_objects))

    def test_hint_cache_invalidates_on_world_change(self):
        c = self.controller(); c.hints()
        c.world = world('campus')
        with patch.object(c.world, 'distance', wraps=c.world.distance) as distance:
            c.hints()
            self.assertEqual(distance.call_count, len(c.world.hint_objects))
