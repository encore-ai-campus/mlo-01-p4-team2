"""Wire-only reduction: physics and full-state restoration stay unchanged."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from . import consumers


class CompactPacketTests(SimpleTestCase):
    async def test_mixed_clients_keep_stops_identity_changes_remote_players_and_effects(self):
        campus = consumers.CampusRoom()
        full = {"id": "local", "nickname": "테스트 이름", "avatar": "female-2", "color": "#4e9b91",
                "floor": "campus", "x": 1200.125, "y": 437.875, "dir": "right", "moving": True,
                "inClassroom": False}
        campus.players = {"local": full}
        campus.cluster.remote = {"remote": {"state": {**full, "id": "remote"}}}
        legacy = SimpleNamespace(compact_moves=False, send=AsyncMock())
        compact = SimpleNamespace(compact_moves=True, send=AsyncMock())
        campus.sockets = {"legacy": legacy, "compact": compact}
        frames = [full, {**full, "x": 1201.375},
                  {**full, "x": 2200.125, "y": 1700.875, "floor": "rooftop", "moving": False, "inClassroom": True},
                  {**full, "nickname": "새 이름", "avatar": "male-1", "color": "#8a78bb"}]
        index = 0

        async def next_frame(_):
            nonlocal index
            if index == len(frames):
                raise asyncio.CancelledError
            campus.pending_moves = {"local": frames[index]}
            campus.cluster.remote_moves = {"remote": {**frames[index], "id": "remote"}}
            campus.pending_effects = {"compact": ['{"type":"jump","actorId":"local"}']}
            index += 1

        with patch.object(consumers.cluster, "enabled", return_value=False), \
                patch.object(consumers.asyncio, "sleep", side_effect=next_frame):
            with self.assertRaises(asyncio.CancelledError):
                await consumers.flush_moves(campus)
        old = [json.loads(call.kwargs["text_data"]) for call in legacy.send.await_args_list]
        new = [json.loads(call.kwargs["text_data"]) for call in compact.send.await_args_list]
        for index, state in enumerate(frames):
            self.assertEqual(old[index]["players"], [state, {**state, "id": "remote"}])
            expected = state if index in (0, 3) else {key: value for key, value in state.items()
                                                      if key not in ("nickname", "avatar", "color")}
            self.assertEqual(new[index]["players"], [expected, {**expected, "id": "remote"}])
            self.assertEqual(new[index]["effects"], [{"type": "jump", "actorId": "local"}])
            self.assertNotIn("effects", old[index])
        self.assertIsNone(campus.flusher)

    async def test_self_control_and_welcome_use_compact_json_without_changing_the_structure(self):
        consumer = consumers.CampusConsumer()
        consumer.send = AsyncMock()
        for payload in ({"type": "welcome", "players": [{"nickname": "한 글 이름", "x": 1.125}]},
                        {"type": "control_state", "state": {"main": {"x": 1.125, "y": 2.875, "moving": False}}}):
            await consumer.send_packet(payload)
            text = consumer.send.await_args.kwargs["text_data"]
            self.assertEqual(json.loads(text), payload)
            self.assertEqual(text, json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
        with self.assertRaises(ValueError):
            await consumer.send_packet({"x": float("nan")})
