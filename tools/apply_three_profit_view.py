"""Apply three-way presentation on a clean feature branch; fail if anchors drift.
No edits to calculator, data, database schema, or monetary formulas.
"""
from pathlib import Path


def one(path, old, new):
    f = Path(path)
    text = f.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{path}: expected one anchor, found {count}: {old[:90]!r}')
    f.write_text(text.replace(old, new, 1), encoding='utf-8')


def dashboard():
    p = 'templates/dashboard.html'
    one(p, '<!-- 요약 카드 -->', '''{% set split = components_for(current_month.year, current_month.month, current_month.dispensing_plus_daily, current_month.non_insurance, current_month.grand_total) %}
<!-- 요약 카드 -->''')
    text = Path(p).read_text(encoding='utf-8')
    top, sep, rest = text.partition('<!-- 📅 0.')
    if not sep or top.count('<div class="col-md-3">') != 4:
        raise RuntimeError('Dashboard summary markup changed')
    top = top.replace('<div class="row mb-4">', '<div class="row row-cols-1 row-cols-md-2 row-cols-xl-5 g-3 mb-4">', 1)
    top = top.replace('<div class="col-md-3">', '<div class="col">')
    Path(p).write_text(top + sep + rest, encoding='utf-8')
    one(p, '''<h6 class="card-subtitle">조제+일매순익</h6>
                <h3 class="card-title">{{ "{:,}".format(current_month.dispensing_plus_daily|default(0, true)) }}원</h3>''', '''<h6 class="card-subtitle">조제료</h6>
                <h3 class="card-title">{{ ("{:,}".format(split.dispensing_fee) ~ "원") if split.breakdown_available else "세부자료 확인 필요" }}</h3>''')
    one(p, '''    <div class="col">
        <div class="card summary-card bg-success text-white">''', '''    <div class="col">
        <div class="card summary-card bg-success text-white">
            <div class="card-body">
                <div class="card-icon"><i class="fas fa-store"></i></div>
                <h6 class="card-subtitle">일매순익</h6>
                <h3 class="card-title">{{ ("{:,}".format(split.daily_net_profit) ~ "원") if split.breakdown_available else "세부자료 확인 필요" }}</h3>
            </div>
        </div>
    </div>
    <div class="col">
        <div class="card summary-card bg-success text-white">''')
    one(p, '<h6 class="card-subtitle">비보험약가차액</h6>', '<h6 class="card-subtitle">비보험마진</h6>')
    one(p, '''<span>조제+일매:</span>
                                <strong class="text-dark">{{ "{:,}".format(w.disp_plus_daily) }}원</strong>''', '''<span>조제료:</span>
                                <strong class="text-dark">{{ "{:,}".format(w.dispensing_sum) }}원</strong>''')
    one(p, '<span>비보험 차액:</span>', '<span>비보험마진:</span>')
    one(p, '''                            <div class="d-flex justify-content-between text-muted mb-1">
                                <span>비보험마진:</span>''', '''                            <div class="d-flex justify-content-between text-muted mb-1">
                                <span>일매순익:</span>
                                <strong class="text-dark">{{ "{:,}".format(w.daily_sum) }}원</strong>
                            </div>
                            <div class="d-flex justify-content-between text-muted mb-1">
                                <span>비보험마진:</span>''')
    one(p, '''<p>조제+일매 합계 <strong>{{ "{:,}".format(balance.combined_val) }}원</strong></p>''', '<p class="text-muted small">조제료·일매순익 세부자료 확인 필요</p>')
    one(p, 'const monthlyData = {{ monthly_data|tojson }};', 'const monthlyData = {{ three_way_series(monthly_data.labels, monthly_data.dispensing_daily, monthly_data.non_insurance, monthly_data.totals)|tojson }};')
    one(p, 'const currentMonth = {{ current_month_data|tojson }};', 'const currentMonth = {{ split|tojson }};')
    one(p, 'const weeklyChartData = {{ weekly_stats.chart_data|tojson }};', '''const weeklyChartData = {{ weekly_stats.chart_data|tojson }};
    weeklyChartData.dispensing = {{ weekly_stats.weeks|map(attribute='dispensing_sum')|list|tojson }};
    weeklyChartData.daily = {{ weekly_stats.weeks|map(attribute='daily_sum')|list|tojson }};''')


