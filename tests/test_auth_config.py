import pytest
from werkzeug.security import check_password_hash

import database


def test_secret_requires_explicit_long_value():
    from auth_config import require_secret_key
    with pytest.raises(RuntimeError):
        require_secret_key({})
    with pytest.raises(RuntimeError):
        require_secret_key({'SECRET_KEY': 'short'})
    assert require_secret_key({'SECRET_KEY': 'a' * 32}) == 'a' * 32


def test_new_admin_requires_bootstrap_password_but_existing_admin_survives(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'SQLITE_PATH', str(tmp_path / 'sales.db'))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('ADMIN_BOOTSTRAP_PASSWORD', raising=False)
    with pytest.raises(RuntimeError):
        database.init_sqlite_db()
    monkeypatch.setenv('ADMIN_BOOTSTRAP_PASSWORD', 'test-bootstrap-password-strong')
    database.init_sqlite_db()
    conn = database.get_db()
    try:
        first = conn.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()['password_hash']
        assert check_password_hash(first, 'test-bootstrap-password-strong')
    finally:
        conn.close()
    monkeypatch.delenv('ADMIN_BOOTSTRAP_PASSWORD')
    database.init_sqlite_db()
    conn = database.get_db()
    try:
        assert conn.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()['password_hash'] == first
    finally:
        conn.close()


def test_existing_admin_password_rotation_is_explicit(tmp_path, monkeypatch):
    from manage_db import rotate_admin_password
    monkeypatch.setattr(database, 'SQLITE_PATH', str(tmp_path / 'sales.db'))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_BOOTSTRAP_PASSWORD', 'test-bootstrap-password-strong')
    database.init_db()
    monkeypatch.setenv('ADMIN_NEW_PASSWORD', 'new-test-only-admin-password')
    rotate_admin_password()
    conn = database.get_db()
    try:
        stored = conn.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()['password_hash']
        assert check_password_hash(stored, 'new-test-only-admin-password')
        assert not check_password_hash(stored, 'test-bootstrap-password-strong')
    finally:
        conn.close()


def test_explicit_bootstrap_provisions_only_missing_admin(tmp_path, monkeypatch):
    from manage_db import bootstrap_admin
    monkeypatch.setattr(database, 'SQLITE_PATH', str(tmp_path / 'sales.db'))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_BOOTSTRAP_PASSWORD', 'test-bootstrap-password-strong')
    database.init_db()
    conn = database.get_db()
    conn.execute("DELETE FROM users WHERE username='admin'")
    conn.commit()
    conn.close()
    bootstrap_admin()
    bootstrap_admin()
    conn = database.get_db()
    try:
        rows = conn.execute("SELECT password_hash FROM users WHERE username='admin'").fetchall()
        assert len(rows) == 1
        assert check_password_hash(rows[0]['password_hash'], 'test-bootstrap-password-strong')
    finally:
        conn.close()
