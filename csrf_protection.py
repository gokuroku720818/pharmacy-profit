"""Session-bound CSRF protection for state-changing application requests."""

import secrets
from flask import request, session


def install(app):
    if app.extensions.get('form_csrf_installed'):
        return

    def form_csrf_token():
        if '_form_csrf' not in session:
            session['_form_csrf'] = secrets.token_urlsafe(32)
        return session['_form_csrf']

    app.jinja_env.globals['form_csrf_token'] = form_csrf_token

    @app.before_request
    def protect_mutations():
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return None
        # These handlers already check their own session-bound CSRF token.
        if request.endpoint in ('extra_profit.view', 'extra_profit.delete', 'save_business_schedule'):
            return None
        supplied = request.headers.get('X-CSRF-Token', '') or request.form.get('csrf_token', '')
        expected = session.get('_form_csrf', '')
        if not supplied or not expected or not secrets.compare_digest(supplied, expected):
            return '화면을 새로고침한 후 다시 시도해 주세요.', 400
        return None

    app.extensions['form_csrf_installed'] = True
