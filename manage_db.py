"""Explicit one-time schema setup; run only against a verified target database."""

import argparse
import os
from werkzeug.security import generate_password_hash

from database import get_db, init_db
from auth_config import require_bootstrap_password


def bootstrap_admin():
    conn = get_db()
    try:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", ('admin',)).fetchone()
        if not existing:
            conn.execute('INSERT INTO users (username, password_hash, pharmacy_name) VALUES (?, ?, ?)',
                         ('admin', generate_password_hash(require_bootstrap_password()), '우리약국'))
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rotate_admin_password():
    password = os.environ.get('ADMIN_NEW_PASSWORD', '')
    if len(password) < 16:
        raise RuntimeError('ADMIN_NEW_PASSWORD must be at least 16 characters')
    conn = get_db()
    try:
        admin = conn.execute("SELECT id FROM users WHERE username = ?", ('admin',)).fetchone()
        if not admin:
            raise RuntimeError('Admin account does not exist in the configured database')
        conn.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                     (generate_password_hash(password), admin['id']))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Prepare the configured pharmacy database')
    parser.add_argument('command', choices=['init', 'rotate-admin-password'])
    args = parser.parse_args()
    if args.command == 'init':
        init_db()
        bootstrap_admin()
    elif args.command == 'rotate-admin-password':
        rotate_admin_password()


if __name__ == '__main__':
    main()
