"""Process-local HTTP connections; each lease belongs to one request."""
import atexit
import http.client
import select
from contextlib import contextmanager
from threading import Condition, Lock
from time import monotonic

class BackendPool:
    def __init__(self, scheme, host, port, timeout, capacity, idle_timeout):
        self.factory = http.client.HTTPSConnection if scheme == 'https' else http.client.HTTPConnection
        self.host, self.port, self.timeout = host, port, timeout
        self.capacity, self.idle_timeout = capacity, idle_timeout
        self.condition = Condition()
        self.idle = []
        self.leased = 0
        self.closed = False

    @staticmethod
    def discard(connection):
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    @contextmanager
    def lease(self):
        deadline = monotonic() + self.timeout
        with self.condition:
            while not self.closed and not self.idle and self.leased >= self.capacity:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError('HTTP connection pool wait expired')
                self.condition.wait(remaining)
            if self.closed:
                raise OSError('HTTP connection pool is closed')
            connection, returned_at = self.idle.pop() if self.idle else (None, 0)
            self.leased += 1
        try:
            if connection is not None:
                # Readable idle sockets may contain FIN or unexpected bytes.
                if (monotonic() - returned_at >= self.idle_timeout
                        or connection.sock is None
                        or select.select([connection.sock], [], [], 0)[0]):
                    self.discard(connection)
                    connection = None
            if connection is None:
                connection = self.factory(self.host, self.port, timeout=self.timeout)
            yield connection
        except BaseException:
            self.discard(connection)
            connection = None
            raise
        finally:
            if connection is not None and connection.sock is None:
                self.discard(connection)
                connection = None
            with self.condition:
                self.leased -= 1
                keep = not self.closed
                if keep and connection is not None:
                    self.idle.append((connection, monotonic()))
                self.condition.notify()
            if not keep:
                self.discard(connection)

    def close(self):
        with self.condition:
            self.closed = True
            idle, self.idle = self.idle, []
            self.condition.notify_all()
        for connection, _ in idle:
            self.discard(connection)

_lock = Lock()
_pool = None
_key = None

def backend_pool(destination, timeout, capacity, idle_timeout):
    global _pool, _key
    key = (destination.scheme, destination.hostname, destination.port, timeout, capacity, idle_timeout)
    with _lock:
        if _key != key or _pool is None:
            if _pool is not None:
                _pool.close()
            _pool = BackendPool(*key)
            _key = key
        return _pool

def close_backend_pool():
    global _pool, _key
    with _lock:
        if _pool is not None:
            _pool.close()
        _pool = _key = None

atexit.register(close_backend_pool)