def calendar():
    p = 'templates/calendar.html'
    one(p, '<!-- 당월 요약 및 AI 월말 예측 배너 -->', '''{% set split = components_for(year, month, (month_summary.dispensing_plus_daily_total or 0) if month_summary else 0, (month_summary.non_insurance_total or 0) if month_summary else 0, (month_summary.grand_total or 0) if month_summary else 0) %}
<!-- 당월 요약 및 AI 월말 예측 배너 -->''')
    text = Path(p).read_text(encoding='utf-8')
    top, sep, rest = text.partition("{% include '_forecast.html' %}")
    if not sep or top.count('<div class="col-12 col-md-4">') != 3:
        raise RuntimeError('Calendar summary markup changed')
    top = top.replace('<div class="row g-2 mb-3">', '<div class="row row-cols-1 row-cols-md-2 row-cols-xl-4 g-2 mb-3">', 1)
    top = top.replace('<div class="col-12 col-md-4">', '<div class="col">')
    Path(p).write_text(top + sep + rest, encoding='utf-8')
    one(p, '''<small class="opacity-75">조제 + 일매순익</small>
            <h5 class="fw-bold mb-0">{{ "{:,}".format((month_summary.dispensing_plus_daily_total or 0) if month_summary else 0) }}원</h5>''', '''<small class="opacity-75">조제료</small>
            <h5 class="fw-bold mb-0">{{ ("{:,}".format(split.dispensing_fee) ~ "원") if split.breakdown_available else ("세부자료 확인 필요" if month_summary else "0원") }}</h5>''')
    one(p, '''    <div class="col">
        <div class="card p-3 border-0 bg-warning text-dark shadow-sm">''', '''    <div class="col">
        <div class="card p-3 border-0 bg-info text-white shadow-sm">
            <small class="opacity-75">일매순익</small>
            <h5 class="fw-bold mb-0">{{ ("{:,}".format(split.daily_net_profit) ~ "원") if split.breakdown_available else ("세부자료 확인 필요" if month_summary else "0원") }}</h5>
        </div>
    </div>
    <div class="col">
        <div class="card p-3 border-0 bg-warning text-dark shadow-sm">''')
    one(p, '<small class="opacity-75">비보험 약가차액</small>', '<small class="opacity-75">비보험마진</small>')
    one(p, '<div class="profit-sub small text-muted">조제: ', '<div class="profit-sub small text-muted">조제료: ')
    one(p, '<div class="profit-sub small text-muted">일매: ', '<div class="profit-sub small text-muted">일매순익: ')
    one(p, '<div class="profit-sub small text-success">비보험: ', '<div class="profit-sub small text-success">비보험마진: ')


