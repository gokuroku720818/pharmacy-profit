import sqlite3
import os
import re
import threading
import time
import weakref
from werkzeug.security import generate_password_hash

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from psycopg2.pool import ThreadedConnectionPool
except ImportError:
    psycopg2 = None
    ThreadedConnectionPool = None

_pg_pool = None
_pg_pool_lock = threading.Lock()
_pool_error = None
_pg_last_used = weakref.WeakKeyDictionary()
_pg_usage_lock = threading.Lock()


def get_pg_pool():
    global _pg_pool, _pool_error
    if _pg_pool is None and psycopg2 and ThreadedConnectionPool:
        with _pg_pool_lock:
            # 더블 체크: 락 획득 사이에 다른 스레드가 이미 생성했을 수 있음
            if _pg_pool is None:
                db_url = get_database_url()
                if db_url:
                    try:
                        _pg_pool = ThreadedConnectionPool(minconn=1, maxconn=10, dsn=db_url, cursor_factory=RealDictCursor, connect_timeout=10)
                        _pool_error = None
                        print("⚡ PostgreSQL 커넥션 풀 활성화 완료 (고속 재사용 모드)")
                    except Exception as e:
                        _pool_error = str(e)
                        print(f"커넥션 풀 생성 실패: {e}")
    return _pg_pool


def get_pool_status():
    global _pg_pool, _pool_error
    return {
        'pool_active': _pg_pool is not None,
        'pool_error': _pool_error,
        'psycopg2_available': psycopg2 is not None,
        'is_postgres': is_postgres(),
    }


class PostgresConnectionWrapper:
    def __init__(self, conn, from_pool=False):
        self.conn = conn
        self.from_pool = from_pool
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def cursor(self):
        return PostgresCursorWrapper(self.conn.cursor())

    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.from_pool:
            pool = get_pg_pool()
            if pool:
                discard = bool(self.conn.closed)
                try:
                    self.conn.rollback()
                except Exception:
                    discard = True
                with _pg_usage_lock:
                    if discard:
                        _pg_last_used.pop(self.conn, None)
                    else:
                        _pg_last_used[self.conn] = time.monotonic()
                pool.putconn(self.conn, close=discard)
                return
        self.conn.close()


def get_db():
    """데이터베이스 연결 반환 (고속 커넥션 풀 우선, 미설정 시 SQLite)"""
    db_url = get_database_url()
    if db_url and psycopg2:
        pool = get_pg_pool()
        if pool:
            # psycopg2 connections do not accept custom Python attributes.
            # Retry one stale connection; never leave a checked-out connection behind.
            for _ in range(2):
                try:
                    pg_conn = pool.getconn()
                except psycopg2.pool.PoolError:
                    break
                try:
                    with _pg_usage_lock:
                        last_used = _pg_last_used.get(pg_conn)
                    if pg_conn.closed:
                        raise psycopg2.InterfaceError("Closed pooled connection")
                    if last_used is None or time.monotonic() - last_used > 60:
                        cursor = pg_conn.cursor()
                        try:
                            cursor.execute("SELECT 1")
                        finally:
                            cursor.close()
                        pg_conn.rollback()
                    return PostgresConnectionWrapper(pg_conn, from_pool=True)
                except Exception:
                    with _pg_usage_lock:
                        _pg_last_used.pop(pg_conn, None)
                    pool.putconn(pg_conn, close=True)
        pg_conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor, connect_timeout=10)
        return PostgresConnectionWrapper(pg_conn, from_pool=False)
    else:
        os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

SQLITE_PATH = os.path.join(os.path.dirname(__file__), 'data', 'sales.db')


def get_database_url():
    url = os.environ.get('DATABASE_URL')
    if url and url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return url


def is_postgres():
    return bool(get_database_url() and psycopg2)


