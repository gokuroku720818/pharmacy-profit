"""One-time exact-match source integration; no real data files are accessed."""
from pathlib import Path


def patch(path, old, new, count=1):
    file = Path(path)
    data = file.read_bytes()
    newline = b'\r\n' if b'\r\n' in data else b'\n'
    before = old.encode('utf-8').replace(b'\n', newline)
    after = new.encode('utf-8').replace(b'\n', newline)
    found = data.count(before)
    if found != count:
        raise AssertionError(f'{path}: expected {count} matches but got {found} for {old[:85]!r}')
    file.write_bytes(data.replace(before, after))


# Create the isolated journal on boot for either SQLite or PostgreSQL.
patch('database.py', '''            settings_json TEXT NOT NULL
        )""")
        conn.commit()''', '''            settings_json TEXT NOT NULL
        )""")
        if is_postgres():
            conn.execute("""CREATE TABLE IF NOT EXISTS extra_profit (
                id BIGSERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                date TEXT NOT NULL,
                amount BIGINT NOT NULL CHECK (amount > 0),
                memo TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        else:
            conn.execute("""CREATE TABLE IF NOT EXISTS extra_profit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                date TEXT NOT NULL,
                amount BIGINT NOT NULL CHECK (amount > 0),
                memo TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_extra_profit_user_date ON extra_profit(user_id, date)')
        conn.commit()''')
patch('database.py', 'COALESCE(SUM(d.total), 0) as total_profit',
      'COALESCE(SUM(d.total), 0) + COALESCE(MAX(x.misc_total), 0) as total_profit')
patch('database.py', 'LEFT JOIN daily_profit d ON u.id = d.user_id\n            GROUP BY', '''LEFT JOIN daily_profit d ON u.id = d.user_id
            LEFT JOIN (SELECT user_id, SUM(amount) AS misc_total FROM extra_profit GROUP BY user_id) x
                ON x.user_id = u.id
            GROUP BY''')

# Overlay misc only at the read layer, keeping monthly_summary's original numbers intact.
patch('app.py', '''            WHERE user_id = ? AND grand_total > 0
            ORDER BY year, month''', '''            WHERE user_id = ?
            ORDER BY year, month''')
patch('app.py', '''        data = [dict(r) for r in rows]
        with _CACHE_LOCK:''', '''        from extra_profit import monthly_totals, merge_monthly_summaries
        data = merge_monthly_summaries(rows, monthly_totals(conn, user_id))
        with _CACHE_LOCK:''')
patch('app.py', '''# 서버 구동 시 DB 및 스키마 자동 초기화
init_db()
''', '''# 서버 구동 시 DB 및 스키마 자동 초기화
init_db()
from extra_profit import install as install_extra_profit
install_extra_profit(app)
''')
patch('app.py', "'non_insurance': int(last_r['non_insurance_total'] or 0),", "'non_insurance': int(last_r['non_insurance_total'] or 0),\n                'extra_profit_total': int(last_r.get('extra_profit_total') or 0),")
patch('app.py', "'non_insurance': 0, 'grand_total': 0, 'diff': 0}", "'non_insurance': 0, 'extra_profit_total': 0, 'grand_total': 0, 'diff': 0}", count=1)
patch('app.py', "'non_insurance': [int(r['non_insurance_total']) for r in rows]", "'non_insurance': [int(r['non_insurance_total']) for r in rows],\n            'extra_profit_total': [int(r.get('extra_profit_total') or 0) for r in rows]")
patch('app.py', '''                'non_insurance_total': nim,
                'grand_total': gt,
                'prev_month_diff': diff''', '''                'non_insurance_total': nim,
                'extra_profit_total': int(r.get('extra_profit_total') or 0),
                'grand_total': gt,
                'prev_month_diff': diff''')
patch('app.py', "    year_total = {\n        'dpd': year_dpd,", "    year_total = {\n        'extra_profit_total': sum(r['extra_profit_total'] for r in report_data),\n        'dpd': year_dpd,")
patch('app.py', "'total': 0, 'dpd': 0, 'nim': 0}", "'total': 0, 'dpd': 0, 'nim': 0, 'extra_profit_total': 0}", count=4)
patch('app.py', "        quarters[q_idx]['nim'] += r['non_insurance_total']", "        quarters[q_idx]['nim'] += r['non_insurance_total']\n        quarters[q_idx]['extra_profit_total'] += r['extra_profit_total']")
patch('app.py', '''        rows = conn.execute(''' + "'''" + '''
            SELECT * FROM monthly_summary WHERE user_id = ? AND year = ? ORDER BY month
        ''' + "'''" + ''', (user_id, year)).fetchall()
        from profit_components import load_monthly_components, attach_components''', '''        rows = [r for r in get_cached_monthly_summary(conn, user_id) if int(r['year']) == year]
        from profit_components import load_monthly_components, attach_components''')
