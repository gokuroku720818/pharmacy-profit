"""The input page must show the same last 20 records regardless of navigation."""
import re

from test_analysis import add, client_for, conn, service


def _recent_dates(response):
    assert response.status_code == 200
    return re.findall(r'<td class="ps-3 fw-bold">(?:<a[^>]+>)?(20\d\d-\d\d-\d\d)', response.get_data(as_text=True))


def test_recent_date_link_loads_existing_values_and_memo_into_form(service, conn):
    add(conn, '2026-09-18', disp=123456, daily=23456, nim=3456, user=1)
    conn.execute("UPDATE daily_profit SET memo = ? WHERE user_id = ? AND date = ?",
                 ('직접 확인한 메모', 1, '2026-09-18'))
    conn.commit()
    service.invalidate_user_cache(1)
    client = client_for(service, user=1)

    history_html = client.get('/input').get_data(as_text=True)
    assert 'href="/input?date=2026-09-18"' in history_html

    edit_html = client.get('/input?date=2026-09-18').get_data(as_text=True)
    assert 'value="2026-09-18"' in edit_html
    assert 'value="123456"' in edit_html
    assert 'value="23456"' in edit_html
    assert 'value="3456"' in edit_html
    assert 'value="직접 확인한 메모"' in edit_html


def test_input_date_cannot_load_another_users_record(service, conn):
    conn.execute('INSERT INTO users (id, username, password_hash, pharmacy_name) VALUES (?, ?, ?, ?)',
                 (2, 'private-history-user', 'test-only', 'Other pharmacy'))
    conn.commit()
    add(conn, '2026-09-17', disp=987654, daily=87654, nim=7654, user=2)
    client = client_for(service, user=1)

    html = client.get('/input?date=2026-09-17').get_data(as_text=True)
    assert 'value="987654"' not in html


def test_dashboard_preload_preserves_last_twenty_across_month_boundary(service, conn):
    conn.execute('INSERT INTO users (id, username, password_hash, pharmacy_name) VALUES (?, ?, ?, ?)',
                 (2, 'test-history-other-user', 'test-only', 'Other test pharmacy'))
    conn.commit()
    for day in range(13, 32):
        add(conn, f'2026-08-{day:02d}', user=1)
    for day in (1, 2):
        add(conn, f'2026-09-{day:02d}', user=1)
    add(conn, '2026-09-03', user=2)
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    service.invalidate_user_cache(1)
    client = client_for(service, user=1)

    cold_dates = _recent_dates(client.get('/input'))
    expected = ['2026-09-02', '2026-09-01'] + [f'2026-08-{day:02d}' for day in range(31, 13, -1)]
    assert cold_dates == expected
    service.invalidate_user_cache(1)
    assert client.get('/').status_code == 200
    warm_dates = _recent_dates(client.get('/input'))
    assert warm_dates == expected, 'Dashboard preload must not truncate last 20 at a month boundary'
    assert '2026-09-03' not in warm_dates, 'No other tenant records may appear'


def test_preloaded_history_is_recent_even_when_latest_summary_is_old(service, conn):
    add(conn, '2024-01-01', user=1)
    add(conn, '2025-01-01', user=1)
    service.recalc_monthly_summary(conn, 1, 2024, 1)
    service.invalidate_user_cache(1)
    client = client_for(service)
    assert client.get('/').status_code == 200
    assert _recent_dates(client.get('/input')) == ['2025-01-01', '2024-01-01']
