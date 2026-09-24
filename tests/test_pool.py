"""Exercise psycopg2-style connections that forbid arbitrary attributes."""
import pytest
import database

if database.psycopg2 is None:
    pytest.skip("psycopg2 is not installed (PostgreSQL-only tests)", allow_module_level=True)

class Connection:
    __slots__ = ('closed', 'pings', 'bad', 'cursor_closed', '__weakref__')
    def __init__(self, bad=False):
        self.closed = False
        self.pings = 0
        self.bad = bad
        self.cursor_closed = False
    def cursor(self):
        connection = self
        class Cursor:
            def execute(self, sql):
                connection.pings += 1
                if connection.bad:
                    raise RuntimeError('disconnected')
            def close(self):
                connection.cursor_closed = True
        return Cursor()
    def rollback(self):
        if self.bad:
            raise RuntimeError('disconnected')
    def close(self):
        self.closed = True

class Pool:
    def __init__(self, conn):
        self.conn = conn
        self.checked_out = 0
        self.discarded = 0
    def getconn(self):
        self.checked_out += 1
        return self.conn
    def putconn(self, conn, close=False):
        self.checked_out -= 1
        if close:
            self.discarded += 1
            conn.close()
            self.conn = Connection()

@pytest.fixture
def pool(monkeypatch):
    pool = Pool(Connection())
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test.invalid/test')
    monkeypatch.setattr(database, 'get_pg_pool', lambda: pool)
    def unexpected_connect(*a, **kw):
        pytest.fail('Healthy pooled connections must not reconnect')
    monkeypatch.setattr(database.psycopg2, 'connect', unexpected_connect)
    return pool

def test_reuses_realistic_connection_without_leaking(pool):
    for _ in range(20):
        conn = database.get_db()
        conn.close()
        conn.close()
    assert pool.checked_out == 0
    assert pool.discarded == 0
    assert pool.conn.pings == 1
    assert pool.conn.cursor_closed

def test_replaces_stale_connection_once(pool):
    pool.conn.bad = True
    conn = database.get_db()
    conn.close()
    assert pool.discarded == 1
    assert pool.checked_out == 0

def test_rollback_failure_discards_connection(pool):
    conn = database.get_db()
    pool.conn.bad = True
    conn.close()
    assert pool.discarded == 1
    assert pool.checked_out == 0


def test_idle_connection_is_checked_again(pool, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(database.time, 'monotonic', lambda: clock[0])
    database.get_db().close()
    clock[0] += 61
    database.get_db().close()
    assert pool.conn.pings == 2
    assert pool.checked_out == 0


def test_exhausted_pool_fallback_is_closed(monkeypatch):
    class ExhaustedPool:
        def getconn(self):
            raise database.psycopg2.pool.PoolError('full')
    direct = Connection()
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test.invalid/test')
    monkeypatch.setattr(database, 'get_pg_pool', lambda: ExhaustedPool())
    def connect(*args, **kwargs):
        assert kwargs['connect_timeout'] == 10
        return direct
    monkeypatch.setattr(database.psycopg2, 'connect', connect)
    wrapper = database.get_db()
    wrapper.close()
    assert direct.closed


def test_execute_reconnection_returns_failed_pooled_connection(monkeypatch):
    class RecoveringConnection(Connection):
        def cursor(self):
            if self.bad:
                raise database.psycopg2.OperationalError('connection lost')
            class Cursor:
                def execute(self, sql, params=()):
                    return None
            return Cursor()

    class LimitedPool:
        def __init__(self):
            self.available = [RecoveringConnection(bad=True)]
            self.in_use = set()

        def getconn(self):
            if not self.available:
                raise database.psycopg2.pool.PoolError('pool exhausted')
            conn = self.available.pop()
            self.in_use.add(conn)
            return conn

        def putconn(self, conn, close=False):
            self.in_use.remove(conn)
            if close:
                conn.close()
                self.available.append(RecoveringConnection())
            else:
                self.available.append(conn)

    pool = LimitedPool()
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test.invalid/test')
    monkeypatch.setattr(database, 'get_pg_pool', lambda: pool)
    monkeypatch.setattr(database.psycopg2, 'connect', lambda *a, **kw: pytest.fail('unpooled connection opened'))

    wrapper = database.PostgresConnectionWrapper(pool.getconn(), from_pool=True)
    wrapper.execute('SELECT 1')
    wrapper.close()
    assert not pool.in_use
    assert len(pool.available) == 1
