"""Synthetic-only regression: show provenance, never rewrite historical ledgers."""
from profit_components import attach_components, total_components
from test_analysis import add, client_for, conn, service


def test_source_mode_displays_actual_daily_fields_and_preserves_ledger_total():
    old_month = [{'year': 2026, 'month': 8, 'dispensing_plus_daily_total': 800,
                  'non_insurance_total': 100, 'grand_total': 1000,
                  'extra_profit_total': 0}]
    daily = {(2026, 8): {'dispensing_fee': 400, 'daily_net_profit': 100,
                         'non_insurance_margin': 50, 'grand_total': 550}}
    [strict] = attach_components(old_month, daily)
    assert strict['breakdown_available'] is False  # CSV retains strict semantics
    [visible] = attach_components(old_month, daily, present_source=True)
    assert visible['breakdown_available'] is True
    assert visible['breakdown_verified'] is False
    assert (visible['dispensing_fee'], visible['daily_net_profit'], visible['non_insurance_margin']) == (400, 100, 50)
    assert visible['breakdown_difference'] == 450
    assert visible['grand_total'] == 1000
    assert total_components([visible])['dispensing_fee'] == 400
    [missing] = attach_components(old_month, {}, present_source=True)
    assert missing['breakdown_available'] is False and missing['dispensing_fee'] is None


def test_report_shows_historical_source_numbers_with_warning_not_fabricated_total(service, conn):
    add(conn, '2026-08-01', disp=400, daily=100, nim=50)
    conn.execute('''INSERT INTO monthly_summary
        (user_id,year,month,dispensing_plus_daily_total,non_insurance_total,grand_total,prev_month_diff)
        VALUES (1,2026,8,800,100,1000,1000)''')
    conn.commit()
    service.invalidate_user_cache(1)
    html = client_for(service).get('/report?year=2026').get_data(as_text=True)
    for value in ('400원', '100원', '50원', '1,000원', '일별 기록 기준', '월장부'):
        assert value in html
    assert conn.execute('SELECT grand_total FROM monthly_summary WHERE user_id=1 AND month=8').fetchone()['grand_total'] == 1000
    reconcile = client_for(service).get('/reconciliation').get_data(as_text=True)
    assert '월장부와 불일치' in reconcile
