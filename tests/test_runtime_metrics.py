import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime_metrics import MeasuredConnection, Metrics, format_metrics


class FakeCursor:
    def __init__(self):
        self.closed = False
    def execute(self, sql, params=None):
        return self
    def fetchone(self):
        return {'a': 1}
    def fetchall(self):
        return [{'a': 1}]
    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self):
        self.closed = 0
        self.last = None
    def cursor(self):
        self.last = FakeCursor()
        return self.last
    def execute(self, sql, params=None):
        return self.cursor().execute(sql, params)
    def commit(self):
        pass
    def close(self):
        self.closed += 1


def test_both_query_paths_measure_execute_and_fetch():
    data = Metrics()
    conn = MeasuredConnection(FakeConn(), data)
    assert conn.execute('SELECT 1').fetchall() == [{'a': 1}]
    cur = conn.cursor()
    assert cur.execute('SELECT 2').fetchone() == {'a': 1}
    cur.close()
    conn.close()
    assert data.queries == 2
    assert data.sql_ms >= 0
    assert data.fetch_ms >= 0
    assert data.close_ms >= 0
    assert conn.raw.closed == 1


def test_double_close_does_not_close_pooled_connection_twice():
    data = Metrics()
    conn = MeasuredConnection(FakeConn(), data)
    conn.close()
    conn.close()
    assert conn.raw.closed == 1


def test_context_manager_returns_wrapper_and_releases_once():
    data = Metrics()
    conn = MeasuredConnection(FakeConn(), data)
    with conn as active:
        assert active is conn
        assert active.execute('SELECT 1').fetchone()['a'] == 1
    assert conn.raw.closed == 1


def test_format_metrics_contains_only_aggregate_numbers_not_sensitive_queries():
    data = Metrics(acquire_ms=1.2, sql_ms=3.4, fetch_ms=5.6, render_ms=7.8, queries=2)
    text = format_metrics('login', 302, 19.2, data)
    assert 'endpoint=login' in text
    assert 'status=302' in text
    assert 'queries=2' in text
    assert 'password' not in text.lower()


def test_installer_measures_flask_request_without_revealing_paths(monkeypatch, capsys):
    import types
    from runtime_metrics import install
    fake_g = types.SimpleNamespace()
    fake_request = types.SimpleNamespace(endpoint='dashboard')
    monkeypatch.setitem(sys.modules, 'flask', types.SimpleNamespace(
        g=fake_g, request=fake_request, has_request_context=lambda: True))
    fake_analysis = types.SimpleNamespace(get_db=lambda: None)
    monkeypatch.setitem(sys.modules, 'profit_analysis', fake_analysis)

    class FakeApp:
        def __init__(self):
            self.before = []
            self.after = []
        def before_request(self, fn):
            self.before.append(fn)
            return fn
        def after_request(self, fn):
            self.after.append(fn)
            return fn

    module = types.SimpleNamespace(app=FakeApp(), get_db=FakeConn,
                                   render_template=lambda template: '<p>ok</p>')
    install(module)
    install(module)
    assert len(module.app.before) == 1
    assert len(module.app.after) == 1
    module.app.before[0]()
    assert module.render_template('dashboard.html') == '<p>ok</p>'
    conn = module.get_db()
    conn.execute('SELECT secret').fetchall()
    conn.close()
    response = types.SimpleNamespace(headers={}, status_code=200)
    monkeypatch.setenv('PERF_LOG_ALL', '1')
    monkeypatch.setenv('PERF_SERVER_TIMING', '1')
    assert module.app.after[0](response) is response
    text = capsys.readouterr().out
    assert 'endpoint=dashboard' in text
    assert 'SELECT secret' not in text
    assert 'sql;dur=' in response.headers['Server-Timing']
