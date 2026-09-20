"""Single-process threaded deployment keeps existing per-user caches consistent.

Gunicorn loads this file automatically from the working directory even when
Render's start command supplies its own worker/thread arguments.
"""
worker_class = 'gthread'
threads = 4
keepalive = 5


def post_worker_init(worker):
    """Enable pool protection, request profiling and bounded analysis reuse."""
    from importlib import import_module
    from pool_guard import install as install_pool_guard
    from runtime_metrics import install as install_metrics
    from analysis_snapshot_cache import install as install_analysis_cache

    app_module = import_module('app')
    install_pool_guard(import_module('database'), app_module.app)
    install_metrics(app_module)
    install_analysis_cache(app_module)
