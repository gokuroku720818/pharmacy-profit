"""Request-scoped template helpers for reconciled three-way and miscellaneous profit.

Read-only display aggregates share app.py's user cache. Every existing write path
invalidates that bucket; an in-flight reader cannot repopulate a cleared bucket.
Standalone Flask test apps keep uncached, isolated behavior.
"""
import sys
import time

from flask import current_app, g, session
from database import get_db
from extra_profit import monthly_totals
from profit_components import attach_components, load_monthly_components, total_components


def _cache_module():
    """Use the owner app's existing invalidation mechanism, never a second cache."""
    module = sys.modules.get('app')
    if module is not None and getattr(module, 'app', None) is current_app._get_current_object():
        return module
    return None


def _cached_source(key, loader):
    """Cache a user-scoped aggregate, never publish a read after invalidation."""
    user_id = session['user_id']
    module = _cache_module()
    bucket = None
    if module is not None:
        with module._CACHE_LOCK:
            bucket = module._USER_CACHE.setdefault(user_id, {})
            entry = bucket.get(key)
            if entry and time.time() - entry['ts'] < module._CACHE_TTL:
                return entry['data']

    conn = get_db()
    try:
        result = loader(conn, user_id)
    finally:
        conn.close()

    if module is not None:
        with module._CACHE_LOCK:
            if module._USER_CACHE.get(user_id) is bucket:
                bucket[key] = {'ts': time.time(), 'data': result}
    return result


def _monthly_extras():
    """The already-cached monthly summary contains the exact same extras."""
    module = _cache_module()
    if module is not None:
        with module._CACHE_LOCK:
            bucket = module._USER_CACHE.get(session['user_id']) or {}
            summary = bucket.get('monthly_summary')
            if summary and time.time() - summary['ts'] < module._CACHE_TTL:
                return {
                    (int(row['year']), int(row['month'])): int(row.get('extra_profit_total') or 0)
                    for row in summary['data']
                }
    return _cached_source('display_extras', monthly_totals)


def install(app):
    """Expose narrow template helpers without changing stored monetary formulas."""
    if app.extensions.get('three_profit_display_installed'):
        return

    @app.context_processor
    def inject_three_profit_helpers():
        if 'user_id' not in session:
            return {}

        def grouped():
            if not hasattr(g, '_three_profit_grouped'):
                g._three_profit_grouped = _cached_source('display_components', load_monthly_components)
            return g._three_profit_grouped

        def extras():
            if not hasattr(g, '_extra_profit_grouped'):
                g._extra_profit_grouped = _monthly_extras()
            return g._extra_profit_grouped

        def annotate(rows):
            extra = extras()
            return [dict(row, extra_profit_total=extra.get((int(row['year']), int(row['month'])), 0))
                    for row in rows]

        def components_for(year, month, combined, non_insurance, grand):
            row = {'year': int(year), 'month': int(month),
                   'dispensing_plus_daily_total': combined,
                   'non_insurance_total': non_insurance,
                   'grand_total': grand}
            return attach_components(annotate([row]), grouped())[0]

        def period_components(year, rows, months=None):
            subset = [dict(row, year=int(year)) for row in rows
                      if months is None or int(row['month']) in months]
            return total_components(attach_components(annotate(subset), grouped()))

        def three_way_series(labels, combined, non_insurance, totals):
            series = {'labels': list(labels), 'dispensing_fee': [],
                      'daily_net_profit': [], 'non_insurance_margin': [],
                      'extra_profit_total': [], 'totals': list(totals)}
            if not (len(labels) == len(combined) == len(non_insurance) == len(totals)):
                raise ValueError('Monthly chart series must have equal lengths')
            for label, dpd, nim, grand in zip(labels, combined, non_insurance, totals):
                year_text, month_text = str(label).split('.')
                split = components_for(int(year_text), int(month_text), dpd, nim, grand)
                for field in ('dispensing_fee', 'daily_net_profit', 'non_insurance_margin'):
                    series[field].append(split[field])
                series['extra_profit_total'].append(split['extra_profit_total'])
            return series

        return {'components_for': components_for,
                'period_components': period_components,
                'three_way_series': three_way_series}

    app.extensions['three_profit_display_installed'] = True
