"""Concurrency regression: invalidated user must never retain an in-flight old read."""
import threading

from test_analysis import service


def test_invalidation_finishes_after_inflight_cache_miss_without_stale_repopulation(service):
    from cache_coherence import install
    install(service)
    service.invalidate_user_cache(1)
    entered = threading.Event()
    release = threading.Event()
    invalidated = threading.Event()
    failures = []
    state = {'amount': 100, 'reads': 0}

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Database:
        def execute(self, statement, params):
            if 'FROM monthly_summary' not in statement:
                return Result([])  # no extra-profit entries
            state['reads'] += 1
            captured = state['amount']
            if state['reads'] == 1:
                entered.set()
                if not release.wait(5):
                    raise AssertionError('timed out waiting for test read release')
            return Result([{'year': 2026, 'month': 8,
                            'dispensing_plus_daily_total': captured,
                            'non_insurance_total': 0,
                            'grand_total': captured,
                            'prev_month_diff': captured}])

    database = Database()

    def reader():
        try:
            service.get_cached_monthly_summary(database, 1)
        except Exception as exc:
            failures.append(exc)

    def invalidate():
        try:
            service.invalidate_user_cache(1)
        except Exception as exc:
            failures.append(exc)
        finally:
            invalidated.set()

    old_read = threading.Thread(target=reader)
    old_read.start()
    assert entered.wait(5)
    state['amount'] = 200  # replacement transaction has committed
    invalidator = threading.Thread(target=invalidate)
    invalidator.start()
    # The invalidation cannot return while the earlier read may republish data.
    assert not invalidated.wait(0.1)
    release.set()
    old_read.join(5)
    invalidator.join(5)
    assert not old_read.is_alive() and not invalidator.is_alive()
    assert not failures, failures
    newest = service.get_cached_monthly_summary(database, 1)
    assert newest[0]['grand_total'] == 200
    assert state['reads'] == 2


def test_installation_is_idempotent(service):
    from cache_coherence import install
    install(service)
    original = service.get_cached_monthly_summary
    install(service)
    assert service.get_cached_monthly_summary is original
