import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError, IntegrityError, InternalError, OperationalError, connection, transaction
from django.test import AsyncClient, Client, RequestFactory, SimpleTestCase, TestCase
from django.views import defaults
import psycopg.errors
from psycopg import IsolationLevel

from .dberrors import is_transient, sqlstate
from .network import local_lan_addresses


def driver_error(django_class, driver_class, message):
    """Django 가 드라이버 오류를 감싼 모양 그대로(원인은 __cause__)."""
    error = django_class(message)
    error.__cause__ = driver_class(message)
    return error


def read_only():
    return driver_error(InternalError, psycopg.errors.ReadOnlySqlTransaction,
                        "cannot execute UPDATE in a read-only transaction")


def lock_timeout():
    return driver_error(OperationalError, psycopg.errors.LockNotAvailable, "canceling statement due to lock timeout")


class LanAddressTests(SimpleTestCase):
    @patch("config.network.socket.gethostbyname_ex")
    def test_only_this_pc_private_addresses_become_allowed_hosts(self, resolve):
        resolve.return_value = ("campus-pc", [], [
            "192.168.0.77", "192.168.0.77", "172.25.176.1", "10.2.3.4",
            "127.0.0.1", "0.0.0.0", "169.254.1.2", "8.8.8.8", "::1", "*", "invalid",
        ])
        self.assertEqual(local_lan_addresses(), ["10.2.3.4", "172.25.176.1", "192.168.0.77"])

    @patch("config.network.socket.gethostbyname_ex", side_effect=OSError("unavailable"))
    def test_lookup_failure_keeps_loopback_server_available(self, resolve):
        self.assertEqual(local_lan_addresses(), [])


class PostgresTlsSettingsTests(SimpleTestCase):
    def options(self, **env):
        """DATABASES OPTIONS from a fresh run of the settings module under `env` (the loaded settings stay as they are)."""
        spec = importlib.util.spec_from_file_location("config._settings_probe", Path(__file__).with_name("settings.py"))
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "config"
        with patch.dict(os.environ, {"DATABASE_ENGINE": "postgresql", "POSTGRES_POOL_MAX_SIZE": "10", **env}):
            spec.loader.exec_module(module)
        return module.DATABASES["default"]["OPTIONS"]

    def test_tls_values_reach_libpq_only_when_set(self):
        options = self.options(POSTGRES_SSLMODE=" verify-full ", POSTGRES_SSLROOTCERT="/etc/cluster-ca.pem")
        self.assertEqual((options["sslmode"], options["sslrootcert"]), ("verify-full", "/etc/cluster-ca.pem"))
        # Explicit, not the server default: same as the ads server even if the cluster default changes (06 §0).
        self.assertEqual(options["isolation_level"], IsolationLevel.READ_COMMITTED)
        self.assertEqual(options["options"], "-c lock_timeout=5s -c idle_in_transaction_session_timeout=60s")
        self.assertEqual(options["pool"], {"min_size": 1, "max_size": 10})  # session settings go with the pool
        options = self.options(POSTGRES_SSLMODE="", POSTGRES_SSLROOTCERT="  ")
        self.assertFalse({"sslmode", "sslrootcert"} & set(options))


class PostgresSessionSettingsTests(TestCase):
    def test_cluster_session_settings_match_the_ads_server(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL session settings")
        values = {}
        with connection.cursor() as cursor:
            for name in ("lock_timeout", "idle_in_transaction_session_timeout", "transaction_isolation"):
                cursor.execute(f"SHOW {name}")
                values[name] = cursor.fetchone()[0]
        self.assertEqual(values, {"lock_timeout": "5s", "idle_in_transaction_session_timeout": "1min",
                                  "transaction_isolation": "read committed"})


class TransientDatabaseErrorTests(TestCase):
    async def test_read_only_error_inside_middleware_becomes_503_with_retry_after(self):
        # 운영처럼 ASGI 로 받는다. 미들웨어 안의 예외는 그 층에서 응답으로 바뀌므로 handler500 이 받아야 한다
        with patch("accounts.session_policy.check_session", side_effect=read_only()):
            response = await AsyncClient(raise_request_exception=False).get("/api/board/menu/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "1")
        self.assertEqual(response.json(), {"error": "database_busy", "message": "잠시 후 다시 시도해 주세요."})

    def test_lock_timeout_inside_a_view_becomes_503_with_retry_after(self):
        client = Client(raise_request_exception=False)
        client.force_login(get_user_model().objects.create_user(username="busy-view"))
        with patch("campus.views.latest_menu", side_effect=lock_timeout()):
            response = client.get("/api/board/menu/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "1")
        self.assertEqual(response.json()["error"], "database_busy")

    def test_other_errors_keep_the_existing_500(self):
        unique = driver_error(IntegrityError, psycopg.errors.UniqueViolation, "duplicate key value")
        existing = defaults.server_error(RequestFactory().get("/api/board/menu/")).content
        for error in (ValueError("bug"), unique, DatabaseError("unknown")):
            with self.subTest(error=type(error).__name__), patch("accounts.session_policy.check_session", side_effect=error):
                response = Client(raise_request_exception=False).get("/api/board/menu/")
            self.assertEqual(response.status_code, 500)
            self.assertNotIn("Retry-After", response)
            self.assertEqual(response.content, existing)

    def test_read_only_error_from_postgresql_is_transient(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL read-only transaction")
        with self.assertRaises(DatabaseError) as caught, transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
            get_user_model().objects.filter(pk=0).update(first_name="x")
        self.assertEqual(sqlstate(caught.exception), "25006")
        self.assertTrue(is_transient(caught.exception))
