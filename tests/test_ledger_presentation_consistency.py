"""Synthetic-only regressions for distinct historical ledger and source totals."""
from test_analysis import add, client_for, conn, service


def _historical_mismatch(conn):
    add(conn, '2026-08-01', disp=400, daily=100, nim=50)
    # The historical Excel 'total' cell is independent of its component cells.
    conn.execute("UPDATE daily_profit SET total=900 WHERE user_id=1 AND date='2026-08-01'")
    conn.execute('''INSERT INTO monthly_summary
        (user_id,year,month,dispensing_plus_daily_total,non_insurance_total,grand_total,prev_month_diff)
        VALUES (1,2026,8,800,100,1000,1000)''')
    conn.commit()


def test_reconciliation_discloses_both_independent_total_differences(service, conn):
    _historical_mismatch(conn)
    response = client_for(service).get('/reconciliation')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Component total is 550, independently imported daily total is 900,
    # and independently imported monthly ledger is 1000.
    assert '월장부-구성항목 차이' in html
    assert '월장부-일별 total 차이' in html
    assert '일별 total-구성항목 차이' in html
    assert '+450' in html and '+100' in html and '+350' in html
    assert conn.execute('SELECT grand_total FROM monthly_summary WHERE user_id=1 AND month=8').fetchone()[0] == 1000
    assert conn.execute('SELECT total FROM daily_profit WHERE user_id=1 AND date=\'2026-08-01\'').fetchone()[0] == 900


def test_annual_composition_uses_daily_components_plus_extras_not_ledger(service, conn):
    _historical_mismatch(conn)
    conn.execute("INSERT INTO extra_profit (user_id, date, amount, memo) VALUES (1, '2026-08-03', 50, 'synthetic')")
    conn.commit()
    service.invalidate_user_cache(1)
    html = client_for(service).get('/report?year=2026').get_data(as_text=True)
    # Composition denominator = 400+100+50+50 = 600, while ledger grand = 1050.
    assert '조제료: <strong>66.7%' in html
    assert '일매순익: <strong>16.7%' in html
    assert '비보험마진: <strong>8.3%' in html
    assert '잡이익: <strong>8.3%' in html
    assert '일별 기록+잡이익 기준 합계' in html
    assert '월장부 전체순익' in html
    assert '1,050원' in html and '600원' in html
    assert conn.execute('SELECT grand_total FROM monthly_summary WHERE user_id=1 AND month=8').fetchone()[0] == 1000
