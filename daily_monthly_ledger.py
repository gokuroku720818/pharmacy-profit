"""Daily entries are the sole source for the displayed monthly financial ledger.

The imported monthly_summary table is retained untouched as historical evidence.
All official screen totals come from daily_profit.total + separately entered extras;
the three daily components are independently summed and discrepancies disclosed.
This is a read-only switch, not a destructive database migration.
"""

import csv
import io
import time
from functools import wraps

from flask import flash as flask_flash, make_response, redirect, render_template_string, request, send_file, session, url_for

from extra_profit import monthly_totals


def load_monthly_ledger(conn, user_id):
    """One portable, account-scoped aggregate for SQLite and PostgreSQL."""
    daily = conn.execute('''
        SELECT SUBSTR(date, 1, 4) AS year_text,
               SUBSTR(date, 6, 2) AS month_text,
               COUNT(*) AS source_day_count,
               SUM(COALESCE(dispensing_fee, 0)) AS dispensing_fee,
               SUM(COALESCE(daily_net_profit, 0)) AS daily_net_profit,
               SUM(COALESCE(non_insurance_margin, 0)) AS non_insurance_margin,
               SUM(COALESCE(total, 0)) AS daily_total
        FROM daily_profit WHERE user_id = ?
        GROUP BY SUBSTR(date, 1, 4), SUBSTR(date, 6, 2)
        ORDER BY year_text, month_text
    ''', (user_id,)).fetchall()
    extras = monthly_totals(conn, user_id)
    by_month = {}
    for source in daily:
        year, month = int(source['year_text']), int(source['month_text'])
        disp = int(source['dispensing_fee'] or 0)
        daily_net = int(source['daily_net_profit'] or 0)
        nim = int(source['non_insurance_margin'] or 0)
        daily_total = int(source['daily_total'] or 0)
        by_month[(year, month)] = {
            'year': year, 'month': month,
            'dispensing_fee': disp, 'daily_net_profit': daily_net,
            'dispensing_plus_daily_total': disp + daily_net,
            'non_insurance_total': nim, 'daily_total': daily_total,
            'daily_total_difference': daily_total - disp - daily_net - nim,
            'source_day_count': int(source['source_day_count']),
        }
    # An extras-only month is valid, but never restore a monthly_summary-only
    # period without actual source rows or separately entered miscellaneous profit.
    for year, month in extras:
        by_month.setdefault((year, month), {
            'year': year, 'month': month,
            'dispensing_fee': 0, 'daily_net_profit': 0,
            'dispensing_plus_daily_total': 0, 'non_insurance_total': 0,
            'daily_total': 0, 'daily_total_difference': 0,
            'source_day_count': 0,
        })
    for key, item in by_month.items():
        item['extra_profit_total'] = int(extras.get(key, 0))
        item['grand_total'] = item['daily_total'] + item['extra_profit_total']
    for (year, month), item in by_month.items():
        prev = (year - 1, 12) if month == 1 else (year, month - 1)
        prior = by_month.get(prev)
        item['previous_month_available'] = prior is not None
        item['prev_month_diff'] = item['grand_total'] - prior['grand_total'] if prior else None
    return [by_month[key] for key in sorted(by_month)]


