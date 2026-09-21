"""Regression coverage for the production pool-exhaustion circuit breaker."""
import importlib
from types import SimpleNamespace

import pytest
import database

if database.psycopg2 is None:
    pytest.skip("psycopg2 is not installed (PostgreSQL-only tests)", allow_module_level=True)


def test_exhausted_pool_never_opens_unpooled_connection(monkeypatch):
    try:
        guard = importlib.import_module('pool_guard')
    except ModuleNotFoundError:
        pytest.fail('Missing pool exhaustion guard: exhausted pools currently create unbounded direct connections')

    class Exhausted:
        def getconn(self):
            raise database.psycopg2.pool.PoolError('connection pool exhausted')

    handlers = {}
    fake_app = SimpleNamespace(register_error_handler=lambda exc, fn: handlers.__setitem__(exc, fn))
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test.invalid/test')
    monkeypatch.setattr(database, 'get_pg_pool', lambda: Exhausted())
    monkeypatch.setattr(database, '_pool_guard_installed', False, raising=False)
    def never_direct(*args, **kwargs):
        pytest.fail('A full pool must not open an unbounded direct PostgreSQL connection')
    monkeypatch.setattr(database.psycopg2, 'connect', never_direct)

    guard.install(database, fake_app)
    with pytest.raises(guard.PoolExhausted):
        database.get_db()
    assert guard.PoolExhausted in handlers
    body, status, headers = handlers[guard.PoolExhausted](guard.PoolExhausted())
    assert status == 503
    assert headers['Retry-After'] == '1'
    assert 'password' not in body.lower()


def test_guard_preserves_pool_checkout_and_release(monkeypatch):
    try:
        guard = importlib.import_module('pool_guard')
    except ModuleNotFoundError:
        pytest.fail('Missing pool guard implementation')

    class Healthy:
        def __init__(self):
            self.conn = object()
            self.released = []
        def getconn(self):
            return self.conn
        def putconn(self, conn, close=False):
            self.released.append((conn, close))

    pool = Healthy()
    db = SimpleNamespace(get_pg_pool=lambda: pool, psycopg2=database.psycopg2)
    fake_app = SimpleNamespace(register_error_handler=lambda *args: None)
    guard.install(db, fake_app)
    first, second = db.get_pg_pool(), db.get_pg_pool()
    assert first is second
    assert first.getconn() is pool.conn
    first.putconn(pool.conn, close=True)
    assert pool.released == [(pool.conn, True)]
    guard.install(db, fake_app)
    assert db.get_pg_pool() is first