patch('app.py', "'비보험마진', '전체합계', '전월대비', '세부자료 상태'", "'비보험마진', '잡이익', '전체합계', '전월대비', '세부자료 상태'")
patch('app.py', '''                         r['non_insurance_margin'] if known else '',
                         r['grand_total'], r['prev_month_diff'],''', '''                         r['non_insurance_margin'] if known else '',
                         r['extra_profit_total'],
                         r['grand_total'], r['prev_month_diff'],''')

# Reconcile weekly totals too, without treating an extra-only day as an operating day.
patch('app.py', '''    date_map = {r['date']: r for r in rows}

    weeks = []''', '''    date_map = {r['date']: r for r in rows}
    extra_map = {r['date']: int(r['amount'] or 0) for r in conn.execute(''' + "'''" + '''
        SELECT date, SUM(amount) AS amount FROM extra_profit
        WHERE user_id = ? AND date BETWEEN ? AND ? GROUP BY date
    ''' + "'''" + ''', (user_id, start_date.isoformat(), end_date.isoformat())).fetchall()}

    weeks = []''')
patch('app.py', '''        nim_sum = 0
        tot_sum = 0''', '''        nim_sum = 0
        extra_sum = 0
        tot_sum = 0''')
patch('app.py', '''            cur_d += datetime.timedelta(days=1)

        daily_avg''', '''            misc = extra_map.get(d_str, 0)
            extra_sum += misc
            tot_sum += misc
            cur_d += datetime.timedelta(days=1)

        daily_avg''')
patch('app.py', "            'nim_sum': nim_sum,\n            'total_sum': tot_sum,", "            'nim_sum': nim_sum,\n            'extra_sum': extra_sum,\n            'total_sum': tot_sum,")
patch('app.py', '''            if target_d in date_map:
                lw_same_period_sum += int(date_map[target_d]['total'] or 0)''', '''            if target_d in date_map:
                lw_same_period_sum += int(date_map[target_d]['total'] or 0)
            lw_same_period_sum += extra_map.get(target_d, 0)''')
patch('app.py', "        'non_insurance': [w['nim_sum'] for w in weeks]", "        'non_insurance': [w['nim_sum'] for w in weeks],\n        'extra_profit_total': [w['extra_sum'] for w in weeks]")

# Add a discoverable entry screen without changing the standalone calculator.
patch('templates/base.html', '''                    <li class="nav-item">
                        <a class="nav-link {% if request.endpoint == 'report' %}active{% endif %}" href="/report">''', '''                    <li class="nav-item">
                        <a class="nav-link {% if request.blueprint == 'extra_profit' %}active{% endif %}" href="/extra-profit">
                            <i class="fas fa-coins"></i> 잡이익 입력
                        </a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link {% if request.endpoint == 'report' %}active{% endif %}" href="/report">''')
patch('templates/base.html', "v='three-profit-20260920'", "v='extra-profit-20260920'")
patch('templates/dashboard.html', 'row-cols-xl-5 g-3 mb-4', 'row-cols-xl-6 g-3 mb-4')
patch('templates/dashboard.html', '''    <div class="col">
        <div class="card summary-card bg-info text-white">''', '''    <div class="col">
        <div class="card summary-card bg-secondary text-white">
            <div class="card-body">
                <div class="card-icon"><i class="fas fa-coins"></i></div>
                <h6 class="card-subtitle">잡이익 <a class="text-white small" href="/extra-profit?year={{ current_month.year }}&month={{ current_month.month }}">입력</a></h6>
                <h3 class="card-title">{{ "{:,}".format(current_month.extra_profit_total|default(0, true)) }}원</h3>
            </div>
        </div>
    </div>
    <div class="col">
        <div class="card summary-card bg-info text-white">''')
patch('templates/dashboard.html', '''                            <div class="d-flex justify-content-between text-muted">
                                <span>영업일/평균:</span>''', '''                            <div class="d-flex justify-content-between text-muted mb-1">
                                <span>잡이익:</span><strong class="text-dark">{{ "{:,}".format(w.extra_sum) }}원</strong>
                            </div>
                            <div class="d-flex justify-content-between text-muted">
                                <span>영업일/평균:</span>''')
patch('templates/calendar.html', 'row-cols-xl-4 g-2 mb-3', 'row-cols-xl-5 g-2 mb-3')
patch('templates/calendar.html', '''{% include '_forecast.html' %}''', '''<div class="mb-3"><a class="btn btn-outline-secondary btn-sm" href="/extra-profit?year={{ year }}&month={{ month }}">
    잡이익 {{ "{:,}".format((month_summary.extra_profit_total or 0) if month_summary else 0) }}원 · 입력/수정
</a></div>
{% include '_forecast.html' %}''')

# Preserve the template's existing newline convention.
patch('templates/report.html', '''                        <th class="text-end">비보험마진</th>
                        <th class="text-end">전체 합계</th>''', '''                        <th class="text-end">비보험마진</th>
                        <th class="text-end">잡이익</th>
                        <th class="text-end">전체 합계</th>''')
patch('templates/report.html', '''                        <td class="text-end text-success fw-bold">{{ "{:,}".format(month_split.non_insurance_margin) }}원</td>''', '''                        <td class="text-end text-success fw-bold">{{ "{:,}".format(month_split.non_insurance_margin) }}원</td>
                        <td class="text-end">{{ "{:,}".format(row.extra_profit_total) }}원</td>''')
