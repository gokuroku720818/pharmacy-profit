"""Opt-in integration coverage against a disposable CI PostgreSQL service.

Only uses SELECT and rolled-back temporary DDL. Never point CI_DATABASE_URL at
production; the workflow supplies a dedicated throwaway PostgreSQL instance.
"""
import concurrent.futures
import os
import threading

import pytest

import database


@pytest.fixture
def postgres_pool(monkeypatch):
    url = os.environ.get('CI_DATABASE_URL')
    if not url:
        pytest.skip('CI_DATABASE_URL is required for real PostgreSQL integration tests')
    assert database.psycopg2 is not None
    previous_pool = database._pg_pool
    assert previous_pool is None, 'Integration database must have an isolated pool'
    monkeypatch.setenv('DATABASE_URL', url)
    try:
        yield database.get_pg_pool()
    finally:
        pool = database._pg_pool
        if pool is not None:
            pool.closeall()
        # Assign directly: using monkeypatch.setattr in fixture teardown would be
        # reversed *after* this fixture, restoring the just-closed pool.
        database._pg_pool = previous_pool
        with database._pg_usage_lock:
            database._pg_last_used.clear()


def test_real_postgres_returns_and_reuses_same_backend(postgres_pool):
    assert postgres_pool is not None
    one = database.get_db()
    try:
        first = one.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
    finally:
        one.close()
    two = database.get_db()
    try:
        second = two.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
    finally:
        two.close()
    assert first == second, 'Repeated requests should reuse a pooled backend'


def test_real_postgres_rollback_prevents_transaction_leak(postgres_pool):
    one = database.get_db()
    try:
        one.execute('CREATE TEMP TABLE perf_ci_uncommitted (id integer)')
    finally:
        one.close()  # Must roll back before putting the connection back.
    two = database.get_db()
    try:
        row = two.execute("SELECT to_regclass('pg_temp.perf_ci_uncommitted') AS relation").fetchone()
        assert row['relation'] is None
        assert two.execute('SELECT 1 AS value').fetchone()['value'] == 1
    finally:
        two.close()


def test_real_postgres_read_checkout_returns_transactional_connection(postgres_pool):
    read = database.get_db()
    try:
        read.use_autocommit_reads()
        assert read.execute('SELECT 1 AS value').fetchone()['value'] == 1
    finally:
        read.close()
    write = database.get_db()
    try:
        assert write.conn.autocommit is False
        write.execute('CREATE TEMP TABLE perf_ci_read_reset (id integer)')
    finally:
        write.close()
    check = database.get_db()
    try:
        assert check.execute("SELECT to_regclass('pg_temp.perf_ci_read_reset') AS relation").fetchone()['relation'] is None
    finally:
        check.close()


def test_real_postgres_four_concurrent_requests_release_connections(postgres_pool):
    barrier = threading.Barrier(4)

    def request(_):
        connection = database.get_db()
        try:
            barrier.wait(timeout=10)
            return connection.execute('SELECT 1 AS value').fetchone()['value']
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(request, range(4))) == [1, 1, 1, 1]
    assert len(postgres_pool._used) == 0


def test_real_postgres_batch_import_preserves_order_and_transaction(postgres_pool):
    import datetime as dt
    from daily_only import DailyImportBatch, _daily_upsert
    connection = database.get_db()
    try:
        connection.execute('''CREATE TEMP TABLE daily_profit (
            user_id INTEGER NOT NULL, date TEXT NOT NULL, day_of_week TEXT,
            dispensing_fee BIGINT, daily_net_profit BIGINT, dispensing_plus_daily BIGINT,
            non_insurance_margin BIGINT, total BIGINT, updated_at TIMESTAMP,
            memo TEXT, UNIQUE(user_id,date)) ON COMMIT DROP''')
        batch = DailyImportBatch(connection)
        for i in range(450):
            day = dt.date(2024, 1, 1) + dt.timedelta(days=i)
            _daily_upsert(batch, 1, day, 100, 20, 10, 130, memo='saved memo')
        # Changing statement shape flushes pending rows; later legacy rows
        # preserve the memo and update amounts for the same date.
        _daily_upsert(batch, 1, dt.date(2024, 1, 1), 200, 20, 10, 230)
        batch.flush()
        row = connection.execute('SELECT COUNT(*) AS n, SUM(total) AS total FROM daily_profit').fetchone()
        assert row['n'] == 450 and row['total'] == 58600
        first = connection.execute("SELECT memo,total FROM daily_profit WHERE date='2024-01-01'").fetchone()
        assert first['memo'] == 'saved memo' and first['total'] == 230
        connection.rollback()
        assert connection.execute("SELECT to_regclass('pg_temp.daily_profit') AS relation").fetchone()['relation'] is None
    finally:
        connection.rollback()
        connection.close()
