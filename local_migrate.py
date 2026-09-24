"""Copy the live PostgreSQL ledger to a NEW local SQLite file.

The source is opened read-only. The destination is assembled off to the side
and moved into place only after its records and totals have been checked.
"""

import argparse
import datetime
import decimal
import os
import secrets
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

import database


TABLES = (
    'users', 'daily_profit', 'monthly_summary', 'user_calculator_settings',
    'business_schedules', 'extra_profit',
)
TOTALS = {
    'daily_profit': ('dispensing_fee', 'daily_net_profit', 'non_insurance_margin', 'total'),
    'monthly_summary': ('dispensing_plus_daily_total', 'non_insurance_total', 'grand_total'),
    'extra_profit': ('amount',),
}


def source_tables(cursor):
    cursor.execute("""SELECT table_name FROM information_schema.tables
                      WHERE table_schema = 'public' AND table_type = 'BASE TABLE'""")
    return {row['table_name'] for row in cursor.fetchall()}


def source_columns(cursor, table):
    cursor.execute("""SELECT column_name FROM information_schema.columns
                      WHERE table_schema = 'public' AND table_name = %s""", (table,))
    return {row['column_name'] for row in cursor.fetchall()}


def make_empty_sqlite(path):
    previous_path = database.SQLITE_PATH
    previous_password = os.environ.get('ADMIN_BOOTSTRAP_PASSWORD')
    try:
        database.SQLITE_PATH = str(path)
        os.environ['ADMIN_BOOTSTRAP_PASSWORD'] = secrets.token_urlsafe(32)
        database.init_sqlite_db()
    finally:
        database.SQLITE_PATH = previous_path
        if previous_password is None:
            os.environ.pop('ADMIN_BOOTSTRAP_PASSWORD', None)
        else:
            os.environ['ADMIN_BOOTSTRAP_PASSWORD'] = previous_password
    with sqlite3.connect(path) as target:
        target.execute('PRAGMA foreign_keys=ON')
        target.execute('DELETE FROM users')  # remove the temporary bootstrap account
        target.execute("""CREATE TABLE business_schedules (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            settings_json TEXT NOT NULL)""")
        target.execute("""CREATE TABLE extra_profit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            amount BIGINT NOT NULL CHECK (amount != 0),
            memo TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        target.execute('CREATE INDEX idx_extra_profit_user_date ON extra_profit(user_id, date)')


def convert(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat(sep=' ') if isinstance(value, datetime.datetime) else value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def copy_table(source, target, table):
    columns = [row[1] for row in target.execute(f'PRAGMA table_info("{table}")')]
    missing = set(columns) - source_columns(source, table)
    # Defaults cover columns added after the original PostgreSQL schema was made.
    if missing - {'otc_calc_mode', 'monthly_otc_net_profit'}:
        raise RuntimeError(f'{table}: source lacks required columns: {sorted(missing)}')
    columns = [name for name in columns if name not in missing]
    names = ', '.join(f'"{name}"' for name in columns)
    placeholders = ', '.join('?' for _ in columns)
    source.execute(f'SELECT {names} FROM "{table}" ORDER BY '
                   + ('user_id' if table == 'business_schedules' else 'id'))
    count = 0
    while True:
        batch = source.fetchmany(500)
        if not batch:
            break
        target.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
                           [tuple(convert(row[name]) for name in columns) for row in batch])
        count += len(batch)
    return count


def verify_table(source, target, table, copied):
    source.execute(f'SELECT COUNT(*) AS n FROM "{table}"')
    original = source.fetchone()['n']
    restored = target.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    if original != restored or copied != original:
        raise RuntimeError(f'{table}: row count mismatch ({original}, {copied}, {restored})')
    for column in TOTALS.get(table, ()):
        source.execute(f'SELECT COALESCE(SUM("{column}"), 0) AS total FROM "{table}"')
        original_total = source.fetchone()['total']
        restored_total = target.execute(
            f'SELECT COALESCE(SUM("{column}"), 0) FROM "{table}"').fetchone()[0]
        if original_total != restored_total:
            raise RuntimeError(f'{table}.{column}: total mismatch')
    return original


def migrate(url, destination=Path(database.SQLITE_PATH)):
    destination = Path(destination)
    if destination.exists() or any(destination.parent.glob(destination.name + '*')):
        raise FileExistsError(f'Local database already exists: {destination}. No files were changed.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix='.sales-import-', suffix='.db', dir=destination.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with closing(psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=10)) as source_conn:
            source_conn.set_session(readonly=True, isolation_level='REPEATABLE READ')
            with source_conn.cursor() as source:
                tables = source_tables(source)
                missing = set(TABLES) - tables
                if missing:
                    raise RuntimeError(f'Source database lacks tables: {sorted(missing)}')
                unknown = tables - set(TABLES)
                for table in sorted(unknown):
                    if not table.replace('_', '').isalnum():
                        raise RuntimeError(f'Unexpected source table: {table}')
                    source.execute(f'SELECT COUNT(*) AS n FROM "{table}"')
                    if source.fetchone()['n']:
                        raise RuntimeError(f'Unrecognized table contains data: {table}. Stop and review migration.')
                make_empty_sqlite(temp_path)
                with sqlite3.connect(temp_path) as target:
                    target.execute('PRAGMA foreign_keys=ON')
                    totals = {}
                    for table in TABLES:
                        copied = copy_table(source, target, table)
                        totals[table] = verify_table(source, target, table, copied)
                    if target.execute('PRAGMA foreign_key_check').fetchone():
                        raise RuntimeError('SQLite foreign key validation failed')
                    if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise RuntimeError('SQLite integrity check failed')
                # Hard link fails if another process created the destination.
                os.link(temp_path, destination)
                return totals
    finally:
        if temp_path.exists():
            temp_path.unlink()


def main():
    parser = argparse.ArgumentParser(description='Read-only PostgreSQL to new local SQLite migration')
    parser.add_argument('--destination', type=Path, default=Path(database.SQLITE_PATH))
    args = parser.parse_args()
    url = os.environ.get('SOURCE_DATABASE_URL', '')
    if not url.startswith(('postgresql://', 'postgres://')):
        parser.error('Set SOURCE_DATABASE_URL privately to the source PostgreSQL URL')
    try:
        totals = migrate(url, args.destination)
    except Exception as exc:
        # psycopg2 errors can contain a connection URI; do not print them.
        print(f'Migration stopped: {type(exc).__name__}. Source remains unchanged.', file=sys.stderr)
        return 1
    print('Migration verified:', ', '.join(f'{table}={count}' for table, count in totals.items()))
    print(f'SQLite database: {args.destination}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
