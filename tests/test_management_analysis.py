"""경영 판단 계산은 합성 기록만 사용하며 DB나 원본 장부를 수정하지 않는다."""
import datetime as dt
import importlib
import csv
import io

import pytest

from test_analysis import add, client_for, conn, service


def row(day, amount, *, total=None):
    return {'date': day, 'dispensing_fee': amount, 'daily_net_profit': 0,
            'non_insurance_margin': 0, 'total': amount if total is None else total}


def review(rows, extras=None, opens=None, today=dt.date(2026, 10, 6), month=9):
    build = importlib.import_module('management_analysis').build_management_review
    return build(rows, extras or {}, 2026, month,
                 {'weekdays': [], 'closed_dates': [], 'open_dates': opens or []}, as_of=today)


def test_zero_operating_day_is_counted_and_day_effects_reconcile():
    rows = [row('2026-08-03', 100), row('2026-08-04', 100),
            row('2026-09-01', 150), row('2026-09-07', 0), row('2026-09-08', 150)]
    result = review(rows, {'2026-08-03': 50, '2026-09-01': 200},
                    [r['date'] for r in rows])
    assert result['current']['entered_business_days'] == 3
    assert result['current']['daily_total'] == 300
    assert result['current']['total'] == 500
    assert result['total_diff'] == 250
    assert [item['diff'] for item in result['components']] == [100, 0, 0, 150]
    assert result['day_effects']['days_effect'] == 100
    assert result['day_effects']['average_effect'] == 0


def test_incomplete_month_uses_same_calendar_cutoff_and_excludes_future_gains():
    rows = [row('2026-08-01', 50), row('2026-08-06', 50), row('2026-08-07', 999),
            row('2026-09-01', 100), row('2026-09-06', 200), row('2026-09-07', 999)]
    extras = {'2026-08-01': 7, '2026-08-07': 999, '2026-09-01': 10, '2026-09-07': 999}
    result = review(rows, extras, [r['date'] for r in rows], today=dt.date(2026, 9, 6))
    assert result['previous']['end'] == '2026-08-06'
    assert result['current']['total'] == 310
    assert result['previous']['total'] == 107
    assert result['total_diff'] == 203
    assert result['future_entry_count'] == 1
    assert result['can_set_goal'] is True
    assert result['remaining_business_days'] == 1


@pytest.mark.parametrize('missing_day', ['2026-08-04', '2026-09-08'])
def test_missing_day_in_either_period_suppresses_day_effects(missing_day):
    opens = ['2026-08-03', '2026-08-04', '2026-09-01', '2026-09-08']
    rows = [row(day, 100) for day in opens if day != missing_day]
    result = review(rows, opens=opens)
    assert result['day_effects'] is None
    period = result['previous'] if missing_day.startswith('2026-08') else result['current']
    assert period['missing_dates'] == [missing_day]


def test_today_without_input_is_pending_and_not_a_past_gap():
    result = review([row('2026-09-01', 100)], opens=['2026-09-01', '2026-09-06', '2026-09-07'],
                    today=dt.date(2026, 9, 6))
    assert result['current']['missing_dates'] == []
    assert result['current']['awaiting_today'] is True
    assert result['remaining_business_days'] == 2
    assert result['target_ready'] is True


def test_off_schedule_entry_disables_operating_day_interpretation():
    result = review([row('2026-08-03', 100), row('2026-09-01', 100)],
                    opens=['2026-08-03'])
    assert result['current']['outside_schedule_count'] == 1
    assert result['current']['daily_total'] == 100
    assert result['day_effects'] is None


def test_total_cell_discrepancy_is_disclosed_and_never_added_to_miscellaneous():
    result = review([row('2026-08-03', 100), row('2026-09-01', 100, total=109)],
                    {'2026-09-01': 20}, ['2026-08-03', '2026-09-01'])
    assert result['current']['daily_total'] == 109
    assert result['current']['extra_total'] == 20
    assert result['discrepancy_diff'] == 9
    assert result['total_diff'] == 29
    assert result['day_effects'] is None


def test_completed_february_compares_full_january_and_rounding_preserves_difference():
    rows = [row('2026-01-01', 33), row('2026-01-02', 33), row('2026-01-31', 34),
            row('2026-02-01', 50), row('2026-02-02', 51)]
    result = review(rows, opens=[r['date'] for r in rows], today=dt.date(2026, 3, 1), month=2)
    assert result['previous']['end'] == '2026-01-31'
    assert result['day_effects']['days_effect'] == -33
    assert result['day_effects']['average_effect'] == 34
    assert result['day_effects']['daily_total_diff'] == 1


def test_empty_previous_period_and_extras_only_month_have_no_invented_day_average():
    result = review([], {'2026-09-01': 100}, opens=['2026-09-01'])
    assert result['current']['total'] == 100
    assert result['current']['daily_total'] == 0
    assert result['has_comparison'] is False
    assert result['day_effects'] is None


