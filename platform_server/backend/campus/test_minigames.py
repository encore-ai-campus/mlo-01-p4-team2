from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from django.test import SimpleTestCase
from . import consumers
from .motion import ControlError, MotionController


class MinigameControlTests(SimpleTestCase):
    def setUp(self):
        self.consumer = c = consumers.CampusConsumer()
        c.controller = MotionController({"floor": "campus", "x": 2317, "y": 262, "dir": "down"})
        point=c.controller.world.objects['minigame-timer']['interactionPoint']
        c.controller.pose.update(x=point[0],y=point[1])
        self.db = AsyncMock(return_value={"play_uuid": "play", "game_code": "timer"})
        patcher = patch.object(consumers.socket_db, "run", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_nearby_start_selects_game_from_server_object(self):
        result = await self.consumer.start_minigame({"objectId": "minigame-timer", "gameCode": "typing"})
        self.assertEqual(result["game_code"], "timer")
        self.db.assert_awaited_once_with(self.consumer.start_minigame_now, "timer")

    async def test_remote_invalid_other_floor_and_blocked_start_are_rejected(self):
        for target in ("minigame-typing", "manager-suhyun", None, []):
            with self.subTest(target=target), self.assertRaises(ControlError):
                await self.consumer.start_minigame({"objectId": target})
        with patch.object(self.consumer.controller.world, "clear", return_value=False):
            with self.assertRaises(ControlError):
                await self.consumer.start_minigame({"objectId": "minigame-timer"})
        self.consumer.controller.pending_transfer = {"toFloor": "rooftop"}
        with self.assertRaises(ControlError):
            await self.consumer.start_minigame({"objectId": "minigame-timer"})
        self.db.assert_not_awaited()

    def test_station_fronts_are_walkable_and_reachable_from_screenshot_and_entrance(self):
        world = self.consumer.controller.world
        starts = [{"x": 2232.9, "y": 297.97}, {"x": 2180, "y": 457.5}]
        for obj in world.objects.values():
            if obj.get('action') != 'minigame':
                continue
            point = obj['interactionPoint']
            self.assertTrue(world.walkable(*point))
            for player in starts:
                self.assertTrue(world.walkable(player["x"], player["y"]))
                self.assertIsNotNone(world.route(player, {"x": point[0], "y": point[1]}))

    def test_revoked_session_does_not_create_game(self):
        with patch.object(self.consumer, "session_user", return_value=None), patch.object(consumers, "start_game") as start:
            self.assertIsNone(self.consumer.start_minigame_now("timer"))
            start.assert_not_called()

    def test_office_entrance_has_a_clear_straight_corridor(self):
        world = self.consumer.controller.world
        self.assertNotIn('reference-campus-108', world.objects)
        self.assertIn('reference-campus-110', world.objects)
        for y in (448, 457.5, 467):
            self.assertTrue(world.clear({'x':2180, 'y':y}, {'x':2360, 'y':y}))
