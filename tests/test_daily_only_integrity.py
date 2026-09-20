"""Daily-only display and write integrity on disposable fixture data."""
import datetime as dt
import io

import openpyxl

from test_analysis import add, client_for, conn, service
from test_retire_monthly_ledger import _install
from test_postgres_integration import postgres_pool


def test_report_and_reconciliation_no_longer_describe_a_separate_month_ledger(service, conn, monkeypatch):
    monkeypatch.setattr(service, 'render_template', service.render_template)
    _install(service, monkeypatch)
    from daily_labels import install
    monkeypatch.setattr(service, '_daily_labels_installed', False, raising=False)
    monkeypatch.setitem(service.app.view_functions, 'historical_reconciliation',
                        service.app.view_functions['historical_reconciliation'])
    install(service)
    add(conn, '2026-08-01', disp=400, daily=100, nim=50)
    conn.execute("UPDATE daily_profit SET total=900 WHERE user_id=1 AND date='2026-08-01'")
    conn.execute("INSERT INTO extra_profit (user_id,date,amount,memo) VALUES (1,'2026-08-02',50,'test')")
    conn.commit()
    service.invalidate_user_cache(1)
    client = client_for(service)
    report = client.get('/report?year=2026').get_data(as_text=True)
    assert '월장부' not in report
    assert '서로 다른 원본의 차액' not in report
    assert '일별 total-세 항목 차이' in report
    assert '950원' in report
    reconciliation = client.get('/reconciliation').get_data(as_text=True)
    assert '이전 월장부' not in reconciliation
    assert '일별 기록으로 자동 생성' in reconciliation


def test_invalid_workbook_rolls_back_all_daily_rows(service, conn, monkeypatch):
    client = _install(service, monkeypatch)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(['날짜','요일','조제료','일매순익','비보험','메모'])
    sheet.append(['2026-09-01','화',100,20,10,'valid'])
    sheet.append(['2026-09-02','수','not-money',20,10,'invalid'])
    payload = io.BytesIO()
    book.save(payload)
    payload.seek(0)
    response = client.post('/upload_excel', data={'excel_file': (payload, 'daily.xlsx')},
                           content_type='multipart/form-data', follow_redirects=True)
    assert response.status_code == 200
    assert '가져오기 실패' in response.get_data(as_text=True)
    assert conn.execute('SELECT COUNT(*) FROM daily_profit WHERE user_id=1').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM monthly_summary WHERE user_id=1').fetchone()[0] == 0


def test_real_postgres_daily_only_upsert_preserves_existing_memo(postgres_pool):
    import database
    from daily_only import _daily_upsert
    connection = database.get_db()
    try:
        connection.execute('''CREATE TEMP TABLE daily_profit (
            user_id INTEGER NOT NULL, date TEXT NOT NULL, day_of_week TEXT,
            dispensing_fee BIGINT, daily_net_profit BIGINT, dispensing_plus_daily BIGINT,
            non_insurance_margin BIGINT, total BIGINT, updated_at TIMESTAMP,
            memo TEXT, UNIQUE(user_id,date)) ON COMMIT DROP''')
        day = dt.date(2026, 8, 1)
        _daily_upsert(connection, 1, day, 400, 100, 50, 550, memo='existing')
        _daily_upsert(connection, 1, day, 500, 100, 50, 650, memo=None)
        actual = connection.execute('SELECT total,memo FROM daily_profit WHERE user_id=? AND date=?',
                                    (1,day.isoformat())).fetchone()
        assert int(actual['total']) == 650
        assert actual['memo'] == 'existing'
    finally:
        connection.rollback()
        connection.close()
