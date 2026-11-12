"""One authenticated browser session per account; a new login replaces the previous one."""
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.sessions.models import Session
import time

from django.db import OperationalError, connection, transaction

from eventing.events import start_platform_session
from .models import ActiveLogin


def session_is_current(key, user_id):
    owner = ActiveLogin.objects.filter(user_id=user_id).first()
    if owner is None:
        # Sessions created before this feature claim ownership once. A stale key
        # never reclaims it, even if the latest session has expired or logged out.
        if not get_user_model().objects.filter(pk=user_id, is_active=True).exists():
            return False
        owner, _ = ActiveLogin.objects.get_or_create(user_id=user_id, defaults={"session_key": key})
    return owner.session_key == key


def replace_login(session, user, now):
    # SQLite cannot upgrade two concurrent read transactions into writers. Retry
    # the complete transaction, never a partly committed ownership change.
    for attempt in range(4):
        try:
            return _replace_login(session, user, now)
        except OperationalError as error:
            if connection.vendor != "sqlite" or "locked" not in str(error).lower() or attempt == 3:
                raise
            time.sleep(.05 * (attempt + 1))


def _replace_login(session, user, now):
    from campus import session_registry
    from .session_policy import expire_record, notify_expiry

    if not session.session_key:
        session.create()
    # 옛 세션의 마지막 위치(다른 프로세스면 Redis)는 잠그기 전에 읽는다. Redis 가 느려도 아래 잠금은 길어지지 않는다
    seen_key = ActiveLogin.objects.filter(user=user).values_list("session_key", flat=True).first()
    seen = None
    if seen_key and seen_key != session.session_key and Session.objects.filter(session_key=seen_key).exists():
        seen = session_registry.latest(seen_key, user.pk)
    with transaction.atomic():
        # Lock an existing account row as well as its optional ownership row, so
        # simultaneous first logins cannot both become current on MySQL.
        get_user_model().objects.select_for_update().get(pk=user.pk)
        previous = ActiveLogin.objects.select_for_update().filter(user=user).first()
        previous_key = previous.session_key if previous else None
        ActiveLogin.objects.update_or_create(user=user, defaults={"session_key": session.session_key})
        if previous_key and previous_key != session.session_key:
            old = Session.objects.select_for_update().filter(session_key=previous_key).first()
            if old is not None:
                data = old.get_decoded()
                if str(data.get(SESSION_KEY)) == str(user.pk):
                    # 그사이 다른 로그인이 주인을 바꿨으면 미리 읽은 위치는 다른 세션 것이다. DB 체크포인트로 갈음한다
                    expire_record(old, data, "replaced", now=now, notify=False,
                                  snapshot=seen if previous_key == seen_key else None)
            # Notify only after commit: the old socket must observe the revoked
            # key, and a failed new login must not close the previous login.
            transaction.on_commit(lambda: notify_expiry(previous_key, "replaced"), robust=True)
        start_platform_session(user, session, now=now)
        session.save()
