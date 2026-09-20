"""Synthetic regression for historical spreadsheet summary vs daily-row mismatches."""
from test_analysis import add, client_for, conn, service


def test_reconciliation_shows_source_figures_without_rewriting_old_months(service, conn):
    # A historic monthly sheet may disagree with its independently imported daily sheet.
    add(conn, '2026-08-01', disp=400, daily=100, nim=50)
    conn.execute('''INSERT INTO monthly_summary
        (user_id,year,month,dispensing_plus_daily_total,non_insurance_total,grand_total,prev_month_diff)
        VALUES (1,2026,8,900,100,1000,1000)''')
    conn.execute('''INSERT INTO monthly_summary
        (user_id,year,month,dispensing_plus_daily_total,non_insurance_total,grand_total,prev_month_diff)
        VALUES (1,2026,7,700,50,750,750)''')
    conn.commit()
    add(conn, '2026-09-01', disp=120, daily=70, nim=30)
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    original = conn.execute('SELECT grand_total FROM monthly_summary WHERE user_id=1 AND month=8').fetchone()['grand_total']

    assert service.app.test_client().get('/reconciliation').status_code == 302
    response = client_for(service).get('/reconciliation')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    html = response.get_data(as_text=True)
    assert '2026년 8월' in html and '월장부와 불일치' in html
    for number in ('400', '100', '50', '1,000', '450'):
        assert number in html
    assert '2026년 7월' in html and '일별 기록 없음' in html
    assert '2026년 9월' in html and '월장부와 일치' in html
    assert conn.execute('SELECT grand_total FROM monthly_summary WHERE user_id=1 AND month=8').fetchone()['grand_total'] == original


def test_reconciliation_is_per_user_and_never_returns_foreign_finances(service, conn):
    conn.execute("INSERT INTO users (username, password_hash, pharmacy_name) VALUES ('other', 'testhash', 'Other')")
    other_id = conn.execute("SELECT id FROM users WHERE username='other'").fetchone()['id']
    add(conn, '2026-04-01', disp=987654321, daily=1, nim=0, user=other_id)
    service.recalc_monthly_summary(conn, other_id, 2026, 4)
    html = client_for(service).get('/reconciliation').get_data(as_text=True)
    assert '987,654,321' not in html and '2026년 4월' not in html
