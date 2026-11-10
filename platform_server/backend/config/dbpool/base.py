from django.db import OperationalError
from dj_db_conn_pool.backends.mysql.base import DatabaseWrapper as PooledDatabaseWrapper
from sqlalchemy.exc import TimeoutError as PoolTimeoutError


class DatabaseWrapper(PooledDatabaseWrapper):
    def get_new_connection(self, conn_params):
        try:
            return super().get_new_connection(conn_params)
        except PoolTimeoutError as error:
            raise OperationalError("Timed out waiting for a database connection from the pool.") from error
