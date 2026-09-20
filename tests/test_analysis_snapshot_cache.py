"""Synthetic-only regression tests for dashboard/calendar read reuse."""
from datetime import date
from types import SimpleNamespace

import analysis_snapshot_cache


def make_app():
    state = {'calls': [], 'invalidations': [], 'today': date(2026, 9, 20), 'value': 10}

    def load(_conn, user_id, year, month):
        state['calls'].append((user_id, year, month))
        return [{'date': f'{year}-{month:02d}-01', 'total': state['value']}]

    def invalidate(user_id=None):
        state['invalidations'].append(user_id)

    app = SimpleNamespace(load_analysis_rows=load,
                          invalidate_user_cache=invalidate,
                          korea_today=lambda: state['today'])
    return app, state


def test_same_user_period_reuses_snapshot_without_exposing_mutable_cache():
    app, state = make_app()
    analysis_snapshot_cache.install(app)
    conn = object()
    first = app.load_analysis_rows(conn, 1, 2026, 9)
    first[0]['total'] = 999
    again = app.load_analysis_rows(conn, 1, 2026, 9)
    assert again == [{'date': '2026-09-01', 'total': 10}]
    assert state['calls'] == [(1, 2026, 9)]
    assert again is not first and again[0] is not first[0]


def test_user_and_period_are_isolated():
    app, state = make_app()
    analysis_snapshot_cache.install(app)
    conn = object()
    app.load_analysis_rows(conn, 1, 2026, 9)
    app.load_analysis_rows(conn, 2, 2026, 9)
    app.load_analysis_rows(conn, 1, 2026, 8)
    assert state['calls'] == [(1, 2026, 9), (2, 2026, 9), (1, 2026, 8)]


def test_save_invalidation_expires_only_affected_user():
    app, state = make_app()
    analysis_snapshot_cache.install(app)
    conn = object()
    app.load_analysis_rows(conn, 1, 2026, 9)
    app.load_analysis_rows(conn, 2, 2026, 9)
    state['value'] = 20
    app.invalidate_user_cache(1)
    assert app.load_analysis_rows(conn, 1, 2026, 9)[0]['total'] == 20
    assert app.load_analysis_rows(conn, 2, 2026, 9)[0]['total'] == 10
    assert state['calls'] == [(1, 2026, 9), (2, 2026, 9), (1, 2026, 9)]
    assert state['invalidations'] == [1]
    app.invalidate_user_cache()
    app.load_analysis_rows(conn, 1, 2026, 9)
    app.load_analysis_rows(conn, 2, 2026, 9)
    assert len(state['calls']) == 5
    assert state['invalidations'] == [1, None]


def test_new_korea_day_cannot_reuse_yesterday_snapshot():
    app, state = make_app()
    analysis_snapshot_cache.install(app)
    conn = object()
    app.load_analysis_rows(conn, 1, 2026, 9)
    state['today'] = date(2026, 9, 21)
    state['value'] = 30
    assert app.load_analysis_rows(conn, 1, 2026, 9)[0]['total'] == 30
    assert len(state['calls']) == 2


def test_snapshot_expires_and_cache_is_bounded(monkeypatch):
    app, state = make_app()
    ticks = [100.0]
    monkeypatch.setattr(analysis_snapshot_cache.time, 'monotonic', lambda: ticks[0])
    analysis_snapshot_cache.install(app, ttl_seconds=10, max_entries=2)
    conn = object()
    app.load_analysis_rows(conn, 1, 2026, 9)
    ticks[0] = 109.0
    app.load_analysis_rows(conn, 1, 2026, 9)
    assert len(state['calls']) == 1
    ticks[0] = 110.0
    app.load_analysis_rows(conn, 1, 2026, 9)
    assert len(state['calls']) == 2
    app.load_analysis_rows(conn, 2, 2026, 9)
    app.load_analysis_rows(conn, 3, 2026, 9)
    app.load_analysis_rows(conn, 1, 2026, 9)
    assert len(state['calls']) == 5


def test_invalidation_during_query_prevents_stale_repopulation():
    app, state = make_app()
    original = app.load_analysis_rows
    first = [True]

    def load(conn, user_id, year, month):
        result = original(conn, user_id, year, month)
        if first[0]:
            first[0] = False
            state['value'] = 25
            app.invalidate_user_cache(user_id)
        return result

    app.load_analysis_rows = load
    analysis_snapshot_cache.install(app)
    conn = object()
    assert app.load_analysis_rows(conn, 1, 2026, 9)[0]['total'] == 10
    assert app.load_analysis_rows(conn, 1, 2026, 9)[0]['total'] == 25
    assert len(state['calls']) == 2


def test_install_is_idempotent():
    app, state = make_app()
    analysis_snapshot_cache.install(app)
    analysis_snapshot_cache.install(app)
    app.load_analysis_rows(object(), 1, 2026, 9)
    app.load_analysis_rows(object(), 1, 2026, 9)
    assert len(state['calls']) == 1
