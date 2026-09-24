"""Supply a stable Flask session key and explicit initial account credentials."""

import hashlib
import os


def require_secret_key(environ=None):
    settings = os.environ if environ is None else environ
    value = settings.get('SECRET_KEY', '')
    if value:
        if len(value) < 32:
            raise RuntimeError('SECRET_KEY must be at least 32 characters')
        return value
    database_url = settings.get('DATABASE_URL', '')
    if database_url:
        # Derive a stable key from the existing private credential.
        return hashlib.sha256(('pharmacy-profit/session/v1:' + database_url).encode()).hexdigest()
    raise RuntimeError('Set SECRET_KEY (32+ characters) for a local database')


def require_bootstrap_password():
    value = os.environ.get('ADMIN_BOOTSTRAP_PASSWORD', '')
    if len(value) < 16:
        raise RuntimeError('A new admin account requires ADMIN_BOOTSTRAP_PASSWORD (at least 16 characters)')
    return value
