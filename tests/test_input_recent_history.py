"""The input page must show the same last 20 records regardless of navigation."""
import re

from test_analysis import add, client_for, conn, service


def _recent_dates(response):
    assert response.status_code == 200
    return re.findall(r'<td class="ps-3 fw-bold">(20\d\d-\d\d-\d\d)</td>', response.get_data(as_text=True))


def test_dashboard_preload_preserves_last_twenty_across_month_boundary(service, conn):
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
