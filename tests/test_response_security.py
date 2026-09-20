"""Commercial financial responses must never enter shared or browser caches."""
from importlib import import_module

from flask import Flask, Response


def _app(tmp_path):
    static = tmp_path / 'static'
    static.mkdir()
    (static / 'app.js').write_text('window.loaded = true;', encoding='utf-8')
    app = Flask(__name__, static_folder=str(static), static_url_path='/static')
    app.config.update(SECRET_KEY='test-only-secret', SEND_FILE_MAX_AGE_DEFAULT=86400)

    @app.get('/login')
    def login():
        return 'login'

    @app.get('/report')
    def report():
        return '<p>private financial totals</p>'

    @app.get('/export/2026')
    def export():
        response = Response('month,total\n9,100\n', mimetype='text/csv')
        response.headers['Cache-Control'] = 'public, max-age=86400'
        response.headers['Content-Disposition'] = 'attachment; filename=report.csv'
        return response

    return app


def test_financial_pages_login_and_download_reject_browser_and_proxy_caches(tmp_path):
    app = _app(tmp_path)
    import_module('response_security').install(app)
    client = app.test_client()
    for path in ('/login', '/report', '/export/2026'):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'private, no-store, max-age=0'
        assert response.headers['Pragma'] == 'no-cache'
        assert response.headers['Expires'] == '0'
        assert response.headers['X-Frame-Options'] == 'DENY'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert response.headers['Referrer-Policy'] == 'no-referrer'
        assert response.headers['X-Robots-Tag'] == 'noindex, nofollow'
    export = client.get('/export/2026')
    assert export.headers['Content-Disposition'] == 'attachment; filename=report.csv'
    assert export.data.startswith(b'month,total')


def test_static_assets_keep_caching_and_security_hook_is_idempotent(tmp_path):
    app = _app(tmp_path)
    install = import_module('response_security').install
    install(app)
    hooks = len(app.after_request_funcs[None])
    install(app)
    assert len(app.after_request_funcs[None]) == hooks
    response = app.test_client().get('/static/app.js')
    assert response.status_code == 200
    assert 'no-store' not in response.headers.get('Cache-Control', '')
    assert 'max-age=86400' in response.headers.get('Cache-Control', '')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['X-Frame-Options'] == 'DENY'
