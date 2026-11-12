import asyncio
import threading
import time
from django.db import DatabaseError, transaction
from django.utils import timezone
from .models import FreezeState, FreezeChange

DEFAULT_MESSAGE = '강의 중입니다. 쉬는 시간에 다시 이용할 수 있어요.'
_cache = None
_until = 0.0
_lock = threading.RLock()
_pending = None


def invalidate():
    global _until
    with _lock:
        _until = 0


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
    global _cache, _until
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
        _until = time.monotonic() + 1.0
        return dict(_cache)


async def async_state():
    global _pending
    if _cache is not None and time.monotonic() < _until:
        return dict(_cache)
    # One refresh per process, not one DB request per frame or per connected user.
    if _pending is None or _pending.done():
        from campus import socket_db
        _pending = asyncio.create_task(socket_db.run(read_state))
    return dict(await asyncio.shield(_pending))


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