def install(module):
    """Switch every ordinary monthly reader before the Gunicorn worker serves traffic.

    Install *before* cache_coherence.install: its per-user lock must wrap this
    loader and all writer invalidations, preventing stale cache repopulation.
    """
    if getattr(module, '_daily_monthly_ledger_installed', False):
        return

    def get_cached_monthly_summary(conn=None, user_id=None):
        now = time.time()
        with module._CACHE_LOCK:
            entry = module._USER_CACHE.get(user_id, {}).get('monthly_summary')
            if entry and now - entry['ts'] < module._CACHE_TTL:
                return entry['data']
        own = conn is None
        if own:
            conn = module.get_db()
        try:
            rows = load_monthly_ledger(conn, user_id)
        finally:
            if own:
                conn.close()
        with module._CACHE_LOCK:
            module._USER_CACHE.setdefault(user_id, {})['monthly_summary'] = {
                'ts': time.time(), 'data': rows,
            }
        return rows

    module.get_cached_monthly_summary = get_cached_monthly_summary

    old_export = module.app.view_functions['export_csv']

    @wraps(old_export)
    def export_daily_csv(year):
        # Keep the original route's login decorator and URL mapping intact.
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user_id = session['user_id']
        pharmacy = session.get('pharmacy_name', '약국')
        rows = [r for r in module.get_cached_monthly_summary(None, user_id)
                if int(r['year']) == year]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([f'[{pharmacy}] {year}년 순익 리포트 - 일장부 자동 합계'])
        writer.writerow(['연도', '월', '조제료', '일매순익', '비보험마진', '잡이익',
                         '전체합계', '전월대비', '일별 합계 차이', '세부자료 상태'])
        for r in rows:
            discrepancy = int(r['daily_total_difference'])
            status = ('잡이익만 입력됨' if not r['source_day_count'] else
                      '일별 합계 차이 확인 필요' if discrepancy else '일별 기록 확인됨')
            writer.writerow([r['year'], r['month'], r['dispensing_fee'],
                             r['daily_net_profit'], r['non_insurance_total'],
                             r['extra_profit_total'], r['grand_total'],
                             r['prev_month_diff'] if r['prev_month_diff'] is not None else '',
                             discrepancy, status])
        output.seek(0)
        return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                         mimetype='text/csv', as_attachment=True,
                         download_name=f'{pharmacy}_순익_{year}.csv')

    module.app.view_functions['export_csv'] = export_daily_csv

    # Preserve the old reconciliation implementation in source control, while
    # exposing the daily-source discrepancy instead of a retired ledger dispute.
    old_reconciliation = module.app.view_functions['historical_reconciliation']

    @wraps(old_reconciliation)
    def daily_reconciliation():
        if 'user_id' not in session:
            return redirect(url_for('login'))
        rows = module.get_cached_monthly_summary(None, session['user_id'])
        html = render_template_string('''<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>일장부 자동 월별 합계 검증</title><style>
body{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 14px;color:#182333}
table{border-collapse:collapse;width:100%;white-space:nowrap}th,td{padding:10px;border-bottom:1px solid #ddd;text-align:right}
.wrap{overflow-x:auto}.warn{color:#a44409;font-weight:bold}
</style></head><body><p><a href="{{ url_for('report') }}">← 연간 리포트</a></p>
<h1>일장부 자동 월별 합계</h1>
<p>공식 전체합계 = 해당 월 일별 total 합계 + 별도 입력 잡이익. 조제료·일매순익·비보험마진은
일별 각 항목을 따로 합산합니다. 일별 total과 세 항목의 합계가 다를 경우 차액을 별도 표시하며
이를 잡이익으로 임의 편입하지 않습니다. 월별 합계는 일별 기록으로 자동 생성되며 별도로 입력하는 월별 장부는 없습니다.</p>
<div class="wrap"><table><thead><tr><th>월</th><th>입력일수</th><th>조제료</th><th>일매순익</th>
<th>비보험마진</th><th>일별 total 합계</th><th>잡이익</th><th>공식 월합계</th>
<th>일별 total-세 항목 차이</th></tr></thead><tbody>
{% for r in rows %}<tr><td>{{ r.year }}-{{ '%02d'|format(r.month) }}</td><td>{{ r.source_day_count }}</td>
<td>{{ '{:,}'.format(r.dispensing_fee) }}</td><td>{{ '{:,}'.format(r.daily_net_profit) }}</td>
<td>{{ '{:,}'.format(r.non_insurance_total) }}</td><td>{{ '{:,}'.format(r.daily_total) }}</td>
<td>{{ '{:,}'.format(r.extra_profit_total) }}</td><td>{{ '{:,}'.format(r.grand_total) }}</td>
<td class="{{ 'warn' if r.daily_total_difference else '' }}">{{ '{:+,}'.format(r.daily_total_difference) }}</td></tr>
{% else %}<tr><td colspan="9">일별 기록과 잡이익이 없습니다.</td></tr>{% endfor %}
</tbody></table></div></body></html>''', rows=rows)
        response = make_response(html)
        response.headers['Cache-Control'] = 'private, no-store, max-age=0'
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    module.app.view_functions['historical_reconciliation'] = daily_reconciliation

    # Legacy context helper still checks component-vs-grand differences. Change
    # only that message to identify the real source as daily total discrepancy.
    # Do not silence the warning: users need to see unbalanced daily rows.
    import profit_display
    if not getattr(profit_display, '_daily_warning_installed', False):
        original_flash = profit_display.flash

        def source_accurate_flash(message, category='message'):
            if not getattr(profit_display, 'SHOW_LEDGER_WARNING', False):
                if category == 'warning' and ('과거 월장부' in str(message) or '일별 total' in str(message)):
                    return None
            if category == 'warning' and '과거 월장부' in str(message):
                message = ('주의: 일별 total 합계와 조제료·일매순익·비보험마진 합계가 서로 다른 날짜가 있습니다. '
                           '공식 월합계는 일별 total을 사용하고 차액은 월별 합계 검증 화면에서 별도로 표시합니다.')
            return original_flash(message, category)

        profit_display.flash = source_accurate_flash
        profit_display._daily_warning_installed = True

    module._daily_monthly_ledger_installed = True
