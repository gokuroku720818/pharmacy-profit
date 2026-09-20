"""Integration assertions use a disposable test database, never a real ledger."""
import csv
import io

from test_analysis import service, conn, add, client_for


def test_verified_three_components_appear_in_dashboard_calendar_report_and_csv(service, conn):
    add(conn, '2026-09-01', disp=12345, daily=6789, nim=4321)
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    client = client_for(service)

    for path in ('/', '/calendar?year=2026&month=9', '/report?year=2026'):
        response = client.get(path)
        assert response.status_code == 200, path
        page = response.get_data(as_text=True)
        for value in ('12,345', '6,789', '4,321', '23,455'):
            assert value in page, (path, value)
        for heading in ('조제료', '일매순익', '비보험마진'):
            assert heading in page, (path, heading)

    trend = client.get('/trend')
    assert trend.status_code == 200
    trend_html = trend.get_data(as_text=True)
    assert 'monthlyComponentChart' in trend_html
    for value in ('12345', '6789', '4321'):
        assert value in trend_html, value

    export = client.get('/export/2026')
    assert export.status_code == 200
    parsed = list(csv.reader(io.StringIO(export.get_data().decode('utf-8-sig'))))
    assert parsed[1][:9] == ['연도', '월', '조제료', '일매순익', '비보험마진', '잡이익', '전체합계', '전월대비', '세부자료 상태']
    september = next(row for row in parsed[2:] if row[0:2] == ['2026', '9'])
    assert september[2:7] == ['12345', '6789', '4321', '0', '23455']
    assert september[8] == '확인됨'


def test_monthly_only_history_keeps_total_and_never_invents_components(service, conn):
    conn.execute('''INSERT INTO monthly_summary
        (user_id, year, month, dispensing_plus_daily_total, non_insurance_total,
         grand_total, prev_month_diff) VALUES (1, 2026, 8, 900, 100, 1000, 0)''')
    conn.commit()
    client = client_for(service)
    for path in ('/', '/calendar?year=2026&month=8', '/report?year=2026'):
        response = client.get(path)
        assert response.status_code == 200, path
        html = response.get_data(as_text=True)
        assert '세부자료 확인 필요' in html, path
        assert '1,000' in html, path

    parsed = list(csv.reader(io.StringIO(client.get('/export/2026').get_data().decode('utf-8-sig'))))
    august = next(row for row in parsed[2:] if row[0:2] == ['2026', '8'])
    assert august[2:5] == ['', '', '']
    assert august[5] == '0'
    assert august[6] == '1000'
    assert august[8] == '세부자료 확인 필요'
