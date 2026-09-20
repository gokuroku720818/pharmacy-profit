"""Single-process threaded deployment keeps existing per-user caches consistent.

Gunicorn loads this file automatically from the working directory even when
Render's start command supplies its own worker/thread arguments.
"""
worker_class = 'gthread'
threads = 4
keepalive = 5


def post_worker_init(worker):
    """Attach per-request timing after the Flask app is loaded, before traffic."""
    from importlib import import_module
    from runtime_metrics import install

    install(import_module('app'))
