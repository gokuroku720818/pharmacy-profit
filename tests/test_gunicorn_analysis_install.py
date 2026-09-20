"""Verify the actual Gunicorn lifecycle installs the production adapter."""
from datetime import date
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pool_guard
import runtime_metrics


def test_worker_init_reuses_daily_analysis_query(monkeypatch):
    calls = []

    def loader(_conn, user_id, year, month):
        calls.append((user_id, year, month))
        return [{'date': '2026-09-20', 'total': 1}]

    fake_app_module = SimpleNamespace(
        app=object(),
        load_analysis_rows=loader,
        invalidate_user_cache=lambda user_id=None: None,
        korea_today=lambda: date(2026, 9, 20),
    )
    monkeypatch.setitem(sys.modules, 'app', fake_app_module)
    monkeypatch.setitem(sys.modules, 'database', SimpleNamespace())
    monkeypatch.setattr(pool_guard, 'install', lambda *_args: None)
    monkeypatch.setattr(runtime_metrics, 'install', lambda *_args: None)
    config = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'gunicorn.conf.py'))
    config['post_worker_init'](None)

    fake_app_module.load_analysis_rows(object(), 1, 2026, 9)
    fake_app_module.load_analysis_rows(object(), 1, 2026, 9)
    assert calls == [(1, 2026, 9)]
