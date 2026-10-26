from django.test import SimpleTestCase

from campus.motion import (ControlError, MotionController, QUICK_TRAVEL_DESTINATIONS,
                           world)


class QuickTravelTests(SimpleTestCase):
    def controller(self):
        return MotionController({'floor': 'campus', 'x': 1998.75, 'y': 910.5, 'dir': 'up'})

    def test_four_destinations_are_walkable(self):
        self.assertEqual(len(QUICK_TRAVEL_DESTINATIONS), 4)
        for floor, x, y in QUICK_TRAVEL_DESTINATIONS.values():
            self.assertTrue(world(floor).walkable(x, y), (floor, x, y))

    def test_server_moves_only_to_whitelisted_destination(self):
        controller = self.controller()
        with self.assertRaises(ControlError):
            controller.command('quick_travel', {'destination': 'roof-secret', 'x': 200})
        self.assertEqual(controller.pose['floor'], 'campus')
        result = controller.command('quick_travel', {'destination': 'roof-workshop'})
        self.assertEqual(result['type'], 'quick_travel_done')
        self.assertEqual((controller.pose['floor'], controller.pose['x'], controller.pose['y']),
                         ('rooftop', 820, 825))
        self.assertFalse(controller.pose['moving'])

    def test_repeat_selection_is_noop_and_other_destination_is_throttled(self):
        controller = self.controller()
        controller.command('quick_travel', {'destination': 'campus-gallery'})
        self.assertTrue(controller.command('quick_travel', {'destination': 'campus-gallery'})['unchanged'])
        with self.assertRaises(ControlError):
            controller.command('quick_travel', {'destination': 'campus-market'})

    def test_classroom_cannot_fast_travel(self):
        controller = self.controller()
        controller.interior = {'subject': 'python'}
        with self.assertRaises(ControlError):
            controller.command('quick_travel', {'destination': 'roof-plaza'})
