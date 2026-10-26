"""Real server collision and routing for the standalone Pepero map."""
import json
from pathlib import Path
from django.test import SimpleTestCase
from .motion import geometry, world

SOURCE = Path(__file__).resolve().parents[2] / 'frontend/maps/pepero-worlds.json'

class EventMapTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.maps = json.loads(SOURCE.read_text(encoding='utf-8'))['worlds']

    def test_event_asset_bodies_block_and_gate_centres_remain_open(self):
        for name, data in self.maps.items():
            w = world(name)
            self.assertTrue(w.walkable(*data['spawn']), name)
            for obj in data['objects']:
                if not obj.get('seasonalArt') or not obj.get('solid'):
                    continue
                with self.subTest(floor=name, asset=obj['id']):
                    if obj['seasonalArt'] == 'gate':
                        self.assertTrue(w.walkable(obj['x']+obj['w']/2, obj['y']+obj['h']/2))
                        for part in obj['collisionParts']:
                            self.assertFalse(w.walkable(obj['x']+part['x']+part['w']/2, obj['y']+part['y']+part['h']/2))
                    elif obj.get('collisionPolygon'):
                        points=obj['collisionPolygon']
                        cx=sum(p[0] for p in points)/len(points);cy=sum(p[1] for p in points)/len(points)
                        self.assertFalse(w.walkable(cx,cy))
                        # Every edge, and inward samples near it, must block the actor radius.
                        for a,b in zip(points,points[1:]+points[:1]):
                            x=(a[0]+b[0])/2;y=(a[1]+b[1])/2
                            self.assertFalse(w.walkable(x,y))
                            self.assertFalse(w.walkable(x*.9+cx*.1,y*.9+cy*.1))
                    else:
                        self.assertFalse(w.walkable(obj['x']+obj['w']/2, obj['y']+obj['h']/2))

    def test_portals_booths_and_classroom_gates_reachable(self):
        for name, data in self.maps.items():
            w = world(name)
            start = dict(zip(('x','y'),data['spawn']))
            for obj in data['objects']:
                if not obj.get('toFloor') and obj.get('action') != 'booth':
                    continue
                p = obj.get('approach') or obj.get('interactionPoint') or [obj['x']+obj['w']/2,obj['y']+obj['h']/2]
                with self.subTest(floor=name, destination=obj['id']):
                    self.assertIsNotNone(w.route(start, dict(zip(('x','y'),p))))
                if obj.get('arrival'):
                    self.assertTrue(world(obj['toFloor']).walkable(*obj['arrival']), obj['id'])
            if name == 'campus':
                for gate in geometry()['gates']:
                    self.assertIsNotNone(w.route(start, {'x':(gate['left']+gate['right'])/2,'y':gate['y']-12}))
