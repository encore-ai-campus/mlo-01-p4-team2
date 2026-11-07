import asyncio
from contextlib import AsyncExitStack
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from autobahn.exception import Disconnected
from channels.layers import get_channel_layer
from django.db import DatabaseError
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from . import chat, consumers, session_registry
from .positions import DEFAULT_POSITION


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.001)


def task_state(task):
    return {"started": task is not None, "done": task.done() if task else None,
            "cancelled": task.cancelled() if task else None}


class CleanupProbe:
    """Exercise actual consumers; replace only DB boundaries and outbound transport."""

    def __init__(self, kind="movement", packet=None, error=None, failure=None):
        self.kind, self.packet, self.error, self.failure = kind, packet, error, failure
        self.incoming = asyncio.Queue()
        self.consumer = self.application = None
        self.ready = False
        self.outgoing, self.saved, self.discarded = [], [], []
        self.joined = set()
        self.injected = False
        self.save_started = asyncio.Event()
        self.save_gate = None

    async def __aenter__(self):
        self.baseline_tasks = asyncio.all_tasks()
        self.stack = AsyncExitStack()
        await self.stack.__aenter__()
        self.layer = get_channel_layer("service" if self.kind == "chat" else "default")
        await self.layer.flush()
        self.stack.enter_context(patch.object(consumers, "room", consumers.CampusRoom()))
        self.room = consumers.room
        self.stack.enter_context(patch.object(consumers, "freeze_state", AsyncMock(return_value={
            "revision": 0, "paused": False, "message": ""})))
        self.stack.enter_context(patch.object(consumers.socket_db, "run", self.database))
        self.cls = consumers.CampusConsumer if self.kind == "movement" else chat.ChatConsumer
        self.original_connect = connect = self.cls.connect

        async def connected(consumer):
            self.consumer = consumer
            await connect(consumer)
            self.ready = True

        self.stack.enter_context(patch.object(self.cls, "connect", connected))
        self.original_add, self.original_discard = self.layer.group_add, self.layer.group_discard

        async def group_add(group, channel):
            await self.original_add(group, channel)
            self.joined.add(group)
            if self.failure == "partial_add" and not self.injected:
                self.injected = True
                raise RuntimeError("partial group add")

        async def group_discard(group, channel):
            self.discarded.append(group)
            if self.failure == "partial_discard" and not self.injected:
                self.injected = True
                raise RuntimeError("first group discard")
            await self.original_discard(group, channel)

        self.stack.enter_context(patch.object(self.layer, "group_add", group_add))
        self.stack.enter_context(patch.object(self.layer, "group_discard", group_discard))
        return self

    async def database(self, function, *args, **kwargs):
        if function is consumers.needs_profile_setup:
            return False
        if function is consumers.check_session:
            return {"authenticated": True, "expiresAt": (time.time() + 3600) * 1000}
        if function is consumers.ensure_platform_session:
            return "probe-platform-session"
        if function is consumers.get_identity:
            return {"nickname": "probe", "avatar": "male-1"}
        if function is consumers.get_position:
            return DEFAULT_POSITION.copy()
        if function is consumers.touch_platform_session:
            return
        name = getattr(function, "__name__", "")
        if name == "identity_now":
            function.__self__.platform_session_uuid = "probe-platform-session"
            return {"nickname": "probe", "avatar": "male-1"}
        if name == "store_position_now":
            self.save_started.set()
            if self.save_gate:
                await self.save_gate.wait()
            if self.failure == "save_error":
                raise DatabaseError("position storage unavailable")
            self.saved.append(dict(args[0]))
            return
        raise AssertionError("Unexpected database boundary: " + repr(function))

    async def send(self, message):
        packet = json.loads(message.get("text", "{}"))
        self.outgoing.append(packet.get("type", message["type"]))
        if self.failure == "early_close" and packet.get("type") in {"welcome", "chat_ready"}:
            self.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
        if packet.get("type") == self.packet and self.error and not self.injected:
            self.injected = True
            if self.packet != "moves" and self.failure != "no_disconnect":
                self.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
            raise self.error("injected transport send failure")

    async def start(self, *, key="probe-session", user=1):
        scope = {"type": "websocket", "path": "/ws/" + self.kind + "/", "headers": [],
                 "query_string": b"control=server", "session": SimpleNamespace(session_key=key),
                 "user": SimpleNamespace(pk=user, is_authenticated=True, is_active=True,
                                         is_staff=False, is_superuser=False)}
        self.application = asyncio.create_task(self.cls.as_asgi()(scope, self.incoming.get, self.send))
        self.incoming.put_nowait({"type": "websocket.connect"})
        await until(lambda: self.ready or self.application.done())
        return self

    async def close(self):
        if not self.application.done():
            self.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await until(self.application.done)
        await asyncio.gather(self.application, return_exceptions=True)

    async def ping(self):
        previous = self.outgoing.count("pong")
        self.incoming.put_nowait({"type": "websocket.receive", "text": '{"type":"ping"}'})
        await until(lambda: self.outgoing.count("pong") > previous or self.application.done())

    def snapshot(self):
        consumer, room = self.consumer, self.room
        pid = getattr(consumer, "player_id", None)
        error = self.application.exception() if self.application.done() and not self.application.cancelled() else None
        return {"app_done": self.application.done(), "error": type(error).__name__ if error else None,
                "room_entries": sum(pid in value for value in (room.players, room.sockets, room.channels,
                                                               room.controllers, room.pending_moves, room.pending_effects)),
                "session_entry": bool(pid and room.sessions.get(getattr(consumer, "session_key", None)) == pid),
                "registry_entry": pid in session_registry._players,
                "groups": [group for group, members in self.layer.groups.items() if consumer.channel_name in members],
                "motion": task_state(getattr(consumer, "motion_task", None)),
                "guard": task_state(getattr(consumer, "guard_task", None)),
                "saving": task_state(getattr(consumer, "saving", None)),
                "connected": getattr(consumer, "connected", None), "saved": self.saved.copy()}

    async def __aexit__(self, *exception):
        if self.save_gate:
            self.save_gate.set()
        tasks = [task for task in asyncio.all_tasks() if task not in self.baseline_tasks]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.application:
            await asyncio.gather(self.application, return_exceptions=True)
        if self.consumer:
            session_registry.unregister(getattr(self.consumer, "player_id", None))
        await self.layer.flush()
        await self.stack.__aexit__(*exception)


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
                                  "service": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
                   CAMPUS_MOVEMENT_REDIS_URL="", CAMPUS_DM_ENABLED=True, CAMPUS_TEST_LEGACY_MOVEMENT=False)
