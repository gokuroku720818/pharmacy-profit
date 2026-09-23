"""Optional lightweight pharmacy work homepage; keeps accounting views unchanged."""
import secrets
from flask import flash, redirect, render_template, request, session, url_for
from database import get_db
from work_store import TABLES, add, change, edit, init_tables, read_home


def install(app_module):
    app = app_module.app
    if app.extensions.get('pharmacy_work_home'):
        return

    # Initialize only the four independent work tables before changing routes.
    conn = get_db()
    try:
        init_tables(conn)
    finally:
        conn.close()

    original_dashboard = app.view_functions['dashboard']
    def work_home():
        # Keep the original dashboard endpoint so its Chart.js assets still load.
        if request.args.get('profit') == '1':
            return original_dashboard()
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if 'work_csrf' not in session:
            session['work_csrf'] = secrets.token_urlsafe(32)
        conn = get_db()
        try:
            data = read_home(conn, session['user_id'], app_module.korea_today(), request.args.get('q', ''))
        finally:
            conn.close()
        return render_template('work_home.html', **data, work_csrf=session['work_csrf'])

    # Existing login redirects and '/' now land on the fast work screen.
    app.view_functions['dashboard'] = work_home

    def guard():
        if 'user_id' not in session:
            return redirect(url_for('login'))
        supplied = request.form.get('csrf_token', '')
        expected = session.get('work_csrf', '')
        if not supplied or not expected or not secrets.compare_digest(supplied, expected):
            return '화면을 새로고침한 후 다시 저장해 주세요.', 400
        return None

    def apply(kind, operation, item_id=None):
        fail = guard()
        if fail is not None:
            return fail
        conn = None
        try:
            conn = get_db()
            if operation == 'add':
                add(conn, kind, session['user_id'], request.form)
            elif operation == 'edit':
                edit(conn, kind, session['user_id'], item_id, request.form)
            else:
                change(conn, kind, session['user_id'], item_id, request.form.get('action', ''),
                       app_module.korea_today())
            flash('업무 내용이 저장되었습니다.', 'success')
        except ValueError as exc:
            if conn is not None:
                conn.rollback()
            flash(str(exc), 'danger')
        except Exception:
            if conn is not None:
                conn.rollback()
            app.logger.exception('Work home update failed')
            flash('저장 중 문제가 발생했습니다. 다시 시도해 주세요.', 'danger')
        finally:
            if conn is not None:
                conn.close()
        return redirect(url_for('dashboard'))

    @app.route('/work/add/<kind>', methods=['POST'])
    def work_add(kind):
        return apply(kind, 'add')

    @app.route('/work/edit/<kind>/<item_id>', methods=['POST'])
    def work_edit(kind, item_id):
        return apply(kind, 'edit', item_id)

    @app.route('/work/change/<kind>/<item_id>', methods=['POST'])
    def work_change(kind, item_id):
        return apply(kind, 'change', item_id)

    app.extensions['pharmacy_work_home'] = True