def report():
    p = 'templates/report.html'
    one(p, '<!-- 🏆 1. 핵심 KPI 4대 지표 카드 -->', '''{% set annual_split = period_components(selected_year, report_data) %}
<!-- 🏆 1. 핵심 KPI 4대 지표 카드 -->''')
    one(p, '''    {% for q in quarters %}
    <div class="col-sm-6 col-lg-3">''', '''    {% for q in quarters %}
    {% set quarter_split = period_components(selected_year, report_data, q.months) %}
    <div class="col-sm-6 col-lg-3">''')
    one(p, '''<span>조제+일매: {{ "{:,}".format(q.dpd) }}</span>
                    <span>비보험: {{ "{:,}".format(q.nim) }}</span>''', '''{% if quarter_split %}
                    <span>조제료: {{ "{:,}".format(quarter_split.dispensing_fee) }}</span>
                    <span>일매순익: {{ "{:,}".format(quarter_split.daily_net_profit) }}</span>
                    <span>비보험마진: {{ "{:,}".format(quarter_split.non_insurance_margin) }}</span>
                    {% else %}<span>세부자료 확인 필요</span>{% endif %}''')
    one(p, '<canvas id="reportCompositionChart"></canvas>', '''{% if annual_split %}<canvas id="reportCompositionChart"></canvas>
                    {% else %}<p class="text-muted small">연간 세부자료 확인 필요</p>{% endif %}''')
    one(p, '''                        <div>
                            <span class="badge bg-primary me-1">■</span> 조제+일매: <strong>{{ dpd_pct }}%</strong>
                        </div>
                        <div>
                            <span class="badge bg-success me-1">■</span> 비보험마진: <strong>{{ nim_pct }}%</strong>
                        </div>''', '''                        {% if annual_split and year_total.grand > 0 %}
                        <div>조제료: <strong>{{ ((annual_split.dispensing_fee / year_total.grand) * 100)|round(1) }}%</strong></div>
                        <div>일매순익: <strong>{{ ((annual_split.daily_net_profit / year_total.grand) * 100)|round(1) }}%</strong></div>
                        <div>비보험마진: <strong>{{ ((annual_split.non_insurance_margin / year_total.grand) * 100)|round(1) }}%</strong></div>
                        {% else %}<div>세부자료 확인 필요</div>{% endif %}''')
    one(p, '''<th class="text-end">조제+일매순익</th>
                        <th class="text-end">비보험약가차액</th>''', '''<th class="text-end">조제료</th>
                        <th class="text-end">일매순익</th>
                        <th class="text-end">비보험마진</th>''')
    one(p, '''                    {% set m_share = ((row.grand_total / year_total.grand) * 100)|round(1) if year_total.grand > 0 else 0 %}''', '''                    {% set m_share = ((row.grand_total / year_total.grand) * 100)|round(1) if year_total.grand > 0 else 0 %}
                    {% set month_split = components_for(selected_year, row.month, row.dispensing_plus_daily_total, row.non_insurance_total, row.grand_total) %}''')
    one(p, '''                        <td class="text-end">{{ "{:,}".format(row.dispensing_plus_daily_total) }}원</td>
                        <td class="text-end text-success fw-bold">{{ "{:,}".format(row.non_insurance_total) }}원</td>''', '''                        {% if month_split.breakdown_available %}
                        <td class="text-end">{{ "{:,}".format(month_split.dispensing_fee) }}원</td>
                        <td class="text-end">{{ "{:,}".format(month_split.daily_net_profit) }}원</td>
                        <td class="text-end text-success fw-bold">{{ "{:,}".format(month_split.non_insurance_margin) }}원</td>
                        {% else %}<td class="text-center text-muted" colspan="3">세부자료 확인 필요</td>{% endif %}''')
    one(p, '''                        <td class="text-end"><strong>{{ "{:,}".format(year_total.dpd) }}원</strong></td>
                        <td class="text-end"><strong>{{ "{:,}".format(year_total.nim) }}원</strong></td>''', '''                        {% if annual_split %}
                        <td class="text-end"><strong>{{ "{:,}".format(annual_split.dispensing_fee) }}원</strong></td>
                        <td class="text-end"><strong>{{ "{:,}".format(annual_split.daily_net_profit) }}원</strong></td>
                        <td class="text-end"><strong>{{ "{:,}".format(annual_split.non_insurance_margin) }}원</strong></td>
                        {% else %}<td class="text-center text-muted" colspan="3">세부자료 확인 필요</td>{% endif %}''')
    one(p, 'const chartPayload = {{ chart_payload|tojson }};', 'const chartPayload = {{ chart_payload|tojson }};\nconst annualSplit = {{ annual_split|tojson }};')
    one(p, 'if (compCtx && chartPayload) {', 'if (compCtx && chartPayload && annualSplit) {')
    one(p, '''labels: ['조제+일매순익', '비보험약가차액'],
            datasets: [{
                data: chartPayload.composition,
                backgroundColor: ['#2563eb', '#10b981'],''', '''labels: ['조제료', '일매순익', '비보험마진'],
            datasets: [{
                data: [annualSplit.dispensing_fee, annualSplit.daily_net_profit, annualSplit.non_insurance_margin],
                backgroundColor: ['#2563eb', '#10b981', '#f59e0b'],''')


def trend():
    p = 'templates/trend.html'
    one(p, '''<div class="row mb-4">
    <div class="col-md-12">''', '''<div class="row mb-4">
    <div class="col-md-12">
        <div class="card">
            <div class="card-header"><i class="fas fa-layer-group"></i> 월별 조제료 · 일매순익 · 비보험마진</div>
            <div class="card-body"><canvas id="monthlyComponentChart" height="260"></canvas>
                <small class="text-muted">원본 세부자료가 없는 달은 금액을 추정하지 않고 표시하지 않습니다.</small></div>
        </div>
    </div>
</div>

<div class="row mb-4">
    <div class="col-md-12">''')
    one(p, 'initTrendCharts(yearlyData, dayOfWeekData, growthData);', '''initTrendCharts(yearlyData, dayOfWeekData, growthData);
    const splitTrendData = {{ three_way_series(ratio_data.labels, ratio_data.dispensing, ratio_data.non_insurance, ratio_data.totals)|tojson }};
    initThreeWayTrendChart(splitTrendData);''')


