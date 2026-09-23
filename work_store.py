"""Small, user-scoped work-board data store. Compatible with SQLite and PostgreSQL."""
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

TABLES = {'note': 'work_notes', 'task': 'work_tasks', 'link': 'work_links', 'stock': 'work_stock'}


def init_tables(conn):
    """Create only new work-board tables; never migrate or touch financial tables."""
    conn.execute('''CREATE TABLE IF NOT EXISTS work_notes (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', due_date TEXT,
        pinned INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS work_tasks (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL, due_date TEXT, repeat_daily INTEGER NOT NULL DEFAULT 0,
        done INTEGER NOT NULL DEFAULT 0, completed_on TEXT, created_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS work_links (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL, url TEXT NOT NULL, created_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS work_stock (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
        minimum_quantity INTEGER NOT NULL DEFAULT 0, expires_on TEXT,
        detail TEXT NOT NULL DEFAULT '', closed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL)''')
    for kind, table in TABLES.items():
        conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_user ON {table}(user_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_work_stock_expiry ON work_stock(user_id, closed, expires_on)')
    conn.commit()


def _text(form, field, limit, required=False):
    value = (form.get(field) or '').strip()
    if len(value) > limit or (required and not value):
        raise ValueError(f'{field}: 글자 수 또는 필수 입력을 확인해 주세요.')
    return value


def _date(form, field):
    value = _text(form, field, 10)
    if value:
        try:
            if len(value) != 10 or date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError:
            raise ValueError('날짜는 YYYY-MM-DD 형식으로 입력해 주세요.') from None
    return value or None


def _number(form, field):
    value = _text(form, field, 12, True)
    try:
        number = int(value)
    except ValueError:
        raise ValueError('재고 수량은 0 이상의 정수여야 합니다.') from None
    if not 0 <= number <= 1000000000:
        raise ValueError('재고 수량은 0~10억 사이여야 합니다.')
    return number


def validate_url(value):
    value = (value or '').strip()
    try:
        url = urlsplit(value)
        valid = (len(value) <= 500 and url.scheme == 'https' and bool(url.hostname)
                 and not url.username and not url.password and not any(c.isspace() for c in value))
        if valid:
            _ = url.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('즐겨찾기 주소는 안전한 https:// 주소만 등록할 수 있습니다.')
    return value


def _values(kind, form):
    if kind not in TABLES:
        raise ValueError('알 수 없는 업무 유형입니다.')
    title = _text(form, 'title', 120, True)
    if kind == 'note':
        return {'title': title, 'detail': _text(form, 'detail', 2000), 'due_date': _date(form, 'due_date')}
    if kind == 'task':
        repeat = 1 if form.get('repeat_daily') == '1' else 0
        return {'title': title, 'due_date': None if repeat else _date(form, 'due_date'), 'repeat_daily': repeat}
    if kind == 'link':
        return {'title': title, 'url': validate_url(form.get('url'))}
    return {'title': title, 'quantity': _number(form, 'quantity'),
            'minimum_quantity': _number(form, 'minimum_quantity'),
            'expires_on': _date(form, 'expires_on'), 'detail': _text(form, 'detail', 500)}


def add(conn, kind, user_id, form):
    values = _values(kind, form)
    item_id = uuid4().hex
    data = {'id': item_id, 'user_id': user_id, **values,
            'created_at': datetime.now(timezone.utc).isoformat()}
    cols = ', '.join(data)
    conn.execute(f"INSERT INTO {TABLES[kind]} ({cols}) VALUES ({', '.join('?' for _ in data)})",
                 tuple(data.values()))
    conn.commit()
    return item_id


def edit(conn, kind, user_id, item_id, form):
    values = _values(kind, form)
    if kind == 'task':
        # A change of repeating mode restarts the completion state intentionally.
        values.update(done=0, completed_on=None)
    assignments = ', '.join(f'{field} = ?' for field in values)
    conn.execute(f'UPDATE {TABLES[kind]} SET {assignments} WHERE id = ? AND user_id = ?',
                 (*values.values(), item_id, user_id))
    conn.commit()


def change(conn, kind, user_id, item_id, action, today):
    if kind not in TABLES:
        raise ValueError('알 수 없는 업무 유형입니다.')
    table = TABLES[kind]
    if action == 'delete':
        conn.execute(f'DELETE FROM {table} WHERE id = ? AND user_id = ?', (item_id, user_id))
    elif kind == 'note' and action in ('toggle', 'pin'):
        field = 'done' if action == 'toggle' else 'pinned'
        conn.execute(f'UPDATE {table} SET {field} = CASE WHEN {field} = 1 THEN 0 ELSE 1 END '
                     'WHERE id = ? AND user_id = ?', (item_id, user_id))
    elif kind == 'task' and action == 'toggle':
        # Daily completion expires on the next Korea-local date.
        conn.execute('''UPDATE work_tasks SET
            completed_on = CASE WHEN repeat_daily = 1 AND completed_on = ? THEN NULL
                                WHEN repeat_daily = 1 THEN ? ELSE completed_on END,
            done = CASE WHEN repeat_daily = 0 THEN CASE WHEN done = 1 THEN 0 ELSE 1 END ELSE done END
            WHERE id = ? AND user_id = ?''', (today.isoformat(), today.isoformat(), item_id, user_id))
    elif kind == 'stock' and action == 'close':
        conn.execute('UPDATE work_stock SET closed = 1 WHERE id = ? AND user_id = ?', (item_id, user_id))
    elif kind == 'stock' and action == 'reopen':
        conn.execute('UPDATE work_stock SET closed = 0 WHERE id = ? AND user_id = ?', (item_id, user_id))
    else:
        raise ValueError('허용되지 않은 업무 변경입니다.')
    conn.commit()


def stock_flags(row, today):
    expiry = row['expires_on']
    expiry_status = None
    if expiry:
        if expiry < today.isoformat():
            expiry_status = 'expired'
        elif expiry <= (today + timedelta(days=90)).isoformat():
            expiry_status = 'soon'
    return {'expiry_status': expiry_status,
            'low_stock': int(row['quantity']) <= int(row['minimum_quantity'])}


def read_home(conn, user_id, today, query=''):
    query = (query or '').strip()[:80]
    result = {}
    for kind, table in TABLES.items():
        cols = 'title, detail' if kind in ('note', 'stock') else 'title, url' if kind == 'link' else 'title'
        clause = ''
        args = [user_id]
        if query:
            clause = ' AND (' + ' OR '.join(f'{col} LIKE ?' for col in cols.split(', ')) + ')'
            args.extend(['%' + query + '%'] * len(cols.split(', ')))
        order = {'note': 'pinned DESC, done ASC, created_at DESC',
                 'task': 'repeat_daily DESC, due_date ASC, created_at DESC',
                 'link': 'created_at DESC',
                 'stock': 'closed ASC, CASE WHEN expires_on IS NULL THEN 1 ELSE 0 END, expires_on ASC, created_at DESC'}[kind]
        rows = conn.execute(f'SELECT * FROM {table} WHERE user_id = ?{clause} ORDER BY {order} LIMIT 80', args).fetchall()
        items = [dict(row) for row in rows]
        if kind == 'task':
            for row in items:
                row['is_done'] = bool(row['completed_on'] == today.isoformat() if row['repeat_daily'] else row['done'])
                row['is_overdue'] = bool(row['due_date'] and row['due_date'] < today.isoformat() and not row['is_done'])
        if kind == 'stock':
            for row in items:
                row.update(stock_flags(row, today))
        result[{'note': 'notes', 'task': 'tasks', 'link': 'links', 'stock': 'stocks'}[kind]] = items
    cutoff = (today + timedelta(days=90)).isoformat()
    predicate = '''user_id = ? AND closed = 0 AND
        ((expires_on IS NOT NULL AND expires_on <= ?) OR quantity <= minimum_quantity)'''
    result['alert_count'] = int(conn.execute(f'SELECT COUNT(*) AS n FROM work_stock WHERE {predicate}',
                                             (user_id, cutoff)).fetchone()['n'])
    alert_rows = conn.execute(f'''SELECT * FROM work_stock WHERE {predicate}
        ORDER BY CASE WHEN expires_on IS NULL THEN 1 ELSE 0 END, expires_on ASC LIMIT 20''',
                              (user_id, cutoff)).fetchall()
    result['alerts'] = [{**dict(row), **stock_flags(row, today)} for row in alert_rows]
    result['unfinished_count'] = int(conn.execute('''SELECT COUNT(*) AS n FROM work_tasks WHERE user_id = ? AND
        ((repeat_daily = 1 AND (completed_on IS NULL OR completed_on != ?)) OR
         (repeat_daily = 0 AND done = 0))''', (user_id, today.isoformat())).fetchone()['n'])
    result['today'] = today.isoformat()
    result['query'] = query
    return result
