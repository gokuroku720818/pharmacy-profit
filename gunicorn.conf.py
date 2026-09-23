"""Single-process threaded deployment keeps existing per-user caches consistent.

Gunicorn loads this file automatically from the working directory. The
Procfile and any Render custom start command must also use one worker;
explicit CLI --workers overrides this default. Four threads remain enabled.
"""
workers = 1
worker_class = 'gthread'
threads = 4
keepalive = 65


def post_worker_init(worker):
    """Install accounting protections first, then optional independent work home."""
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

    try:
        from work_home import install as install_work_home
        install_work_home(app_module)
    except Exception:
        # Do not make an optional work dashboard failure take down accounting.
        worker.log.exception('Work homepage unavailable; keeping original dashboard')
