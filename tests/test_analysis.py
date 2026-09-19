import datetime as dt
import importlib
import sqlite3
import sys

import pytest


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    import database
    monkeypatch.setattr(database, 'SQLITE_PATH', str(tmp_path / 'sales.db'))
    if 'app' not in sys.modules:
        module = importlib.import_module('app')
    else:
        module = sys.modules['app']
        database.init_db()
    import profit_analysis
    monkeypatch.setattr(profit_analysis, 'korea_today', lambda: dt.date(2026, 9, 19))
    monkeypatch.setattr(module, 'korea_today', lambda: dt.date(2026, 9, 19))
    module._USER_CACHE.clear()
    module.app.config.update(TESTING=True)
    return module


@pytest.fixture
def conn(service):
    connection = service.get_db()
    yield connection
    connection.close()


def add(conn, day, disp=100, daily=20, nim=10, user=1):
    date = dt.date.fromisoformat(day)
    conn.execute('''INSERT INTO daily_profit
        (user_id,date,day_of_week,dispensing_fee,daily_net_profit,
        dispensing_plus_daily,non_insurance_margin,total)
        VALUES (?,?,?,?,?,?,?,?)''',
        (user, day, '월화수목금토일'[date.weekday()], disp, daily, disp+daily, nim, disp+daily+nim))
    conn.commit()


def test_past_gaps_are_not_remaining_business_days(service, conn):
    add(conn, '2026-09-01')
    result = service.get_month_forecast(conn, 1, 2026, 9, as_of=dt.date(2026, 9, 29))
    assert result['remaining_business_days'] == 2
    assert result['missing_count'] == 23
    assert result['yoy_growth_pct'] is None


def test_only_recent_eight_weeks_and_zero_days_enter_average(service, conn):
    add(conn, '2026-06-01', 100000, 0, 0)  # Monday, outside window
    add(conn, '2026-08-24', 100, 0, 0)
    add(conn, '2026-08-31', 0, 0, 0)
    result = service.get_month_forecast(conn, 1, 2026, 9, as_of=dt.date(2026, 9, 28))
    assert result['weekday_samples']['월']['mean'] == 50
    assert result['weekday_samples']['월']['count'] == 2
    assert result['weekday_samples']['월']['low'] == 0


def test_schedule_includes_sunday_and_excludes_explicit_holiday(service, conn):
    schedule = {'weekdays': [0,1,2,3,4,5,6], 'closed_dates': ['2026-09-28'], 'open_dates': []}
    result = service.get_month_forecast(conn, 1, 2026, 9, as_of=dt.date(2026, 9, 27), schedule=schedule)
    assert result['remaining_business_days'] == 3  # Sun, Tue, Wed
    assert result['forecast_total'] is None  # no observations: not a zero forecast


def test_past_month_is_never_extrapolated(service, conn):
    add(conn, '2026-08-03')
    result = service.get_month_forecast(conn, 1, 2026, 8, as_of=dt.date(2026, 9, 19))
    assert result['remaining_business_days'] == 0
    assert result['expected_additional'] == 0
    assert result['forecast_total'] == 130
    assert result['is_past'] is True


def test_forecast_excludes_future_entries_and_other_users(service, conn):
    add(conn, '2026-09-28', 999999, 0, 0)
    result = service.get_month_forecast(conn, 1, 2026, 9, as_of=dt.date(2026, 9, 19))
    assert result['current_total'] == 0
    assert result['future_entry_count'] == 1
    assert result['forecast_total'] is None


def test_monthly_only_balance_does_not_invent_breakdown(service, conn):
    summary = {'grand_total': 1000, 'dispensing_plus_daily_total': 900, 'non_insurance_total': 100}
    result = service.get_profit_balance_diagnosis(conn, 1, 2026, 9, entered_rows=[], month_summary_row=summary)
    assert result['has_breakdown'] is False
    assert result['disp_val'] is None
    assert result['daily_val'] is None
    assert result['total'] == 1000


def test_briefing_reports_decline_and_day_adjusted_change(service, conn):
    add(conn, '2026-08-01', 100, 0, 0)
    add(conn, '2026-08-02', 100, 0, 0)
    add(conn, '2026-09-01', 80, 0, 0)
    add(conn, '2026-09-02', 70, 0, 0)
    result = service.get_ai_narrative_briefing(conn, 1, {}, None)
    comparison = result['month_comparison']
    assert comparison['diff'] == -50
    assert comparison['daily_avg_diff'] == -25
    assert comparison['components'][0]['diff'] == -50
    assert '감소' in ' '.join(result['paragraphs'])
    assert '방어선' not in ' '.join(result['paragraphs'])


def test_unequal_input_days_are_explicit_not_called_business_days(service, conn):
    add(conn, '2026-08-01', 100, 0, 0)
    add(conn, '2026-08-02', 100, 0, 0)
    add(conn, '2026-09-02', 100, 0, 0)
    result = service.get_ai_narrative_briefing(conn, 1, {}, None)['month_comparison']
    assert result['current_days'] == 1
    assert result['previous_days'] == 2
    assert result['daily_avg_diff'] == 0
    assert result['diff'] == -100


