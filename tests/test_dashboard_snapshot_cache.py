"""Synthetic requests prove reuse, freshness and tenant separation."""
import datetime as dt

import pytest

from test_analysis import add, client_for, conn, service


def seed(service, conn):
    add(conn, '2026-09-01', disp=100, daily=0, nim=0)
    service.recalc_monthly_summary(conn, 1, 2026, 9)


def test_warm_dashboard_only_queries_daily_source(service, conn, monkeypatch):
    seed(service, conn)
    client = client_for(service)
    assert client.get('/').status_code == 200
    statements = []
    original = service.get_db
    def tracked():
        connection = original()
        connection.set_trace_callback(lambda sql: statements.append(sql)
            if sql.lstrip().upper().startswith('SELECT') else None)
        return connection
    monkeypatch.setattr(service, 'get_db', tracked)
    response = client.get('/')
    assert response.status_code == 200
    assert 'data-current-base="100"' in response.get_data(as_text=True)
    assert len(statements) == 1 and 'FROM daily_profit' in statements[0]


def test_next_korean_day_refreshes_future_entry_and_cutoff(service, conn, monkeypatch):
    import profit_analysis
    seed(service, conn)
    add(conn, '2026-09-20', disp=900, daily=0, nim=0)
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    client = client_for(service)
    assert 'data-current-base="100"' in client.get('/').get_data(as_text=True)
    monkeypatch.setattr(service, 'korea_today', lambda: dt.date(2026, 9, 20))
    monkeypatch.setattr(profit_analysis, 'korea_today', lambda: dt.date(2026, 9, 20))
    assert 'data-current-base="1000"' in client.get('/').get_data(as_text=True)


def test_snapshot_expires_after_one_minute(service, conn, monkeypatch):
    seed(service, conn)
    client = client_for(service)
    assert client.get('/').status_code == 200
    calls = []
    original = service.get_recent_weeks_profit_stats
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'get_recent_weeks_profit_stats', counted)
    clock = service.time.monotonic()
    monkeypatch.setattr(service.time, 'monotonic', lambda: clock + 61)
    assert client.get('/').status_code == 200
    assert calls == [1]


def test_save_refreshes_cached_dashboard_immediately(service, conn):
    seed(service, conn)
    client = client_for(service)
    assert 'data-current-base="100"' in client.get('/').get_data(as_text=True)
    response = client.post('/input', data={'date': '2026-09-01', 'dispensing_fee': '700',
        'daily_net_profit': '0', 'non_insurance_margin': '0', 'memo': 'synthetic'})
    assert response.status_code == 302
    assert 'data-current-base="700"' in client.get('/').get_data(as_text=True)


def test_snapshots_are_separate_for_each_user(service, conn):
    seed(service, conn)
    conn.execute("INSERT INTO users (id,username,password_hash,pharmacy_name) VALUES (2,'other','unused','Other')")
    conn.commit()
    add(conn, '2026-09-01', disp=900, daily=0, nim=0, user=2)
    service.recalc_monthly_summary(conn, 2, 2026, 9)
    first, second = client_for(service, user=1), client_for(service, user=2)
    for _ in range(2):
        assert 'data-current-base="100"' in first.get('/').get_data(as_text=True)
        assert 'data-current-base="900"' in second.get('/').get_data(as_text=True)


def test_failed_source_read_does_not_publish_snapshot(service, conn, monkeypatch):
    seed(service, conn)
    original = service.get_recent_weeks_profit_stats
    def broken(*args, **kwargs):
        raise RuntimeError('synthetic database interruption')
    monkeypatch.setattr(service, 'get_recent_weeks_profit_stats', broken)
    client = client_for(service)
    with pytest.raises(RuntimeError):
        client.get('/')
    monkeypatch.setattr(service, 'get_recent_weeks_profit_stats', original)
    assert client.get('/').status_code == 200