def test_dashboard_shows_same_period_miscellaneous_and_reuses_existing_queries(service, conn, monkeypatch):
    add(conn, '2026-08-01', disp=100, daily=0, nim=0)
    add(conn, '2026-09-01', disp=200, daily=0, nim=0)
    conn.execute("INSERT INTO extra_profit (user_id,date,amount,memo) VALUES (1,'2026-08-01',30,'test')")
    conn.execute("INSERT INTO extra_profit (user_id,date,amount,memo) VALUES (1,'2026-09-01',50,'test')")
    conn.execute("INSERT INTO extra_profit (user_id,date,amount,memo) VALUES (1,'2026-09-25',9999,'future')")
    conn.commit()
    service.recalc_monthly_summary(conn, 1, 2026, 8)
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    client = client_for(service)
    statements = []
    original = service.get_db

    def tracked_db():
        connection = original()
        connection.set_trace_callback(lambda sql: statements.append(sql)
                                      if sql.lstrip().upper().startswith('SELECT') else None)
        return connection

    monkeypatch.setattr(service, 'get_db', tracked_db)
    assert client.get('/').status_code == 200
    statements.clear()
    html = client.get('/').get_data(as_text=True)
    assert 'id="managementReview"' in html
    assert 'data-current-total="250"' in html
    assert 'data-current-base="200"' in html
    assert 'data-period-diff="120"' in html
    assert len(statements) == 1, statements  # validate fresh daily source only
    assert 'FROM daily_profit' in statements[0]
    # Existing invalidation must also refresh the new, read-only figures.
    conn.execute("UPDATE daily_profit SET dispensing_fee=300,dispensing_plus_daily=300,total=300 WHERE user_id=1 AND date='2026-09-01'")
    conn.commit()
    service.invalidate_user_cache(1)
    html = client.get('/').get_data(as_text=True)
    assert 'data-current-total="350"' in html


def test_served_daily_csv_adds_source_day_count_without_changing_original_columns(service, conn, monkeypatch):
    from test_retire_monthly_ledger import _install
    client = _install(service, monkeypatch)
    add(conn, '2026-09-01', disp=100, daily=0, nim=0)
    add(conn, '2026-09-02', disp=0, daily=0, nim=0)
    conn.execute("INSERT INTO extra_profit (user_id,date,amount,memo) VALUES (1,'2026-09-01',20,'test')")
    conn.commit()
    parsed = list(csv.reader(io.StringIO(client.get('/export/2026').data.decode('utf-8-sig'))))
    headers, data = parsed[1], parsed[2]
    assert data[:7] == ['2026', '9', '100', '0', '0', '20', '120']
    assert data[headers.index('입력일수')] == '2'
    assert data[headers.index('잡이익 제외 합계')] == '100'
    assert '일별 total' in data[headers.index('집계 기준')]


def test_old_selected_month_does_not_load_every_miscellaneous_date_in_between(service, conn):
    for day, amount in [('2022-07-01', 7), ('2024-12-01', 999), ('2026-09-01', 11)]:
        conn.execute('INSERT INTO extra_profit (user_id,date,amount,memo) VALUES (1,?,?,?)',
                     (day, amount, 'synthetic'))
    conn.commit()
    result = service.get_recent_weeks_profit_stats(conn, 1, rows=[],
                     extra_range=(dt.date(2022, 7, 1), dt.date(2022, 8, 31)))
    assert result['extra_by_date'] == {'2022-07-01': 7, '2026-09-01': 11}
    assert result['weeks'][1]['extra_sum'] == 11


def test_future_selected_month_never_displays_an_inverted_date_range(service, conn):
    add(conn, '2026-10-01', disp=999, daily=0, nim=0)
    service.recalc_monthly_summary(conn, 1, 2026, 10)
    html = client_for(service).get('/').get_data(as_text=True)
    assert '선택한 달은 아직 시작되지 않았습니다' in html
    assert '2026-10-01~2026-09-19' not in html
    assert 'data-current-total="0"' in html


def test_shared_miscellaneous_query_keeps_date_range_index_search(service, conn):
    statements = []
    conn.set_trace_callback(lambda sql: statements.append(sql)
                           if sql.lstrip().upper().startswith('SELECT') else None)
    service.get_recent_weeks_profit_stats(conn, 1, rows=[],
                       extra_range=(dt.date(2022, 7, 1), dt.date(2022, 8, 31)))
    plan = conn.execute('EXPLAIN QUERY PLAN ' + statements[0]).fetchall()
    searches = [r[3] for r in plan if 'SEARCH extra_profit' in r[3]]
    assert searches
    assert all('date>?' in detail and 'date<?' in detail for detail in searches), searches
