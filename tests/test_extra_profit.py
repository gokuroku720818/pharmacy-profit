"""Disposable-data contract for occasional miscellaneous profit."""
import csv
import io

from test_analysis import add, client_for, conn, service


def _token(client):
    page = client.get('/extra-profit?year=2026&month=9')
    assert page.status_code == 200
    with client.session_transaction() as sess:
        return sess['extra_profit_csrf']


def test_misc_adds_to_grand_without_changing_the_three_base_components(service, conn):
    add(conn, '2026-09-01', disp=100, daily=200, nim=300)
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    client = client_for(service)
    token = _token(client)
    response = client.post('/extra-profit', data={'csrf_token': token, 'date':'2026-09-01', 'amount':'700', 'memo':'잡이익'}, follow_redirects=True)
    assert response.status_code == 200
    assert '700' in response.get_data(as_text=True)
    assert conn.execute('SELECT grand_total FROM monthly_summary WHERE user_id=1 AND year=2026 AND month=9').fetchone()['grand_total'] == 600
    result = client.get('/')
    assert result.status_code == 200
    html = result.get_data(as_text=True)
    assert '1,300' in html and '잡이익' in html
    from profit_components import attach_components, load_monthly_components
    row = next(r for r in service.get_cached_monthly_summary(None,1) if r['month']==9)
    split = attach_components([row], load_monthly_components(conn, 1))[0]
    assert split['breakdown_available']
    assert [split[key] for key in ('dispensing_fee','daily_net_profit','non_insurance_margin','extra_profit_total','grand_total')] == [100,200,300,700,1300]
    report = client.get('/report?year=2026').get_data(as_text=True)
    assert '1,300' in report and '잡이익' in report
    exported = list(csv.reader(io.StringIO(client.get('/export/2026').data.decode('utf-8-sig'))))
    assert '잡이익' in exported[1]
    row_csv = next(r for r in exported[2:] if r[0:2]==['2026','9'])
    assert '700' in row_csv and '1300' in row_csv


def test_summary_only_month_and_misc_only_month_preserve_base_ledger(service, conn):
    conn.execute('INSERT INTO monthly_summary (user_id,year,month,dispensing_plus_daily_total,non_insurance_total,grand_total,prev_month_diff) VALUES (1,2026,8,900,100,1000,1000)')
    conn.commit()
    client = client_for(service)
    token = _token(client)
    for month, amount in [(8, 200), (10, 400)]:
        assert client.post('/extra-profit', data={'csrf_token':token,'date':f'2026-{month:02d}-10','amount':str(amount),'memo':'기타'}).status_code == 302
    summaries = {(r['year'],r['month']):r for r in service.get_cached_monthly_summary(None, 1)}
    assert summaries[(2026,8)]['grand_total']==1200
    assert summaries[(2026,10)]['grand_total']==400
    assert summaries[(2026,8)]['dispensing_plus_daily_total']==900
    assert conn.execute('SELECT grand_total FROM monthly_summary WHERE user_id=1 AND year=2026 AND month=8').fetchone()['grand_total']==1000
    assert '세부자료 확인 필요' in client.get('/report?year=2026').get_data(as_text=True)


def test_update_delete_reject_foreign_user_and_invalidate_cache(service,conn):
    client = client_for(service)
    token = _token(client)
    client.post('/extra-profit', data={'csrf_token':token,'date':'2026-09-02','amount':'100','memo':'기타'})
    record = conn.execute('SELECT id FROM extra_profit WHERE user_id=1').fetchone()['id']
    assert client.get('/extra-profit?year=2026&month=9').status_code == 200
    assert client.post('/extra-profit', data={'csrf_token':token,'id':str(record),'date':'2026-09-02','amount':'230','memo':'정산차액'}).status_code == 302
    assert service.get_cached_monthly_summary(None,1)[0]['grand_total']==230
    foreign=client_for(service,user=2)
    other_token=_token(foreign)
    assert foreign.post('/extra-profit', data={'csrf_token':other_token,'id':str(record),'date':'2026-09-02','amount':'999','memo':'탈취'}).status_code==404
    assert foreign.post(f'/extra-profit/{record}/delete',data={'csrf_token':other_token}).status_code==404
    assert conn.execute('SELECT amount FROM extra_profit WHERE id=?',(record,)).fetchone()['amount']==230
    assert client.post(f'/extra-profit/{record}/delete',data={'csrf_token':token}).status_code==302
    assert service.get_cached_monthly_summary(None,1)==[]


def test_misc_requires_login_csrf_and_valid_date_amount_and_memo(service,conn):
    anon=service.app.test_client()
    assert anon.get('/extra-profit').status_code==302
    assert anon.post('/extra-profit',data={'date':'2026-09-01','amount':'100','memo':'x'}).status_code==302
    client=client_for(service)
    token=_token(client)
    for item in ({'csrf_token':'wrong','date':'2026-09-01','amount':'100','memo':'x'},
                 {'csrf_token':token,'date':'2026-02-30','amount':'100','memo':'x'},
                 {'csrf_token':token,'date':'2026-09-01','amount':'0','memo':'x'},
                 {'csrf_token':token,'date':'2026-09-01','amount':'abc','memo':'x'},
                 {'csrf_token':token,'date':'2026-09-01','amount':'100','memo':'   '}):
        assert client.post('/extra-profit',data=item).status_code==400
    assert conn.execute('SELECT COUNT(*) AS n FROM extra_profit').fetchone()['n']==0
    # 음수 금액 정상 등록 검증
    res_neg = client.post('/extra-profit', data={'csrf_token':token,'date':'2026-09-01','amount':'-500','memo':'음수잡이익'})
    assert res_neg.status_code == 302
    assert conn.execute('SELECT amount FROM extra_profit WHERE user_id=1').fetchone()['amount'] == -500
