from asgiref.sync import async_to_sync
from django.db import connections
from django.test.runner import DiscoverRunner


class PostgreSQLTestRunner(DiscoverRunner):
    def teardown_databases(self, old_config, **kwargs):
        # The socket worker intentionally retains its own connection between calls.
        from campus import socket_db
        async_to_sync(socket_db.run)(connections.close_all)
        super().teardown_databases(old_config, **kwargs)
