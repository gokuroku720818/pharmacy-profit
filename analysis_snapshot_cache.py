"""Short-lived, user-scoped reuse for dashboard/calendar daily-analysis reads.

The existing app has one Gunicorn worker and invalidates its per-user cache
when data is saved. This adapter shares only the read-only analysis snapshot;
it does not cache rendered HTML, sessions, calculations or calculator settings.
"""

from collections import OrderedDict
from functools import wraps
import threading
import time


def install(app_module, ttl_seconds=10, max_entries=64):
    """Wrap an existing analysis loader and its save-invalidation hook once."""
    if getattr(app_module, '_analysis_snapshot_cache_installed', False):
        return
    original_load = app_module.load_analysis_rows
    original_invalidate = app_module.invalidate_user_cache
    lock = threading.Lock()
    entries = OrderedDict()
    generation = [0]

    @wraps(original_load)
    def cached_load(conn, user_id, year, month):
        # Forecast and relative-week analysis change when the KST date changes.
        key = (user_id, year, month, app_module.korea_today().isoformat())
        with lock:
            now = time.monotonic()
            entry = entries.get(key)
            if entry is not None and now < entry[0]:
                entries.move_to_end(key)
                return [dict(row) for row in entry[1]]
            revision = generation[0]

        # Never hold the global cache lock while PostgreSQL/SQLite is queried.
        snapshot = [dict(row) for row in original_load(conn, user_id, year, month)]
        with lock:
            # If any save completed during the query, do not reinsert stale data.
            if revision == generation[0] and ttl_seconds > 0 and max_entries > 0:
                entries[key] = (time.monotonic() + ttl_seconds, snapshot)
                entries.move_to_end(key)
                while len(entries) > max_entries:
                    entries.popitem(last=False)
        return [dict(row) for row in snapshot]

    @wraps(original_invalidate)
    def invalidating(user_id=None):
        with lock:
            generation[0] += 1
            if user_id is None:
                entries.clear()
            else:
                for key in list(entries):
                    if key[0] == user_id:
                        del entries[key]
        return original_invalidate(user_id)

    app_module.load_analysis_rows = cached_load
    app_module.invalidate_user_cache = invalidating
    app_module._analysis_snapshot_cache_installed = True
