from unittest import TestCase
from unittest.mock import patch

from django.db import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from .dbpool.base import DatabaseWrapper, PooledDatabaseWrapper


class PoolFailureTests(TestCase):
    def test_pool_exhaustion_uses_django_error_handling(self):
        failure = PoolTimeoutError("Pool is full")
        wrapper = DatabaseWrapper({"ENGINE": "config.dbpool"})
        with patch.object(PooledDatabaseWrapper, "get_new_connection", side_effect=failure):
            with self.assertRaises(OperationalError) as caught:
                wrapper.get_new_connection({})
        self.assertIs(caught.exception.__cause__, failure)

    def test_driver_errors_are_not_retried_or_replaced(self):
        failure = OperationalError("Connection lost")
        wrapper = DatabaseWrapper({"ENGINE": "config.dbpool"})
        with patch.object(PooledDatabaseWrapper, "get_new_connection", side_effect=failure) as connect:
            with self.assertRaises(OperationalError) as caught:
                wrapper.get_new_connection({})
        self.assertIs(caught.exception, failure)
        connect.assert_called_once_with({})
