"""Synthetic-only contract: daily rows, not imported monthly totals, own the monthly ledger."""

from test_analysis import add, client_for, conn, service


def test_monthly_ledger_uses_daily_total_and_extras_not_imported_monthly(service, conn):
    from daily_monthly_ledger import load_monthly_ledger

    # Legacy summary-only month must not materialize a month without daily data.
    conn.execute('''INSERT INTO monthly_summary
        (user_id, year, month, dispensing_plus_daily_total, non_insurance_total, grand_total, prev_month_diff)
        VALUES (1, 2026, 7, 1, 2, 999999, 0)''')
    add(conn, '2026-08-01', disp=400, daily=100, nim=50)
    add(conn, '2026-08-02', disp=100, daily=20, nim=30)
    # Independently imported daily total takes precedence over three components.
    conn.execute("UPDATE daily_profit SET total=900 WHERE user_id=1 AND date='2026-08-01'")
    conn.execute('''INSERT INTO monthly_summary
        (user_id, year, month, dispensing_plus_daily_total, non_insurance_total, grand_total, prev_month_diff)
        VALUES (1, 2026, 8, 777, 888, 999999, 0)''')
    conn.execute("INSERT INTO extra_profit (user_id,date,amount,memo) VALUES (1,'2026-08-03',50,'synthetic')")
    conn.commit()

    rows = load_monthly_ledger(conn, 1)
    assert [(r['year'], r['month']) for r in rows] == [(2026, 8)]
    august = rows[0]
    assert august['dispensing_plus_daily_total'] == 620
    assert august['non_insurance_total'] == 80
    assert august['extra_profit_total'] == 50
    assert august['grand_total'] == 1100  # 900 + 150 + 50
    assert august['daily_total_difference'] == 350  # daily total minus components
    # The original imported ledger and all daily records remain intact.
    assert conn.execute('SELECT grand_total FROM monthly_summary WHERE user_id=1 AND month=8').fetchone()[0] == 999999
    assert conn.execute("SELECT total FROM daily_profit WHERE user_id=1 AND date='2026-08-01'").fetchone()[0] == 900


def test_daily_monthly_ledger_is_user_scoped_and_recomputes_previous_calendar_month(service, conn):
    from daily_monthly_ledger import load_monthly_ledger

    conn.execute("INSERT INTO users (username,password_hash,pharmacy_name) VALUES ('second','test-hash','other')")
    conn.commit()
    add(conn, '2026-08-31', disp=80, daily=20, nim=0)
    add(conn, '2026-09-01', disp=100, daily=20, nim=10)
    add(conn, '2026-09-01', disp=99999, daily=0, nim=0, user=2)
    conn.execute("INSERT INTO extra_profit (user_id,date,amount,memo) VALUES (1,'2026-09-03',7,'synthetic')")
    conn.commit()
    mine = load_monthly_ledger(conn, 1)
    assert [r['grand_total'] for r in mine] == [100, 137]
    assert mine[1]['prev_month_diff'] == 37
    assert load_monthly_ledger(conn, 2)[0]['grand_total'] == 99999


def test_dashboard_report_trend_and_export_use_one_daily_monthly_source(service, conn, monkeypatch):
    from daily_monthly_ledger import install
    import profit_display

    add(conn, '2026-08-01', disp=400, daily=100, nim=50)
    conn.execute("UPDATE daily_profit SET total=900 WHERE user_id=1 AND date='2026-08-01'")
    conn.execute('''INSERT INTO monthly_summary
        (user_id, year, month, dispensing_plus_daily_total, non_insurance_total, grand_total, prev_month_diff)
        VALUES (1, 2026, 8, 800, 100, 999999, 0)''')
    conn.execute("INSERT INTO extra_profit (user_id,date,amount,memo) VALUES (1,'2026-08-03',50,'synthetic')")
    conn.commit()
    # Install mutates the runtime entry points. Restore all of them afterwards
    # so earlier legacy-compatibility tests retain their own expected behavior.
    monkeypatch.setattr(service, 'get_cached_monthly_summary', service.get_cached_monthly_summary)
    monkeypatch.setattr(service, '_daily_monthly_ledger_installed', False, raising=False)
    monkeypatch.setitem(service.app.view_functions, 'export_csv', service.app.view_functions['export_csv'])
    monkeypatch.setitem(service.app.view_functions, 'historical_reconciliation',
                        service.app.view_functions['historical_reconciliation'])
    monkeypatch.setattr(profit_display, 'flash', profit_display.flash)
    monkeypatch.setattr(profit_display, '_daily_warning_installed', False, raising=False)
    install(service)
    client = client_for(service)
    assert service.get_cached_monthly_summary(None, 1)[0]['grand_total'] == 950
    for path in ('/', '/report?year=2026', '/trend', '/calendar?year=2026&month=8'):
        response = client.get(path)
        assert response.status_code == 200, path
        assert '999,999' not in response.get_data(as_text=True), path
    exported = client.get('/export/2026')
    assert exported.status_code == 200
    csv_text = exported.data.decode('utf-8-sig')
    assert '950' in csv_text and '999999' not in csv_text
    assert '400' in csv_text and '100' in csv_text and '50' in csv_text
    assert '일별 합계 차이' in csv_text
    reconciliation = client.get('/reconciliation')
    assert reconciliation.status_code == 200
    html = reconciliation.get_data(as_text=True)
    assert '공식 월합계' in html and '950' in html and '999,999' not in html
