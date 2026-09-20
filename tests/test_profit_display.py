"""Flask request context must be isolated by signed-in user, with one group query."""
import sqlite3

from flask import Flask
import profit_display


def test_context_functions_reuse_one_query_and_hide_unknown(monkeypatch):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE daily_profit (user_id INTEGER, date TEXT,
        dispensing_fee INTEGER, daily_net_profit INTEGER,
        non_insurance_margin INTEGER, total INTEGER)''')
    conn.execute("INSERT INTO daily_profit VALUES (1, '2026-08-01', 120, 70, 30, 220)")
    conn.commit()
    calls = []

    class DB:
        def execute(self, sql, params):
            calls.append(params)
            return conn.execute(sql, params)
        def close(self):
            pass

    monkeypatch.setattr(profit_display, 'get_db', lambda: DB())
    app = Flask(__name__)
    app.secret_key = 'disposable-unit-test-only'
    profit_display.install(app)
    with app.test_request_context('/'):
        from flask import session
        session['user_id'] = 1
        helpers = app.template_context_processors[None][-1]()
        known = helpers['components_for'](2026, 8, 190, 30, 220)
        missing = helpers['components_for'](2026, 7, 190, 30, 220)
        chart = helpers['three_way_series'](['2026.08', '2026.07'], [190, 190], [30, 30], [220, 220])
        assert [known[k] for k in ('dispensing_fee', 'daily_net_profit', 'non_insurance_margin')] == [120, 70, 30]
        assert missing['dispensing_fee'] is None
        assert chart['dispensing_fee'] == [120, None]
        assert chart['daily_net_profit'] == [70, None]
        assert helpers['period_components'](2026, [{'month': 8, 'dispensing_plus_daily_total': 190,
                                                      'non_insurance_total': 30, 'grand_total': 220}])['dispensing_fee'] == 120
        assert len(calls) == 1
    conn.close()


def test_anonymous_requests_never_load_financial_data(monkeypatch):
    app = Flask(__name__)
    app.secret_key = 'unit-test-only'
    profit_display.install(app)
    monkeypatch.setattr(profit_display, 'get_db', lambda: (_ for _ in ()).throw(AssertionError('no DB')))
    with app.test_request_context('/login'):
        assert app.template_context_processors[None][-1]() == {}
