"""Request-scoped template helpers for reconciled three-way and miscellaneous profit.

Read-only display aggregates share app.py's user cache. Every existing write path
invalidates that bucket; an in-flight reader cannot repopulate a cleared bucket.
Standalone Flask test apps keep uncached, isolated behavior.
"""
import sys
import time

from flask import current_app, flash, g, make_response, redirect, render_template_string, request, session, url_for
from database import get_db
from extra_profit import merge_monthly_summaries, monthly_totals
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
            return attach_components(annotate([row]), grouped(), present_source=True)[0]

        def period_components(year, rows, months=None):
            subset = [dict(row, year=int(year)) for row in rows
                      if months is None or int(row['month']) in months]
            return total_components(attach_components(annotate(subset), grouped(), present_source=True))

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

        # Warn about independently imported source ledgers on financial screens.
        # The real source totals and official monthly totals must never be conflated.
        module = _cache_module()
        if module is not None and request.path in ('/', '/dashboard', '/calendar', '/report', '/trend'):
            summaries = module.get_cached_monthly_summary(None, session['user_id'])
            source_rows = grouped()
            if any((int(row['year']), int(row['month'])) in source_rows and
                   not row['breakdown_available']
                   for row in attach_components(summaries, source_rows)):
                flash('주의: 조제료·일매순익·비보험마진은 일별 기록 기준 금액입니다. '
                      '과거 월장부의 전체 합계와 다를 수 있으므로 합산하지 마세요. '
                      '차액은 /reconciliation 월별 대조 화면에서 확인하세요.', 'warning')

        return {'components_for': components_for,
                'period_components': period_components,
                'three_way_series': three_way_series}

    @app.route('/reconciliation', methods=['GET'])
    def historical_reconciliation():
        """Read-only user-scoped comparison of three independently imported sums."""
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user_id = session['user_id']
        conn = get_db()
        try:
            rows = conn.execute('''
                SELECT year, month, dispensing_plus_daily_total, non_insurance_total,
                       grand_total, prev_month_diff
                FROM monthly_summary WHERE user_id = ? ORDER BY year, month
            ''', (user_id,)).fetchall()
            extras = monthly_totals(conn, user_id)
            sources = load_monthly_components(conn, user_id)
            summaries = merge_monthly_summaries(rows, extras)
            checked = attach_components(summaries, sources)
        finally:
            conn.close()
        entries = []
        for item in checked:
            source = sources.get((int(item['year']), int(item['month'])))
            if source is None:
                status = '일별 기록 없음'
                difference = None
                daily_field_difference = None
                source_cell_difference = None
                combined_diff = None
                margin_diff = None
            else:
                status = '월장부와 일치' if item['breakdown_available'] else '월장부와 불일치'
                component_total = (int(source['dispensing_fee']) + int(source['daily_net_profit'])
                                   + int(source['non_insurance_margin']))
                ledger_without_extra = int(item['grand_total']) - int(item['extra_profit_total'])
                difference = ledger_without_extra - component_total
                daily_field_difference = ledger_without_extra - int(source['grand_total'])
                source_cell_difference = int(source['grand_total']) - component_total
                combined_diff = int(item['dispensing_plus_daily_total']) - int(source['dispensing_fee']) - int(source['daily_net_profit'])
                margin_diff = int(item['non_insurance_total']) - int(source['non_insurance_margin'])
            entries.append({'year': item['year'], 'month': item['month'], 'status': status,
                            'source': source, 'grand_total': item['grand_total'],
                            'extra': item['extra_profit_total'], 'difference': difference,
                            'daily_field_difference': daily_field_difference,
                            'source_cell_difference': source_cell_difference,
                            'combined_diff': combined_diff, 'margin_diff': margin_diff})
        page = render_template_string('''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>월별 세부자료 대조</title><style>
body{font-family:system-ui,sans-serif;max-width:1200px;margin:24px auto;padding:0 14px;color:#182333}
a{color:#135bb4}p{line-height:1.6}.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px;white-space:nowrap}
th,td{padding:10px;border-bottom:1px solid #d9e0e8;text-align:right}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
.warn{color:#a44409;font-weight:700}.ok{color:#16703f}.empty{color:#636b75}
</style></head><body>
<p><a href="{{ url_for('report') }}">← 연간 리포트로 돌아가기</a></p>
<h1>월별 세부자료 대조</h1>
<p>월장부와 일별 기록은 과거 엑셀에서 따로 가져왔을 수 있습니다. 아래 세 금액은 <strong>일별 기록 합계</strong>입니다.
'월장부와 불일치'는 확정된 월 세부금액이 아니라 대조용 숫자입니다. 월장부 총액·잡이익·기존 데이터는 변경하지 않습니다.</p>
<div class="table-wrap"><table><thead><tr><th>월</th><th>대조 상태</th><th>일별 조제료</th><th>일별 일매순익</th><th>일별 비보험마진</th><th>월장부 전체합계</th><th>월장부-구성항목 차이</th><th>월장부-일별 total 차이</th><th>일별 total-구성항목 차이</th><th>조제+일매 차이</th><th>비보험 차이</th></tr></thead>
<tbody>{% for row in entries %}<tr><td>{{ row.year }}년 {{ row.month }}월</td>
<td class="{% if row.status == '월장부와 일치' %}ok{% elif row.source %}warn{% else %}empty{% endif %}">{{ row.status }}</td>
<td>{{ '{:,}'.format(row.source.dispensing_fee) if row.source else '—' }}</td>
<td>{{ '{:,}'.format(row.source.daily_net_profit) if row.source else '—' }}</td>
<td>{{ '{:,}'.format(row.source.non_insurance_margin) if row.source else '—' }}</td>
<td>{{ '{:,}'.format(row.grand_total) }}</td>
<td>{{ '{:+,}'.format(row.difference) if row.difference is not none else '—' }}</td>
<td>{{ '{:+,}'.format(row.daily_field_difference) if row.daily_field_difference is not none else '—' }}</td>
<td>{{ '{:+,}'.format(row.source_cell_difference) if row.source_cell_difference is not none else '—' }}</td>
<td>{{ '{:+,}'.format(row.combined_diff) if row.combined_diff is not none else '—' }}</td>
<td>{{ '{:+,}'.format(row.margin_diff) if row.margin_diff is not none else '—' }}</td></tr>{% else %}<tr><td colspan="11">월별 자료가 없습니다.</td></tr>{% endfor %}</tbody></table></div>
<p>차이는 각각 월장부 원합계(잡이익 제외), 일별 total 셀의 합, 일별 세 구성항목의 합을 기준으로 계산합니다. 일별 기록이 없는 달은 차액도 표시하지 않습니다.</p>
</body></html>''', entries=entries)
        response = make_response(page)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    app.extensions['three_profit_display_installed'] = True