def adapt_sql_for_postgres(sql):
    """SQLite 전용 쿼리를 PostgreSQL 문법으로 호환 변환 (괄호 누락 방지)"""
    # 1. INSERT OR REPLACE INTO daily_profit
    if 'INSERT OR REPLACE INTO daily_profit' in sql:
        vals_idx = sql.upper().find('VALUES')
        if vals_idx != -1:
            header_part = sql[:vals_idx]
            values_part = sql[vals_idx:]

            c_start = header_part.find('(')
            c_end = header_part.rfind(')')
            cols = header_part[c_start+1:c_end].strip()

            v_start = values_part.find('(')
            v_end = values_part.rfind(')')
            vals = values_part[v_start+1:v_end].strip()

            vals = vals.replace('?', '%s').replace("datetime('now', 'localtime')", "NOW()")

            return f"""
                INSERT INTO daily_profit ({cols})
                VALUES ({vals})
                ON CONFLICT (user_id, date) DO UPDATE SET
                    day_of_week = EXCLUDED.day_of_week,
                    dispensing_fee = EXCLUDED.dispensing_fee,
                    daily_net_profit = EXCLUDED.daily_net_profit,
                    dispensing_plus_daily = EXCLUDED.dispensing_plus_daily,
                    non_insurance_margin = EXCLUDED.non_insurance_margin,
                    total = EXCLUDED.total,
                    memo = COALESCE(EXCLUDED.memo, daily_profit.memo),
                    updated_at = NOW()
            """

    # 2. INSERT OR REPLACE INTO monthly_summary
    elif 'INSERT OR REPLACE INTO monthly_summary' in sql:
        vals_idx = sql.upper().find('VALUES')
        if vals_idx != -1:
            header_part = sql[:vals_idx]
            values_part = sql[vals_idx:]

            c_start = header_part.find('(')
            c_end = header_part.rfind(')')
            cols = header_part[c_start+1:c_end].strip()

            v_start = values_part.find('(')
            v_end = values_part.rfind(')')
            vals = values_part[v_start+1:v_end].strip()

            vals = vals.replace('?', '%s')

            return f"""
                INSERT INTO monthly_summary ({cols})
                VALUES ({vals})
                ON CONFLICT (user_id, year, month) DO UPDATE SET
                    dispensing_plus_daily_total = EXCLUDED.dispensing_plus_daily_total,
                    non_insurance_total = EXCLUDED.non_insurance_total,
                    grand_total = EXCLUDED.grand_total,
                    prev_month_diff = EXCLUDED.prev_month_diff
            """

    # 일반 쿼리 변환
    sql = sql.replace('?', '%s')
    sql = sql.replace("datetime('now', 'localtime')", "NOW()")

    # 3. INSERT INTO users (RETURNING id 로 lastrowid 지원)
    if 'INSERT INTO users' in sql and 'RETURNING id' not in sql:
        sql = sql.rstrip().rstrip(';') + ' RETURNING id'

    return sql


class PostgresCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor
        self.lastrowid = None

    def execute(self, sql, params=None):
        adapted_sql = adapt_sql_for_postgres(sql)
        self.cursor.execute(adapted_sql, params or ())
        if 'RETURNING id' in adapted_sql:
            row = self.cursor.fetchone()
            if row:
                self.lastrowid = row['id'] if isinstance(row, dict) else row[0]
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def close(self):
        self.cursor.close()

    @property
    def description(self):
        return self.cursor.description




