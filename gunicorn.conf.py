"""Single-process threaded deployment keeps existing per-user caches consistent.

Gunicorn loads this file automatically from the working directory. The
Procfile and any Render custom start command must also use one worker;
explicit CLI --workers overrides this default. Four threads remain enabled.
"""
import os

workers = 1
worker_class = 'gthread'
threads = 4
keepalive = 65
timeout = 120
max_requests = 1000
max_requests_jitter = 50
worker_tmp_dir = '/dev/shm' if os.path.exists('/dev/shm') else None


def post_worker_init(worker):
    """Install daily-only accounting and labels before cache locks and metrics."""
    from importlib import import_module
    app_module = import_module('app')
    if hasattr(app_module, 'bootstrap_app_extensions'):
        app_module.bootstrap_app_extensions(app_module)
    else:
        from pool_guard import install as install_pool_guard
        from runtime_metrics import install as install_metrics
        from cache_coherence import install as install_cache_coherence
        from daily_only import install as install_daily_only
        from daily_labels import install as install_daily_labels
        from response_security import install as install_response_security

        install_response_security(app_module.app)
        install_pool_guard(import_module('database'), app_module.app)
        install_daily_only(app_module)
        install_daily_labels(app_module)
        install_cache_coherence(app_module)
        install_metrics(app_module)

