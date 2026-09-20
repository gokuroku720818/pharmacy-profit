"""Single-process threaded deployment keeps existing per-user caches consistent.

Gunicorn loads this file automatically from the working directory even when
Render's start command supplies its own worker/thread arguments.
"""
worker_class = 'gthread'
threads = 4
keepalive = 5


def post_worker_init(worker):
    """Enable pool protection, cache coherence and metrics before traffic."""
    from importlib import import_module
    from pool_guard import install as install_pool_guard
    from runtime_metrics import install as install_metrics
    from cache_coherence import install as install_cache_coherence

    app_module = import_module('app')
    install_pool_guard(import_module('database'), app_module.app)
    install_cache_coherence(app_module)
    install_metrics(app_module)
