"""Authenticated authoritative movement, presence and transient movement effects."""
import asyncio
from copy import copy, deepcopy
from contextlib import suppress
from importlib import import_module
import json
import logging
import math
import time
from types import SimpleNamespace
from uuid import uuid4

from channels.consumer import get_handler_name
from channels.exceptions import ChannelFull, StopConsumer
from django.conf import settings
from django.contrib.auth import get_user
from django.db import DatabaseError
from django.utils import timezone

from accounts.models import get_identity, needs_profile_setup
from accounts.session_policy import check_session
from eventing.events import ensure_platform_session, record_floor_transfer, touch_platform_session
from event_story.achievement_services import record_manager_action, record_guide_talk
from event_story.game_services import GameError, start_game
from . import cluster, session_registry, poke, socket_db
from campus_control.service import async_state as freeze_state
from .interest import InterestView, SpatialIndex
from .motion import CLASSROOM_TRANSITION_DELAY, MotionController, ControlError, World, geometry, world
from .positions import DIRECTIONS, FLOOR_BOUNDS, flush_movement_event, get_position, movement_event_due_at, save_position, validate_position
from .chat_tokens import issue_actor_token
from .socket_lifecycle import CleanupWebsocketConsumer


class CampusRoom:
    group = "campus.main"

    def __init__(self):
        self.players = {}
        self.channels = {}
        self.interiors = set()
        self.sessions = {}
        self.controllers = {}
        self.lock = asyncio.Lock()
        self.sockets = {}  # player_id → 소켓(묶음을 채널 계층 없이 바로 쓴다)
        self.pending_moves = {}  # 다음 묶음에 실을 최신 상태(사람마다 하나)
        self.move_identities = {}
        self.interest_dirty = True
        self.pending_effects = {}  # player_id → 다음 묶음에 실을 꾸밈 효과(JSON 글)들(계획 §8)
        self.flusher = None
        self.cluster = cluster.Cluster()  # 다른 프로세스의 사람(Redis 를 쓸 때만, 계획 M3)


room = CampusRoom()
MOVES_INTERVAL = 0.1
REST_TICK = 0.5  # 가만히 있는 사람의 이동 틱 간격(시간으로 바뀌는 것 — 포탈 대기 등 — 을 살피는 상한)
PLATFORM_TOUCH_SECONDS = 15  # touch_platform_session 이 last_seen_at 을 고치는 간격과 같다