def init_db():
    """데이터베이스 테이블 초기화 및 마이그레이션"""
    db_url = get_database_url()

    if db_url and psycopg2:
        init_postgres_db(db_url)
    else:
        init_sqlite_db()

    conn = get_db()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS business_schedules (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            settings_json TEXT NOT NULL
        )""")
        conn.commit()
    finally:
        conn.close()


def init_sqlite_db():
    """SQLite 로컬 데이터베이스 초기화"""
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            pharmacy_name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    ''')

    admin = cursor.execute('SELECT * FROM users WHERE username = ?', ('admin',)).fetchone()
    if not admin:
        default_hash = generate_password_hash('7581')
        cursor.execute('''
            INSERT INTO users (username, password_hash, pharmacy_name)
            VALUES (?, ?, ?)
        ''', ('admin', default_hash, '우리약국'))
        print("기본 계정 생성 완료")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_profit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 1,
            date TEXT NOT NULL,
            day_of_week TEXT NOT NULL,
            dispensing_fee INTEGER DEFAULT 0,
            daily_net_profit INTEGER DEFAULT 0,
            dispensing_plus_daily INTEGER DEFAULT 0,
            non_insurance_margin INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            memo TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, date)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS monthly_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 1,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            dispensing_plus_daily_total INTEGER DEFAULT 0,
            non_insurance_total INTEGER DEFAULT 0,
            grand_total INTEGER DEFAULT 0,
            prev_month_diff INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, year, month)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_calculator_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            dispensing_fee INTEGER DEFAULT 0,
            dispensing_cut INTEGER DEFAULT 0,
            non_insurance_fee INTEGER DEFAULT 0,
            non_insurance_margin INTEGER DEFAULT 0,
            otc_calc_mode TEXT DEFAULT 'direct',
            monthly_otc_net_profit INTEGER DEFAULT 8000000,
            daily_otc_sales INTEGER DEFAULT 0,
            work_days INTEGER DEFAULT 25,
            otc_margin_rate REAL DEFAULT 0.35,
            monthly_drug_cost INTEGER DEFAULT 0,
            pharmacist_salary INTEGER DEFAULT 0,
            staff_salary INTEGER DEFAULT 0,
            meal_cost INTEGER DEFAULT 0,
            rent_cost INTEGER DEFAULT 0,
            maintenance_cost INTEGER DEFAULT 0,
            supplies_cost INTEGER DEFAULT 0,
            software_cost INTEGER DEFAULT 0,
            barcode_cost INTEGER DEFAULT 0,
            electricity_cost INTEGER DEFAULT 0,
            communication_cost INTEGER DEFAULT 0,
            water_purifier_cost INTEGER DEFAULT 0,
            security_cost INTEGER DEFAULT 0,
            tax_accountant_cost INTEGER DEFAULT 0,
            association_fee INTEGER DEFAULT 0,
            card_fee_rate REAL DEFAULT 0.02,
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # SQLite 컬럼 안전 추가 (기존 테이블 호환)
    try:
        cursor.execute("ALTER TABLE user_calculator_settings ADD COLUMN otc_calc_mode TEXT DEFAULT 'direct'")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE user_calculator_settings ADD COLUMN monthly_otc_net_profit INTEGER DEFAULT 8000000")
    except Exception:
        pass

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_daily_user_date ON daily_profit(user_id, date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_daily_user_dow ON daily_profit(user_id, total, day_of_week)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_monthly_user_ym ON monthly_summary(user_id, year, month)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_monthly_user_gt ON monthly_summary(user_id, grand_total)')

    conn.commit()
    conn.close()
    print("로컬 SQLite DB 초기화 완료")


def init_postgres_db(db_url):
    """PostgreSQL 클라우드 DB 초기화 및 기존 데이터 자동 이관"""
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    # 1. users 테이블
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            pharmacy_name VARCHAR(100) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2. daily_profit 테이블
    cur.execute('''
        CREATE TABLE IF NOT EXISTS daily_profit (
            id SERIAL PRIMARY KEY,
            user_id INT REFERENCES users(id) ON DELETE CASCADE,
            date VARCHAR(10) NOT NULL,
            day_of_week VARCHAR(10) NOT NULL,
            dispensing_fee BIGINT DEFAULT 0,
            daily_net_profit BIGINT DEFAULT 0,
            dispensing_plus_daily BIGINT DEFAULT 0,
            non_insurance_margin BIGINT DEFAULT 0,
            total BIGINT DEFAULT 0,
            memo TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, date)
        )
    ''')

    # 3. monthly_summary 테이블
    cur.execute('''
        CREATE TABLE IF NOT EXISTS monthly_summary (
            id SERIAL PRIMARY KEY,
            user_id INT REFERENCES users(id) ON DELETE CASCADE,
            year INT NOT NULL,
            month INT NOT NULL,
            dispensing_plus_daily_total BIGINT DEFAULT 0,
            non_insurance_total BIGINT DEFAULT 0,
            grand_total BIGINT DEFAULT 0,
            prev_month_diff BIGINT DEFAULT 0,
            UNIQUE(user_id, year, month)
        )
    ''')

    # 4. user_calculator_settings 테이블
    cur.execute('''
        CREATE TABLE IF NOT EXISTS user_calculator_settings (
            id SERIAL PRIMARY KEY,
            user_id INT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            dispensing_fee BIGINT DEFAULT 0,
            dispensing_cut BIGINT DEFAULT 0,
            non_insurance_fee BIGINT DEFAULT 0,
            non_insurance_margin BIGINT DEFAULT 0,
            otc_calc_mode VARCHAR(20) DEFAULT 'direct',
            monthly_otc_net_profit BIGINT DEFAULT 8000000,
            daily_otc_sales BIGINT DEFAULT 0,
            work_days INT DEFAULT 25,
            otc_margin_rate NUMERIC(5,4) DEFAULT 0.35,
            monthly_drug_cost BIGINT DEFAULT 0,
            pharmacist_salary BIGINT DEFAULT 0,
            staff_salary BIGINT DEFAULT 0,
            meal_cost BIGINT DEFAULT 0,
            rent_cost BIGINT DEFAULT 0,
            maintenance_cost BIGINT DEFAULT 0,
            supplies_cost BIGINT DEFAULT 0,
            software_cost BIGINT DEFAULT 0,
            barcode_cost BIGINT DEFAULT 0,
            electricity_cost BIGINT DEFAULT 0,
            communication_cost BIGINT DEFAULT 0,
            water_purifier_cost BIGINT DEFAULT 0,
            security_cost BIGINT DEFAULT 0,
            tax_accountant_cost BIGINT DEFAULT 0,
            association_fee BIGINT DEFAULT 0,
            card_fee_rate NUMERIC(5,4) DEFAULT 0.02,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # PostgreSQL 컬럼 안전 추가 (기존 테이블 호환)
    try:
        cur.execute("ALTER TABLE user_calculator_settings ADD COLUMN IF NOT EXISTS otc_calc_mode VARCHAR(20) DEFAULT 'direct'")
        cur.execute("ALTER TABLE user_calculator_settings ADD COLUMN IF NOT EXISTS monthly_otc_net_profit BIGINT DEFAULT 8000000")
    except Exception:
        pass

    cur.execute('CREATE INDEX IF NOT EXISTS idx_pg_daily_user_date ON daily_profit(user_id, date)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_pg_daily_user_dow ON daily_profit(user_id, total, day_of_week)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_pg_monthly_user_ym ON monthly_summary(user_id, year, month)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_pg_monthly_user_gt ON monthly_summary(user_id, grand_total)')
    conn.commit()

    # 4. 기존 데이터가 비어있다면 로컬 sales.db 에서 Neon DB로 자동 마이그레이션
    cur.execute('SELECT COUNT(*) as cnt FROM daily_profit')
    cnt = cur.fetchone()['cnt']
    if cnt == 0 and os.path.exists(SQLITE_PATH):
        print("⚡ 클라우드 DB에 기존 데이터 자동 이관 시작...")
        try:
            s_conn = sqlite3.connect(SQLITE_PATH)
            s_conn.row_factory = sqlite3.Row

            # 유저 복사
            for u in s_conn.execute('SELECT * FROM users').fetchall():
                cur.execute('''
                    INSERT INTO users (id, username, password_hash, pharmacy_name, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (username) DO NOTHING
                ''', (u['id'], u['username'], u['password_hash'], u['pharmacy_name'], u['created_at']))

            # 일별 데이터 복사 (1,175건)
            dailies = s_conn.execute('SELECT * FROM daily_profit').fetchall()
            for d in dailies:
                cur.execute('''
                    INSERT INTO daily_profit
                    (id, user_id, date, day_of_week, dispensing_fee, daily_net_profit,
                     dispensing_plus_daily, non_insurance_margin, total, memo, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, date) DO NOTHING
                ''', (d['id'], d['user_id'], d['date'], d['day_of_week'], d['dispensing_fee'],
                      d['daily_net_profit'], d['dispensing_plus_daily'], d['non_insurance_margin'],
                      d['total'], d['memo'], d['created_at'], d['updated_at']))

            # 월별 요약 복사
            for m in s_conn.execute('SELECT * FROM monthly_summary').fetchall():
                cur.execute('''
                    INSERT INTO monthly_summary
                    (id, user_id, year, month, dispensing_plus_daily_total, non_insurance_total, grand_total, prev_month_diff)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, year, month) DO NOTHING
                ''', (m['id'], m['user_id'], m['year'], m['month'], m['dispensing_plus_daily_total'],
                      m['non_insurance_total'], m['grand_total'], m['prev_month_diff']))

            # 시퀀스(ID 자동증가 번호) 동기화
            cur.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), COALESCE(MAX(id), 1)) FROM users")
            cur.execute("SELECT setval(pg_get_serial_sequence('daily_profit', 'id'), COALESCE(MAX(id), 1)) FROM daily_profit")
            cur.execute("SELECT setval(pg_get_serial_sequence('monthly_summary', 'id'), COALESCE(MAX(id), 1)) FROM monthly_summary")

            conn.commit()
            s_conn.close()
            print(f"🎉 성공! 기존 {len(dailies)}일치 순익 데이터가 Neon 클라우드 DB로 영구 보존 이관되었습니다!")
        except Exception as e:
            print(f"데이터 자동 마이그레이션 중 오류: {e}")
            conn.rollback()

    cur.close()
    conn.close()
    print("PostgreSQL 클라우드 DB 준비 완료")


def recalc_monthly_summary(conn, user_id, year, month):
    """특정 사용자의 특정 월 합계를 재계산"""
    try:
        cursor = conn.cursor()
        date_prefix = f"{year}-{month:02d}"

        row = cursor.execute('''
            SELECT
                COALESCE(SUM(dispensing_plus_daily), 0) as dpd,
                COALESCE(SUM(non_insurance_margin), 0) as nim,
                COALESCE(SUM(total), 0) as gt
            FROM daily_profit
            WHERE user_id = ? AND date LIKE ?
        ''', (user_id, date_prefix + '%',)).fetchone()

        # 전월 합계
        prev_month = month - 1
        prev_year = year
        if prev_month == 0:
            prev_month = 12
            prev_year = year - 1

        prev_row = cursor.execute('''
            SELECT COALESCE(grand_total, 0) as gt
            FROM monthly_summary
            WHERE user_id = ? AND year = ? AND month = ?
        ''', (user_id, prev_year, prev_month)).fetchone()

        prev_total = int(prev_row['gt'] or 0) if prev_row else 0
        dpd = int(row['dpd'] or 0) if row else 0
        nim = int(row['nim'] or 0) if row else 0
        gt = int(row['gt'] or 0) if row else 0
        diff = gt - prev_total

        cursor.execute('''
            INSERT OR REPLACE INTO monthly_summary
            (user_id, year, month, dispensing_plus_daily_total, non_insurance_total, grand_total, prev_month_diff)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, year, month, dpd, nim, gt, diff))

        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        raise e


def get_all_users_stats():
    """모든 가입 회원 목록 및 각 약국의 통계 반환 (관리자 전용)"""
    conn = get_db()
    try:
        users = conn.execute('''
            SELECT 
                u.id, u.username, u.pharmacy_name, u.created_at,
                COUNT(d.id) as total_entries,
                MIN(d.date) as min_date,
                MAX(d.date) as max_date,
                COALESCE(SUM(d.total), 0) as total_profit
            FROM users u
            LEFT JOIN daily_profit d ON u.id = d.user_id
            GROUP BY u.id, u.username, u.pharmacy_name, u.created_at
            ORDER BY u.id ASC
        ''').fetchall()
    finally:
        conn.close()
    return users


def delete_user_and_data(user_id):
    """특정 회원의 모든 데이터와 계정 삭제 (관리자 전용)"""
    conn = get_db()
    try:
        conn.execute('DELETE FROM user_calculator_settings WHERE user_id = ?', (user_id,))
        conn.execute('DELETE FROM daily_profit WHERE user_id = ?', (user_id,))
        conn.execute('DELETE FROM monthly_summary WHERE user_id = ?', (user_id,))
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
    finally:
        conn.close()


if __name__ == '__main__':
    init_db()
