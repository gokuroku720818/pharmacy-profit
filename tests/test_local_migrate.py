import sqlite3

import pytest

import local_migrate


class SourceCursor:
    def __init__(self, conn):
        self.conn = conn
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, sql, params=None):
        if 'information_schema.tables' in sql:
            names = [r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            self.result = iter([{'table_name': name} for name in names])
        elif 'information_schema.columns' in sql:
            self.result = iter([{'column_name': r[1]} for r in self.conn.execute(
                f'PRAGMA table_info("{params[0]}")')])
        else:
            self.result = iter(self.conn.execute(sql, params or ()))

    def fetchall(self):
        return list(self.result)

    def fetchone(self):
        return next(self.result, None)

    def fetchmany(self, count):
        rows = []
        for _ in range(count):
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)
        return rows


class SourceConnection:
    def __init__(self, conn):
        self.conn = conn

    def set_session(self, readonly, isolation_level):
        assert readonly and isolation_level == 'REPEATABLE READ'

    def cursor(self):
        return SourceCursor(self.conn)

    def close(self):
        self.conn.close()


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / 'source.db'
    local_migrate.make_empty_sqlite(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO users (id,username,password_hash,pharmacy_name) VALUES (7,'admin','hash','약국')")
    conn.execute("""INSERT INTO daily_profit
                 (id,user_id,date,day_of_week,dispensing_fee,daily_net_profit,
                  dispensing_plus_daily,non_insurance_margin,total,memo)
                 VALUES (10,7,'2026-09-24','목',100,200,300,50,350,'확인')""")
    conn.execute("INSERT INTO extra_profit (id,user_id,date,amount,memo) VALUES (13,7,'2026-09-24',-17,'조정')")
    conn.execute("INSERT INTO business_schedules (user_id,settings_json) VALUES (7,'{}')")
    conn.execute('INSERT INTO user_calculator_settings (user_id,dispensing_fee) VALUES (7,123)')
    conn.commit()
    conn.close()
    def connect(*a, **kw):
        source_db = sqlite3.connect(path)
        source_db.row_factory = sqlite3.Row
        return SourceConnection(source_db)
    monkeypatch.setattr(local_migrate.psycopg2, 'connect', connect)
    return path


def test_migrate_preserves_records_and_blocks_existing_target(source, tmp_path):
    dest = tmp_path / 'data' / 'sales.db'
    totals = local_migrate.migrate('postgresql://example.invalid/test', dest)
    assert totals['users'] == totals['daily_profit'] == totals['extra_profit'] == 1
    with sqlite3.connect(dest) as db:
        assert db.execute('SELECT id, memo, total FROM daily_profit').fetchone() == (10, '확인', 350)
        assert db.execute('SELECT amount FROM extra_profit').fetchone() == (-17,)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    with pytest.raises(FileExistsError):
        local_migrate.migrate('postgresql://example.invalid/test', dest)


def test_unrecognized_data_blocks_import_without_creating_target(source, tmp_path):
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE unrecognized (id INTEGER)')
        db.execute('INSERT INTO unrecognized VALUES (1)')
    dest = tmp_path / 'local.db'
    with pytest.raises(RuntimeError, match='Unrecognized table'):
        local_migrate.migrate('postgresql://example.invalid/test', dest)
    assert not dest.exists()
