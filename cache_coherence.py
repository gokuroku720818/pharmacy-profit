"""Keep in-flight financial reads from repopulating invalidated user caches.

The existing cache loaders publish results after releasing _CACHE_LOCK. Merely
popping the bucket on a write is insufficient when a loader began earlier.
Serialize user-scoped financial readers AND the dashboard/input GET paths that
publish input_cache in one threaded Gunicorn worker. Unrelated users retain
independent locks. No financial calculations or standalone calculator changes.
Cross-process writes need a shared version strategy if worker count is raised.
"""
from functools import wraps
from inspect import signature
from threading import Lock, RLock


_USER_READERS = (
    'get_cached_monthly_summary',
    'get_cached_current_month_dailies',
    'get_cached_dow_avg',
)


def install(module):
    """Wrap cache entrypoints and financial page reads once, before traffic."""
    if getattr(module, '_cache_coherence_installed', False):
        return
    registry_guard = Lock()
    locks = {}
    admin_guard = RLock()

    def for_user(user_id):
        # An already-held user RLock must never need registry_guard again:
        # an all-user purge acquires registry_guard before waiting for user
        # locks, and nested page/helper calls must not invert that order.
        existing = locks.get(user_id)
        if existing is not None:
            return existing
        with registry_guard:
            return locks.setdefault(user_id, RLock())

    for name in _USER_READERS:
        original = getattr(module, name)
        params = signature(original)

        def make_reader(fn, signature_):
            @wraps(fn)
            def synchronized(*args, **kwargs):
                user_id = signature_.bind_partial(*args, **kwargs).arguments.get('user_id')
                with for_user(user_id):
                    return fn(*args, **kwargs)
            return synchronized

        setattr(module, name, make_reader(original, params))

    # The dashboard and /input GET each publish an input_cache entry AFTER
    # several queries. Without covering the whole request, a concurrent
    # committed write can invalidate and then have that stale entry restored.
    from flask import request, session
    for endpoint in ('dashboard', 'input_sales'):
        original_view = module.app.view_functions[endpoint]

        def make_page(fn, endpoint_name):
            @wraps(fn)
            def synchronized_page(*args, **kwargs):
                if request.method != 'GET' or 'user_id' not in session:
                    return fn(*args, **kwargs)
                user_id = session['user_id']
                with for_user(user_id):
                    return fn(*args, **kwargs)
            return synchronized_page

        module.app.view_functions[endpoint] = make_page(original_view, endpoint)

    original_invalidate_user = module.invalidate_user_cache

    @wraps(original_invalidate_user)
    def invalidate_user_cache(user_id=None):
        if user_id is not None:
            with for_user(user_id), admin_guard:
                return original_invalidate_user(user_id)

        # Wait for every registered reader. Newly registered users cannot
        # enter while the registry is held. Nested reads use the fast path.
        with registry_guard:
            active = list(locks.values())
            for lock in active:
                lock.acquire()
            try:
                with admin_guard:
                    return original_invalidate_user(None)
            finally:
                for lock in reversed(active):
                    lock.release()

    module.invalidate_user_cache = invalidate_user_cache
    original_admin_read = module.get_cached_admin_stats
    original_admin_invalidate = module.invalidate_admin_cache

    @wraps(original_admin_read)
    def get_cached_admin_stats(*args, **kwargs):
        with admin_guard:
            return original_admin_read(*args, **kwargs)

    @wraps(original_admin_invalidate)
    def invalidate_admin_cache(*args, **kwargs):
        with admin_guard:
            return original_admin_invalidate(*args, **kwargs)

    module.get_cached_admin_stats = get_cached_admin_stats
    module.invalidate_admin_cache = invalidate_admin_cache
    module._cache_coherence_installed = True
