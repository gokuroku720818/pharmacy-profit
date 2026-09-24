from flask import Flask
from test_analysis import service


def test_mutating_form_and_json_requests_require_session_token():
    from csrf_protection import install
    app = Flask(__name__)
    app.secret_key = 'test-only-session-secret'
    app.add_url_rule('/write', 'write', lambda: 'saved', methods=['POST'])
    install(app)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_form_csrf'] = 'known-token'
    assert client.post('/write', data={'value': '1'}).status_code == 400
    assert client.post('/write', json={'value': 1}).status_code == 400
    assert client.post('/write', data={'csrf_token': 'known-token'}).status_code == 200
    assert client.post('/write', json={'value': 1}, headers={'X-CSRF-Token': 'known-token'}).status_code == 200


def test_get_renders_token_once_and_existing_protected_routes_keep_own_tokens():
    from csrf_protection import install
    app = Flask(__name__)
    app.secret_key = 'test-only-session-secret'
    app.add_url_rule('/page', 'page', lambda: app.jinja_env.globals['form_csrf_token']())
    app.add_url_rule('/business-schedule', 'save_business_schedule', lambda: 'existing guard', methods=['POST'])
    install(app)
    client = app.test_client()
    first = client.get('/page').get_data(as_text=True)
    assert first and client.get('/page').get_data(as_text=True) == first
    assert client.post('/business-schedule').status_code == 200


def test_session_switch_and_logout_are_post_only(service):
    client = service.app.test_client()
    with client.session_transaction() as sess:
        sess.update(user_id=1, username='admin', pharmacy_name='Test', _form_csrf='known-token')
    assert client.get('/logout').status_code == 405
    assert client.get('/admin/switch_user/2').status_code == 405
    assert client.get('/admin/switch_back').status_code == 405
    assert client.post('/logout').status_code == 400
    assert client.post('/logout', data={'csrf_token': 'known-token'}).status_code == 302
