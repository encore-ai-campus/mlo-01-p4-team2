"""앱 → DB 경로 확인 `/health/db`(감시 M9, MON-4 계획 46 §2.1): 앱의 연결로 쿼리 한 번, 대기 서버면 실패, 세션·사용자를 보지 않음, 200/503 만."""
from unittest import mock

from django.db import InterfaceError, OperationalError, connection
from django.test import TestCase, tag
from django.test.utils import CaptureQueriesContext

from tests.support.cases import Cases
from tests.support.factories import make_advertiser
from tests.support.helpers import PROD_ENV, run_python

URL = "/health/db"
OK = b'{"status": "ok"}'
UNAVAILABLE = b'{"status": "unavailable"}'


def standby_cursor():
    """`pg_is_in_recovery()` 가 참인 연결(쓰기 입구가 대기 서버에 닿음)의 커서."""
    cursor = mock.MagicMock()
    cursor.__enter__.return_value.fetchone.return_value = (True,)
    return cursor


@tag("security")
class DbHealthTests(Cases, TestCase):
    def case_primary_is_200_with_one_query_and_nothing_else(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(URL)
        self.assertEqual((response.status_code, response.content), (200, OK))
        self.assertEqual([q["sql"] for q in queries.captured_queries], ["select pg_is_in_recovery()"])

    def case_a_logged_in_session_is_not_read_or_written(self):
        """세션 쿠키를 실어도 세션·사용자 표를 읽지 않는다 — 미들웨어는 접근할 때만 읽고, 뷰는 보지 않는다."""
        self.client.force_login(make_advertiser())
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([q["sql"] for q in queries.captured_queries], ["select pg_is_in_recovery()"])
        self.assertEqual(dict(response.cookies), {})  # Set-Cookie 없음

    def case_a_standby_behind_the_write_entry_is_503(self):
        with mock.patch.object(connection, "cursor", return_value=standby_cursor()), \
                self.assertLogs("serving_api.views", "WARNING") as logs:
            response = self.client.get(URL)
        self.assertEqual((response.status_code, response.content), (503, UNAVAILABLE))
        self.assertEqual(logs.output, ["WARNING:serving_api.views:DB 경로 확인 실패: 쓰기 입구가 대기 서버"])

    def case_db_errors_are_503_without_their_text(self):
        for error in (OperationalError('connection to server at "10.9.9.9", port 5000 failed: server closed the connection unexpectedly'),
                      InterfaceError("connection already closed")):
            with self.subTest(type(error).__name__), mock.patch.object(connection, "cursor", side_effect=error), \
                    self.assertLogs("serving_api.views", "WARNING") as logs:
                response = self.client.get(URL)
            self.assertEqual((response.status_code, response.content), (503, UNAVAILABLE))  # 주소·문구는 몸에 없다
            (line,) = logs.output
            self.assertIn(f"DB 경로 확인 실패 {type(error).__name__} ", line)  # 까닭은 로그 한 줄에만

    def case_only_get(self):
        self.assertEqual(self.client.post(URL).status_code, 405)

    def case_a_refused_connection_is_503_through_the_real_stack(self):
        """흉내가 아니라 운영 설정·미들웨어 전체로(새 프로세스), DB 가 받지 않는 포트에서 503. 뷰 앞의 무엇이라도 DB 에 닿으면 500 이 되므로
        세션·인증 미들웨어가 DB 를 건드리지 않는다는 증명도 겸한다."""
        code = ("import django; django.setup()\n"
                "from django.test import Client\n"
                "r = Client().get('/health/db', secure=True, HTTP_HOST='ads.example.com')\n"
                "print(r.status_code, r.content.decode())\n")
        result = run_python(code, "config.settings.prod", {**PROD_ENV, "DB_HOST": "127.0.0.1", "DB_PORT": "1"})
        self.assertEqual((result.returncode, result.stdout.strip()), (0, '503 {"status": "unavailable"}'), result.stderr[-600:])
        self.assertIn("DB 경로 확인 실패 OperationalError", result.stderr)

    def test_db_health(self):
        self.run_cases()
