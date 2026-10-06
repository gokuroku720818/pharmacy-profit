"""A read started before a write must not restore input cache after invalidation."""
import threading

from test_analysis import add, client_for, conn, service


def _assert_page_cannot_repopulate_after_invalidation(service, monkeypatch, path, hook):
    from cache_coherence import install
    install(service)
    client = client_for(service)
    entered = threading.Event()
    release = threading.Event()
    invalidated = threading.Event()
    failures = []
    statuses = []
    original = getattr(service, hook)

    def slow_read(*args, **kwargs):
        entered.set()
        if not release.wait(8):
            raise AssertionError('timed out waiting for in-flight financial page')
        return original(*args, **kwargs)

    monkeypatch.setattr(service, hook, slow_read)

    def page_reader():
        try:
            statuses.append(client.get(path).status_code)
        except Exception as exc:
            failures.append(exc)

    def invalidator():
        try:
            service.invalidate_user_cache(1)
        except Exception as exc:
            failures.append(exc)
        finally:
            invalidated.set()

    page = threading.Thread(target=page_reader)
    purge = threading.Thread(target=invalidator)
    page.start()
    try:
        assert entered.wait(5), f'{path} never reached the uncached read'
        purge.start()
        # Invalidation cannot complete while an old page can publish input_cache.
        assert not invalidated.wait(0.2), f'{path} invalidated before old cache publication'
    finally:
        release.set()
        page.join(8)
        if purge.ident is not None:
            purge.join(8)
    assert not page.is_alive() and not purge.is_alive()
    assert not failures, failures
    assert statuses == [200]
    with service._CACHE_LOCK:
        assert 'input_cache' not in service._USER_CACHE.get(1, {})
        assert 'dashboard_context' not in service._USER_CACHE.get(1, {})


def test_dashboard_inflight_read_cannot_republish_stale_input_cache(service, conn, monkeypatch):
    add(conn, '2026-09-01')
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    service.invalidate_user_cache(1)
    _assert_page_cannot_repopulate_after_invalidation(
        service, monkeypatch, '/', 'get_recent_weeks_profit_stats')


def test_input_inflight_read_cannot_republish_stale_input_cache(service, conn, monkeypatch):
    add(conn, '2026-09-01')
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    service.invalidate_user_cache(1)
    _assert_page_cannot_repopulate_after_invalidation(
        service, monkeypatch, '/input', 'get_yoy_day_comparison')


def test_all_user_purge_does_not_deadlock_nested_dashboard_cache_read(service, conn, monkeypatch):
    from cache_coherence import install
    install(service)
    add(conn, '2026-09-01')
    service.recalc_monthly_summary(conn, 1, 2026, 9)
    service.invalidate_user_cache(1)
    entered = threading.Event()
    release = threading.Event()
    purging = threading.Event()
    invalidated = threading.Event()
    failures = []
    result = []
    original = service.get_cached_monthly_summary

    def paused_summary(*args, **kwargs):
        entered.set()  # Outer dashboard view already holds this user's RLock.
        if not release.wait(8):
            raise AssertionError('timed out releasing nested financial cache read')
        return original(*args, **kwargs)  # Needs a reentrant user lock, not registry_guard.

    monkeypatch.setattr(service, 'get_cached_monthly_summary', paused_summary)
    client = client_for(service)

    def read():
        try:
            result.append(client.get('/').status_code)
        except Exception as exc:
            failures.append(exc)

    def purge():
        purging.set()
        try:
            service.invalidate_user_cache()
        except Exception as exc:
            failures.append(exc)
        finally:
            invalidated.set()

    page_thread = threading.Thread(target=read, daemon=True)
    purge_thread = threading.Thread(target=purge, daemon=True)
    page_thread.start()
    try:
        assert entered.wait(5)
        purge_thread.start()
        assert purging.wait(5)
        assert not invalidated.wait(0.2)
    finally:
        release.set()
        page_thread.join(8)
        if purge_thread.ident is not None:
            purge_thread.join(8)
    assert not page_thread.is_alive() and not purge_thread.is_alive(), 'global cache purge deadlocked'
    assert not failures, failures
    assert result == [200]
    with service._CACHE_LOCK:
        assert not service._USER_CACHE


def test_other_user_input_page_is_not_blocked_by_one_slow_dashboard(service, monkeypatch):
    from cache_coherence import install
    install(service)
    started = threading.Event()
    release = threading.Event()
    original = service.get_recent_weeks_profit_stats
    failures = []
    status = []

    def slow_first_user(conn, user_id, *args, **kwargs):
        if user_id == 1:
            started.set()
            if not release.wait(8):
                raise AssertionError('timed out releasing user 1')
        return original(conn, user_id, *args, **kwargs)

    monkeypatch.setattr(service, 'get_recent_weeks_profit_stats', slow_first_user)

    def read_first():
        try:
            status.append(client_for(service, user=1).get('/').status_code)
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=read_first)
    thread.start()
    try:
        assert started.wait(5)
        # User 2 should not share the first user's page/cache lock.
        assert client_for(service, user=2).get('/input').status_code == 200
    finally:
        release.set()
        thread.join(8)
    assert not thread.is_alive()
    assert not failures, failures
    assert status == [200]