async def flush_moves(campus):
    """0.1초마다 변경을 묶고, view=nearby 연결에는 같은 층·주변 상태만 보낸다.

    받는 쪽 메시지 수가 '걷는 사람 수'에서 1 로 준다. 직렬화는 한 번이고 채널 계층(복사·전체 훑기)을 거치지 않는다.
    그 사람에게 갈 꾸밈 효과(점프·찌르기)도 같은 메시지의 `effects` 에 싣는다(계획 §8): 소켓마다 초당 10프레임을 넘지 않는다.
    브라우저는 이미 아는 사람만 갱신하므로, 떠난 뒤 늦게 도착한 묶음이 그 사람을 되살리지 않는다.
    여러 프로세스면(계획 M3) 이 프로세스 사람의 변경만 다른 프로세스에 알리고, 받은 것은 다음 자기 묶음에 합친다.
    """
    identities = campus.move_identities
    try:
        campus.cluster.remote_moves.clear()  # 이 프로세스에 사람이 없던 동안 쌓인 것은 버린다
        while campus.players:
            await asyncio.sleep(MOVES_INTERVAL)
            local = list(campus.pending_moves.values())
            campus.pending_moves.clear()
            if cluster.enabled():
                await campus.cluster.moved(local)
                await campus.cluster.save_positions(session_registry.drain_dirty())
            players = local + list(campus.cluster.remote_moves.values())
            campus.cluster.remote_moves.clear()
            effects, campus.pending_effects = campus.pending_effects, {}
            interest_dirty, campus.interest_dirty = campus.interest_dirty, False
            if not players and not effects and not interest_dirty:
                continue
            scoped = any(getattr(socket, "nearby_moves", False) is True for socket in campus.sockets.values())
            index = SpatialIndex([*campus.players.values(),
                                  *(row["state"] for row in campus.cluster.remote.values()), *players]) if scoped else None
            changed = {player["id"] for player in players}
            compact_players = []
            for player in players:
                identity = tuple(player.get(key) for key in ("nickname", "avatar", "color"))
                # Identity changes carry a full state; walking keeps only the dynamic fields.
                compact_players.append(player if identities.get(player["id"]) != identity else {
                    key: value for key, value in player.items() if key not in ("nickname", "avatar", "color")})
                identities[player["id"]] = identity
            for player_id in identities.keys() - campus.players.keys() - campus.cluster.remote.keys():
                identities.pop(player_id)
            heads = {}
            for player_id, socket in list(campus.sockets.items()):
                mine = effects.get(player_id)
                if getattr(socket, "nearby_moves", False) is True:
                    observer = campus.players.get(player_id)
                    if observer is None:
                        continue
                    packet = socket.interest.packet(index, observer, changed,
                        compact=socket.compact_moves, overview=getattr(socket, "view_overview", False) is True)
                    if mine:
                        packet["effects"] = [effect for text in mine if
                            (effect := json.loads(text)).get("actorId", player_id) in socket.interest.visible | {player_id}]
                    if packet["players"] or packet.get("hidden") or packet.get("effects"):
                        try:
                            await socket.send(text_data=json.dumps(packet, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
                        except Exception:
                            logging.getLogger(__name__).debug("Could not send nearby presence", exc_info=True)
                    continue
                if not players and not mine:
                    continue
                try:
                    compact = getattr(socket, "compact_moves", False) is True
                    if compact not in heads:
                        heads[compact] = '{"type":"moves","players":' + json.dumps(
                            compact_players if compact else players, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                    await socket.send(text_data=heads[compact] + (',"effects":[' + ",".join(mine) + "]}" if mine else "}"))
                except Exception:  # noqa: BLE001 — 끊기는 중인 소켓 하나가 다른 사람의 묶음을 막지 않는다
                    logging.getLogger(__name__).debug("Could not send a moves batch", exc_info=True)
    finally:
        campus.flusher = None
SLOW_DB_SECONDS = 1.0


async def send_to_sockets(campus, payloads):
    """다른 프로세스에서 온 입장·퇴장을 이 프로세스의 모든 소켓에, 효과는 받을 사람에게 보낸다(계획 M3·M8)."""
    for payload in payloads:
        campus.interest_dirty = True
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        for socket in list(campus.sockets.values()):
            try:
                outgoing = text
                if getattr(socket, "nearby_moves", False) is True and payload["type"] == "join":
                    outgoing = json.dumps({**payload, "player": {**payload["player"], "inView": False}}, ensure_ascii=False, separators=(",", ":"))
                await socket.send(text_data=outgoing)
            except Exception:  # noqa: BLE001 — 끊기는 중인 소켓 하나가 다른 사람의 소식을 막지 않는다
                logging.getLogger(__name__).debug("Could not send a cluster presence change", exc_info=True)
    effects, campus.cluster.effects_in = campus.cluster.effects_in, []
    for effect in effects:
        await deliver_effect(campus, effect["payload"], effect["to"], effect["near"])


VIEW_RANGE = 1200  # 점프가 보이는 거리: 기본 확대(1.5)에서 2560×1440 화면의 모서리까지. 찌르기는 poke.SYNC_RANGE


def nearby(state, floor, x, y, reach):
    """그 사람이 이 층 (x, y) 의 효과를 보는가: 같은 층·교실 밖·reach 안."""
    return (state.get("floor", "campus") == floor and not state.get("inClassroom")
            and math.hypot(state["x"] - x, state["y"] - y) <= reach)


BATCHED_EFFECTS = {"jump", "poke", "poke_state", "poke_hit", "pepero_inventory"}  # 다음 0.1초 묶음에 싣는다. 채팅은 바로(묶음을 모르는 옛 탭도 받게)


async def deliver_effect(campus, payload, to=None, near=None):
    """이 프로세스의 소켓에만 보낸다. to = 받는 player_id 들, near = (층, x, y, 거리), 둘 다 없으면 모두."""
    if to is not None:
        targets = [player_id for player_id in to if player_id in campus.sockets]
    elif near is not None:
        targets = [player_id for player_id in list(campus.sockets) if player_id in campus.players and nearby(campus.players[player_id], *near)]
    else:
        targets = list(campus.sockets)
    if not targets:
        return
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if payload["type"] in BATCHED_EFFECTS:
        for player_id in targets:
            campus.pending_effects.setdefault(player_id, []).append(text)
        return
    for socket in [campus.sockets[player_id] for player_id in targets if player_id in campus.sockets]:
        try:
            await socket.send(text_data=text)
        except Exception:  # noqa: BLE001 — 끊기는 중인 소켓 하나가 다른 사람의 효과를 막지 않는다
            logging.getLogger(__name__).debug("Could not send a campus effect", exc_info=True)


async def share_effect(campus, payload, to=None, near=None):
    """찌르기·점프·채팅을 받을 사람에게 보낸다(계획 M8): 이 프로세스의 소켓에는 바로, 다른 프로세스에는 0.1초 묶음(cluster)에 한 번.

    받는 사람마다 채널 계층 메시지(찌르기는 받는 사람마다 Redis PUBLISH)를 만들던 것이 100명에서 프로세스를 넘치게 했다.
    """
    to = None if to is None else list(to)
    await deliver_effect(campus, payload, to, near)
    if cluster.enabled():
        remote = None if to is None else [player_id for player_id in to if player_id in campus.cluster.remote]
        if remote is None or remote:
            campus.cluster.effects.append({"payload": payload, "to": remote, "near": near})


_background = set()


def in_background(coroutine):
    """기다리지 않고 돌린다(작업이 끝나기 전에 사라지지 않게 참조를 쥔다)."""
    task = asyncio.create_task(coroutine)
    _background.add(task)
    task.add_done_callback(_finished)
    return task


def _finished(task):
    _background.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logging.getLogger(__name__).warning("Background campus work failed", exc_info=task.exception())


def note_slow_db(what, started):
    """DB 를 기다린 시간이 길면 한 줄 남긴다(계획 0d): 이동 끊김이 DB 대기 때문인지 운영 로그로 가린다."""
    elapsed = time.monotonic() - started
    if elapsed >= SLOW_DB_SECONDS:
        logging.getLogger(__name__).warning("campus %s waited %.2fs for the database", what, elapsed)


class CampusConsumer(CleanupWebsocketConsumer):
    logout_prepared = False
    session_check_interval = 10
    idle_timeout = 60
    position_interval = 5
    jump_duration = 0.72
    colors = {"#dfa34d", "#4e9b91", "#8a78bb", "#cb7773", "#648eb5"}
    directions = DIRECTIONS
    floor_bounds = FLOOR_BOUNDS

    async def connect(self):
        self.player_id = None
        self.logout_prepared = False
        self.guard_task = None
        self.motion_task = None
        self.groups_joined = []
        self.controller = None
        self.control_lock = asyncio.Lock()
        self.server_control = not getattr(settings, "CAMPUS_TEST_LEGACY_MOVEMENT", False) or b"control=server" in self.scope.get("query_string", b"")
        self.compact_moves = b"moves=compact" in self.scope.get("query_string", b"").split(b"&")
        self.nearby_moves = b"view=nearby" in self.scope.get("query_string", b"").split(b"&")
        self.interest = InterestView()
        self.view_overview = bool(self.scope["user"].is_staff or self.scope["user"].is_superuser)
        self.session_group = None
        self.policy_status = None
        self.last_activity_write = -math.inf
        self.position_dirty = False
        self.pending_floor_transfer = None
        self.movement_event_due_at = None
        self.seen_by_peers = None  # 다른 사람에게 마지막으로 보낸 내 상태(바뀐 것이 있을 때만 다시 보낸다)
        self.control_key = None  # 마지막으로 보낸 control_state 의 바탕(같으면 다시 보내지 않는다)
        self.wake = asyncio.Event()  # 쉬고 있는 이동 틱을 깨운다(입력·명령·문 변경)
        self.last_platform_touch = -math.inf
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
        # check_session may have updated legacy policy fields; do not overwrite
        # them with the session snapshot already read by AuthMiddlewareStack.
        fresh_session = import_module(settings.SESSION_ENGINE).SessionStore(session_key=self.session_key)
        self.platform_session_uuid = await socket_db.run(ensure_platform_session, self.user, fresh_session)
        self.session_group = session_registry.session_group(self.session_key)
        self.player_id = uuid4().hex
        self.last_seen = time.monotonic()
        self.last_move = 0
        self.last_poke = -math.inf
        self.pepero_remaining = 0
        self.last_jump = -math.inf
        self.poke_requested = False
        self.poke_input_scope = None
        self.poke_scope = None
        self.poke_started = None
        self.poke_stream_id = None
        self.poke_direction = None
        self.poke_watchers = set()
        self.last_checkpoint = self.last_seen
        self.position_changed_at = None
        identity = await socket_db.run(get_identity, self.user)
        position = await socket_db.run(get_position, self.user)
        self.movement_event_due_at = await socket_db.run(movement_event_due_at, self.user)
        live = await socket_db.run(cluster.stored_position, self.session_key, self.user.pk) if cluster.enabled() else None
        if live and validate_position(live[0]):
            # 같은 세션이 다른 프로세스에서 막 끊겼다(그 프로세스가 죽은 경우 등): DB 체크포인트(5초)보다 새 위치로 이어 붙는다(계획 M3)
            position = validate_position(live[0])
        self.state = {
            "id": self.player_id, "peperoRemaining": 0, **identity, **position, "color": "#dfa34d", "moving": False, "running": False, "guarding": False,
        }
        if self.server_control:
            self.controller = MotionController(position)
            corrected = self.controller.pose
            self.position_dirty = any(corrected[key] != position[key] for key in ("x", "y", "floor", "dir"))
            if self.position_dirty:
                self.position_changed_at = timezone.now()
            self.state.update(corrected, color=geometry()["avatarColors"].get(identity.get("avatar"), "#dfa34d"))
        if live:
            self.position_dirty, self.position_changed_at = True, live[1]
        await room.cluster.start(room, lambda payloads, campus=room: send_to_sockets(campus, payloads))
        async with room.lock:
            from event_story.achievement_services import DISCOVERY_GROUP
            for group in (room.group, self.session_group, DISCOVERY_GROUP):
                self.groups_joined.append(group)
                await self.channel_layer.group_add(group, self.channel_name)
            # Subscribe before the final check so a login replacing this key
            # during the handshake cannot leave an unnoticed stale socket.
            if await self.current_identity() is None:
                await self.discard_groups()
                await self.close(code=4401)
                return
            previous_id = room.sessions.get(self.session_key)
            if previous_id and previous_id in room.players:
                # A lost TCP connection may outlive a Wi-Fi change. Replace its
                # presence immediately; a delayed disconnect must not touch us.
                previous = room.players.pop(previous_id)
                previous_controller = room.controllers.pop(previous_id, None)
                if self.server_control and previous_controller is not None:
                    self.controller = copy(previous_controller)
                    self.controller.pose = previous_controller.pose.copy()
                    self.controller.interior = deepcopy(previous_controller.interior)
                    self.controller.exit_return = previous_controller.exit_return.copy()
                    self.controller.keys = set();self.controller.route = []
                    self.controller.last_input = -math.inf;self.controller.pending_transfer = None
                    self.controller.pose["moving"] = False;self.controller.pose["running"] = False;self.controller.pose["guarding"] = False
                    if self.controller.interior:self.controller.interior["actor"].update(moving=False, running=False, guarding=False)
                    self.state.update(self.controller.pose)
                else:
                    self.state.update({key:previous[key] for key in ("floor","x","y","dir")})
                self.state["moving"] = False;self.state["running"] = False;self.state["guarding"] = False
                live_position = session_registry.latest(self.session_key, self.user.pk)
                if live_position:
                    self.position_changed_at = live_position[1]
                    self.position_dirty = True
                session_registry.unregister(previous_id)
                previous_channel = room.channels.pop(previous_id, None)
                room.sockets.pop(previous_id, None)
                room.pending_moves.pop(previous_id, None)
                room.interiors.discard(previous_id)
                if previous_channel:
                    await self.channel_layer.group_discard(room.group, previous_channel)
                    await self.channel_layer.group_discard(self.session_group, previous_channel)
                    with suppress(ChannelFull):
                        await self.channel_layer.send(previous_channel, {"type":"presence.replaced"})
                await self.tell_local({"type":"leave", "id":previous_id})
                await room.cluster.left(previous_id)
            mine = cluster.session_hash(self.session_key)
            for row in list(room.cluster.remote.values()):
                if row["session"] == mine:
                    # 같은 세션이 다른 프로세스에 남아 있다(세션 고정 배정이 장애로 바뀐 경우). 그쪽이 떠나며 다른 프로세스에 알린다
                    with suppress(ChannelFull):
                        await self.channel_layer.send(row["channel"], {"type": "presence.replaced"})
            session_registry.register(self.player_id, self.session_key, self.user.pk)
            self.state["inClassroom"] = bool(self.controller and self.controller.interior)
            room.players[self.player_id] = self.state.copy()
            room.channels[self.player_id] = self.channel_name
            room.sockets[self.player_id] = self
            room.sessions[self.session_key] = self.player_id
            if room.flusher is None:
                room.flusher = asyncio.create_task(flush_moves(room))
            if self.controller:
                room.controllers[self.player_id] = self.controller
                if self.controller.interior:room.interiors.add(self.player_id)
            await self.accept()
            players = [*room.players.values(), *(row["state"] for row in room.cluster.remote.values())]
            if self.nearby_moves:
                self.interest.packet(SpatialIndex(players), self.state, set(), compact=self.compact_moves, overview=self.view_overview)
                players = [{**player, "inView": player["id"] in self.interest.visible or player["id"] == self.player_id} for player in players]
            self.chat_token_floor = self.state["floor"]
            self.chat_token = issue_actor_token(self.user.pk, self.session_key, self.player_id, self.chat_token_floor)
            await self.send_packet({"type": "welcome", "selfId": self.player_id, "players": players,
                "session": self.policy_status, "serverControl": self.server_control, "chatToken": self.chat_token})
            # A newcomer may have received an older remote snapshot than the last public batch.
            room.move_identities.clear()
            self.seen_by_peers = self.state.copy()
            await self.tell_local({"type": "join", "player": self.state.copy()})
            await room.cluster.joined(self)
        await self.send_packet({"type": "pepero_status", "remaining": self.pepero_remaining})
        await self.sync_freeze()
        self.guard_task = asyncio.create_task(self.check_session_loop())
        if self.server_control:
            await self.send_control_state()
            self.motion_task = asyncio.create_task(self.motion_loop())

    async def dispatch(self, message):
        # Channels 4.2+ 는 핸들러마다 DB 연결 정리(aclose_old_connections)를 위해 DB 스레드를 한 번 왕복한다.
        # 이 소켓의 DB 접근은 모두 socket_db.run 을 거치고 그것이 호출 전에 연결을 정리하므로, 메시지마다의 왕복은 뺀다(계획 2a)
        handler = getattr(self, get_handler_name(message), None)
        if handler is None:
            raise ValueError(f"No handler for message type {message['type']}")
        await handler(message)

    async def receive(self, text_data=None, bytes_data=None):
        if not self.player_id or self.player_id not in room.players:
            return
        try:
            await self.handle_message(text_data)
        finally:
            self.wake_motion()  # 반영한 뒤에 깨운다(틱 처리 중에 온 입력도 다음 틱이 바로 본다, 계획 1e)

    async def handle_message(self, text_data):
        if text_data is None or len(text_data) > 4096:
            await self.leave()
            await self.close(code=1009)
            return
        try:
            data = json.loads(text_data)
        except (ValueError, RecursionError):
            await self.leave()
            await self.close(code=1007)
            return
        if not isinstance(data, dict):
            return
        pause = await self.sync_freeze()
        action_request = data.get("type") in {"input", "move", "control"}
        permitted = data.get("type") == "control" and data.get("action") == "prepare_logout"
        stale = pause['revision'] > 0 and data.get('freezeRevision') != pause['revision']
        if action_request and not permitted and (pause['paused'] or stale):
            if data.get('type') == 'control':
                await self.send_packet({'type': 'control_result', 'requestId': data.get('requestId'),
                                        'error': pause['message'] if pause['paused'] else '이용 상태가 바뀌었어요. 키를 놓고 다시 시도해 주세요.'})
            return
        if self.logout_prepared and data.get("type") in {"input", "move"}:
            return
        if self.server_control and data.get("type") == "control" and data.get("action") == "prepare_logout":
            await self.receive_control(data)
            return
        if timezone.now().timestamp() * 1000 >= self.policy_status.get("expiresAt", 0):
            if await self.current_identity() is None:
                await self.session_expired({"reason": self.policy_status.get("reason", "signed_out")})
                return
        if data.get("type") == "input" and self.server_control:
            try:
                requested = data.get("poking", False)
                if type(requested) is not bool:
                    raise ControlError("찌르기 입력을 확인해 주세요.")
                async with self.control_lock:
                    self.controller.input(data.get("keys"))
                    self.last_seen = time.monotonic()
                    if requested and "pokeContext" in data:
                        self.poke_input_scope = data["pokeContext"]
                    elif not requested:
                        self.poke_input_scope = None
                    self.poke_requested = requested and self.poke_input_scope == self.current_poke_scope()
                    await self.tick_poke()
            except ControlError as error:
                await self.send_packet({"type": "control_error", "message": str(error)})
            return
        if data.get("type") == "control" and self.server_control:
            await self.receive_control(data)
            return
        if data.get("type") == "chat":
            await self.send_packet({"type": "control_error", "message": "채팅 연결로 메시지를 보내 주세요."})
            return
        if data.get("type") == "ping":
            self.last_seen = time.monotonic()
            await self.send_packet({"type": "pong"})  # DB 를 기다리지 않는다: 브라우저는 10초 동안 소식이 없으면 다시 연결한다
            if self.last_seen - self.last_platform_touch >= PLATFORM_TOUCH_SECONDS:  # DB 가 실제로 고치는 주기(계획 4b)
                self.last_platform_touch = self.last_seen
                in_background(socket_db.run(touch_platform_session, self.platform_session_uuid))
            return
        if data.get("type") == "leave":
            # The final frame bypasses movement throttling and precedes socket close.
            if self.server_control:
                await self.receive_movement({**self.controller.pose, "color": self.state["color"]}, final=True, authoritative=True)
            else:
                await self.receive_movement(data, final=True)
            await self.leave()
            await self.close(code=1000)
            return
        if data.get("type") == "move":
            if self.server_control:
                await self.send_packet({"type": "control_error", "message": "좌표 대신 이동 키 입력을 보내 주세요."})
            else:
                await self.receive_movement(data)

    async def receive_movement(self, data, final=False, authoritative=False):
        if self.player_id not in room.players:
            return
        floor = data.get("floor", self.state["floor"])
        position = validate_position({**data, "floor": floor})
        if (position is None or type(data.get("color")) is not str
                or data["color"] not in self.colors or type(data.get("moving")) is not bool
                or ("running" in data and type(data["running"]) is not bool)
                or ("guarding" in data and type(data["guarding"]) is not bool)):
            return
        now = time.monotonic()
        if not authoritative:
            self.last_seen = now
        previous_floor = self.state["floor"]
        floor_changed = floor != previous_floor
        in_classroom = bool(getattr(self, "controller", None) and self.controller.interior)
        visibility_changed = self.state.get("inClassroom", False) != in_classroom
        publish = not authoritative or final or floor_changed or visibility_changed or now - self.last_move >= 0.1
        if not authoritative and not final and not floor_changed and not visibility_changed and now - self.last_move < 0.045:
            return
        changed = any(self.state[key] != value for key, value in position.items())
        if changed:
            self.position_dirty = True
            self.position_changed_at = timezone.now()
        if floor_changed:
            self.pending_floor_transfer = (previous_floor, floor)
        # Identity is always taken from the session, never from the message.
        self.state.update(**position, color=data["color"], moving=False if final else data["moving"],
                          running=False if final else data.get("running", False), guarding=False if final else data.get("guarding", False), inClassroom=in_classroom)
        if authoritative and not (final or floor_changed or visibility_changed):
            publish = self.peers_should_see(now)
        if publish:
            self.last_move = now
        session_registry.update(self.player_id, self.state, self.position_changed_at)
        async with room.lock:
            if self.player_id in room.players:
                room.players[self.player_id] = self.state.copy()
                if getattr(self, "controller", None) and self.controller.interior:
                    room.interiors.add(self.player_id)
                else:
                    room.interiors.discard(self.player_id)
                if publish:
                    self.seen_by_peers = self.state.copy()
                    if authoritative:
                        room.pending_moves[self.player_id] = self.seen_by_peers  # 0.1초마다 묶어 보낸다(계획 1d)
                    else:
                        await self.broadcast({"type": "move", "player": self.state.copy()})
        # 이동 처리는 저장을 기다리지 않는다(계획 3단계). 드문 층 이동과 마지막 프레임만 저장이 끝날 때까지 기다린다(최종 위치가 빠지지 않게)
        await self.checkpoint(force=final or floor_changed, wait=final or floor_changed)
        if changed and not final and publish:
            await self.touch_activity(wait=not authoritative)  # 서버 판정 이동(이동 틱)은 활동 기록도 기다리지 않는다(계획 3단계)

    def peers_should_see(self, now):
        """서버 판정 프레임을 다른 사람에게 보낼지(계획 1a): 다른 사람이 보는 상태가 바뀔 때만 보낸다.
        정지·방향 전환·이름·아바타 등은 바로, 이동 중 위치만 0.1초 간격이다. 그래서 마지막 정지 위치(`moving=false`)는 반드시 간다."""
        seen = getattr(self, "seen_by_peers", None)
        if seen is None or any(seen.get(key) != value for key, value in self.state.items() if key not in ("x", "y")):
            return True
        return (seen["x"], seen["y"]) != (self.state["x"], self.state["y"]) and now - self.last_move >= 0.1

    def pose_changed(self):
        """이번 틱에 상태를 갱신할 일이 있는가: 서버 판정 위치가 바뀌었거나 다른 사람이 아직 못 본 변경(이름 등)이 있다."""
        pose = self.controller.pose
        return (any(self.state.get(key) != pose.get(key) for key in ("floor", "x", "y", "dir", "moving", "running", "guarding"))
                or self.state.get("inClassroom", False) != bool(self.controller.interior) or self.state != getattr(self, "seen_by_peers", None))

    async def send_control_state(self, only_changes=False):
        controller, interior = self.controller, self.controller.interior
        key = (tuple(self.state.items()), tuple(controller.pose.items()),
               interior and (interior["subject"], tuple(interior["actor"].items())))  # 화면에 실리는 것의 바탕: 내 상태·서버 판정 위치·교실
        cooling = bool(interior) and time.monotonic() - controller.last_classroom_transition < CLASSROOM_TRANSITION_DELAY
        if only_changes and key == getattr(self, "control_key", None) and not cooling:
            return False  # 화면에 보일 것이 그대로다(가만히 있는 사람, 계획 1b). 교실 출입 대기 시간은 줄어드는 동안 계속 보낸다
        self.control_key = key
        packet = self.controller.snapshot()
        packet["main"] = {**self.state, **packet["main"]}
        if packet["interior"]:
            packet["interior"]["actor"].update(avatar=self.state.get("avatar", "male-1"), color=self.state["color"])
        payload = {"type": "control_state", "state": packet}
        floor = packet["main"]["floor"]
        if getattr(self, "chat_token_floor", floor) != floor:
            self.chat_token_floor = floor
            self.chat_token = issue_actor_token(self.user.pk, self.session_key, self.player_id, floor)
            payload["chatToken"] = self.chat_token
        await self.send_packet(payload)
        return True

    def wake_motion(self):
        wake = getattr(self, "wake", None)
        if wake is not None:
            wake.set()

    def busy(self, now):
        """입력 없이도 다음 틱에 바뀔 것이 있는가: 키 유효 시간 안·경로 이동·교실 안 걷기·교실 출입 대기·찌르기."""
        c = self.controller
        return bool((c.keys and now - c.last_input <= poke.INPUT_LEASE) or c.route  # 키 유효 시간은 motion.tick 과 같은 0.35초
                    or (c.interior and (c.interior["actor"]["moving"] or now - c.last_classroom_transition < CLASSROOM_TRANSITION_DELAY))
                    or self.poke_requested or self.poke_started is not None)

    async def sync_freeze(self):
        state = await freeze_state()
        signature = (state['revision'], state['paused'], state.get('unavailable', False))
        if getattr(self, 'freeze_signature', None) != signature:
            self.freeze_signature = signature
            c = self.controller
            if c:
                c.keys.clear(); c.route = []; c.last_input = -math.inf
                c.pending_transfer = None; c.classroom_exit_armed = False
                c.ignore_current_portal()
                c.pose.update(moving=False, running=False, guarding=False)
                if c.interior:
                    c.interior['actor'].update(moving=False, running=False, guarding=False)
            self.poke_requested = False
            self.poke_input_scope = None
            await self.stop_poke()
            await self.send_packet({'type': 'campus_freeze', **state})
        return state

    async def motion_loop(self):
        previous, resting = time.monotonic(), False
        try:
            while self.player_id in room.players:
                if resting:
                    # 가만히 있으면 30Hz 틱을 돌리지 않는다(계획 1e). 입력·명령·문 변경은 바로 깨우고, 시간으로 바뀌는 것은 REST_TICK 안에 살핀다
                    with suppress(TimeoutError):
                        async with asyncio.timeout(REST_TICK):
                            await self.wake.wait()
                else:
                    await asyncio.sleep(1 / 30)
                self.wake.clear()
                now = time.monotonic()
                seconds = now - previous
                previous = now
                async with self.control_lock:
                    if self.player_id not in room.players or self.logout_prepared:
                        return
                    pause = await self.sync_freeze()
                    events = [] if pause['paused'] else self.controller.tick(seconds, now)
                    moved = self.pose_changed()
                    if moved:
                        await self.receive_movement({**self.controller.pose, "color": self.state["color"]}, authoritative=True)
                    if self.controller.interior and self.controller.interior["actor"]["moving"]:
                        await self.touch_activity(wait=False)
                    if not pause['paused']:
                        await self.tick_poke()
                    sent = await self.send_control_state(only_changes=True)
                    for event in events:
                        await self.send_packet({"type": "control_event", "event": event})
                    resting = not (moved or events or sent or self.busy(now))
        except (asyncio.CancelledError, StopConsumer):
            raise
        except Exception:
            logging.getLogger(__name__).exception("Campus movement calculation failed")
            await self.close(code=1011)

    async def receive_control(self, data):
        request_id = data.get("requestId")
        action = data.get("action")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or not isinstance(action, str):
            return
        arguments = data.get("data", {})
        if not isinstance(arguments, dict):
            return
        if self.logout_prepared and action != "prepare_logout":
            await self.send_packet({"type": "control_result", "requestId": request_id,
                                    "error": "로그아웃 처리 중입니다."})
            return
        # 점프(꾸밈 동작)는 조작마다 DB 를 보지 않는다(계획 M8): 소켓 DB 스레드는 하나라 포화되면 그 사람의 다음 메시지가 모두 밀렸다.
        # 만료 시각 확인(메시지마다)·10초 세션 확인·로그아웃 알림이 지킨다. 상태를 바꾸는 조작은 그대로 확인한다
        try:
            if action != "jump" and await self.current_identity() is None:
                await self.session_expired({"reason": self.policy_status.get("reason", "signed_out")})
                return
            self.last_seen = time.monotonic()
            async with self.control_lock:
                if action == "prepare_logout":
                    result = await self.prepare_logout()
                    await self.send_packet({"type": "control_result", "requestId": request_id, "result": result})
                    return
                if action == "jump":
                    result = await self.receive_jump(arguments)
                    await self.send_packet({"type": "control_result", "requestId": request_id, "result": result})
                    await self.touch_activity(wait=False)
                    return
                if action == "story_access":
                    from event_story.access import issue
                    result = issue(self.scope["user"].pk, self.controller, arguments)
                    await self.send_packet({"type": "control_result", "requestId": request_id, "result": result})
                    return
                if action == "kkuwang_last_bite":
                    result = await self.kkuwang_last_bite(arguments)
                    await self.send_packet({"type": "control_result", "requestId": request_id, "result": result})
                    return
                if action == "pepero_refill":
                    result = await self.refill_pepero(arguments)
                    await self.send_packet({"type": "control_result", "requestId": request_id, "result": result})
                    return
                if action == "game_start":
                    result = await self.start_minigame(arguments)
                    await self.send_packet({"type": "control_result", "requestId": request_id, "result": result})
                    await self.touch_activity()
                    return
                if action == "guide_talk":
                    result = await self.talk_to_guide(arguments)
                    await self.send_packet({"type": "control_result", "requestId": request_id, "result": result})
                    await self.touch_activity()
                    return
                if action == "manager_greet":
                    result = await self.greet_manager(arguments)
                    await self.send_packet({"type": "control_result", "requestId": request_id, "result": result})
                    await self.touch_activity()
                    return
                if action == "poke":
                    result = await self.receive_poke(arguments)
                    await self.send_packet({"type": "control_result", "requestId": request_id, "result": result})
                    await self.touch_activity()
                    return
                if action == "navigate":
                    result = await asyncio.to_thread(self.controller.command, action, arguments)
                else:
                    result = self.controller.command(action, arguments)
                if action == "begin_transfer":
                    from ad_bridge.placements import transfer_token
                    result["ads_token"] = transfer_token(self.scope["user"].pk, result["transfer_id"])
                await self.receive_movement({**self.controller.pose, "color": self.state["color"]}, authoritative=True)
                await self.send_control_state()
                await self.send_packet({"type": "control_result", "requestId": request_id, "result": result})
                await self.touch_activity()
        except (ControlError, GameError) as error:
            await self.send_packet({"type": "control_result", "requestId": request_id, "error": str(error)})
        except DatabaseError:
            if action == "game_start":
                logging.getLogger(__name__).exception("Could not start a minigame")
                await self.send_packet({"type": "control_result", "requestId": request_id,
                                        "error": "게임을 시작하지 못했어요. 다시 시도해 주세요."})
                return
            if action in {"manager_greet", "guide_talk"}:
                logging.getLogger(__name__).exception("Could not save an NPC conversation")
                message = "인사 기록을 저장하지 못했어요. 다시 시도해 주세요." if action == "manager_greet" else "대화 기록을 저장하지 못했어요. 다시 시도해 주세요."
                await self.send_packet({"type": "control_result", "requestId": request_id, "error": message})
                return
            if action != "prepare_logout":
                raise
            logging.getLogger(__name__).exception("Could not save the final logout position")
            await self.send_packet({"type": "control_result", "requestId": request_id,
                                    "error": "마지막 위치를 저장하지 못했어요. 다시 시도해 주세요."})

    async def prepare_logout(self):
        if self.logout_prepared:
            return {"saved": True}
        self.controller.input([])
        self.controller.route.clear()
        self.controller.pose["moving"] = False
        self.poke_requested = False
        await self.stop_poke()
        saving = getattr(self, "saving", None)
        if saving is not None:
            await asyncio.shield(saving)
        state = {**self.state, **self.controller.pose, "moving": False}
        observed_at = timezone.now()
        transfer = self.pending_floor_transfer
        self.movement_event_due_at = await socket_db.run(
            self.store_position_now, state, observed_at, transfer[0] if transfer else None,
            require_session=True)
        self.state.update(state)
        self.position_changed_at = observed_at
        self.position_dirty = False
        self.pending_floor_transfer = None
        self.logout_prepared = True
        session_registry.update(self.player_id, self.state, observed_at)
        async with room.lock:
            room.players[self.player_id] = self.state.copy()
            room.pending_moves[self.player_id] = self.state.copy()
        return {"saved": True}

    async def receive_jump(self, arguments):
        expected = self.current_poke_scope()
        if arguments != expected or self.controller.pending_transfer:
            raise ControlError("현재 공간에서 다시 점프해 주세요.")
        now = time.monotonic()
        if now - self.last_jump < self.jump_duration:
            raise ControlError("착지한 뒤 다시 점프해 주세요.")
        if self.player_id not in room.players:
            raise ControlError("연결된 뒤 이용해 주세요.")
        self.last_jump = now
        packet = {"type": "jump", "id": uuid4().hex, "actorId": self.player_id,
                  "floor": self.controller.pose["floor"], "durationMs": 720, "height": 52, **expected}
        # A cosmetic action never updates position_dirty, the database, or the event outbox.
        if self.controller.interior:
            await self.send_packet(packet)
        else:
            pose = self.controller.pose
            await share_effect(room, packet, near=(pose["floor"], pose["x"], pose["y"], VIEW_RANGE))
        return {"type": "jump", "id": packet["id"]}

    async def start_minigame(self, arguments):
        obj_id = arguments.get("objectId")
        obj = self.controller.world.objects.get(obj_id) if isinstance(obj_id, str) else None
        point = obj.get("interactionPoint") if obj else None
        if (not obj or obj.get("action") != "minigame" or self.controller.interior
                or self.controller.pending_transfer or not point
                or self.controller.world.distance(obj, self.controller.pose) >= obj.get("interactionRadius", 35)
                or not self.controller.world.clear(self.controller.pose, {"x": point[0], "y": point[1]})):
            raise ControlError("게임 단말 가까이에서 상호작용해 주세요.")
        self.controller.input([])
        self.controller.route.clear()
        result = await socket_db.run(self.start_minigame_now, obj["gameCode"])
        if result is None:
            raise ControlError("로그인 상태를 확인한 뒤 다시 시도해 주세요.")
        return result

    def start_minigame_now(self, code):
        user = self.session_user()
        return start_game(user, code, self.platform_session_uuid) if user is not None else None

    async def talk_to_guide(self, arguments):
        obj_id = arguments.get("objectId")
        controller = self.controller
        obj = controller.world.objects.get(obj_id) if isinstance(obj_id, str) else None
        if (obj_id != "campus-guide-kkuwang" or not obj or obj.get("action") != "guide"
                or controller.interior or controller.pending_transfer or controller.pose["floor"] != "campus"
                or controller.world.distance(obj, controller.pose) > obj.get("interactionRadius", 70)):
            raise ControlError("18층 꾸왕이 가까이에서 대화해 주세요.")
        controller.input([])
        controller.route.clear()
        result = await socket_db.run(self.guide_talk_now, self.pepero_remaining)
        if result is None:
            raise ControlError("로그인 상태를 확인한 뒤 다시 시도해 주세요.")
        return {"type": "guide_talk", **result}

    def guide_talk_now(self, remaining):
        user = self.session_user()
        return record_guide_talk(user, remaining, self.platform_session_uuid) if user is not None else None

    async def greet_manager(self, arguments):
        obj_id = arguments.get("objectId")
        controller = self.controller
        interior = controller.interior
        active = world(interior["subject"]) if interior else controller.world
        pose = interior["actor"] if interior else controller.pose
        obj = active.objects.get(obj_id) if isinstance(obj_id, str) else None
        point = obj.get("interactionPoint") if obj else None
        if not obj or obj.get("action") != "manager_greet" or controller.pending_transfer or not point:
            raise ControlError("매니저 가까이에서 상호작용해 주세요.")
        clear_world = World({**active.data, "colliders": [c for c in active.data["colliders"] if c.get("objectId") != obj_id]})
        if active.distance(obj, pose) >= obj.get("interactionRadius", 55) or not clear_world.clear(pose, {"x":point[0], "y":point[1]}):
            raise ControlError("매니저 가까이에서 상호작용해 주세요.")
        result = await socket_db.run(self.manager_action_now, obj.get("managerId",obj_id), "greet")
        if result is None:
            raise ControlError("로그인 상태를 확인한 뒤 다시 시도해 주세요.")
        return {"type":"manager_greeting", "managerName":obj["name"],
                "dialogue":"이곳에는 숨겨진 이스터에그가 많다던데...", **result}

    def manager_action_now(self, manager_id, action):
        user = self.session_user()
        return record_manager_action(user, manager_id, action, self.platform_session_uuid) if user is not None else None

    async def record_manager_poke(self, manager_id):
        if manager_id is None:
            return
        try:
            result = await socket_db.run(self.manager_action_now, manager_id, "poke")
        except DatabaseError:
            logging.getLogger(__name__).exception("Could not save a manager poke")
            await self.send_packet({"type": "control_error", "message": "찌르기 기록을 저장하지 못했어요. 다시 시도해 주세요."})
            return
        if result is None:
            self.poke_requested = False
            await self.session_expired({"reason": self.policy_status.get("reason", "signed_out")})
            return
        if result["newly_completed"]:
            await self.send_packet({"type": "achievement_update", **result})

    async def achievement_discoveries(self, event):
        if await self.current_identity() is not None:
            await self.send_packet({"type": "achievement_discoveries", "discoveries": event["discoveries"]})

    async def achievement_update(self, event):
        if await self.current_identity() is not None:
            await self.send_packet({'type': 'achievement_update', **event['result']})

    async def kkuwang_last_bite(self, arguments):
        from event_story.achievement_services import FRAGILE_ID, complete_global_achievement, achievement_snapshot
        from event_story.models import EventProgress
        obj = self.controller.world.objects.get("campus-guide-kkuwang")
        if (self.controller.interior or self.controller.pending_transfer
                or self.controller.pose["floor"] != "campus" or not obj
                or self.controller.world.distance(obj, self.controller.pose) > 100):
            raise ControlError("18층 꾸왕이 가까이에서 말을 걸어 주세요.")
        if self.pepero_remaining != 1:
            return {"eligible": False}
        def claim_state():
            from event_story.game_services import require_open
            require_open()
            already_claimed = EventProgress.objects.filter(
                event_id=FRAGILE_ID, status=EventProgress.Status.COMPLETED,
            ).exists()
            account_limited = EventProgress.objects.filter(
                user_id=self.user.pk, status=EventProgress.Status.COMPLETED,
                event__event_type="EASTER_EGG",
            ).exclude(event_id=FRAGILE_ID).exists()
            return {"already_claimed": already_claimed, "account_limited": account_limited}
        state = await socket_db.run(claim_state)
        if state["already_claimed"]:
            return {"eligible": False, "already_claimed": True}
        if state["account_limited"]:
            return {"eligible": False, "account_limited": True}
        self.controller.input([])
        self.controller.route = []
        if arguments.get("claim") is not True:
            return {"eligible": True}
        def save():
            newly = complete_global_achievement(self.user, FRAGILE_ID, timezone.now(), session_uuid=self.platform_session_uuid)
            already_claimed = not newly and EventProgress.objects.filter(
                event_id=FRAGILE_ID, status=EventProgress.Status.COMPLETED,
            ).exists()
            account_limited = not newly and not already_claimed and EventProgress.objects.filter(
                user_id=self.user.pk, status=EventProgress.Status.COMPLETED,
                event__event_type="EASTER_EGG",
            ).exclude(event_id=FRAGILE_ID).exists()
            return {"eligible": newly, "already_claimed": already_claimed,
                    "account_limited": account_limited,
                    "achievements": achievement_snapshot(self.user),
                    "newly_completed": [str(FRAGILE_ID)] if newly else []}
        result = await socket_db.run(save)
        if not result["eligible"]:
            return result
        self.pepero_remaining = 10
        self.publish_pepero_inventory()
        await self.send_packet({"type":"pepero_status", "remaining":10})
        await self.send_poke_to(self.poke_audience(self.current_poke_scope()), {
            "type":"pepero_inventory", "actorId":self.player_id,
            "remaining":10, **self.current_poke_scope()})
        await self.send_packet({"type":"achievement_update", "result":result, **result})
        await self.send_control_state()
        return {"remaining":10, **result}

    async def refill_pepero(self, arguments):
        obj_id = arguments.get("objectId")
        obj = self.controller.world.objects.get(obj_id) if isinstance(obj_id, str) else None
        point = obj.get("interactionPoint") if obj else None
        if (not obj or obj.get("action") != "pepero_refill" or self.controller.interior
                or self.controller.pending_transfer or self.controller.world.distance(obj, self.controller.pose) >= obj.get('interactionRadius', 35)
                or not point or not self.controller.world.clear(self.controller.pose, {"x": point[0], "y": point[1]})):
            raise ControlError("빼빼로 상자 앞에서 E를 눌러 주세요.")
        self.pepero_remaining = 10
        self.publish_pepero_inventory()
        await self.send_packet({"type": "pepero_status", "remaining": 10})
        await self.send_poke_to(self.poke_audience(self.current_poke_scope()), {
            "type": "pepero_inventory", "actorId": self.player_id,
            "remaining": 10, **self.current_poke_scope(),
        })
        await self.send_control_state()
        return {"type": "pepero_refill", "remaining": 10}

    def publish_pepero_inventory(self):
        # Keep connection snapshots current without scheduling a global movement update.
        # Hits already carry remaining to nearby viewers; refills use a targeted effect.
        self.state["peperoRemaining"] = self.pepero_remaining
        seen = getattr(self, "seen_by_peers", None)
        if seen is not None:
            seen["peperoRemaining"] = self.pepero_remaining
        if self.player_id in room.players:
            room.players[self.player_id] = self.state.copy()

    async def spend_pepero(self, target_id, blocked):
        before = self.pepero_remaining
        broken = before > 0 and target_id is not None and blocked
        if before > 0 and target_id is not None:
            self.pepero_remaining = 0 if blocked else max(0, before - 1)
            self.publish_pepero_inventory()
            await self.send_packet({"type": "pepero_status", "remaining": self.pepero_remaining})
        return {"peperoBefore": before, "peperoBroken": broken}

    async def receive_poke(self, arguments):
        actor = self.controller.interior["actor"] if self.controller.interior else self.controller.pose
        if actor.get("guarding") or self.controller.pending_transfer:
            raise ControlError("막기나 이동이 끝난 뒤 찔러 주세요.")
        interior = self.controller.interior
        expected = {"context": "classroom", "subject": interior["subject"]} if interior else {"context": "world", "floor": self.controller.pose["floor"]}
        if arguments != expected:
            raise ControlError("화면이 바뀌었어요. 현재 화면에서 다시 눌러 주세요.")
        now = time.monotonic()
        if now - self.last_poke < poke.COOLDOWN:
            raise ControlError("잠시 후 다시 찔러 주세요.")
        async with room.lock:
            if self.player_id not in room.players:
                raise ControlError("연결된 뒤 이용해 주세요.")
            self.last_poke = now
            if interior:
                pose = interior["actor"]
                direction = interior.get("poke_direction", pose["dir"])
                active_world = world(interior["subject"])
                targets = poke.classroom_targets(interior["subject"])
            else:
                pose = self.controller.pose
                direction = self.controller.poke_direction
                active_world = self.controller.world
                targets = [player for player_id, player in self.everyone() if player_id != self.player_id and player["floor"] == pose["floor"]] + poke.world_targets(active_world)
            target_id = poke.find_target(pose, targets, active_world, direction)
            blocked = bool(target_id and next((target for target in targets if target.get("id") == target_id), {}).get("guarding"))
            manager_id = active_world.objects.get(target_id, {}).get("managerId",target_id) if self.pepero_remaining > 0 and not blocked and active_world.objects.get(target_id, {}).get("action") == "manager_greet" else None
            pepero_effect = await self.spend_pepero(target_id, blocked)
            packet = {**pepero_effect, "remaining": self.pepero_remaining, "type": "poke", "id": uuid4().hex, "actorId": self.player_id,
                      "targetId": target_id, "dir": direction, "floor": self.controller.pose["floor"],
                      "blocked": blocked, **expected}
            await self.send_poke_to(self.poke_audience(expected), packet)
        # Database work must not hold the room-wide movement lock.
        await self.record_manager_poke(manager_id)
        return {"type": "poke", "hit": target_id is not None and not blocked, "blocked": blocked}

    def current_poke_scope(self):
        interior = self.controller.interior
        return {"context": "classroom", "subject": interior["subject"]} if interior else {"context": "world", "floor": self.controller.pose["floor"]}

    def everyone(self):
        """교실 밖 사람(이 프로세스와 다른 프로세스, 계획 M3) — 찌르기 대상·관객."""
        for player_id, player in room.players.items():
            if player_id not in room.interiors:
                yield player_id, player
        for player_id, row in room.cluster.remote.items():
            if not row["state"].get("inClassroom"):
                yield player_id, row["state"]

    def poke_audience(self, scope):
        if scope["context"] == "classroom":
            return {self.player_id}
        pose = self.controller.pose
        return {self.player_id} | {player_id for player_id, player in self.everyone() if player["floor"] == scope["floor"]
                                   and math.hypot(player["x"] - pose["x"], player["y"] - pose["y"]) <= poke.SYNC_RANGE}

    async def send_poke_to(self, recipients, payload):
        # Cosmetic effects go directly to nearby sockets, never to the global presence group.
        await share_effect(room, payload, to=recipients)

    def poke_state(self, active, now):
        return {"remaining": self.pepero_remaining, "type": "poke_state", "id": self.poke_stream_id, "actorId": self.player_id,
                "active": active, "dir": self.poke_direction, "floor": self.state["floor"],
                "phaseMs": int(max(0, now - self.poke_started) * 1000) % 250,
                "stroke": int(max(0, now - self.poke_started) * 1000) // 250 % 2, **self.poke_scope}

    async def stop_poke(self):
        if getattr(self, "poke_started", None) is None:
            return
        await self.send_poke_to(self.poke_watchers, self.poke_state(False, time.monotonic()))
        self.poke_started = None
        self.poke_watchers = set()
        self.poke_scope = None

    async def tick_poke(self):
        now = time.monotonic()
        actor=self.controller.interior["actor"] if self.controller.interior else self.controller.pose
        if (self.controller.pending_transfer or actor.get("guarding") or not self.poke_requested or now - self.controller.last_input > poke.INPUT_LEASE
                or time.time() * 1000 >= self.policy_status.get("expiresAt", 0)):
            await self.stop_poke()
            return
        scope = self.current_poke_scope()
        if self.poke_scope is not None and scope != self.poke_scope:
            # Old key holds must not carry through a floor transfer or classroom doorway.
            self.poke_requested = False
            await self.stop_poke()
            return
        if now - self.last_poke < poke.COOLDOWN:
            return
        starting = self.poke_started is None
        if starting:
            self.poke_started = now
            self.poke_stream_id = uuid4().hex
            self.poke_scope = scope
        interior = self.controller.interior
        pose = interior["actor"] if interior else self.controller.pose
        direction = interior.get("poke_direction", pose["dir"]) if interior else self.controller.poke_direction
        changed_direction = self.poke_direction != direction
        self.poke_direction = direction
        self.last_poke = now
        manager_id = None
        async with room.lock:
            if self.player_id not in room.players:
                await self.stop_poke()
                return
            audience = self.poke_audience(scope)
            departed = self.poke_watchers - audience
            if departed:
                await self.send_poke_to(departed, self.poke_state(False, now))
            refresh = now - getattr(self, "last_poke_sync", -math.inf) >= .5
            updated = audience if starting or changed_direction or refresh else audience - self.poke_watchers
            if starting or changed_direction or refresh:self.last_poke_sync = now
            if updated:
                await self.send_poke_to(updated, self.poke_state(True, now))
            self.poke_watchers = audience
            active_world = world(interior["subject"]) if interior else self.controller.world
            targets = poke.classroom_targets(interior["subject"]) if interior else [
                player for player_id, player in self.everyone() if player_id != self.player_id and player["floor"] == pose["floor"]] + poke.world_targets(active_world)
            target_id = poke.find_target(pose, targets, active_world, direction)
            if target_id is not None:
                blocked=bool(next((target for target in targets if target.get("id")==target_id),{}).get("guarding"))
                manager_id = active_world.objects.get(target_id, {}).get("managerId",target_id) if self.pepero_remaining > 0 and not blocked and active_world.objects.get(target_id, {}).get("action") == "manager_greet" else None
                pepero_effect = await self.spend_pepero(target_id, blocked)
                await self.send_poke_to(audience, {
                    **pepero_effect, "remaining": self.pepero_remaining, "type": "poke_hit", "id": uuid4().hex, "actorId": self.player_id,
                    "targetId": target_id, "dir": direction, "floor": self.state["floor"], "blocked":blocked, **scope,
                })
        await self.record_manager_poke(manager_id)

    async def touch_activity(self, wait=True):
        now = time.monotonic()
        if now - self.last_activity_write < settings.CAMPUS_SESSION_ACTIVITY_WRITE_INTERVAL_SECONDS:
            return
        self.last_activity_write = now
        if wait:
            await self.write_activity()
        else:
            in_background(self.write_activity())

    async def write_activity(self):
        try:
            state = await socket_db.run(check_session, self.session_key, touch=True)
        except Exception:  # noqa: BLE001 — 따로 도는 기록의 오류는 남기기만 한다(다음 활동 때 다시 기록한다)
            logging.getLogger(__name__).warning("Could not record campus activity", exc_info=True)
            return
        if state.get("reason") != "retry":
            self.policy_status = state

    async def send_packet(self, payload):
        await self.send(text_data=json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")))

    async def tell_local(self, payload):
        """입장·퇴장은 이 프로세스의 소켓에 바로 보낸다(계획 M2·M3).

        채널 계층(Redis)을 거치면 늦게 도착해, 막 붙은 소켓이 welcome 과 join 을 모두 놓칠 수 있다.
        다른 프로세스는 묶음 흐름(cluster)으로 알게 되어 자기 소켓에 알린다.
        """
        room.interest_dirty = True
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        for player_id, socket in list(room.sockets.items()):
            if player_id != self.player_id:
                try:
                    outgoing = text
                    if getattr(socket, "nearby_moves", False) is True and payload["type"] == "join":
                        outgoing = json.dumps({**payload, "player": {**payload["player"], "inView": False}}, ensure_ascii=False, separators=(",", ":"))
                    await socket.send(text_data=outgoing)
                except Exception:  # noqa: BLE001 — 끊기는 중인 소켓 하나가 다른 사람의 소식을 막지 않는다
                    logging.getLogger(__name__).debug("Could not send a presence change", exc_info=True)

    async def broadcast(self, payload):
        await self.channel_layer.group_send(room.group, {
            "type": "presence.packet", "sender": self.player_id, "payload": payload,
        })

    async def presence_packet(self, event):
        # 채널 계층으로 오는 방송은 이동뿐이다. 찌르기·점프는 share_effect로 보낸다.
        if self.player_id in room.players and event["sender"] != self.player_id:
            payload = event["payload"]
            if self.nearby_moves and payload.get("type") == "move":
                state = payload["player"]
                if payload.get("identityOnly"):
                    await self.send_packet({"type": "peer_identity", "player": {
                        key: state[key] for key in ("id", "nickname", "avatar", "color")}})
                if state["id"] in room.players:
                    room.pending_moves[state["id"]] = state
                elif state["id"] in room.cluster.remote:
                    room.cluster.remote[state["id"]]["state"] = state
                    room.cluster.remote_moves[state["id"]] = state
            else:
                await self.send_packet(payload)

    async def current_identity(self):
        return await socket_db.run(self.identity_now)

    def identity_now(self):
        user = self.session_user()
        return get_identity(user) if user is not None else None

    def session_user(self):
        # A fresh session store catches logout/password changes in another tab.
        state = check_session(self.session_key)
        if state.get("reason") != "retry":
            self.policy_status = state
        if not state["authenticated"] and state.get("reason") != "retry":
            return None
        store = import_module(settings.SESSION_ENGINE).SessionStore(session_key=self.session_key)
        user = get_user(SimpleNamespace(session=store))
        if not user.is_authenticated or not user.is_active or user.pk != self.user.pk:
            return None
        return user

    async def store_position(self, position, observed_at, transfer_from=None):
        return await socket_db.run(self.store_position_now, position, observed_at, transfer_from)

    def store_position_now(self, position, observed_at, transfer_from=None, *, require_session=False):
        policy_status = self.policy_status
        user = self.session_user()
        if position is None:
            # 대기 표본만 내는 저장은 세션 정책 상태를 바꾸지 않는다: 끊을 때의 정리 저장이 close 로그 사유(계획 0c)를 덮지 않게
            self.policy_status = policy_status
        if require_session and user is None:
            raise ControlError("로그인 상태를 확인한 뒤 다시 시도해 주세요.")
        if user is not None:
            if position is None:
                flush_movement_event(user)
            elif transfer_from:
                saved = record_floor_transfer(
                    user, self.platform_session_uuid, transfer_from, position["floor"], position, observed_at
                )
            else:
                saved = save_position(user, position, observed_at, session_uuid=self.platform_session_uuid)
            if require_session and not saved:
                raise ControlError("마지막 위치를 저장하지 못했어요. 다시 시도해 주세요.")
            return movement_event_due_at(user)

    async def checkpoint(self, force=False, wait=True):
        """최신 위치 하나를 저장한다(5초마다, 층 이동·종료는 바로). 쓰기는 따로 돌리고 `wait` 일 때만 끝까지 기다린다(계획 3단계).

        저장 중에 온 새 위치는 다음 저장에 간다(저장할 것은 언제나 최신 위치 하나다). 층 이동이 아직 저장되지 않았으면 기한을 기다리지 않는다.
        """
        saving = getattr(self, "saving", None)
        if saving is not None and not saving.done():
            if not wait:
                return  # 저장 중이다: 끝난 뒤 다음 기회(다음 틱·세션 확인)에 다시 본다
            await asyncio.shield(saving)
        force = force or self.pending_floor_transfer is not None
        sample_due = self.movement_event_due_at is not None and timezone.now() >= self.movement_event_due_at
        if not self.position_dirty and self.pending_floor_transfer is None and not sample_due:
            return
        if not force and time.monotonic() - self.last_checkpoint < self.position_interval:
            return
        self.last_checkpoint = time.monotonic()
        state = self.state.copy() if self.position_dirty else None
        self.saving = asyncio.create_task(self.write_position(state, self.position_changed_at, self.pending_floor_transfer))
        if wait:
            await asyncio.shield(self.saving)

    async def write_position(self, state, observed_at, transfer):
        try:
            started = time.monotonic()
            self.movement_event_due_at = await self.store_position(state, observed_at, transfer[0] if transfer else None)
            note_slow_db("position save", started)
        except DatabaseError:
            logging.getLogger(__name__).exception("Could not save the campus position")
        except Exception:  # noqa: BLE001 — 따로 도는 저장의 오류는 여기서 남긴다(이동 처리는 계속한다). 위치는 dirty 로 남아 다음에 다시 저장한다
            logging.getLogger(__name__).exception("Unexpected error while saving the campus position")
        else:
            # A newer frame may arrive while the database write is awaiting.
            if state is not None and self.position_changed_at == observed_at:
                self.position_dirty = False
            if self.pending_floor_transfer == transfer:
                self.pending_floor_transfer = None

    async def check_session_loop(self):
        """위치 저장은 5초(`position_interval`)마다, 세션 확인은 10초(`session_check_interval`)마다 한다(계획 3단계·4a).
        저장은 따로 돌아 기다리지 않는다. 세션 확인은 메시지 처리 순서를 지키도록 자기에게 보낸다(입력 처리와 같은 줄이라 자주 하지 않는다)."""
        last_check = time.monotonic()
        while True:
            await asyncio.sleep(min(self.session_check_interval, self.position_interval))
            await self.checkpoint(wait=False)  # 멈춘 뒤의 마지막 위치도 5초 안에 저장한다
            if time.monotonic() - last_check < self.session_check_interval:
                continue
            last_check = time.monotonic()
            try:
                await self.channel_layer.send(self.channel_name, {"type": "presence.check"})
            except ChannelFull:
                pass  # 이 소켓의 큐가 밀려 있다: 이번 확인은 건너뛰고 다음 주기에 다시 한다(감시 작업이 끝나지 않게, 계획 2b)

    async def presence_check(self, event):
        if self.player_id not in room.players:
            return
        # Reuse the normal session guard rather than querying the database for each stroke.
        if (getattr(self, "poke_requested", False) and self.poke_started is not None
                and time.monotonic() - self.controller.last_input <= poke.INPUT_LEASE):
            await self.touch_activity()
        started = time.monotonic()
        try:
            identity = await self.current_identity()
        except DatabaseError:
            # DB 가 잠깐 안 될 때(장애 전환 등) 로그인 만료로도, 소켓 오류로도 다루지 않는다. 다음 확인에서 다시 본다(계획 6b)
            logging.getLogger(__name__).warning("campus session check could not reach the database; keeping the socket", exc_info=True)
            return
        note_slow_db("session check", started)
        if identity is None:
            await self.session_expired({"reason": self.policy_status.get("reason", "signed_out")})
            return
        if time.monotonic() - self.last_seen > self.idle_timeout:
            await self.leave()
            await self.close(code=4408)
            return
        if any(self.state[key] != value for key, value in identity.items()):
            self.state.update(identity)
            async with room.lock:
                room.players[self.player_id] = self.state.copy()
                self.seen_by_peers = self.state.copy()
                await self.broadcast({"type": "move", "player": self.state.copy(), "identityOnly": True})
            await self.send_packet({"type": "identity", "player": self.state.copy()})

    async def leave(self):
        player_id = getattr(self, "player_id", None)
        await self.finish_task(getattr(self, "saving", None), cancel=False)
        if player_id in room.players:
            self.poke_requested = False
            try:
                await self.stop_poke()
            except Exception:
                logging.getLogger(__name__).exception("Could not stop campus poke during cleanup")
            try:
                await self.checkpoint(force=True)
            except Exception:
                logging.getLogger(__name__).exception("Could not checkpoint campus position during cleanup")
        session_registry.unregister(player_id)
        async with room.lock:
            room.interiors.discard(player_id)
            room.channels.pop(player_id, None)
            room.sockets.pop(player_id, None)
            room.pending_moves.pop(player_id, None)
            room.pending_effects.pop(player_id, None)
            room.controllers.pop(player_id, None)
            session_key = getattr(self, "session_key", None)
            if player_id and room.sessions.get(session_key) == player_id:
                room.sessions.pop(session_key, None)
            if player_id and room.players.pop(player_id, None) is not None:
                try:
                    await self.tell_local({"type": "leave", "id": player_id})
                except Exception:
                    logging.getLogger(__name__).exception("Could not publish local campus leave")
                try:
                    await room.cluster.left(player_id)
                except Exception:
                    logging.getLogger(__name__).exception("Could not publish cluster campus leave")
        await self.discard_groups()

    async def close(self, code=None, reason=None):
        if code not in (None, 1000):
            # 서버가 끊는 이유(계획 0c). 브라우저는 4401 이면 로그인 화면으로 가므로 재로그인의 원인을 운영 로그로 가린다
            logging.getLogger(__name__).warning("campus socket closed by server code=%s policy=%s user=%s",
                                                code, (self.policy_status or {}).get("reason"), getattr(self.user, "pk", None))
        await super().close(code=code, reason=reason)

    async def presence_replaced(self, event):
        await self.leave()
        await self.close(code=4409)

    async def session_expired(self, event):
        if self.player_id not in room.players:
            return
        reason = event.get("reason", "signed_out")
        if reason in {"idle", "maximum", "invalid", "replaced"}:
            await self.send_packet({"type": "session_expired", "reason": reason})
        await self.leave()
        await self.close(code=4401)

    async def disconnect(self, close_code):
        await self.finish_task(getattr(self, "motion_task", None))
        await self.finish_task(getattr(self, "guard_task", None))
        await self.leave()
