from uuid import UUID
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from eventing.models import PlatformSession
from .models import EventProgress, GameDefinition, GamePlay
from .achievement_services import TIMER_ID, TYPING_ID, achievement_snapshot

CONFIGS = {"timer": {"targetMs": 11110, "toleranceMs": 0, "overtimeMs": 2000},
           "typing": {"minimumAccuracy": 95}}


class GameError(Exception):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


def require_open():
    from .main import ensure_open
    from learning.services import LearningError
    try:
        ensure_open()
    except LearningError as error:
        raise GameError(str(error), error.status) from error


def receipt(play):
    return {"play_uuid": str(play.pk), "status": play.status,
            "duration_ms": play.duration_ms, "started_at": play.started_at.isoformat(),
            "ended_at": play.ended_at.isoformat() if play.ended_at else None}


def active_session(user, session_id):
    try:
        session_id = UUID(str(session_id))
    except (ValueError, TypeError):
        raise GameError("다시 로그인해 주세요.", 401)
    session = PlatformSession.objects.select_for_update().filter(
        pk=session_id, user=user, ended_at__isnull=True).first()
    if session is None:
        raise GameError("게임을 시작한 접속이 종료됐어요.", 409)
    return session


@transaction.atomic
def start_game(user, code, session_id):
    require_open()
    get_user_model().objects.select_for_update().get(pk=user.pk)
    if code not in CONFIGS:
        raise GameError("지원하는 게임을 확인해 주세요.")
    game = GameDefinition.objects.filter(game_code=code, is_enabled=True, is_placed=True).first()
    if game is None:
        raise GameError("지금은 이 게임을 실행할 수 없어요.", 409)
    session = active_session(user, session_id)
    GamePlay.objects.filter(user=user, status="RUNNING").update(status="INTERRUPTED", ended_at=timezone.now())
    play = GamePlay.objects.create(user=user, session=session, game=game)
    return {**receipt(play), "game_code": code, "game_name": game.game_name, "config": dict(CONFIGS[code])}


@transaction.atomic
def finish_game(user, play_id, session_id, data):
    require_open()
    if not isinstance(data, dict) or set(data) != {"duration_ms", "status"}:
        raise GameError("게임 결과 형식을 확인해 주세요.")
    status, duration = data["status"], data["duration_ms"]
    if status not in ("SUCCESS", "FAILED", "INTERRUPTED"):
        raise GameError("게임 종료 상태를 확인해 주세요.")
    if duration is not None and (type(duration) is not int or duration < 0):
        raise GameError("플레이 시간을 확인해 주세요.")
    if status != "INTERRUPTED" and duration is None:
        raise GameError("플레이 시간이 필요해요.")
    get_user_model().objects.select_for_update().get(pk=user.pk)
    session = active_session(user, session_id)
    try:
        play = GamePlay.objects.select_for_update().select_related("game").get(pk=play_id, user=user)
    except GamePlay.DoesNotExist:
        raise Http404("게임 기록을 찾을 수 없어요.")
    if play.game.game_code not in CONFIGS:
        raise GameError("이 게임은 메인 미션에서 결과를 제출해 주세요.", 409)
    if play.session_id != session.pk:
        raise GameError("게임을 시작한 접속과 달라요.", 409)
    if play.game.game_code == "timer" and status == "SUCCESS" and duration != 11110:
        raise GameError("타이머 성공 기록은 11.11초여야 해요.")
    if play.status != "RUNNING":
        if (play.status, play.duration_ms) == (status, duration):
            return {**receipt(play), "achievements": achievement_snapshot(user), "newly_completed": []}
        raise GameError("이미 다른 결과로 종료된 게임이에요.", 409)
    now = timezone.now()
    if duration is not None and duration > int((now-play.started_at).total_seconds()*1000) + 2000:
        raise GameError("게임 시작 시각과 플레이 시간이 맞지 않아요.")
    # shortcut: JS 판정 결과를 저장한다. 경품 연동 전에 서버 검증을 강화한다.
    play.status, play.duration_ms, play.ended_at = status, duration, now
    play.save(update_fields=["status", "duration_ms", "ended_at"])
    newly_completed = []
    if play.status == GamePlay.Status.SUCCESS:
        event_id = {"timer": TIMER_ID, "typing": TYPING_ID}[play.game.game_code]
        progress, _ = EventProgress.objects.get_or_create(
            user=user, event_id=event_id, defaults={"started_at": play.started_at})
        if progress.status != EventProgress.Status.COMPLETED:
            progress.status, progress.completed_at = EventProgress.Status.COMPLETED, play.ended_at
            progress.save(update_fields=["status", "completed_at"])
            newly_completed.append(str(event_id))
    return {**receipt(play), "achievements": achievement_snapshot(user), "newly_completed": newly_completed}
