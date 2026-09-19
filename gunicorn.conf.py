"""Keep slow database/file requests from blocking every other visitor.

Gunicorn automatically reads this file, including existing Render services whose
start command is still `gunicorn app:app`. Leave worker count at its default to
avoid duplicating the in-process cache and increasing memory on small instances.
"""
worker_class = 'gthread'
threads = 4
