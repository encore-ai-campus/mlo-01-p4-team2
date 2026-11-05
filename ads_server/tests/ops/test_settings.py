import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.test import SimpleTestCase, TestCase

from config.env import load_env, load_env_for_web
from tests.support.helpers import DB_ENV, PROD_ENV, run_python
from tests.support.cases import Cases

SHOW_SECRET = "from django.conf import settings; print(settings.SECRET_KEY)"


class ProdSettingsTests(Cases, SimpleTestCase):
    def case_prod_requires_secret_key(self):
        from config.settings.base import INSECURE_DEV_KEY

        env = {k: v for k, v in PROD_ENV.items() if k != "SECRET_KEY"}
        r = run_python(SHOW_SECRET, "config.settings.prod", env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SECRET_KEY", r.stderr)
        for key in (INSECURE_DEV_KEY, "short"):  # dev 기본 키·짧은 키도 거부한다
            self.assertNotEqual(run_python(SHOW_SECRET, "config.settings.prod", dict(PROD_ENV, SECRET_KEY=key)).returncode, 0, key)

    def case_prod_requires_each_deployment_value(self):
        for missing in ("ALLOWED_HOSTS", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "ADMIN_URL_PATH", "MEDIA_ROOT", "ASSET_PUBLIC_BASE_URL", "TRUSTED_PROXY_COUNT", "CSRF_TRUSTED_ORIGINS"):
            env = {k: v for k, v in PROD_ENV.items() if k != missing}
            r = run_python(SHOW_SECRET, "config.settings.prod", env)
            self.assertNotEqual(r.returncode, 0, missing)

    def case_prod_requires_a_trusted_proxy_count_of_at_least_one(self):
        """6-5c(설계 §10 "배포 구성"): 운영은 앞단 nginx 뒤라 신뢰할 프록시 수가 필수이고 1 이상이다(기본값 없음).
        0 이면 모든 요청이 nginx IP 하나로 보여 로그인 잠금·감사 로그의 IP 가 무너진다 — 6-3h 의 추정 경고 대신 기동 단계에서 막는다."""
        show = "from django.conf import settings; print(settings.TRUSTED_PROXY_COUNT)"
        for bad in ("0", "-1", "one"):
            r = run_python(show, "config.settings.prod", dict(PROD_ENV, TRUSTED_PROXY_COUNT=bad))
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("TRUSTED_PROXY_COUNT", r.stderr, bad)
        r = run_python(show, "config.settings.prod", dict(PROD_ENV, TRUSTED_PROXY_COUNT="2"))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "2"), r.stderr[-300:])
        r = run_python(show, "config.settings.dev", dict(DB_ENV, TRUSTED_PROXY_COUNT=""))  # dev(로컬 runserver)는 지금처럼 0(빈 값은 없는 것과 같다)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "0"), r.stderr[-300:])

    def case_prod_web_starts_without_an_event_store(self):
        """6-3e(설계 §10 "이벤트 저장소 설정"): 웹은 MongoDB 를 쓰지 않으므로 MONGO_HOSTS 가 없어도 기동한다(None — 웹 노드에 비밀번호를 두지 않는다).
        있으면 읽고, 비밀번호는 설정 문자열에 섞지 않는다. 설정이 없을 때 멈추는 쪽은 중계다(test_event_relay 의 RelayWithoutEventStoreTests)."""
        show = "from django.conf import settings; print(settings.EVENT_STORE)"
        r = run_python(show, "config.settings.prod", PROD_ENV)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "None"), r.stderr)
        r = run_python(show, "config.settings.prod", dict(PROD_ENV, MONGO_HOSTS="a:1,b:2", MONGO_USER="u", MONGO_PASSWORD="pw", MONGO_REPLICA_SET="rs0"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("'HOSTS': ['a:1', 'b:2']", r.stdout)
        self.assertIn("'REPLICA_SET': 'rs0'", r.stdout)

    def case_prod_debug_cannot_be_enabled_by_env(self):
        r = run_python("from django.conf import settings; print(settings.DEBUG)", "config.settings.prod",
                       dict(PROD_ENV, DJANGO_DEBUG="1"))
        self.assertEqual(r.stdout.strip(), "False", r.stderr)

    def test_prod_settings(self):
        self.run_cases()


class DevAndTestSettingsTests(Cases, SimpleTestCase):
    def case_debug_default_false(self):
        r = run_python("from django.conf import settings; print(settings.DEBUG)", "config.settings.dev", DB_ENV)
        self.assertEqual(r.stdout.strip(), "False", r.stderr)

    def case_debug_can_be_enabled_in_dev_only(self):
        r = run_python("from django.conf import settings; print(settings.DEBUG)", "config.settings.dev",
                       dict(DB_ENV, DJANGO_DEBUG="1"))
        self.assertEqual(r.stdout.strip(), "True", r.stderr)

    def case_test_settings_ignore_everything_but_the_database_target(self):
        """동작을 바꾸는 값(SECRET_KEY·관리자 경로)은 무시하고, 접속 대상(DB_*)만 환경에서 읽는다."""
        code = ("from django.conf import settings as s; d = s.DATABASES['default']; "
                "print(s.SECRET_KEY.startswith('test-only'), s.ADMIN_URL_PATH, d['NAME'], d['HOST'])")
        r = run_python(code, "config.settings.test", dict(DB_ENV, SECRET_KEY="from-env", ADMIN_URL_PATH="from-env/"))
        self.assertEqual(r.stdout.strip(), "True test-admin/ ads_x db.example.com", r.stderr)

    def case_dev_requires_database_credentials(self):
        for missing in ("DB_USER", "DB_PASSWORD"):
            r = run_python("from django.conf import settings; settings.DATABASES", "config.settings.dev",
                           {k: v for k, v in DB_ENV.items() if k != missing})
            self.assertNotEqual(r.returncode, 0, missing)
            self.assertIn(missing, r.stderr)

    def case_settings_are_checked_at_startup(self):
        """버그 탐색 K1: 쓰는 순간에만 검사하던 설정을 시스템 검사(check·migrate·runserver·check --deploy)가 미리 막는다 — 잘못 배포하면 매체 요청이 모두 500 이던 것."""
        from django.core.checks import run_checks
        from django.test import override_settings

        from config.checks import check_settings

        self.assertEqual(check_settings(), [])
        limits = settings.MEDIA_RATE_LIMITS
        bad = {
            "MEDIA_RATE_LIMITS": [{"decisions": (150, 300)}, dict(limits, extra=(1, 1)), dict(limits, reads=(0, 300)), dict(limits, reads=(float("inf"), 300)),
                                  dict(limits, reads=(True, 300)), dict(limits, reads=(150, 0)), dict(limits, reads=(150, 1.5)), dict(limits, reads=150)],
            "ADS_SELECTION_POLICY": ["nope", ["x"]],
            "ADS_ENGINE_EXTRA_FILTERS": [("nope",), "status"],
            "REACTION_RULES": [("known-decision", "nope"), "known-decision"],
            "BILLING_CHARGE_POLICY": ["nope"],
            "SIGNUP_GRANT_POINTS": [-1, True, "1"],
            "ASSET_PUBLIC_BASE_URL": ["ads.example.com", "ftp://ads.example.com", "https://", None, "http://[::1"],
            "BUDGET_DAY_OFFSET_HOURS": [15, -13, 10**9, 1.5],
        }
        for name, values in bad.items():
            for value in values:
                with self.subTest(name=name, value=value), override_settings(**{name: value}):
                    self.assertEqual([(e.id, e.msg.split()[0].split("[")[0].rstrip(":")) for e in check_settings()], [("config.E001", name)])
        with override_settings(BUDGET_DAY_OFFSET_HOURS=-12, ASSET_PUBLIC_BASE_URL="http://127.0.0.1:8001"):
            self.assertEqual(check_settings(), [])  # 경계값·dev 의 http 주소는 통과
        with override_settings(BUDGET_DAY_OFFSET_HOURS=14):
            self.assertEqual(check_settings(), [])
        with override_settings(SIGNUP_GRANT_POINTS=-1):
            self.assertIn("config.E001", [e.id for e in run_checks()])  # 등록돼 있다(check·migrate 가 돈다)

    def test_dev_and_test_settings(self):
        self.run_cases()


class TimeAndDatabaseTests(SimpleTestCase):
    def test_timezone_is_utc(self):
        self.assertTrue(settings.USE_TZ)
        self.assertEqual(settings.TIME_ZONE, "UTC")


class PostgresSessionTests(Cases, TestCase):
    """연결이 계획 docs/plans/00 P1-1 의 세션 설정으로 열린다(격리 수준, 잠금 대기 제한, 정렬, 시간대)."""

    def case_session_settings(self):
        with transaction.atomic(), connection.cursor() as cur:  # 격리 수준은 트랜잭션을 여는 BEGIN 에 붙는다 — 트랜잭션 밖의 값은 서버 기본값이다
            cur.execute("SELECT current_setting('transaction_isolation'), current_setting('lock_timeout'), current_setting('server_encoding'), current_setting('idle_in_transaction_session_timeout')")
            isolation, lock_timeout, encoding, idle_timeout = cur.fetchone()
            cur.execute("SELECT 'a' = 'A', 'e' = 'é', 'B' < 'a', 'z' < 'é', upper('é')")
            case_equal, accent_equal, upper_before_lower, accent_after_z, upper_e = cur.fetchone()
        self.assertEqual(isolation, "read committed")
        self.assertEqual(lock_timeout, "5s")  # 넘으면 55P03 → 503 + Retry-After(결정 D9). 기본(0)은 끝없이 기다린다
        self.assertEqual(idle_timeout, "1min")  # 트랜잭션을 연 채 멈춘 연결은 60초 뒤 서버가 끊어 행 잠금을 푼다(버그 탐색 K3). 기본(0)은 끝없이 쥔다
        self.assertEqual(encoding, "UTF8")
        # 정렬 builtin C.UTF-8(결정 D11): 같음은 대소문자·악센트를 구분하고(유일 제약이 MySQL utf8mb4_0900_as_cs 와 같게), 순서는 코드값이다.
        # 대소문자 변환은 유니코드라 대소문자 무시 검색(icontains)이 ASCII 밖의 글자도 접는다
        self.assertEqual((case_equal, accent_equal), (False, False))
        self.assertEqual((upper_before_lower, accent_after_z), (True, True))
        self.assertEqual(upper_e, "É")
        self.assertEqual(connection.timezone_name, "UTC")

    def case_commit_is_durable_on_this_server(self):
        """수신 2xx 의 약속은 커밋이 디스크에 내려간 뒤다(T25). 서버가 커밋마다 WAL 을 내리지 않게 설정돼 있으면 알린다."""
        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('fsync'), current_setting('synchronous_commit'), current_setting('full_page_writes')")
            fsync, synchronous_commit, full_page_writes = cur.fetchone()
        self.assertEqual((fsync, full_page_writes), ("on", "on"))
        self.assertNotEqual(synchronous_commit, "off")

    def test_postgres_session(self):
        self.run_cases()


class DotenvTests(Cases, SimpleTestCase):
    def _env_cleanup(self, name):
        old = os.environ.pop(name, None)
        self.addCleanup(lambda: os.environ.pop(name, None) if old is None else os.environ.__setitem__(name, old))

    def case_dotenv_loaded_when_present(self):
        self._env_cleanup("ADS_TEST_ONE")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("ADS_TEST_ONE=from_file\n")
            load_env(p)
        self.assertEqual(os.environ["ADS_TEST_ONE"], "from_file")

    def case_real_env_overrides_dotenv(self):
        self._env_cleanup("ADS_TEST_TWO")
        os.environ["ADS_TEST_TWO"] = "from_real_env"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("ADS_TEST_TWO=from_file\n")
            load_env(p)
        self.assertEqual(os.environ["ADS_TEST_TWO"], "from_real_env")

    def case_missing_dotenv_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            load_env(Path(d) / "does-not-exist.env")  # 예외가 없어야 한다

    def test_dotenv(self):
        self.run_cases()



class WebEntrySettingsTests(Cases, SimpleTestCase):
    """6-3a(설계 §10 "웹 진입점의 설정 모듈"): 웹 진입점은 `.env` 의 설정 모듈을 따르지 않는다 — 실제 환경변수에 있으면 그 값, 없으면 prod.
    예시 `.env`(전에는 dev 줄이 있었다)를 복사한 운영 웹 서버가 공개된 개발 키·Secure 쿠키 없이 dev 로 조용히 뜨던 문제(리팩터링 세션 검토 O1)."""

    DEV_DOTENV = "DJANGO_SETTINGS_MODULE=config.settings.dev\nADS_TEST_WEB=from_file\n"

    def dotenv_dir(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        (Path(directory.name) / ".env").write_text(self.DEV_DOTENV)
        return directory.name

    def keep_env(self, *names):
        saved = {name: os.environ.get(name) for name in names}
        for name in names:
            os.environ.pop(name, None)
        self.addCleanup(lambda: [os.environ.pop(n, None) if v is None else os.environ.__setitem__(n, v) for n, v in saved.items()])

    def case_the_dotenv_settings_module_is_ignored_but_other_values_are_loaded(self):
        self.keep_env("DJANGO_SETTINGS_MODULE", "ADS_TEST_WEB")
        load_env_for_web(Path(self.dotenv_dir()) / ".env")
        self.assertEqual((os.environ["DJANGO_SETTINGS_MODULE"], os.environ["ADS_TEST_WEB"]), ("config.settings.prod", "from_file"))

    def case_a_real_environment_variable_still_chooses_the_module(self):
        self.keep_env("DJANGO_SETTINGS_MODULE", "ADS_TEST_WEB")
        os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.dev"  # 부하·장애 시험처럼 실제 환경변수로 지정
        load_env_for_web(Path(self.dotenv_dir()) / ".env")
        self.assertEqual(os.environ["DJANGO_SETTINGS_MODULE"], "config.settings.dev")

    def case_wsgi_and_asgi_start_with_prod_even_if_the_dotenv_says_dev(self):
        """실제 진입점 모듈을 새 프로세스에서 불러온다. `.env` 위치만 임시 폴더로 바꾼다(개발자의 `.env` 와 무관하게)."""
        directory = self.dotenv_dir()
        for module in ("config.wsgi", "config.asgi"):
            code = f"import config.env as e, pathlib; e.BASE_DIR = pathlib.Path({directory!r}); import {module}; from django.conf import settings; print(settings.SETTINGS_MODULE)"
            r = run_python(code, None, PROD_ENV)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "config.settings.prod"), (module, r.stderr[-400:]))
            r = run_python(code, "config.settings.dev", DB_ENV)  # 실제 환경변수로 지정하면 그 값
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "config.settings.dev"), (module, r.stderr[-400:]))

    def test_web_entry_settings(self):
        self.run_cases()
