from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
import threading

from asgiref.sync import async_to_sync
from django.db import connection, connections, transaction
from django.test import SimpleTestCase, TransactionTestCase

from campus import socket_db
from config.connections import keep_thread_connection


class ConnectionPolicyTests(SimpleTestCase):
    def test_pool_keeps_zero_max_age(self):
        database = SimpleNamespace(settings_dict={"CONN_MAX_AGE": 0, "OPTIONS": {"pool": {"max_size": 10}}})
        with patch("config.connections.connections", {"default": database}):
            keep_thread_connection()
        self.assertEqual(database.settings_dict["CONN_MAX_AGE"], 0)

    def test_nonpooled_socket_keeps_existing_reuse_policy(self):
        database = SimpleNamespace(settings_dict={"CONN_MAX_AGE": 0, "OPTIONS": {}})
        with patch("config.connections.connections", {"default": database}):
            keep_thread_connection()
        self.assertEqual(database.settings_dict["CONN_MAX_AGE"], 60)
        self.assertTrue(database.settings_dict["CONN_HEALTH_CHECKS"])


@skipUnless(connection.vendor == "postgresql" and connection.settings_dict["OPTIONS"].get("pool"),
            "Requires PostgreSQL with the configured connection pool")
class PostgreSQLConnectionPoolTests(TransactionTestCase):
    def test_pool_replaces_a_terminated_idle_connection(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            old_pid = cursor.fetchone()[0]
        connection.close()
        with connection.Database.connect(**connection.get_connection_params(), autocommit=True) as admin:
            admin.execute("SELECT pg_terminate_backend(%s)", (old_pid,))
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            self.assertNotEqual(cursor.fetchone()[0], old_pid)

    def test_twenty_threads_share_at_most_ten_connections(self):
        barrier = threading.Barrier(20, timeout=10)
        connection.close()

        def read(_):
            barrier.wait()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid(), pg_sleep(0.05)")
                    return cursor.fetchone()[0]
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=20) as executor:
            pids = list(executor.map(read, range(20)))
        self.assertEqual(len(pids), 20)
        self.assertLessEqual(len(set(pids)), 10)

    def test_socket_returns_connection_after_success_and_rollback(self):
        connection.close()
        wrappers = []

        def read():
            wrappers.append(connections["default"])
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone()[0]

        self.assertEqual(async_to_sync(socket_db.run)(read), 1)
        self.assertIsNone(wrappers[-1].connection)

        def fail():
            with transaction.atomic():
                read()
                raise ValueError("rollback probe")

        with self.assertRaisesMessage(ValueError, "rollback probe"):
            async_to_sync(socket_db.run)(fail)
        self.assertIsNone(wrappers[-1].connection)
        self.assertEqual(async_to_sync(socket_db.run)(read), 1)
