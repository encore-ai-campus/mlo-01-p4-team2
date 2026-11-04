from unittest.mock import patch
from django.test import SimpleTestCase
from campus.motion import MotionController, world, mission_geometry, ControlError
from event_story.access import issue

class MissionRoomTests(SimpleTestCase):
    def controller(self, n=1):
        e=mission_geometry()['entrances'][n-1]
        return MotionController({'floor':'rooftop','x':e['x'],'y':e['y'],'dir':'up'})
    def enter(self,n=1):
        c=self.controller(n)
        with patch('campus_control.service.read_state',return_value={'paused':False}):c.command('mission_room_enter',{'room':n})
        return c
    def test_rooms_are_not_auto_entered(self):
        c=self.controller();c.tick(.02);self.assertIsNone(c.interior)
        self.assertEqual(self.enter().interior['subject'],'mission1')
    def test_wrong_floor_far_entrance_and_freeze_are_rejected(self):
        c=self.controller();c.pose['x']-=180
        with patch('campus_control.service.read_state',return_value={'paused':False}),self.assertRaises(ControlError):c.command('mission_room_enter',{'room':1})
        c=self.controller();c.pose['floor']='campus'
        with patch('campus_control.service.read_state',return_value={'paused':False}),self.assertRaises(ControlError):c.command('mission_room_enter',{'room':1})
    def test_all_stations_reachable_and_block_solid_bases(self):
        for n in (1,2):
            w=world('mission'+str(n));self.assertTrue(w.walkable(*w.data['spawn']))
            for o in w.data['objects']:
                if not o.get('code'):continue
                self.assertFalse(w.walkable(o['x']+o['w']/2,o['y']+o['h']/2))
                self.assertTrue(w.walkable(*o['interactionPoint']))
                self.assertIsNotNone(w.route(dict(zip(('x','y'),w.data['spawn'])),dict(zip(('x','y'),o['interactionPoint']))))
    def test_interact_only_near_station_and_ticket_requires_inside(self):
        c=self.enter();
        with self.assertRaises(ControlError):issue('test-user',self.controller(),{'target':'room1'})
        with self.assertRaises(ControlError):issue('test-user',c,{'target':'room1'})
        point=world('mission1').data['objects'][0]['interactionPoint'];c.interior['actor'].update(zip(('x','y'),point))
        self.assertEqual(c.command('interact',{})['code'],'sql_memory')
        self.assertIn('ticket',issue('test-user',c,{'target':'room1'}))
        with self.assertRaises(ControlError):issue('test-user',c,{'target':'room2'})
    def test_exit_restores_rooftop_and_stops_keys(self):
        c=self.enter();original=dict(c.pose);c.last_classroom_transition-=3
        c.interior['actor'].update(zip(('x','y'),world('mission1').data['exit']['interactionPoint']))
        c.input(['arrowdown']);c.tick(.02);self.assertIsNotNone(c.interior)
        c.command('interact',{});self.assertIsNone(c.interior);self.assertEqual(c.pose['floor'],'rooftop')
        self.assertEqual(c.pose['x'],original['x']);self.assertFalse(c.keys)
    def test_roof_buildings_and_managers_have_reachable_approaches(self):
        for e in mission_geometry()['entrances']:
            w=world(e['floor']);b=e['building']
            self.assertFalse(w.walkable(b['x']+b['w']/2,b['y']+b['h']/2))
            self.assertTrue(w.walkable(e['x'],e['y']))
            self.assertIsNotNone(w.route({'x':2325,'y':1565},{'x':e['x'],'y':e['y']}))
        for npc in mission_geometry()['npcs']:
            w=world(npc['floor']);self.assertTrue(w.walkable(npc['x']+(60 if npc['id']=='manager-suhyeon' else 0),npc['y']+(0 if npc['id']=='manager-suhyeon' else 50)))

    def test_compact_room_bounds_and_clockwise_workstations(self):
        w=world('mission2');self.assertEqual((w.data['width'],w.data['height']),(400,340))
        self.assertFalse(w.walkable(410,250));self.assertFalse(w.walkable(200,330))
        stations=[o for o in w.data['objects'] if o.get('code')]
        self.assertEqual([o['facing'] for o in stations],['south','west','north','east'])
        self.assertTrue(all(o['artHeight']<=66 for o in stations))
