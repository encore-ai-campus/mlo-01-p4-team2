"""Run cafeteria refresh and expired-session cleanup."""
import signal
import threading

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "식단 사진 갱신과 만료 세션 정리를 실행합니다."

    def handle(self, *args, **options):
        from accounts.session_cleanup import start_session_cleanup
        from campus.menu_updater import start_menu_updater

        start_menu_updater()
        start_session_cleanup()
        stop = threading.Event()
        for name in (signal.SIGTERM, signal.SIGINT):
            signal.signal(name, lambda *_: stop.set())
        self.stdout.write("campus jobs running")
        stop.wait()
