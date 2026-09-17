import sqlite3
import os
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'sales.db')


def get_db():
    """데이터베이스 연결 반환"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """데이터베이스 테이블 초기화 및 멀티 유저 스키마 마이그레이션"""
    conn = get_db()
    cursor = conn.cursor()

    # 1. 사용자(약국) 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            pharmacy_name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    ''')

    # 기본 사용자 (약사님 본인 계정) 생성
    admin = cursor.execute('SELECT * FROM users WHERE username = ?', ('admin',)).fetchone()
    if not admin:
        default_hash = generate_password_hash('7581')
        cursor.execute('''
            INSERT INTO users (username, password_hash, pharmacy_name)
            VALUES (?, ?, ?)
        ''', ('admin', default_hash, '우리약국'))
        print("기본 계정 생성 완료: 아이디=admin, 비번=7581")

    # 2. 일별 순익 테이블
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

    # 3. 월별 합계 테이블
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

    # 기존 컬럼에 user_id가 누락되었을 경우를 대비한 안전한 ALTER TABLE
    for tbl in ['daily_profit', 'monthly_summary']:
        cols = [col[1] for col in cursor.execute(f"PRAGMA table_info({tbl})").fetchall()]
        if 'user_id' not in cols:
            cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN user_id INTEGER DEFAULT 1")

    # 인덱스
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_daily_user_date ON daily_profit(user_id, date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_monthly_user_ym ON monthly_summary(user_id, year, month)')

    conn.commit()
    conn.close()
    print("멀티 유저 데이터베이스 초기화 완료")


def recalc_monthly_summary(conn, user_id, year, month):
    """특정 사용자의 특정 월 합계를 재계산"""
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

    prev_total = prev_row['gt'] if prev_row else 0
    diff = row['gt'] - prev_total

    cursor.execute('''
        INSERT OR REPLACE INTO monthly_summary
        (user_id, year, month, dispensing_plus_daily_total, non_insurance_total, grand_total, prev_month_diff)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, year, month, row['dpd'], row['nim'], row['gt'], diff))

def get_all_users_stats():
    """모든 가입 회원 목록 및 각 약국의 통계 반환 (관리자 전용)"""
    conn = get_db()
    users = conn.execute('''
        SELECT 
            u.id, u.username, u.pharmacy_name, u.created_at,
            COUNT(d.id) as total_entries,
            MIN(d.date) as min_date,
            MAX(d.date) as max_date,
            COALESCE(SUM(d.total), 0) as total_profit
        FROM users u
        LEFT JOIN daily_profit d ON u.id = d.user_id
        GROUP BY u.id
        ORDER BY u.id ASC
    ''').fetchall()
    conn.close()
    return users


def delete_user_and_data(user_id):
    """특정 회원의 모든 데이터와 계정 삭제 (관리자 전용)"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM daily_profit WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM monthly_summary WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()


if __name__ == '__main__':
    init_db()
