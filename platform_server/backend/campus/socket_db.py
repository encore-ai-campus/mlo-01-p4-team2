"""소켓 DB 작업을 프로세스별 스레드 하나에서 순서대로 처리한다.

풀을 쓰면 작업이 끝날 때 연결을 반환한다. 풀을 쓰지 않으면 기존 연결을 재사용한다.
"""
import asyncio
import functools
import logging
from concurrent.futures import ThreadPoolExecutor

from django.db import DatabaseError, close_old_connections

from config.connections import keep_thread_connection
from config.dberrors import is_transient, sqlstate

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="campus-socket-db")


def _call(fn, args, kwargs):
    keep_thread_connection()  # 이 스레드의 연결만 이어 쓴다(HTTP 요청은 그대로)
    close_old_connections()  # 오래됐거나 오류가 난 연결만 닫는다
    try:
        return fn(*args, **kwargs)
    finally:
        close_old_connections()  # 풀 연결은 성공·실패 모두 반환한다.


async def run(fn, *args, **kwargs):
    """`fn(*args, **kwargs)` 를 소켓 DB 스레드에서 실행한다(`database_sync_to_async(fn)(...)` 대신)."""
    try:
        return await asyncio.get_running_loop().run_in_executor(_executor, functools.partial(_call, fn, args, kwargs))
    except DatabaseError as error:
        if is_transient(error):  # 처리는 부른 쪽이 한다. 여기서는 장애 전환·잠금 대기가 언제 있었는지만 남긴다
            logger.warning("campus socket database call %s failed: %s sqlstate=%s",
                           getattr(fn, "__qualname__", fn), type(error).__name__, sqlstate(error))
        raise


# 연결 중 DB 를 잠깐 못 씀 = "잠시 후 다시"(표준 1013 과 같은 뜻). daphne(autobahn)는 앱이 1000·3000–4999 만 보내게 해서
# 1013 은 보낼 수 없다(보내면 예외 → 1011). 브라우저(presence.js)는 4401·4409 밖의 코드를 받으면 간격을 늘려 다시 붙는다(이동·채팅 같음)
TRY_AGAIN_LATER = 4013


async def try_again_later(consumer):
    """연결 중 DB 를 잠깐 못 쓰면 4013 으로 닫아 브라우저가 기다렸다 다시 붙게 한다(04 §23).

    accept 전에 닫으면 daphne 가 닫힘 코드 없이 403 으로 거절하므로 받은 뒤 닫는다.
    accept 전의 실패에만 부른다(이미 받은 소켓을 다시 accept 하면 daphne 가 AttributeError 를 낸다).
    """
    await consumer.accept()
    await consumer.close(code=TRY_AGAIN_LATER)