class SocketCleanupTests(SimpleTestCase):
    def assert_clean(self, probe):
        row = probe.snapshot()
        self.assertEqual(row["room_entries"], 0, row)
        self.assertFalse(row["session_entry"], row)
        self.assertFalse(row["registry_entry"], row)
        self.assertFalse(row["groups"], row)
        for task in (row["motion"], row["guard"], row["saving"]):
            self.assertTrue(not task["started"] or task["done"], row)
        if probe.kind == "chat":
            self.assertFalse(row["connected"], row)

    async def test_normal_and_early_close_are_idempotent(self):
        for kind in ("movement", "chat"):
            for failure in (None, "early_close"):
                with self.subTest(kind=kind, failure=failure):
                    async with CleanupProbe(kind, failure=failure) as probe:
                        await probe.start()
                        await probe.close()
                        self.assert_clean(probe)
                        await probe.consumer.disconnect(1000)
                        await probe.consumer.disconnect(1000)
                        self.assert_clean(probe)
                        self.assertIsNone(probe.application.exception())

    async def test_failure_before_connect_initialization_keeps_original_error(self):
        for cls in (consumers.CampusConsumer, chat.ChatConsumer):
            with self.subTest(consumer=cls.__name__):
                layer = get_channel_layer(cls().channel_layer_alias)
                with patch.object(layer, "new_channel", AsyncMock(side_effect=RuntimeError("new channel failed"))):
                    with self.assertRaisesMessage(RuntimeError, "new channel failed"):
                        await cls()({"type": "websocket"}, AsyncMock(), AsyncMock())

    async def test_initial_and_active_transport_failures_cleanup(self):
        for kind, packets in (("movement", ("welcome", "pepero_status", "pong")),
                              ("chat", ("chat_ready", "pong"))):
            for packet in packets:
                for error in (Disconnected, OSError):
                    with self.subTest(kind=kind, packet=packet, error=error):
                        async with CleanupProbe(kind, packet, error) as probe:
                            await probe.start()
                            if packet == "pong":
                                await probe.ping()
                            await until(probe.application.done)
                            self.assertIsNone(probe.application.exception())
                            self.assert_clean(probe)

    async def test_partial_initialization_and_group_failure_do_not_skip_cleanup(self):
        for kind in ("movement", "chat"):
            for failure in ("partial_add", "partial_discard"):
                with self.subTest(kind=kind, failure=failure):
                    async with CleanupProbe(kind, failure=failure) as probe:
                        await probe.start()
                        await probe.close()
                        self.assert_clean(probe)
                        if failure == "partial_add":
                            self.assertIsInstance(probe.application.exception(), RuntimeError)
                        else:
                            self.assertEqual(set(probe.discarded), probe.joined)
                            self.assertGreaterEqual(len(probe.discarded), len(probe.joined) + 1)

    async def test_completed_failed_tasks_are_observed_and_do_not_block_cleanup(self):
        async def failed():
            raise RuntimeError("completed task failure")

        for kind in ("movement", "chat"):
            with self.subTest(kind=kind):
                async with CleanupProbe(kind) as probe:
                    await probe.start()
                    consumer = probe.consumer
                    for name in (("guard_task", "motion_task", "saving") if kind == "movement" else ("guard_task",)):
                        task = getattr(consumer, name, None)
                        if task:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                        setattr(consumer, name, asyncio.create_task(failed()))
                    await asyncio.sleep(0)
                    with self.assertLogs("campus.socket_lifecycle", level="ERROR") as logs:
                        await probe.close()
                    self.assertTrue(any("completed task failure" in message for message in logs.output))
                    self.assert_clean(probe)

    async def test_unknown_send_and_handler_errors_propagate_after_cleanup(self):
        for kind in ("movement", "chat"):
            for error in (RuntimeError, DatabaseError, OSError):
                with self.subTest(kind=kind, error=error):
                    async with CleanupProbe(kind) as probe:
                        await probe.start()
                        probe.consumer.policy_status["expiresAt"] = 0
                        probe.consumer.current_identity = AsyncMock(side_effect=error("handler boundary failed"))
                        await probe.ping()
                        await until(probe.application.done)
                        self.assertIsInstance(probe.application.exception(), error)
                        self.assert_clean(probe)
            async with CleanupProbe(kind, "pong", RuntimeError) as probe:
                await probe.start()
                await probe.ping()
                await until(probe.application.done)
                self.assertIsInstance(probe.application.exception(), RuntimeError)
                self.assert_clean(probe)

    async def test_database_save_failure_is_logged_and_cleanup_continues(self):
        async with CleanupProbe(failure="save_error") as probe:
            await probe.start()
            probe.consumer.position_dirty = True
            probe.consumer.position_changed_at = timezone.now()
            with self.assertLogs("campus.consumers", level="ERROR") as logs:
                await probe.close()
            self.assertTrue(any("position storage unavailable" in message for message in logs.output))
            self.assertTrue(probe.consumer.position_dirty)
            self.assert_clean(probe)

    async def test_pending_save_finishes_and_keeps_last_position_on_transport_close(self):
        async with CleanupProbe(packet="pong", error=Disconnected) as probe:
            await probe.start()
            consumer = probe.consumer
            consumer.position_dirty = True
            consumer.position_changed_at = timezone.now()
            consumer.state["x"] += 9
            expected = consumer.state["x"]
            probe.save_gate = asyncio.Event()

            async def save_message(text):
                await consumer.checkpoint(force=True)

            consumer.handle_message = save_message
            probe.incoming.put_nowait({"type": "websocket.receive", "text": "save"})
            await probe.save_started.wait()
            with self.assertRaises(Exception):
                await consumer.send_packet({"type": "pong"})
            await asyncio.sleep(.01)
            self.assertFalse(consumer.saving.done())
            self.assertFalse(probe.application.done())
            probe.save_gate.set()
            await until(probe.application.done)
            self.assertIsNone(probe.application.exception())
            self.assertEqual(probe.saved[-1]["x"], expected)
            self.assertFalse(consumer.position_dirty)
            self.assert_clean(probe)

    async def test_background_batch_send_failure_ends_the_connection(self):
        async with CleanupProbe(packet="moves", error=Disconnected) as probe:
            await probe.start()
            probe.room.pending_moves[probe.consumer.player_id] = probe.consumer.state.copy()
            await until(probe.application.done)
            self.assertIsNone(probe.application.exception())
            self.assert_clean(probe)

    async def test_background_motion_transport_stop_is_observed_as_disconnect(self):
        async with CleanupProbe(failure="no_disconnect") as probe:
            await probe.start()
            probe.packet, probe.error = "control_state", Disconnected
            with self.assertLogs("campus.socket_lifecycle", level="INFO") as logs:
                probe.incoming.put_nowait({"type": "websocket.receive", "text": '{"type":"input","keys":["d"]}'})
                await until(probe.application.done)
            self.assertTrue(any("Disconnected" in message for message in logs.output))
            self.assertFalse(any("ERROR:" in message for message in logs.output))
            self.assertIsNone(probe.application.exception())
            self.assert_clean(probe)

    async def test_last_position_is_saved_on_normal_close(self):
        async with CleanupProbe() as probe:
            await probe.start()
            probe.consumer.position_dirty = True
            probe.consumer.position_changed_at = timezone.now()
            probe.consumer.state["x"] += 13
            expected = probe.consumer.state.copy()
            await probe.close()
            self.assertEqual(probe.saved, [expected])
            self.assert_clean(probe)

    async def test_late_old_cleanup_keeps_replacement_and_other_user(self):
        async with CleanupProbe() as probe:
            await probe.start()
            old = probe.consumer
            sockets = []

            async def open_socket(key, user):
                consumer, incoming, packets = consumers.CampusConsumer(), asyncio.Queue(), []

                async def outgoing(message):
                    packets.append(json.loads(message.get("text", "{}")))

                scope = {**old.scope, "session": SimpleNamespace(session_key=key),
                         "user": SimpleNamespace(pk=user, is_authenticated=True, is_active=True,
                                                 is_staff=False, is_superuser=False)}
                application = asyncio.create_task(consumer(scope, incoming.get, outgoing))
                sockets.append((consumer, incoming, packets, application))
                incoming.put_nowait({"type": "websocket.connect"})
                await until(lambda: getattr(consumer, "motion_task", None) is not None or application.done())
                self.assertFalse(application.done())
                return consumer

            with patch.object(consumers.CampusConsumer, "connect", probe.original_connect):
                peer = await open_socket("other-session", 2)
                replacement = await open_socket("probe-session", 1)
            old.state["x"] = 999
            old.position_dirty = True
            old.position_changed_at = timezone.now()
            await probe.close()
            self.assertEqual(probe.room.sessions["probe-session"], replacement.player_id)
            self.assertIn(replacement.player_id, probe.room.players)
            self.assertIn(peer.player_id, probe.room.players)
            self.assertNotIn(old.player_id, session_registry._players)
            self.assertNotIn(999, [row["x"] for row in probe.saved])
            for consumer, incoming, packets, application in sockets:
                incoming.put_nowait({"type": "websocket.receive", "text": '{"type":"ping"}'})
                await until(lambda packets=packets: any(row.get("type") == "pong" for row in packets))
                incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
                await until(application.done)
                self.assertIsNone(application.exception())
            self.assert_clean(probe)
