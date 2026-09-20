"""Small, opt-in request profiler for the existing Flask/Gunicorn application.

Never logs URLs, query text/parameters, cookies, usernames, amounts or passwords.
Measurements are server-side timings, not browser rendering or cold-start timings.
"""

from dataclasses import dataclass
from functools import wraps
import os
import time


@dataclass
class Metrics:
    acquire_ms: float = 0.0
    sql_ms: float = 0.0
    fetch_ms: float = 0.0
    close_ms: float = 0.0
    render_ms: float = 0.0
    connections: int = 0
    queries: int = 0


def _milliseconds(start):
    return (time.perf_counter() - start) * 1000


class MeasuredCursor:
    def __init__(self, raw, data):
        self.raw = raw
        self.data = data

    def execute(self, sql, params=None):
        start = time.perf_counter()
        try:
            if params is None:
                self.raw.execute(sql)
            else:
                self.raw.execute(sql, params)
            return self
        finally:
            self.data.queries += 1
            self.data.sql_ms += _milliseconds(start)

    def fetchone(self):
        start = time.perf_counter()
        try:
            return self.raw.fetchone()
        finally:
            self.data.fetch_ms += _milliseconds(start)

    def fetchall(self):
        start = time.perf_counter()
        try:
            return self.raw.fetchall()
        finally:
            self.data.fetch_ms += _milliseconds(start)

    def fetchmany(self, *args, **kwargs):
        start = time.perf_counter()
        try:
            return self.raw.fetchmany(*args, **kwargs)
        finally:
            self.data.fetch_ms += _milliseconds(start)

    def __iter__(self):
        return iter(self.raw)

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def close(self):
        return self.raw.close()


class MeasuredConnection:
    def __init__(self, raw, data):
        self.raw = raw
        self.data = data
        self._closed = False

    def cursor(self, *args, **kwargs):
        return MeasuredCursor(self.raw.cursor(*args, **kwargs), self.data)

    def execute(self, sql, params=None):
        start = time.perf_counter()
        try:
            if params is None:
                cursor = self.raw.execute(sql)
            else:
                cursor = self.raw.execute(sql, params)
            return MeasuredCursor(cursor, self.data)
        finally:
            self.data.queries += 1
            self.data.sql_ms += _milliseconds(start)

    def close(self):
        if self._closed:
            return
        # Underlying PostgreSQL wrapper is already double-close safe.
        self._closed = True
        start = time.perf_counter()
        try:
            return self.raw.close()
        finally:
            self.data.close_ms += _milliseconds(start)

    def __enter__(self):
        if hasattr(self.raw, '__enter__'):
            self.raw.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        if hasattr(self.raw, '__exit__'):
            result = self.raw.__exit__(exc_type, exc, traceback)
            # psycopg2 wrapper closes on exit; sqlite3's manager does not.
            self._closed = bool(getattr(self.raw, '_closed', False))
            return result
        self.close()
        return False

    def __getattr__(self, name):
        return getattr(self.raw, name)


def format_metrics(endpoint, status, total_ms, data):
    """Only aggregate numbers and a Flask endpoint name enter the log line."""
    return (
        'PERF endpoint=%s status=%s total_ms=%.1f acquire_ms=%.1f '
        'sql_ms=%.1f fetch_ms=%.1f close_ms=%.1f render_ms=%.1f '
        'connections=%s queries=%s'
        % (endpoint, status, total_ms, data.acquire_ms, data.sql_ms,
           data.fetch_ms, data.close_ms, data.render_ms, data.connections,
           data.queries)
    )


def install(app_module):
    """Install once after Gunicorn loads app; no effect on the dev/test import path."""
    from flask import g, has_request_context, request

    flask_app = app_module.app
    if getattr(flask_app, '_runtime_metrics_installed', False):
        return
    flask_app._runtime_metrics_installed = True
    original_get_db = app_module.get_db
    original_render = app_module.render_template

    @wraps(original_get_db)
    def measured_get_db(*args, **kwargs):
        if not has_request_context() or not hasattr(g, '_perf_data'):
            return original_get_db(*args, **kwargs)
        data = g._perf_data
        start = time.perf_counter()
        try:
            conn = original_get_db(*args, **kwargs)
        finally:
            data.acquire_ms += _milliseconds(start)
        data.connections += 1
        return MeasuredConnection(conn, data)

    @wraps(original_render)
    def measured_render(*args, **kwargs):
        if not has_request_context() or not hasattr(g, '_perf_data'):
            return original_render(*args, **kwargs)
        start = time.perf_counter()
        try:
            return original_render(*args, **kwargs)
        finally:
            g._perf_data.render_ms += _milliseconds(start)

    app_module.get_db = measured_get_db
    app_module.render_template = measured_render
    # Functions in profit_analysis import get_db independently.
    import profit_analysis
    profit_analysis.get_db = measured_get_db

    @flask_app.before_request
    def _perf_start():
        g._perf_data = Metrics()
        g._perf_start = time.perf_counter()

    @flask_app.after_request
    def _perf_finish(response):
        data = g._perf_data
        elapsed = _milliseconds(g._perf_start)
        if os.getenv('PERF_SERVER_TIMING') == '1':
            response.headers['Server-Timing'] = (
                'app;dur=%.1f, db;dur=%.1f, sql;dur=%.1f, render;dur=%.1f'
                % (elapsed, data.acquire_ms, data.sql_ms + data.fetch_ms, data.render_ms)
            )
        try:
            threshold = max(0, int(os.getenv('PERF_LOG_SLOW_MS', '300')))
        except ValueError:
            threshold = 300
        if os.getenv('PERF_LOG_ALL') == '1' or elapsed >= threshold or response.status_code >= 500:
            # Endpoint identifiers, not URL/query strings; no customer data in logs.
            print(format_metrics(request.endpoint or 'unmatched', response.status_code,
                                 elapsed, data), flush=True)
        return response