def charts():
    p = 'static/js/charts.js'
    one(p, '''label: '조제+일매순익',
                    data: monthlyData.dispensing_daily,
                    borderColor: COLORS.success,
                    backgroundColor: 'transparent',
                    borderDash: [5, 5],
                    tension: 0.3,
                    pointRadius: 0''', '''label: '조제료',
                    data: monthlyData.dispensing_fee,
                    borderColor: COLORS.success,
                    backgroundColor: 'transparent',
                    borderDash: [5, 5],
                    tension: 0.3,
                    pointRadius: 0
                }, {
                    label: '일매순익',
                    data: monthlyData.daily_net_profit,
                    borderColor: COLORS.purple,
                    backgroundColor: 'transparent',
                    borderDash: [4, 4],
                    tension: 0.3,
                    pointRadius: 0''')
    one(p, '''label: '비보험약가차액',
                    data: monthlyData.non_insurance,''', '''label: '비보험마진',
                    data: monthlyData.non_insurance_margin,''')
    one(p, 'if (compCtx && currentMonth) {', 'if (compCtx && currentMonth && currentMonth.breakdown_available) {')
    one(p, '''labels: ['조제+일매순익', '비보험약가차액'],
                datasets: [{
                    data: [currentMonth.dispensing_plus_daily || 0, currentMonth.non_insurance || 0],
                    backgroundColor: [COLORS.primary, COLORS.success],''', '''labels: ['조제료', '일매순익', '비보험마진'],
                datasets: [{
                    data: [currentMonth.dispensing_fee, currentMonth.daily_net_profit, currentMonth.non_insurance_margin],
                    backgroundColor: [COLORS.primary, COLORS.success, COLORS.warning],''')
    one(p, '''label: '조제+일매순익',
                    data: weeklyData.disp_plus_daily,
                    backgroundColor: COLORS.primary,
                    borderRadius: 4''', '''label: '조제료',
                    data: weeklyData.dispensing,
                    backgroundColor: COLORS.primary,
                    borderRadius: 4
                },
                {
                    label: '일매순익',
                    data: weeklyData.daily,
                    backgroundColor: COLORS.purple,
                    borderRadius: 4''')
    one(p, '''label: '비보험약가차액',
                    data: weeklyData.non_insurance,''', '''label: '비보험마진',
                    data: weeklyData.non_insurance,''')
    with Path(p).open('a', encoding='utf-8') as f:
        f.write('''

// Unknown historical component values are intentional Chart.js gaps, not zeros.
function initThreeWayTrendChart(data) {
    const canvas = document.getElementById('monthlyComponentChart');
    if (!canvas || !data) return;
    const fields = [
        ['조제료', 'dispensing_fee', COLORS.primary],
        ['일매순익', 'daily_net_profit', COLORS.success],
        ['비보험마진', 'non_insurance_margin', COLORS.warning]
    ];
    new Chart(canvas, {
        type: 'bar',
        data: {labels: data.labels, datasets: fields.map(([label, key, color]) => ({
            label: label, data: data[key], backgroundColor: color, borderRadius: 3
        }))},
        options: {responsive: true, maintainAspectRatio: false,
            plugins: {legend: {position: 'top'}},
            scales: {x: {stacked: true}, y: {stacked: true, ticks: {callback: v => formatNumber(v)}}}
        }
    });
}
''')


def app():
    p = 'app.py'
    one(p, 'app = Flask(__name__)\n', '''app = Flask(__name__)
from profit_display import install as install_profit_display
install_profit_display(app)
''')
    one(p, '''        'non_insurance': [int(r['non_insurance_total']) for r in rows]
    }

    heatmap_data = []''', '''        'non_insurance': [int(r['non_insurance_total']) for r in rows],
        'totals': [int(r['grand_total']) for r in rows]
    }

    heatmap_data = []''')
    old_query = "        rows = conn.execute('''\n            SELECT * FROM monthly_summary WHERE user_id = ? AND year = ? ORDER BY month\n        ''', (user_id, year)).fetchall()\n    finally:\n        conn.close()\n\n    output = io.StringIO()"
    new_query = "        rows = conn.execute('''\n            SELECT * FROM monthly_summary WHERE user_id = ? AND year = ? ORDER BY month\n        ''', (user_id, year)).fetchall()\n        from profit_components import load_monthly_components, attach_components\n        grouped = load_monthly_components(conn, user_id)\n        detailed_rows = attach_components(rows, grouped)\n    finally:\n        conn.close()\n\n    output = io.StringIO()"
    one(p, old_query, new_query)
    one(p, '''    writer.writerow(['연도', '월', '조제+일매순익', '비보험약가차액', '전체합계', '전월대비'])
    for r in rows:
        writer.writerow([r['year'], r['month'], r['dispensing_plus_daily_total'],
                         r['non_insurance_total'], r['grand_total'], r['prev_month_diff']])''', '''    writer.writerow(['연도', '월', '조제료', '일매순익', '비보험마진', '전체합계', '전월대비', '세부자료 상태'])
    for r in detailed_rows:
        known = r['breakdown_available']
        writer.writerow([r['year'], r['month'],
                         r['dispensing_fee'] if known else '',
                         r['daily_net_profit'] if known else '',
                         r['non_insurance_margin'] if known else '',
                         r['grand_total'], r['prev_month_diff'],
                         '확인됨' if known else '세부자료 확인 필요'])''')


if __name__ == '__main__':
    for transform in (dashboard, calendar, report, trend, charts, app):
        transform()
    print('Three-way UI applied without calculator/data/schema changes.')
