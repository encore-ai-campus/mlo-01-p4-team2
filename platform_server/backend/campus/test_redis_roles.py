"""Role Redis routing without external Redis servers."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from accounts.session_policy import notify_expiry
from direct_messages.live import notify
from . import cluster
from .chat import ChatConsumer
from .consumers import CampusConsumer
from .session_registry import session_group


MEMORY = {"BACKEND": "channels.layers.InMemoryChannelLayer"}
SPLIT_LAYERS = {"default": MEMORY, "service": MEMORY}
MOVEMENT_URL = "redis://movement.internal:6379/0"
SERVICE_URL = "redis://service.internal:6379/0"


class RedisSettingsTests(SimpleTestCase):
    def load_settings(self, shared="", movement="", service=""):
        spec = importlib.util.spec_from_file_location(
            "config.redis_roles_test_settings", Path(__file__).parents[1] / "config" / "settings.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"CAMPUS_REDIS_URL": shared,
                                    "CAMPUS_MOVEMENT_REDIS_URL": movement,
                                    "CAMPUS_SERVICE_REDIS_URL": service,
                                    "DATABASE_ENGINE": "sqlite", "DJANGO_DEBUG": "1"}), \
                patch("dotenv.load_dotenv"):
            spec.loader.exec_module(module)
        return module

    def test_legacy_shared_url_and_equal_role_urls_reuse_one_layer(self):
        for values in ({"shared": MOVEMENT_URL},
                       {"movement": MOVEMENT_URL, "service": MOVEMENT_URL}):
            with self.subTest(values=values):
                config = self.load_settings(**values)
                self.assertEqual(set(config.CHANNEL_LAYERS), {"default"})
                self.assertEqual(config.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"], [MOVEMENT_URL])

    def test_split_urls_override_the_shared_fallback(self):
        config = self.load_settings(shared="redis://old.internal:6379/0",
                                    movement=MOVEMENT_URL, service=SERVICE_URL)
        self.assertEqual(config.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"], [MOVEMENT_URL])
        self.assertEqual(config.CHANNEL_LAYERS["service"]["CONFIG"]["hosts"], [SERVICE_URL])
        self.assertEqual(config.CHANNEL_LAYERS["service"]["CONFIG"]["prefix"], "campus.service")
        self.assertEqual(config.CAMPUS_MOVEMENT_REDIS_URL, MOVEMENT_URL)
        fallback = self.load_settings(shared=SERVICE_URL, movement=MOVEMENT_URL)
        self.assertEqual(fallback.CHANNEL_LAYERS["service"]["CONFIG"]["hosts"], [SERVICE_URL])

    def test_empty_urls_preserve_a_single_memory_layer(self):
        self.assertEqual(self.load_settings().CHANNEL_LAYERS, {"default": MEMORY})

    def test_partial_role_configuration_fails_instead_of_using_memory(self):
        for values in ({"movement": MOVEMENT_URL}, {"service": SERVICE_URL}):
            with self.subTest(values=values), self.assertRaisesMessage(ValueError, "Configure both"):
                self.load_settings(**values)


@override_settings(CHANNEL_LAYERS=SPLIT_LAYERS)
class RedisRoleRoutingTests(SimpleTestCase):
    async def test_role_channels_are_separate_and_expiry_reaches_both(self):
        movement, service = get_channel_layer(), get_channel_layer("service")
        self.assertEqual(CampusConsumer().channel_layer_alias, "default")
        self.assertEqual(ChatConsumer().channel_layer_alias, "service")
        channels = [await layer.new_channel() for layer in (movement, service)]
        for layer, channel in zip((movement, service), channels):
            await layer.group_add(session_group("login-key"), channel)
        await sync_to_async(notify_expiry)("login-key", "replaced")
        for layer, channel in zip((movement, service), channels):
            self.assertEqual(await layer.receive(channel), {"type": "session.expired", "reason": "replaced"})

    async def test_private_notification_goes_only_to_service(self):
        from direct_messages.live import user_group

        movement, service = get_channel_layer(), get_channel_layer("service")
        channels = [await layer.new_channel() for layer in (movement, service)]
        for layer, channel in zip((movement, service), channels):
            await layer.group_add(user_group(7), channel)
        await sync_to_async(notify)(7, "conversation-id")
        self.assertEqual(await service.receive(channels[1]),
                         {"type": "direct.message", "conversationId": "conversation-id"})
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(movement.receive(channels[0]), timeout=.02)

    def test_failed_notification_still_attempts_the_other_role_then_raises(self):
        for failed_alias in ("default", "service"):
            with self.subTest(failed_alias=failed_alias):
                error = OSError("Redis unavailable")
                layers = {alias: SimpleNamespace(group_send=AsyncMock(
                    side_effect=error if alias == failed_alias else None)) for alias in SPLIT_LAYERS}
                with patch("accounts.session_policy.get_channel_layer", side_effect=layers.__getitem__), \
                        self.assertLogs("accounts.session_policy", level="WARNING"), \
                        self.assertRaises(OSError) as raised:
                    notify_expiry("login-key", "signed_out")
                self.assertIs(raised.exception, error)
                for layer in layers.values():
                    layer.group_send.assert_awaited_once()

    @override_settings(CHANNEL_LAYERS={"default": MEMORY})
    def test_single_layer_preserves_chat_and_sends_expiry_once(self):
        self.assertEqual(ChatConsumer().channel_layer_alias, "default")
        layer = SimpleNamespace(group_send=AsyncMock())
        with patch("accounts.session_policy.get_channel_layer", return_value=layer) as lookup:
            notify_expiry("login-key", "idle")
        lookup.assert_called_once_with("default")
        layer.group_send.assert_awaited_once()


@override_settings(CAMPUS_MOVEMENT_REDIS_URL=MOVEMENT_URL, CAMPUS_SERVICE_REDIS_URL=SERVICE_URL)
class MovementPositionRedisTests(SimpleTestCase):
    async def test_position_writer_uses_movement_redis(self):
        pipe = SimpleNamespace(set=Mock(), execute=AsyncMock())
        client = SimpleNamespace(pipeline=Mock(return_value=pipe))
        position = {"floor": "campus", "x": 120, "y": 45, "dir": "left"}
        connect = Mock(return_value=client)
        fake_redis = SimpleNamespace(asyncio=SimpleNamespace(Redis=SimpleNamespace(from_url=connect)))
        with patch.dict("sys.modules", {"redis": fake_redis}):
            await cluster.Cluster().save_positions([("key", 7, position, timezone.now())])
        connect.assert_called_once_with(MOVEMENT_URL)
        self.assertEqual(pipe.set.call_args.args[0], cluster.position_key("key"))
        pipe.execute.assert_awaited_once()

    def test_position_reader_uses_movement_redis_from_service_or_jobs(self):
        observed = timezone.now()
        position = {"floor": "campus", "x": 120, "y": 45, "dir": "left"}
        client = SimpleNamespace(get=Mock(return_value=json.dumps(
            {"user": "7", "position": position, "observedAt": observed.isoformat()})))
        connect = Mock(return_value=client)
        with patch.object(cluster, "_sync_redis", None), \
                patch.dict("sys.modules", {"redis": SimpleNamespace(Redis=SimpleNamespace(from_url=connect))}):
            self.assertEqual(cluster.stored_position("key", 7), (position, observed))
        connect.assert_called_once_with(MOVEMENT_URL, socket_timeout=1)
