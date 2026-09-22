import datetime as dt

import profit_display
from test_analysis import add, client_for, conn, service


def test_warm_dashboard_opens_no_database_connections(service, conn, monkeypatch):
    add(conn, '2026-09-18')
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    client = client_for(service)
    assert client.get('/').status_code == 200
    def unexpected_connection():
        raise AssertionError('Warm dashboard reopened the database')
    monkeypatch.setattr(service, 'get_db', unexpected_connection)
    monkeypatch.setattr(profit_display, 'get_db', unexpected_connection)
    assert client.get('/').status_code == 200


def test_context_refreshes_after_save_and_does_not_cache_flash(service, conn):
    add(conn, '2026-09-18')
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    client = client_for(service)
    client.get('/')
    response = client.post('/input', data=dict(date='2026-09-18', dispensing_fee='123456',
                          daily_net_profit='20', non_insurance_margin='10', memo='updated'))
    assert response.status_code == 302
    html = client.get('/').get_data(as_text=True)
    assert '123,456원' in html
    assert '순익이 저장되었습니다' in html
    assert '순익이 저장되었습니다' not in client.get('/').get_data(as_text=True)


def test_context_expires_on_korean_date_change_and_ttl(service, conn, monkeypatch):
    add(conn, '2026-09-18')
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    client = client_for(service)
    client.get('/')
    calls = []
    original = service.load_analysis_rows
    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'load_analysis_rows', tracked)
    monkeypatch.setattr(service, 'korea_today', lambda: dt.date(2026, 9, 20))
    client.get('/')
    assert len(calls) == 1
    service._USER_CACHE[1]['dashboard_context']['ts'] -= service._CACHE_TTL + 1
    client.get('/')
    assert len(calls) == 2


def test_dashboard_context_is_account_scoped(service, conn):
    add(conn, '2026-09-18', disp=765432)
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    assert '765,432원' in client_for(service, user=1).get('/').get_data(as_text=True)
    assert '765,432원' not in client_for(service, user=2).get('/').get_data(as_text=True)
