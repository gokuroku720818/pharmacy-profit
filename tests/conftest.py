"""Isolated credentials for synthetic test databases only."""
import os

os.environ.setdefault('SECRET_KEY', 'test-only-secret-for-disposable-sqlite-data')
os.environ.setdefault('ADMIN_BOOTSTRAP_PASSWORD', 'test-only-admin-bootstrap-password')