def test_year_comparison_missing_does_not_claim_zero_growth(service, conn):
    add(conn, '2026-09-01')
    result = service.get_yoy_day_comparison(conn, 1, '2026-09-01')
    assert result['growth_pct'] is None
    assert result['yoy_total'] is None


def test_annual_briefing_does_not_claim_stability_from_decline(service):
    rows = [{'month': 1, 'grand_total': 100}]
    result = service.generate_annual_narrative_report(2026, rows,
        {'grand':100,'dpd':90,'nim':10}, {'grand':1000}, rows[0], rows[0], [])
    text = ' '.join(result)
    assert '감소' in text
    assert '방어선' not in text
    assert '비교 기간' in text


def client_for(service, user=1):
    client = service.app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=user, username='tester', pharmacy_name='Test', schedule_csrf='test-token')
    return client


def test_schedule_persists_and_is_user_scoped(service, conn):
    client = client_for(service)
    response = client.post('/business-schedule', data={
        'csrf_token':'test-token','weekdays':['0','6'], 'closed_dates':'2026-09-28',
        'open_dates':'2026-09-29', 'year':'2026','month':'9'})
    assert response.status_code == 302
    from profit_analysis import get_business_schedule
    assert get_business_schedule(conn, 1)['weekdays'] == [0,6]
    assert get_business_schedule(conn, 2)['weekdays'] == [0,1,2,3,4,5]
    result = service.get_month_forecast(conn, 1, 2026, 9, as_of=dt.date(2026,9,27))
    assert result['remaining_business_days'] == 2


def test_invalid_schedule_is_rejected_without_overwrite(service, conn):
    client = client_for(service)
    for data in [
        {'weekdays':['8']}, {'closed_dates':'2026-02-30'},
        {'closed_dates':'2026-09-28','open_dates':'2026-09-28'},
    ]:
        response = client.post('/business-schedule', data={'csrf_token':'test-token',**data})
        assert response.status_code == 400
    assert conn.execute('SELECT COUNT(*) FROM business_schedules').fetchone()[0] == 0
    assert client.post('/business-schedule', data={'weekdays':['0']}).status_code == 400


def test_pages_render_no_data_and_summary_only(service, conn):
    client = client_for(service)
    for url in ['/', '/calendar?year=2026&month=9', '/report', '/calculator']:
        response = client.get(url)
        assert response.status_code == 200
    conn.execute('INSERT INTO monthly_summary (user_id,year,month,grand_total,dispensing_plus_daily_total,non_insurance_total) VALUES (1,2026,9,1000,900,100)')
    conn.commit()
    service._USER_CACHE.clear()
    response = client.get('/')
    assert response.status_code == 200
    assert '세부 자료 없음' in response.get_data(as_text=True)


def test_pages_render_actuals_and_missing_last_year(service, conn):
    add(conn,'2026-09-01')
    service.recalc_monthly_summary(conn,1,2026,9)
    client = client_for(service)
    for url in ['/', '/calendar?year=2026&month=9', '/input', '/report']:
        response = client.get(url)
        assert response.status_code == 200


def test_complete_month_forecast_has_hand_checked_range(service, conn):
    for day, amount in [('2026-09-07', 100), ('2026-09-14', 200), ('2026-09-21', 300)]:
        add(conn, day, amount, 0, 0)
    schedule = {'weekdays':[0], 'closed_dates':[], 'open_dates':[]}
    result = service.get_month_forecast(conn,1,2026,9,as_of=dt.date(2026,9,28),schedule=schedule,last_year_total=400)
    assert result['missing_count'] == 0
    assert result['forecast_total'] == 800
    assert result['forecast_low'] == 700
    assert result['forecast_high'] == 900
    assert result['yoy_growth_pct'] == 100.0


def test_tenant_history_does_not_leak_into_forecast(service, conn):
    conn.execute("INSERT INTO users (id,username,password_hash,pharmacy_name) VALUES (2,'other','unused','Other')")
    add(conn,'2026-09-14',999999,0,0,user=2)
    result = service.get_month_forecast(conn,1,2026,9,as_of=dt.date(2026,9,28))
    assert result['sample_count'] == 0
    assert result['forecast_total'] is None


def test_month_summary_only_is_not_displayed_as_zero(service, conn):
    conn.execute('INSERT INTO monthly_summary (user_id,year,month,grand_total) VALUES (1,2026,8,700)')
    conn.commit()
    result = service.get_month_forecast(conn,1,2026,8,as_of=dt.date(2026,9,19))
    assert result['current_total'] == 700
    assert result['summary_only'] is True
    assert result['forecast_total'] == 700


def test_summary_only_does_not_claim_daily_amounts_are_excluded(service, conn):
    conn.execute('INSERT INTO monthly_summary (user_id,year,month,grand_total) VALUES (1,2026,8,700)')
    conn.commit()
    result = service.get_month_forecast(conn,1,2026,8,as_of=dt.date(2026,9,19),last_year_total=500)
    assert result['missing_count'] == 0
    assert result['yoy_growth_pct'] is None  # completeness is unknown
    client = client_for(service)
    text = client.get('/calendar?year=2026&month=8').get_data(as_text=True)
    assert '합계와 예측에 보충하지 않았습니다' not in text
    assert '일별 세부 자료 없음' in text
