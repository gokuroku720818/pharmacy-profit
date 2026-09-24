"""Startup must never invent or recreate financial entries."""

import database


def test_importing_app_does_not_create_or_migrate_a_database(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    target = tmp_path / 'empty.db'
    environment = dict(os.environ, SECRET_KEY='synthetic-test-key-with-sufficient-length',
                       ADMIN_BOOTSTRAP_PASSWORD='synthetic-bootstrap-password')
    environment.pop('DATABASE_URL', None)
    result = subprocess.run([sys.executable, '-c',
        "import database; database.SQLITE_PATH = %r; import app" % str(target)],
        cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not target.exists()


def test_startup_preserves_existing_extras_without_inserting_legacy_values(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'SQLITE_PATH', str(tmp_path / 'sales.db'))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)

    database.init_db()
    conn = database.get_db()
    try:
        assert conn.execute('SELECT COUNT(*) FROM extra_profit').fetchone()[0] == 0
        conn.execute("INSERT INTO extra_profit (user_id,date,amount,memo) VALUES (1,'2023-04-01',245600,'현금')")
        conn.commit()
    finally:
        conn.close()

    database.init_db()
    conn = database.get_db()
    try:
        assert [(r['date'], r['amount']) for r in conn.execute(
            'SELECT date, amount FROM extra_profit ORDER BY id').fetchall()] == [('2023-04-01', 245600)]
    finally:
        conn.close()


def test_postgres_startup_does_not_import_local_sqlite_automatically(tmp_path, monkeypatch):
    source = tmp_path / 'sales.db'
    source.write_bytes(b'legacy-file-present')
    monkeypatch.setattr(database, 'SQLITE_PATH', str(source))

    class Cursor:
        def execute(self, sql, params=None):
            return self
        def fetchone(self):
            return {'cnt': 0}
        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()
        def commit(self):
            pass
        def rollback(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(database.psycopg2, 'connect', lambda *a, **kw: Connection())
    reads = []
    def unexpected_sqlite_read(*a, **kw):
        reads.append(True)
        raise RuntimeError('legacy file should not be read')
    monkeypatch.setattr(database.sqlite3, 'connect', unexpected_sqlite_read)
    database.init_postgres_db('postgresql://test.invalid/test')
    assert reads == []
