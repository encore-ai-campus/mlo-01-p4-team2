import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from django.db import DatabaseError, close_old_connections, transaction
from django.utils import timezone
from .models import FreezeState, FreezeChange

DEFAULT_MESSAGE = '강의 중입니다. 쉬는 시간에 다시 이용할 수 있어요.'
_cache = None
_until = 0.0
_lock = threading.RLock()
REFRESH_SECONDS = 1.0
MAX_STATE_AGE_SECONDS = 3.0
_snapshot = None
_refresh_future = None
_refresh_lock = threading.Lock()
_refresh_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='campus-freeze-db')
logger = logging.getLogger(__name__)


def invalidate():
    global _until, _snapshot
    with _lock:
        _until = 0
        _snapshot = None


def _interrupt_after_pause():
    from event_story.interrupt import interrupt_running
    with transaction.atomic():
        if FreezeState.objects.filter(pk=1,paused=True).exists():interrupt_running()


def _synchronize_auto(state):
    from .schedule import snapshot
    if state.mode != 'AUTO':return state,None
    scheduled=snapshot()
    if state.paused==scheduled['paused'] and state.message==scheduled['message']:return state,scheduled
    with transaction.atomic():
        locked=FreezeState.objects.select_for_update().get(pk=1)
        if locked.mode=='AUTO':
            scheduled=snapshot()
            if locked.paused!=scheduled['paused'] or locked.message!=scheduled['message']:
                entered_pause=not locked.paused and scheduled['paused']
                locked.paused=scheduled['paused'];locked.message=scheduled['message'];locked.revision+=1
                locked.save(update_fields=['paused','message','revision','updated_at'])
                FreezeChange.objects.create(actor=None,paused=locked.paused,revision=locked.revision,message=locked.message)
                if entered_pause:
                    # Release the shared freeze-row lock before settling game rows.
                    transaction.on_commit(_interrupt_after_pause)
        state=locked
    return state,scheduled if state.mode=='AUTO' else None


def read_state(force=False):
    global _cache, _until, _snapshot
    with _lock:
        if not force and _cache is not None and time.monotonic() < _until:
            return dict(_cache)
        try:
            state, _ = FreezeState.objects.get_or_create(pk=1)
            state,scheduled=_synchronize_auto(state)
            _cache = {'paused': state.paused, 'revision': state.revision, 'message': state.message, 'mode':state.mode, **({k:v for k,v in scheduled.items() if k not in ('paused','message')} if scheduled else {})}
        except DatabaseError:
            # Never allow actions because the pause store is temporarily unreachable.
            _cache = {'paused': True, 'revision': (_cache or {}).get('revision', 0),
                      'message': '서버 이용 상태를 확인하고 있어요. 잠시 기다려 주세요.', 'unavailable': True}
        observed_at = time.monotonic()
        _until = observed_at + REFRESH_SECONDS
        # 이동 루프는 DB 조회 중인 _lock을 잡지 않고 완성된 스냅샷만 읽는다.
        _snapshot = (dict(_cache), observed_at)
        return dict(_cache)


def _unavailable(state=None):
    return {'paused': True, 'revision': (state or {}).get('revision', 0),
            'message': '서버 이용 상태를 확인하고 있어요. 잠시 기다려 주세요.', 'unavailable': True}


def _refresh_state():
    # 위치 저장·세션 확인 큐와 분리한다. 연결 정리는 실제 DB 작업 스레드에서 한다.
    global _snapshot, _until, _cache
    try:
        close_old_connections()
        return read_state()
    except Exception:
        logger.exception('Could not refresh campus movement availability')
        with _lock:
            state = _unavailable(_snapshot[0] if _snapshot else None)
            observed_at = time.monotonic()
            _cache = dict(state)
            _snapshot = (state, observed_at)
            _until = observed_at + REFRESH_SECONDS
        return dict(state)
    finally:
        close_old_connections()


def _ensure_refresh():
    global _refresh_future
    # 완료되지 않은 조회는 하나만 유지한다. 소켓 취소로 DB 작업을 중복 제출하지 않는다.
    with _refresh_lock:
        if _refresh_future is None or _refresh_future.done():
            _refresh_future = _refresh_executor.submit(_refresh_state)
        return _refresh_future


async def movement_state():
    """이동용 조회: 갱신은 예약만 하고 DB 잠금이나 조회 완료를 기다리지 않는다."""
    snapshot = _snapshot
    now = time.monotonic()
    if snapshot is None or now >= _until:
        _ensure_refresh()
    # 최초 조회 전과 3초 넘게 낡은 상태는 차단한다. 관리자도 장애를 우회하지 않는다.
    if snapshot is None or now - snapshot[1] >= MAX_STATE_AGE_SECONDS:
        return _unavailable(snapshot[0] if snapshot else None)
    return dict(snapshot[0])


async def async_state():
    """채팅 등 기존 호출자는 최신 상태를 기다리되 이동용 DB 실행 경로를 공유한다."""
    snapshot = _snapshot
    if snapshot is not None and time.monotonic() < _until:
        return dict(snapshot[0])
    return dict(await asyncio.shield(asyncio.wrap_future(_ensure_refresh())))


class RevisionConflict(Exception):
    pass


def change(actor, paused, message, expected_revision, mode='MANUAL'):
    with transaction.atomic():
        FreezeState.objects.get_or_create(pk=1)
        state = FreezeState.objects.select_for_update().get(pk=1)
        if state.revision != expected_revision:
            raise RevisionConflict
        if mode not in ('AUTO','MANUAL'):raise ValueError('지원하지 않는 운영 모드입니다.')
        if mode=='AUTO':
            from .schedule import snapshot
            scheduled=snapshot();paused=scheduled['paused'];message=scheduled['message']
        message = message or DEFAULT_MESSAGE
        if state.paused != paused or state.message != message or state.mode != mode:
            # Conditional update also serializes independent SQLite connections.
            updated = FreezeState.objects.filter(pk=1, revision=expected_revision).update(
                paused=paused, message=message, mode=mode, revision=expected_revision + 1, updated_at=timezone.now())
            if not updated:
                raise RevisionConflict
            FreezeChange.objects.create(actor=actor, paused=paused,
                                        revision=expected_revision + 1, message=message)
    if paused:
        from event_story.interrupt import interrupt_running
        with transaction.atomic():
            interrupt_running()
    invalidate()
    return read_state(force=True)


def admin_exempt(user):
    return bool(getattr(user,'is_authenticated',False) and getattr(user,'is_active',False) and getattr(user,'is_staff',False))

def for_user(state,user):
    bypass=admin_exempt(user) and not state.get('unavailable',False)
    return {**state,'canManage':admin_exempt(user),'adminBypass':bypass,'blocked':bool(state['paused'] and not bypass)}
