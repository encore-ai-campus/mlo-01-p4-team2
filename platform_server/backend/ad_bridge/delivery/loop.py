"""campus_jobs 안에서 노출을 광고 서버로 보내는 백그라운드 작업자(AB-1 §4.1·§4.4·§4.5).

노출은 요청 안에서 보내지 않고 여기서만 보낸다. 작업자 K개가 같은 루프를 돌며,
기록 확보는 조건부 UPDATE와 임대라 한 기록을 한 작업자만 보낸다.
"""

import logging
import threading

from django.apps import apps
from django.conf import settings
from django.db import close_old_connections, connections

from config.dberrors import is_transient, sqlstate

logger = logging.getLogger(__name__)
DEFAULT_WORKERS = 4
MAX_WORKERS = 8
IDLE_SECONDS = 1
BATCH_LIMIT = 100


def worker_count():
    raw = getattr(settings, "ADS_DISPATCH_WORKERS", "")
    try:
        count = int(raw) if str(raw).strip() else DEFAULT_WORKERS
        if not 1 <= count <= MAX_WORKERS:
            raise ValueError
    except (TypeError, ValueError):
        logger.warning("ADS_DISPATCH_WORKERS는 1~%d의 정수여야 합니다. 기본값 %d로 돕니다.", MAX_WORKERS, DEFAULT_WORKERS)
        count = DEFAULT_WORKERS
    return count


def dispatch_loop(stop, *, client, policy, dispatch=None):
    """멈춤 신호까지 기한이 된 노출을 보낸다. 회차마다의 예외는 남기고 다음 회차로 간다."""
    from ..impressions import store
    from .worker import dispatch_due_impressions

    dispatch = dispatch or dispatch_due_impressions
    while not apps.ready:
        if stop.wait(1):
            return
    while not stop.is_set():
        attempted = 0
        try:
            close_old_connections()
            counts = dispatch(client=client, store=store, limit=BATCH_LIMIT, policy=policy, should_stop=stop.is_set)
            attempted = counts["attempted"]
        except Exception as error:
            if is_transient(error):
                logger.warning("광고 노출 전송이 DB를 잠깐 쓰지 못했습니다(%s sqlstate=%s). 다음 회차에 다시 합니다.",
                               type(error).__name__, sqlstate(error))
            else:
                logger.exception("광고 노출 전송 회차가 실패했습니다. 다음 회차에 다시 합니다.")
            connections.close_all()
        # 보낼 것이 있었으면 바로 다음 회차, 없으면 잠깐 쉰다.
        if not attempted:
            stop.wait(IDLE_SECONDS)


def start_dispatch(stop=None):
    """ADS_ENABLED일 때 작업자 스레드를 띄운다. 설정 오류는 예외로 올린다 — 부르는 쪽(campus_jobs)이 잡는다."""
    if not getattr(settings, "ADS_ENABLED", False):
        return []
    from ..api.config import AdsConfig
    from ..routing import RoutedAdsClient
    from .policy import RetryPolicy

    client = RoutedAdsClient(AdsConfig.from_env())
    policy = RetryPolicy()
    stop = stop or threading.Event()
    count = worker_count()
    workers = [threading.Thread(target=dispatch_loop, args=(stop,), kwargs={"client": client, "policy": policy},
                                name=f"ads-impression-dispatch-{index}", daemon=True) for index in range(count)]
    for worker in workers:
        worker.start()
    return workers
