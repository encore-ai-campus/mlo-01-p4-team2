"""소켓 DB 작업을 프로세스별 스레드 하나에서 순서대로 처리한다.

풀을 쓰면 작업이 끝날 때 연결을 반환한다. 풀을 쓰지 않으면 기존 연결을 재사용한다.
"""
import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections

from config.connections import keep_thread_connection

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
    return await asyncio.get_running_loop().run_in_executor(_executor, functools.partial(_call, fn, args, kwargs))
