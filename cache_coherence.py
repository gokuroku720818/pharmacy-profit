"""Keep an in-flight user cache read from repopulating a completed invalidation.

The existing cache loaders publish their result after releasing _CACHE_LOCK. Merely
popping the bucket on a write is insufficient when a loader began earlier.
Serialize each user's financial display loaders with its invalidation in the
single-worker Gunicorn process; unrelated users retain independent locks.
No database schema, financial calculations, or standalone calculator changes.
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
    """Wrap existing display cache entrypoints once, before Gunicorn traffic."""
    if getattr(module, '_cache_coherence_installed', False):
        return
    registry_guard = Lock()
    locks = {}
    admin_guard = RLock()

    def for_user(user_id):
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

    original_invalidate_user = module.invalidate_user_cache

    @wraps(original_invalidate_user)
    def invalidate_user_cache(user_id=None):
        if user_id is not None:
            with for_user(user_id), admin_guard:
                return original_invalidate_user(user_id)

        # Wait for all existing readers before clearing every user's cache.
        # New readers cannot register while the all-users purge is in progress.
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
