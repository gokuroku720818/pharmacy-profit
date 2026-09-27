"""Regressions for observed latency and interrupted-request failures."""
import datetime as dt
import io
import types
import threading

import openpyxl
import pytest
from flask import Flask

import database
import profit_display
from test_analysis import add, client_for, conn, service
from test_retire_monthly_ledger import _install


def test_daily_summary_display_does_not_repeat_aggregate(service, conn, monkeypatch):
    add(conn, '2026-09-01', disp=100, daily=200, nim=300)
    conn.execute("UPDATE daily_profit SET total=700 WHERE user_id=1")
    conn.execute("INSERT INTO extra_profit (user_id,date,amount,memo) VALUES (1,'2026-09-02',50,'extra')")
    conn.commit()
    client = _install(service, monkeypatch)
    service.get_cached_monthly_summary(None, 1)
    def no_duplicate_connection():
        pytest.fail('display must reuse the daily ledger already loaded')
    monkeypatch.setattr(profit_display, 'get_db', no_duplicate_connection)
    html = client.get('/report?year=2026').get_data(as_text=True)
    assert '750원' in html
    assert '100원' in html and '200원' in html and '300원' in html


def test_input_comparison_refreshes_at_korean_midnight(service, conn, monkeypatch):
    add(conn, '2026-09-19', disp=100)
    add(conn, '2026-09-20', disp=200)
    today = [dt.date(2026, 9, 19)]
    monkeypatch.setattr(service, 'korea_today', lambda: today[0])
    import profit_analysis
    monkeypatch.setattr(profit_analysis, 'korea_today', lambda: today[0])
    captured = []
    monkeypatch.setattr(service, 'render_template', lambda _, **ctx: captured.append(ctx) or 'ok')
    client = client_for(service)
    assert client.get('/input').status_code == 200
    assert captured[-1]['yoy_day']['current_date'] == '2026-09-19'
    today[0] = dt.date(2026, 9, 20)
    assert client.get('/input').status_code == 200
    assert captured[-1]['yoy_day']['current_date'] == '2026-09-20'


def test_early_request_response_survives_metrics(monkeypatch):
    from runtime_metrics import install
    import profit_analysis
    monkeypatch.setattr(profit_analysis, 'get_db', profit_analysis.get_db)
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.before_request(lambda: ('please reload', 400))
    install(types.SimpleNamespace(app=app, get_db=lambda: None, render_template=lambda: ''))
    response = app.test_client().post('/input')
    assert response.status_code == 400
    assert response.get_data(as_text=True) == 'please reload'


@pytest.mark.parametrize('first_sql', ['INSERT INTO ledger VALUES (1)', 'SELECT 1'])
def test_disconnect_does_not_resume_in_middle_of_transaction(monkeypatch, first_sql):
    class Raw:
        autocommit = False
        closed = False
        def __init__(self):
            self.count = 0
        def cursor(self):
            return self
        def execute(self, sql, params=()):
            self.count += 1
            if self.count == 2:
                raise database.psycopg2.OperationalError('connection lost')
        def close(self):
            self.closed = True
    raw = Raw()
    wrapper = database.PostgresConnectionWrapper(raw)
    replacement = Raw()
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test.invalid/test')
    monkeypatch.setattr(database.psycopg2, 'connect', lambda *a, **kw: replacement)
    wrapper.execute(first_sql)
    with pytest.raises(database.psycopg2.OperationalError):
        wrapper.execute('SELECT 2')
    assert replacement.count == 0


