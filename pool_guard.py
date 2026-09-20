"""Fail closed on an exhausted PostgreSQL connection pool.

The existing database.get_db() catches psycopg2.pool.PoolError and opens a
fresh, unpooled connection. Under load that defeats maxconn and can exhaust
PostgreSQL. Install this adapter once in Gunicorn after importing the app and
before accepting requests; no schema, calculations, or credentials are changed.
"""

from functools import wraps
import threading


class PoolExhausted(RuntimeError):
    """No pooled connection available; callers should retry the request."""


class GuardedPool:
    """Translate only pool-capacity failures; keep the original pool API."""

    def __init__(self, pool, pool_error):
        self._pool = pool
        self._pool_error = pool_error

    def getconn(self, *args, **kwargs):
        try:
            return self._pool.getconn(*args, **kwargs)
        except self._pool_error as exc:
            # database.get_db() intentionally catches PoolError and falls back
            # to direct connections. A distinct exception prevents that path.
            raise PoolExhausted('PostgreSQL connection pool temporarily unavailable') from exc

    def putconn(self, *args, **kwargs):
        return self._pool.putconn(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._pool, name)


def install(database_module, flask_app):
    """Install once per worker, preserving pool identity and close semantics."""
    if getattr(database_module, '_pool_guard_installed', False):
        return

    original_get_pool = database_module.get_pg_pool
    lock = threading.Lock()
    original_pool = None
    guarded_pool = None

    @wraps(original_get_pool)
    def guarded_get_pool():
        nonlocal original_pool, guarded_pool
        pool = original_get_pool()
        if pool is None:
            return None
        if pool is original_pool:
            return guarded_pool
        with lock:
            if pool is not original_pool:
                guarded_pool = GuardedPool(pool, database_module.psycopg2.pool.PoolError)
                original_pool = pool
            return guarded_pool

    def temporarily_unavailable(_error):
        return (
            '서비스가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요.',
            503,
            {'Retry-After': '1', 'Cache-Control': 'no-store'},
        )

    flask_app.register_error_handler(PoolExhausted, temporarily_unavailable)
    database_module.get_pg_pool = guarded_get_pool
    database_module._pool_guard_installed = True
