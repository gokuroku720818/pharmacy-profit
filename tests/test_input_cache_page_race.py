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