patch('templates/report.html', '''{% else %}<td class="text-center text-muted" colspan="3">세부자료 확인 필요</td>{% endif %}''', '''{% else %}<td class="text-center text-muted" colspan="3">세부자료 확인 필요</td><td class="text-end">{{ "{:,}".format(row.extra_profit_total) }}원</td>{% endif %}''', count=1)
patch('templates/report.html', '''                        <td class="text-end"><strong>{{ "{:,}".format(annual_split.non_insurance_margin) }}원</strong></td>''', '''                        <td class="text-end"><strong>{{ "{:,}".format(annual_split.non_insurance_margin) }}원</strong></td>
                        <td class="text-end"><strong>{{ "{:,}".format(year_total.extra_profit_total) }}원</strong></td>''')
patch('templates/report.html', '''{% else %}<td class="text-center text-muted" colspan="3">세부자료 확인 필요</td>{% endif %}''', '''{% else %}<td class="text-center text-muted" colspan="3">세부자료 확인 필요</td><td class="text-end"><strong>{{ "{:,}".format(year_total.extra_profit_total) }}원</strong></td>{% endif %}''', count=1)
patch('templates/report.html', "labels: ['조제료', '일매순익', '비보험마진'],", "labels: ['조제료', '일매순익', '비보험마진', '잡이익'],")
patch('templates/report.html', '''data: [annualSplit.dispensing_fee, annualSplit.daily_net_profit, annualSplit.non_insurance_margin],''', '''data: [annualSplit.dispensing_fee, annualSplit.daily_net_profit, annualSplit.non_insurance_margin, {{ year_total.extra_profit_total }}],''')
patch('templates/report.html', "backgroundColor: ['#2563eb', '#10b981', '#f59e0b'],", "backgroundColor: ['#2563eb', '#10b981', '#f59e0b', '#64748b'],")
patch('templates/report.html', '''                    <span>비보험마진: {{ "{:,}".format(quarter_split.non_insurance_margin) }}</span>''', '''                    <span>비보험마진: {{ "{:,}".format(quarter_split.non_insurance_margin) }}</span>
                    <span>잡이익: {{ "{:,}".format(q.extra_profit_total) }}</span>''')

# The chart must visually reconcile the fourth, supplemental series.
patch('static/js/charts.js', '''                    label: '비보험마진',
                    data: monthlyData.non_insurance_margin,
                    borderColor: COLORS.orange,
                    backgroundColor: 'transparent',
                    borderDash: [3, 3],
                    tension: 0.3,
                    pointRadius: 0
                }]''', '''                    label: '비보험마진',
                    data: monthlyData.non_insurance_margin,
                    borderColor: COLORS.orange,
                    backgroundColor: 'transparent',
                    borderDash: [3, 3],
                    tension: 0.3,
                    pointRadius: 0
                }, {
                    label: '잡이익',
                    data: monthlyData.extra_profit_total,
                    borderColor: COLORS.info,
                    backgroundColor: 'transparent',
                    borderDash: [2, 5],
                    tension: 0.3,
                    pointRadius: 0
                }]''')
patch('static/js/charts.js', "labels: ['조제료', '일매순익', '비보험마진'],", "labels: ['조제료', '일매순익', '비보험마진', '잡이익'],", count=1)
patch('static/js/charts.js', '''data: [currentMonth.dispensing_fee, currentMonth.daily_net_profit, currentMonth.non_insurance_margin],''', '''data: [currentMonth.dispensing_fee, currentMonth.daily_net_profit, currentMonth.non_insurance_margin, currentMonth.extra_profit_total],''')
patch('static/js/charts.js', '''backgroundColor: [COLORS.primary, COLORS.success, COLORS.warning],''', '''backgroundColor: [COLORS.primary, COLORS.success, COLORS.warning, COLORS.info],''')

# Existing CSV tests must reflect a new explicit column, without weakening prior assertions.
patch('tests/test_three_profit_http.py', '''assert parsed[1][:8] == ['연도', '월', '조제료', '일매순익', '비보험마진', '전체합계', '전월대비', '세부자료 상태']''', '''assert parsed[1][:9] == ['연도', '월', '조제료', '일매순익', '비보험마진', '잡이익', '전체합계', '전월대비', '세부자료 상태']''')
patch('tests/test_three_profit_http.py', '''assert september[2:6] == ['12345', '6789', '4321', '23455']''', '''assert september[2:7] == ['12345', '6789', '4321', '0', '23455']''')
patch('tests/test_three_profit_http.py', "assert september[7] == '확인됨'", "assert september[8] == '확인됨'")
patch('tests/test_three_profit_http.py', "assert august[5] == '1000'", "assert august[5] == '0'\n    assert august[6] == '1000'")
patch('tests/test_three_profit_http.py', "assert august[7] == '세부자료 확인 필요'", "assert august[8] == '세부자료 확인 필요'")

print('Applied isolated miscellaneous-profit integration and CSV contract updates.')
