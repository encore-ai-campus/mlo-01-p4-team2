"""Authenticated public chat and private-message notifications; no movement state."""
import asyncio
from contextlib import suppress
from datetime import timedelta
from importlib import import_module
import json
import logging
import math
import time
from types import SimpleNamespace

from channels.consumer import get_handler_name
from channels.exceptions import ChannelFull
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.contrib.auth import get_user, get_user_model
from django.core import signing
from django.db import DatabaseError, transaction
from django.utils import timezone

from accounts.models import get_identity, needs_profile_setup
from accounts.session_policy import check_session
from direct_messages.live import user_group
from eventing.events import SESSION_KEY, record_chat
from eventing.models import ChatMessage
from . import session_registry, socket_db
from .chat_tokens import verify_actor_token

logger = logging.getLogger(__name__)


class ChatRateLimited(Exception):
    pass


class ChatConsumer(AsyncWebsocketConsumer):
    group = "campus.chat.public"
    session_check_interval = 10
    chat_interval = 0.8
    max_chat_length = 200

    async def connect(self):
        self.groups_joined = []
        self.guard_task = None
        self.connected = False
        self.last_chat = self.last_activity_write = -math.inf
        self.user = self.scope["user"]
        if not self.user.is_authenticated or not self.user.is_active:
            await self.close(code=4401)
            return
        if await socket_db.run(needs_profile_setup, self.user):
            await self.close(code=4403)
            return
        self.session_key = self.scope["session"].session_key
        self.policy_status = await socket_db.run(check_session, self.session_key)
        if not self.policy_status["authenticated"]:
            await self.close(code=4401)
            return
        self.platform_session_uuid = None
        groups = [self.group, session_registry.session_group(self.session_key)]
        if settings.CAMPUS_DM_ENABLED:
            groups.append(user_group(self.user.pk))
        for group in groups:
            await self.channel_layer.group_add(group, self.channel_name)
            self.groups_joined.append(group)
        if await self.current_identity() is None:
            await self.disconnect(4401)
            await self.close(code=4401)
            return
        await self.accept()
        self.connected = True
        await self.send_packet({"type": "chat_ready"})
        self.guard_task = asyncio.create_task(self.check_session_loop())

    async def dispatch(self, message):
        # DB calls already use socket_db.run; avoid a second connection-cleanup thread hop.
        handler = getattr(self, get_handler_name(message), None)
        if handler is None:
            raise ValueError(f"No handler for message type {message['type']}")
        await handler(message)

    async def receive(self, text_data=None, bytes_data=None):
        if not self.connected:
            return
        if text_data is None or len(text_data) > 4096:
            await self.close(code=1009)
            return
        try:
            data = json.loads(text_data)
        except (ValueError, RecursionError):
            await self.close(code=1007)
            return
        if not isinstance(data, dict):
            return
        if timezone.now().timestamp() * 1000 >= self.policy_status.get("expiresAt", 0):
            if await self.current_identity() is None:
                await self.session_expired({"reason": self.policy_status.get("reason", "signed_out")})
                return
        if data.get("type") == "ping":
            await self.send_packet({"type": "pong"})
        elif data.get("type") == "chat":
            await self.receive_chat(data)

    async def receive_chat(self, data):
        text = data.get("text")
        if not isinstance(text, str):
            await self.chat_error("메시지를 입력해 주세요.")
            return
        text = " ".join(text.split())
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            await self.chat_error("메시지에 사용할 수 없는 문자가 있어요.")
            return
        if not text or len(text) > self.max_chat_length or any(ord(c) < 32 or ord(c) == 127 for c in text):
            await self.chat_error("메시지는 1~200자로 입력해 주세요.")
            return
        try:
            actor = verify_actor_token(data.get("actorToken"), self.user.pk, self.session_key)
        except signing.BadSignature:
            await self.chat_error("이동 연결을 확인한 뒤 다시 보내 주세요.")
            return
        now = time.monotonic()
        if now - self.last_chat < self.chat_interval:
            await self.chat_error("잠시 후 다시 보내 주세요.")
            return
        identity = await self.current_identity()
        if identity is None:
            await self.session_expired({"reason": self.policy_status.get("reason", "signed_out")})
            return
        try:
            message_id = await socket_db.run(self.store_chat, text, actor["floor"])
        except ChatRateLimited:
            await self.chat_error("잠시 후 다시 보내 주세요.")
            return
        except DatabaseError:
            logger.exception("Could not store accepted chat message")
            await self.chat_error("메시지를 저장하지 못했어요. 잠시 후 다시 보내 주세요.")
            return
        self.last_chat = now
        await self.channel_layer.group_send(self.group, {"type": "chat.packet", "payload": {
            "type": "chat", "message": {"id": message_id, "playerId": actor["actorId"],
                "nickname": identity["nickname"], "floor": actor["floor"], "text": text,
                "sentAt": timezone.now().isoformat()}}})
        if now - self.last_activity_write >= settings.CAMPUS_SESSION_ACTIVITY_WRITE_INTERVAL_SECONDS:
            self.last_activity_write = now
            try:
                state = await socket_db.run(check_session, self.session_key, touch=True)
                if state.get("reason") != "retry":
                    self.policy_status = state
            except DatabaseError:
                logger.warning("Could not record chat activity", exc_info=True)

    async def current_identity(self):
        return await socket_db.run(self.identity_now)

    def identity_now(self):
        state = check_session(self.session_key)
        if state.get("reason") != "retry":
            self.policy_status = state
        if not state["authenticated"] and state.get("reason") != "retry":
            return None
        store = import_module(settings.SESSION_ENGINE).SessionStore(session_key=self.session_key)
        user = get_user(SimpleNamespace(session=store))
        if not user.is_authenticated or not user.is_active or user.pk != self.user.pk:
            return None
        self.platform_session_uuid = store.get(SESSION_KEY)
        return get_identity(user)

    def store_chat(self, text, floor):
        if not self.platform_session_uuid:
            raise DatabaseError("Movement session is not ready")
        with transaction.atomic():
            # One DB lock preserves the cooldown across chat connections and processes.
            get_user_model().objects.select_for_update().get(pk=self.user.pk)
            recent = timezone.now() - timedelta(seconds=self.chat_interval)
            if ChatMessage.objects.filter(user=self.user, sent_at__gt=recent).exists():
                raise ChatRateLimited
            return record_chat(self.user, self.platform_session_uuid, text, floor)

    async def check_session_loop(self):
        while True:
            await asyncio.sleep(self.session_check_interval)
            try:
                await self.channel_layer.send(self.channel_name, {"type": "chat.check"})
            except ChannelFull:
                pass

    async def chat_check(self, event):
        if not self.connected:
            return
        try:
            if await self.current_identity() is None:
                await self.session_expired({"reason": self.policy_status.get("reason", "signed_out")})
        except DatabaseError:
            logger.warning("Chat session check could not reach the database; keeping the socket", exc_info=True)

    async def chat_packet(self, event):
        if self.connected:
            await self.send_packet(event["payload"])

    async def direct_message(self, event):
        if self.connected and settings.CAMPUS_DM_ENABLED:
            await self.send_packet({"type": "private_message", "conversationId": event["conversationId"]})

    async def session_expired(self, event):
        if not self.connected:
            return
        reason = event.get("reason", "signed_out")
        if reason in {"idle", "maximum", "invalid", "replaced"}:
            await self.send_packet({"type": "session_expired", "reason": reason})
        self.connected = False
        await self.close(code=4401)

    async def chat_error(self, message):
        await self.send_packet({"type": "chat_error", "message": message})

    async def send_packet(self, payload):
        await self.send(text_data=json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")))

    async def disconnect(self, close_code):
        self.connected = False
        if self.guard_task:
            self.guard_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.guard_task
        for group in self.groups_joined:
            await self.channel_layer.group_discard(group, self.channel_name)
        self.groups_joined.clear()
