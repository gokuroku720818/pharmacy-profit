"""Require operator-provided session and initial account credentials."""

import os


def require_secret_key(environ=None):
    value = (os.environ if environ is None else environ).get('SECRET_KEY', '')
    if len(value) < 32:
        raise RuntimeError('SECRET_KEY must be set to a random value of at least 32 characters')
    return value


def require_bootstrap_password():
    value = os.environ.get('ADMIN_BOOTSTRAP_PASSWORD', '')
    if len(value) < 16:
        raise RuntimeError('A new admin account requires ADMIN_BOOTSTRAP_PASSWORD (at least 16 characters)')
    return value
