"""Performance contracts for selective cache invalidation and month-range queries."""
from pathlib import Path

from test_analysis import add, client_for, conn, service


ROOT = Path(__file__).resolve().parents[1]


def _entry(value):
    return {'ts': 9999999999, 'data': value}


def test_daily_profit_invalidation_preserves_unrelated_caches(service):
    service._USER_CACHE[1] = {
        'business_schedule': _entry({'weekdays': [0, 1, 2, 3, 4, 5]}),
        'calculator_settings': _entry({'rent_cost': 1}),
        'display_extras': _entry({(2026, 9): 10}),
        'monthly_summary': _entry([{'year': 2026, 'month': 9}]),
        'dow_avg': _entry({'월': 1}),
        'display_components': _entry({(2026, 9): {}}),
        'input_cache': _entry({'recent': []}),
        'daily_2026_09': _entry([]),
        'analysis:2026-09:2026-09-19': _entry([]),
        'weekly_stats:4:2026-09-19': _entry({}),
        'dashboard_ctx:2026-09-19': _entry({}),
        'calendar_ctx:2026:9:2026-09-19': _entry({}),
    }

    service.invalidate_daily_profit_cache(1)
    keys = set(service._USER_CACHE[1])

    assert keys == {'business_schedule', 'calculator_settings', 'display_extras'}


def test_extra_profit_invalidation_preserves_daily_analysis(service):
    service._USER_CACHE[1] = {
        'business_schedule': _entry({'weekdays': [0]}),
        'calculator_settings': _entry({'rent_cost': 1}),
        'display_components': _entry({(2026, 9): {}}),
        'input_cache': _entry({'recent': []}),
        'daily_2026_09': _entry([]),
        'analysis:2026-09:2026-09-19': _entry([]),
        'dow_avg': _entry({'월': 1}),
        'monthly_summary': _entry([]),
        'display_extras': _entry({}),
        'weekly_stats:4:2026-09-19': _entry({}),
        'dashboard_ctx:2026-09-19': _entry({}),
        'calendar_ctx:2026:9:2026-09-19': _entry({}),
    }

    service.invalidate_extra_profit_cache(1)
    keys = set(service._USER_CACHE[1])

    assert 'monthly_summary' not in keys
    assert 'display_extras' not in keys
    assert not any(key.startswith('weekly_stats:') for key in keys)
    assert not any(key.startswith('dashboard_ctx:') for key in keys)
    assert not any(key.startswith('calendar_ctx:') for key in keys)
    for preserved in (
        'business_schedule', 'calculator_settings', 'display_components',
        'input_cache', 'daily_2026_09', 'analysis:2026-09:2026-09-19', 'dow_avg'
    ):
        assert preserved in keys


def test_schedule_change_reuses_warm_financial_caches_without_database(service, conn, monkeypatch):
    add(conn, '2026-09-19')
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    client = client_for(service)

    # Warm all dashboard dependencies, including display components and weekly stats.
    assert client.get('/').status_code == 200

    response = client.post('/business-schedule', data={
        'csrf_token': 'test-token',
        'weekdays': ['0', '1', '2', '3', '4', '5'],
        'closed_dates': '2026-09-21',
        'open_dates': '',
        'year': '2026',
        'month': '9',
    })
    assert response.status_code == 302
    assert service._USER_CACHE[1]['business_schedule']['data']['closed_dates'] == ['2026-09-21']

    def no_db():
        raise AssertionError('schedule-only change should not force financial DB reload')

    monkeypatch.setattr(service, 'get_db', no_db)
    import profit_display
    monkeypatch.setattr(profit_display, 'get_db', no_db)

    # The page context was invalidated, but all financial source caches remained hot.
    assert client.get('/').status_code == 200


def test_month_queries_use_index_friendly_ranges_in_source():
    for path in ('app.py', 'database.py', 'profit_analysis.py', 'extra_profit.py'):
        source = (ROOT / path).read_text(encoding='utf-8')
        assert 'date LIKE ?' not in source, path

    assert 'date >= ? AND date < ?' in (ROOT / 'database.py').read_text(encoding='utf-8')
    assert 'date >= ? AND date < ?' in (ROOT / 'extra_profit.py').read_text(encoding='utf-8')


def test_sqlite_month_range_plan_uses_user_date_index(service, conn):
    daily_plan = conn.execute(
        'EXPLAIN QUERY PLAN SELECT total FROM daily_profit '
        'WHERE user_id = ? AND date >= ? AND date < ?',
        (1, '2026-09-01', '2026-10-01')
    ).fetchall()
    daily_detail = ' '.join(str(row['detail']) for row in daily_plan)
    assert 'SEARCH daily_profit' in daily_detail
    assert 'user_id=?' in daily_detail and 'date>?' in daily_detail and 'date<?' in daily_detail

    extra_plan = conn.execute(
        'EXPLAIN QUERY PLAN SELECT amount FROM extra_profit '
        'WHERE user_id = ? AND date >= ? AND date < ?',
        (1, '2026-09-01', '2026-10-01')
    ).fetchall()
    extra_detail = ' '.join(str(row['detail']) for row in extra_plan)
    assert 'SEARCH extra_profit' in extra_detail
    assert 'user_id=?' in extra_detail and 'date>?' in extra_detail and 'date<?' in extra_detail