def test_import_batches_rows_and_rolls_back_late_invalid_cell(service, conn, monkeypatch):
    client = _install(service, monkeypatch)
    counts = {'single': 0, 'batch': 0}
    original = service.get_db
    class CountConnection:
        def __init__(self):
            self.raw = original()
        def execute(self, sql, params=()):
            counts['single'] += 1
            return self.raw.execute(sql, params)
        def executemany(self, sql, rows):
            counts['batch'] += 1
            return self.raw.executemany(sql, rows)
        def __getattr__(self, name):
            return getattr(self.raw, name)
    monkeypatch.setattr(service, 'get_db', CountConnection)
    def upload(invalid=False):
        book = openpyxl.Workbook()
        book.active.append(['날짜', '요일', '조제료', '일매순익', '비보험', '메모'])
        for i in range(450):
            day = dt.date(2024, 1, 1) + dt.timedelta(days=i)
            book.active.append([day.isoformat(), '', 100, 20, 10, 'memo'])
        if invalid:
            book.active.append(['2026-01-01', '', 'bad', 20, 10, ''])
        buf = io.BytesIO()
        book.save(buf)
        book.close()
        buf.seek(0)
        return client.post('/upload_excel', data={'excel_file': (buf, 'daily.xlsx')},
                           content_type='multipart/form-data')
    assert upload(True).status_code == 302
    assert conn.execute('SELECT COUNT(*) FROM daily_profit').fetchone()[0] == 0
    counts.update(single=0, batch=0)
    assert upload().status_code == 302
    assert conn.execute('SELECT COUNT(*), SUM(total) FROM daily_profit').fetchone()[:] == (450, 58500)
    assert counts['single'] == 0
    assert 1 <= counts['batch'] <= 3


def test_calculator_setting_invalidation_waits_for_old_read(service):
    from cache_coherence import install
    install(service)
    entered, release, invalidated = (threading.Event() for _ in range(3))
    errors = []
    class Result:
        def fetchone(self):
            entered.set()
            assert release.wait(5)
            return {'rent_cost': 100}
    class Connection:
        def execute(self, sql, params):
            return Result()
    def read():
        try:
            service.get_cached_calculator_settings(Connection(), 1)
        except Exception as exc:
            errors.append(exc)
    def invalidate():
        service.invalidate_user_cache_keys(1, keys=('calculator_settings',))
        invalidated.set()
    reader = threading.Thread(target=read)
    writer = threading.Thread(target=invalidate)
    reader.start()
    try:
        assert entered.wait(5)
        writer.start()
        assert not invalidated.wait(0.1)
    finally:
        release.set()
        reader.join(5)
        if writer.ident:
            writer.join(5)
    assert not errors
    assert 'calculator_settings' not in service._USER_CACHE.get(1, {})


def test_gzip_explicitly_refused_is_not_sent(service):
    response = client_for(service).get('/', headers={'Accept-Encoding': 'gzip;q=0, identity'})
    assert response.status_code == 200
    assert 'Content-Encoding' not in response.headers
    assert 'Accept-Encoding' in response.vary


def test_cold_input_reuses_recent_row_and_hot_input_opens_no_db(service, conn, monkeypatch):
    add(conn, '2026-09-19')
    queries = []
    opens = []
    original = service.get_db
    def tracked():
        opens.append(1)
        db = original()
        db.set_trace_callback(lambda sql: queries.append(sql) if sql.lstrip().startswith('SELECT') else None)
        return db
    monkeypatch.setattr(service, 'get_db', tracked)
    client = client_for(service)
    assert client.get('/input').status_code == 200
    assert len(queries) == 2
    assert len(opens) == 1
    assert client.get('/input').status_code == 200
    assert len(opens) == 1


def test_postgres_batch_uses_three_round_trips_for_450_rows():
    class Cursor:
        def __init__(self):
            self.commands = []
            self.closed = False
        def mogrify(self, sql, row):
            assert sql == 'INSERT INTO example (value) VALUES (%s)'
            return f'INSERT INTO example (value) VALUES ({row[0]})'.encode()
        def execute(self, sql):
            self.commands.append(sql)
        def close(self):
            self.closed = True
    cursor = Cursor()
    raw = types.SimpleNamespace(cursor=lambda: cursor)
    database.PostgresConnectionWrapper(raw).executemany(
        'INSERT INTO example (value) VALUES (?)', [(i,) for i in range(450)])
    assert [len(command.split(b';')) for command in cursor.commands] == [200, 200, 50]
    assert b'VALUES (449)' in cursor.commands[-1]
    assert cursor.closed
