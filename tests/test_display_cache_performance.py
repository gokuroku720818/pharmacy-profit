"""Display aggregation performance and invalidation with synthetic records only."""

import profit_display
from test_analysis import add, client_for, conn, service


def test_repeated_dashboard_does_not_reload_display_aggregations(service, conn, monkeypatch):
    add(conn, '2026-09-01', disp=100, daily=200, nim=300)
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    client = client_for(service)
    statements = []
    original_get_db = profit_display.get_db

    def tracked_get_db():
        connection = original_get_db()
        connection.set_trace_callback(lambda sql: statements.append(sql) if sql.lstrip().upper().startswith('SELECT') else None)
        return connection

    monkeypatch.setattr(profit_display, 'get_db', tracked_get_db)
    first = client.get('/')
    assert first.status_code == 200
    assert '600' in first.get_data(as_text=True)
    # The dashboard's cached monthly summary already includes miscellaneous totals.
    assert not any('FROM extra_profit' in sql for sql in statements), statements
    assert sum('FROM daily_profit' in sql for sql in statements) == 1, statements

    statements.clear()
    second = client.get('/')
    assert second.status_code == 200
    assert statements == [], statements


def test_display_aggregations_refresh_after_invalidation(service, conn, monkeypatch):
    add(conn, '2026-09-01', disp=100, daily=200, nim=300)
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    client = client_for(service)
    queries = []
    original_get_db = profit_display.get_db

    def tracked_get_db():
        connection = original_get_db()
        connection.set_trace_callback(lambda sql: queries.append(sql) if sql.lstrip().upper().startswith('SELECT') else None)
        return connection

    monkeypatch.setattr(profit_display, 'get_db', tracked_get_db)
    assert '100원' in client.get('/').get_data(as_text=True)
    queries.clear()
    conn.execute("UPDATE daily_profit SET dispensing_fee=150, dispensing_plus_daily=350, total=650 WHERE user_id=1 AND date='2026-09-01'")
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    service.invalidate_user_cache(1)
    html = client.get('/').get_data(as_text=True)
    assert '150원' in html
    assert '650원' in html
    assert sum('FROM daily_profit' in sql for sql in queries) == 1, queries


def test_analysis_snapshot_omits_large_input_memos(service, conn):
    add(conn, '2026-09-01')
    conn.execute("UPDATE daily_profit SET memo = ? WHERE date = '2026-09-01'", ('x' * 100_000,))
    conn.commit()

    rows = service.load_analysis_rows(conn, 1, 2026, 9)
    assert len(rows) == 1
    assert rows[0]['total'] == 130
    assert rows[0]['dispensing_fee'] == 100
    assert 'memo' not in rows[0]
