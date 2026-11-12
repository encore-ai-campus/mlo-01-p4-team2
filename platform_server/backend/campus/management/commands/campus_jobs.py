"""Run cafeteria refresh, expired-session cleanup and (when ads are on) ad impression delivery."""
import logging
import signal
import threading

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


def start_ad_dispatch():
    """광고 노출 전송 작업자를 띄운다. 어떤 예외도 밖으로 내보내지 않는다(AB-1 §4.1 C2).

    run.py는 자식 하나가 끝나면 서비스 PC 전체를 멈추므로, 광고 쪽 문제로 campus_jobs가 끝나면 안 된다.
    """
    try:
        from ad_bridge.delivery.loop import start_dispatch
        return start_dispatch()
    except Exception as error:
        # 이 코드의 설정 오류 메시지에는 값이 없다(ad_bridge/api/config.py, routing.py).
        logger.error("광고 노출 전송 작업자를 시작하지 못했습니다. 광고 노출이 광고 서버로 가지 않습니다: %s: %s",
                     type(error).__name__, error)
        return []


class Command(BaseCommand):
    help = "식단 사진 갱신과 만료 세션 정리, 광고를 켰으면 광고 노출 전송을 실행합니다."

    def handle(self, *args, **options):
        from accounts.session_cleanup import start_session_cleanup
        from campus.menu_updater import start_menu_updater

        from django.conf import settings

        start_menu_updater()
        start_session_cleanup()
        workers = start_ad_dispatch()
        # 이 앱은 INFO 로그를 내보내지 않으므로 작업자 상태는 표준 출력에 한 줄 쓴다(AB-1 C4 — 작업자가 없는지 알아챔).
        if workers:
            self.stdout.write(f"ads impression dispatch: {len(workers)} workers")
        elif getattr(settings, "ADS_ENABLED", False):
            self.stdout.write("ads impression dispatch: NOT RUNNING (see the error above)")
        else:
            self.stdout.write("ads impression dispatch: off (ADS_ENABLED=0)")
        stop = threading.Event()
        for name in (signal.SIGTERM, signal.SIGINT):
            signal.signal(name, lambda *_: stop.set())
        self.stdout.write("campus jobs running")
        stop.wait()
