"""Optional miscellaneous gains ledger. Never rewrites dispensing or monthly base records."""
import datetime
import re
import secrets

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from database import get_db
import profit_analysis

bp = Blueprint('extra_profit', __name__)


def monthly_totals(conn, user_id):
    """Return miscellaneous totals keyed by (year, month), scoped to one user."""
    rows = conn.execute('''
        SELECT SUBSTR(date, 1, 4) AS year_text, SUBSTR(date, 6, 2) AS month_text,
               SUM(amount) AS amount
        FROM extra_profit WHERE user_id = ?
        GROUP BY SUBSTR(date, 1, 4), SUBSTR(date, 6, 2)
    ''', (user_id,)).fetchall()
    return {(int(r['year_text']), int(r['month_text'])): int(r['amount'] or 0) for r in rows}


def merge_monthly_summaries(base_rows, extras):
    """Add extras to READ results, never overwrite imported/month-only source ledgers."""
    items = {(int(r['year']), int(r['month'])): dict(r) for r in base_rows}
    base_totals = {key: int(row['grand_total'] or 0) for key, row in items.items()}
    for (year, month), amount in extras.items():
        if (year, month) not in items:
            items[(year, month)] = {
                'year': year, 'month': month, 'dispensing_plus_daily_total': 0,
                'non_insurance_total': 0, 'grand_total': 0, 'prev_month_diff': 0,
            }
    result = []
    for (year, month), row in sorted(items.items()):
        misc = extras.get((year, month), 0)
        row['extra_profit_total'] = misc
        row['grand_total'] = int(row['grand_total'] or 0) + misc
        prev = (year - 1, 12) if month == 1 else (year, month - 1)
        if (year, month) in base_totals:
            row['prev_month_diff'] = int(row['prev_month_diff'] or 0) + misc - extras.get(prev, 0)
        else:
            row['prev_month_diff'] = row['grand_total'] - base_totals.get(prev, 0) - extras.get(prev, 0)
        if row['grand_total'] > 0:
            result.append(row)
    return result


def _csrf_ok():
    actual = request.form.get('csrf_token', '')
    expected = session.get('extra_profit_csrf', '')
    return bool(actual and expected and secrets.compare_digest(actual, expected))


def _validate():
    raw_date = request.form.get('date', '').strip()
    raw_amount = request.form.get('amount', '').strip()
    memo = request.form.get('memo', '').strip()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_date):
        raise ValueError('날짜를 확인해 주세요.')
    date = datetime.date.fromisoformat(raw_date)
    if not (2000 <= date.year <= 2100):
        raise ValueError('날짜 범위를 확인해 주세요.')
    if not re.fullmatch(r'[+-]?\d{1,13}', raw_amount):
        raise ValueError('금액은 정수로 입력해 주세요.')
    amount = int(raw_amount)
    if not (-1_000_000_000_000 <= amount <= 1_000_000_000_000) or amount == 0:
        raise ValueError('금액은 0을 제외한 유효한 범위로 입력해 주세요.')
    if not 1 <= len(memo) <= 200:
        raise ValueError('내용은 1~200자로 입력해 주세요.')
    return date.isoformat(), amount, memo


def _invalidate(user_id):
    # This import runs after app startup, avoiding a module import cycle.
    from app import invalidate_extra_profit_cache
    invalidate_extra_profit_cache(user_id)


@bp.before_request
def require_login():
    if 'user_id' not in session:
        return redirect(url_for('login'))


@bp.route('/extra-profit', methods=['GET', 'POST'])
def view():
    user_id = session['user_id']
    if request.method == 'POST':
        if not _csrf_ok():
            return '화면을 새로고침한 후 다시 저장해 주세요.', 400
        try:
            day, amount, memo = _validate()
        except (ValueError, TypeError):
            return '날짜·금액·내용을 확인해 주세요.', 400
        raw_id = request.form.get('id', '').strip()
        if raw_id and (not raw_id.isdigit() or int(raw_id) <= 0):
            return '수정 대상이 올바르지 않습니다.', 400
        conn = get_db()
        try:
            if raw_id:
                item_id = int(raw_id)
                existing = conn.execute('SELECT id FROM extra_profit WHERE id=? AND user_id=?', (item_id, user_id)).fetchone()
                if not existing:
                    abort(404)
                conn.execute('UPDATE extra_profit SET date=?, amount=?, memo=? WHERE id=? AND user_id=?',
                             (day, amount, memo, item_id, user_id))
            else:
                conn.execute('INSERT INTO extra_profit (user_id, date, amount, memo) VALUES (?, ?, ?, ?)',
                             (user_id, day, amount, memo))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        _invalidate(user_id)
        flash('잡이익이 저장되었습니다.', 'success')
        return redirect(url_for('extra_profit.view', year=int(day[:4]), month=int(day[5:7])))

    today = profit_analysis.korea_today()
    year = request.args.get('year', today.year, type=int)
    month = request.args.get('month', today.month, type=int)
    if not 2000 <= year <= 2100 or not 1 <= month <= 12:
        return '연월을 확인해 주세요.', 400
    edit_id = request.args.get('edit', 0, type=int)
    session.setdefault('extra_profit_csrf', secrets.token_urlsafe(32))
    conn = get_db()
    try:
        month_start = datetime.date(year, month, 1)
        next_month = (month_start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        rows = conn.execute('''SELECT id, date, amount, memo FROM extra_profit
             WHERE user_id=? AND date >= ? AND date < ? ORDER BY date DESC,id DESC''',
             (user_id, month_start.isoformat(), next_month.isoformat())).fetchall()
        entries = [dict(r) for r in rows]
        editing = None
        if edit_id:
            row = conn.execute('SELECT id,date,amount,memo FROM extra_profit WHERE id=? AND user_id=?',
                               (edit_id,user_id)).fetchone()
            if row is None:
                abort(404)
            editing = dict(row)
    finally:
        conn.close()
    return render_template('extra_profit.html', entries=entries, editing=editing,
        month_total=sum(int(entry['amount']) for entry in entries), year=year, month=month,
        today=today.isoformat(), csrf_token=session['extra_profit_csrf'])


@bp.route('/extra-profit/<int:item_id>/delete', methods=['POST'])
def delete(item_id):
    if not _csrf_ok():
        return '화면을 새로고침한 후 다시 삭제해 주세요.', 400
    user_id = session['user_id']
    conn = get_db()
    try:
        existing = conn.execute('SELECT date FROM extra_profit WHERE id=? AND user_id=?',
                                (item_id, user_id)).fetchone()
        if not existing:
            abort(404)
        conn.execute('DELETE FROM extra_profit WHERE id=? AND user_id=?', (item_id, user_id))
        conn.commit()
        date = existing['date']
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _invalidate(user_id)
    flash('잡이익 항목을 삭제했습니다.', 'success')
    return redirect(url_for('extra_profit.view', year=int(date[:4]), month=int(date[5:7])))


def install(app):
    """Register only once. Database schema is initialized in database.init_db()."""
    if not app.extensions.get('extra_profit_installed'):
        app.register_blueprint(bp)
        app.extensions['extra_profit_installed'] = True
