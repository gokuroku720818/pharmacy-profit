"""
약국 순익 관리 시스템 - 다중 약국 지원 온라인 SaaS 버전
"""
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db, init_db, recalc_monthly_summary, get_all_users_stats, delete_user_and_data
from functools import wraps
import datetime
import calendar
import json
import csv
import io
import os
import re
import openpyxl
import msoffcrypto

app = Flask(__name__)
app.secret_key = 'pharmacy-profit-saas-super-secret-key-2026'

# 서버 구동 시 DB 및 스키마 자동 초기화
init_db()


def login_required(f):
    """로그인 필수 데코레이터"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """관리자(admin) 전용 데코레이터"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        # 원래 관리자이거나 현재 admin인 경우 허용
        is_admin = (session.get('username') == 'admin') or ('original_admin_id' in session)
        if not is_admin:
            flash('관리자만 접근할 수 있는 페이지입니다.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def get_latest_month_summary(user_id, conn=None):
    """데이터가 있는 가장 최근 월의 순익 요약 (연결 재사용 최적화)"""
    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True
    row = conn.execute(
        'SELECT * FROM monthly_summary WHERE user_id = ? AND grand_total > 0 ORDER BY year DESC, month DESC LIMIT 1',
        (user_id,)
    ).fetchone()
    if should_close:
        conn.close()

    if row:
        return {
            'year': row['year'],
            'month': row['month'],
            'dispensing_plus_daily': row['dispensing_plus_daily_total'],
            'non_insurance': row['non_insurance_total'],
            'grand_total': row['grand_total'],
            'diff': row['prev_month_diff']
        }
    now = datetime.date.today()
    return {'year': now.year, 'month': now.month, 'dispensing_plus_daily': 0, 'non_insurance': 0, 'grand_total': 0, 'diff': 0}


def get_month_forecast(conn, user_id, year, month):
    """1. 이번 달 최종 순익 자동 예측기"""
    date_prefix = f"{year}-{month:02d}"

    entered_rows = conn.execute(
        'SELECT date, day_of_week, total FROM daily_profit WHERE user_id = ? AND date LIKE ?',
        (user_id, date_prefix + '%')
    ).fetchall()

    current_total = sum(r['total'] for r in entered_rows)
    entered_days = set(int(r['date'].split('-')[2]) for r in entered_rows)

    dow_rows = conn.execute(
        'SELECT day_of_week, AVG(total) as avg_total FROM daily_profit WHERE user_id = ? AND total > 0 GROUP BY day_of_week',
        (user_id,)
    ).fetchall()
    dow_avg = {r['day_of_week']: int(r['avg_total']) for r in dow_rows}

    max_days = calendar.monthrange(year, month)[1]
    day_names = ['월', '화', '수', '목', '금', '토', '일']

    expected_additional = 0
    remaining_business_days = 0

    for day in range(1, max_days + 1):
        if day not in entered_days:
            try:
                dt = datetime.date(year, month, day)
                dow = day_names[dt.weekday()]
                if dow != '일':
                    avg_val = dow_avg.get(dow, 0)
                    expected_additional += avg_val
                    remaining_business_days += 1
            except ValueError:
                pass

    forecast_total = current_total + expected_additional

    prev_year_summary = conn.execute(
        'SELECT grand_total FROM monthly_summary WHERE user_id = ? AND year = ? AND month = ?',
        (user_id, year - 1, month)
    ).fetchone()

    last_year_total = prev_year_summary['grand_total'] if prev_year_summary else 0
    yoy_growth_pct = round(((forecast_total - last_year_total) / last_year_total * 100), 1) if last_year_total > 0 else 0

    return {
        'year': year,
        'month': month,
        'current_total': current_total,
        'entered_count': len(entered_rows),
        'remaining_business_days': remaining_business_days,
        'expected_additional': expected_additional,
        'forecast_total': forecast_total,
        'last_year_total': last_year_total,
        'yoy_growth_pct': yoy_growth_pct
    }


def get_yoy_day_comparison(conn, user_id, date_str=None):
    """2. 작년 오늘 vs 올해 오늘 1:1 맞춤 비교"""
    if not date_str:
        latest = conn.execute('SELECT date FROM daily_profit WHERE user_id = ? ORDER BY date DESC LIMIT 1', (user_id,)).fetchone()
        if not latest:
            return None
        date_str = latest['date']

    today_row = conn.execute('SELECT * FROM daily_profit WHERE user_id = ? AND date = ?', (user_id, date_str)).fetchone()
    if not today_row:
        return None

    target_dt = datetime.date.fromisoformat(date_str)
    yoy_dt = target_dt - datetime.timedelta(days=364)
    yoy_date_str = yoy_dt.isoformat()

    yoy_row = conn.execute('SELECT * FROM daily_profit WHERE user_id = ? AND date = ?', (user_id, yoy_date_str)).fetchone()

    if not yoy_row:
        yoy_row = conn.execute(
            'SELECT * FROM daily_profit WHERE user_id = ? AND date LIKE ? AND day_of_week = ? ORDER BY date LIMIT 1',
            (user_id, f"{target_dt.year - 1}-{target_dt.month:02d}%", today_row['day_of_week'])
        ).fetchone()

    last_year_total = yoy_row['total'] if yoy_row else 0
    last_year_date = yoy_row['date'] if yoy_row else yoy_date_str

    diff = today_row['total'] - last_year_total
    growth_pct = round((diff / last_year_total * 100), 1) if last_year_total > 0 else 0

    return {
        'current_date': date_str,
        'current_dow': today_row['day_of_week'],
        'current_total': today_row['total'],
        'current_dispensing': today_row['dispensing_fee'],
        'current_daily': today_row['daily_net_profit'],
        'current_non_insurance': today_row['non_insurance_margin'],
        'yoy_date': last_year_date,
        'yoy_total': last_year_total,
        'diff': diff,
        'growth_pct': growth_pct
    }


def get_profit_balance_diagnosis(conn, user_id, year, month):
    """3. 순익 황금비율 진단"""
    date_prefix = f"{year}-{month:02d}"
    row = conn.execute('''
        SELECT 
            COALESCE(SUM(dispensing_fee), 0) as disp,
            COALESCE(SUM(daily_net_profit), 0) as daily,
            COALESCE(SUM(non_insurance_margin), 0) as nim,
            COALESCE(SUM(total), 0) as total
        FROM daily_profit WHERE user_id = ? AND date LIKE ?
    ''', (user_id, date_prefix + '%')).fetchone()

    total = row['total']
    disp = row['disp']
    daily = row['daily']
    nim = row['nim']

    if total == 0:
        s_row = conn.execute('SELECT * FROM monthly_summary WHERE user_id = ? AND year = ? AND month = ?', (user_id, year, month)).fetchone()
        if s_row and s_row['grand_total'] > 0:
            total = s_row['grand_total']
            nim = s_row['non_insurance_total']
            dpd = s_row['dispensing_plus_daily_total']
            disp = int(dpd * 0.6)
            daily = dpd - disp

    if total > 0:
        disp_pct = round((disp / total) * 100, 1)
        daily_pct = round((daily / total) * 100, 1)
        nim_pct = round((nim / total) * 100, 1)
    else:
        disp_pct, daily_pct, nim_pct = 0, 0, 0

    if disp_pct >= 75:
        status = "warning"
        status_text = "조제 편중 주의"
        comment = "조제료 의존도가 높습니다. 일반약 및 영양제 매약 상담을 보강하면 처방 변동 리스크를 방어할 수 있습니다."
    elif daily_pct >= 25 and nim_pct >= 10:
        status = "success"
        status_text = "최상의 황금 비율"
        comment = "조제, 매약, 비급여 마진이 이상적인 삼각 균형을 이루고 있어 약국 수익성이 매우 탄탄합니다."
    elif daily_pct >= 20:
        status = "primary"
        status_text = "안정적 포트폴리오"
        comment = "일반 매약 비중이 안정적이며 양호한 수익 밸런스를 유지하고 있습니다."
    else:
        status = "info"
        status_text = "표준 조제형 약국"
        comment = "처방 조제 중심 구조입니다. 비보험·비급여 품목군 마진율 개선을 점검해 보세요."

    return {
        'disp_val': disp,
        'daily_val': daily,
        'nim_val': nim,
        'total': total,
        'disp_pct': disp_pct,
        'daily_pct': daily_pct,
        'nim_pct': nim_pct,
        'status': status,
        'status_text': status_text,
        'comment': comment
    }


# ==========================================
# 🔑 인증 라우트 (회원가입, 로그인, 로그아웃)
# ==========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']

        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['pharmacy_name'] = user['pharmacy_name']
            flash(f"{user['pharmacy_name']}에 오신 것을 환영합니다!", 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'danger')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        pharmacy_name = request.form['pharmacy_name'].strip()
        username = request.form['username'].strip()
        password = request.form['password']

        conn = get_db()
        exist = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()

        if exist:
            flash('이미 존재하는 아이디입니다. 다른 아이디를 사용해 주세요.', 'warning')
            conn.close()
            return redirect(url_for('register'))

        p_hash = generate_password_hash(password)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (username, password_hash, pharmacy_name)
            VALUES (?, ?, ?)
        ''', (username, p_hash, pharmacy_name))
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()

        session['user_id'] = new_id
        session['username'] = username
        session['pharmacy_name'] = pharmacy_name

        flash(f'🎉 {pharmacy_name} 계정이 생성되었습니다! 순익 관리를 시작하세요.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('정상적으로 로그아웃되었습니다.', 'info')
    return redirect(url_for('login'))


# ==========================================
# 📊 메인 기능 라우트 (약국별 격리)
# ==========================================

@app.route('/')
@login_required
def dashboard():
    user_id = session['user_id']
    conn = get_db()

    current_month = get_latest_month_summary(user_id, conn)

    rows = conn.execute('''
        SELECT year, month, dispensing_plus_daily_total, non_insurance_total, grand_total
        FROM monthly_summary
        WHERE user_id = ? AND grand_total > 0
        ORDER BY year, month
    ''', (user_id,)).fetchall()

    monthly_data = {
        'labels': [f"{r['year']}.{r['month']:02d}" for r in rows],
        'totals': [r['grand_total'] for r in rows],
        'dispensing_daily': [r['dispensing_plus_daily_total'] for r in rows],
        'non_insurance': [r['non_insurance_total'] for r in rows]
    }

    years_data = {}
    for r in rows:
        yr = r['year']
        if yr not in years_data:
            years_data[yr] = [0] * 12
        years_data[yr][r['month'] - 1] = r['grand_total']

    year_compare = {
        'labels': [f'{m}월' for m in range(1, 13)],
        'datasets': [
            {'label': f'{yr}년', 'data': data}
            for yr, data in sorted(years_data.items())
            if any(v > 0 for v in data)
        ]
    }

    forecast = get_month_forecast(conn, user_id, current_month['year'], current_month['month'])
    yoy_day = get_yoy_day_comparison(conn, user_id)
    balance = get_profit_balance_diagnosis(conn, user_id, current_month['year'], current_month['month'])

    conn.close()

    return render_template('dashboard.html',
                           current_month=current_month,
                           monthly_data=monthly_data,
                           current_month_data=current_month,
                           year_compare=year_compare,
                           forecast=forecast,
                           yoy_day=yoy_day,
                           balance=balance)


@app.route('/input', methods=['GET', 'POST'])
@login_required
def input_sales():
    user_id = session['user_id']
    conn = get_db()

    if request.method == 'POST':
        date_str = request.form['date']
        dispensing = int(request.form.get('dispensing_fee', 0))
        daily_net = int(request.form.get('daily_net_profit', 0))
        non_insurance = int(request.form.get('non_insurance_margin', 0))
        memo = request.form.get('memo', '')

        dt = datetime.datetime.strptime(date_str, '%Y-%m-%d')
        dow = ['월', '화', '수', '목', '금', '토', '일'][dt.weekday()]
        dpd = dispensing + daily_net
        total = dpd + non_insurance

        try:
            conn.execute('''
                INSERT OR REPLACE INTO daily_profit
                (user_id, date, day_of_week, dispensing_fee, daily_net_profit,
                 dispensing_plus_daily, non_insurance_margin, total, memo, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            ''', (user_id, date_str, dow, dispensing, daily_net, dpd, non_insurance, total, memo))
            conn.commit()

            recalc_monthly_summary(conn, user_id, dt.year, dt.month)
            flash(f'{date_str} ({dow}) 순익이 저장되었습니다. 합계: {total:,}원', 'success')
        except Exception as e:
            flash(f'저장 실패: {str(e)}', 'danger')

        conn.close()
        referrer = request.referrer or ''
        if 'calendar' in referrer:
            return redirect(url_for('calendar_view', year=dt.year, month=dt.month))
        return redirect(url_for('input_sales'))

    recent = conn.execute('''
        SELECT * FROM daily_profit WHERE user_id = ? ORDER BY date DESC LIMIT 20
    ''', (user_id,)).fetchall()

    today = datetime.date.today().isoformat()
    yoy_day = get_yoy_day_comparison(conn, user_id)

    conn.close()
    return render_template('input.html', today=today, recent_sales=recent, yoy_day=yoy_day)


@app.route('/calendar')
@login_required
def calendar_view():
    user_id = session['user_id']
    conn = get_db()

    years_rows = conn.execute('SELECT DISTINCT year FROM monthly_summary WHERE user_id = ? AND grand_total > 0 ORDER BY year DESC', (user_id,)).fetchall()
    years = [r['year'] for r in years_rows]

    latest = get_latest_month_summary(user_id)
    default_year = latest['year'] if latest['year'] > 0 else datetime.date.today().year
    default_month = latest['month'] if latest['month'] > 0 else datetime.date.today().month

    year = request.args.get('year', default_year, type=int)
    month = request.args.get('month', default_month, type=int)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    date_prefix = f"{year}-{month:02d}"
    rows = conn.execute('SELECT * FROM daily_profit WHERE user_id = ? AND date LIKE ?', (user_id, date_prefix + '%')).fetchall()
    profit_by_day = {}
    for r in rows:
        d_num = int(r['date'].split('-')[2])
        profit_by_day[d_num] = dict(r)

    calendar.setfirstweekday(calendar.SUNDAY)
    cal_weeks = calendar.monthcalendar(year, month)

    month_summary = conn.execute(
        'SELECT * FROM monthly_summary WHERE user_id = ? AND year = ? AND month = ?',
        (user_id, year, month)
    ).fetchone()

    total_days_worked = len(rows)
    avg_daily = (month_summary['grand_total'] // total_days_worked) if (month_summary and total_days_worked > 0) else 0

    forecast = get_month_forecast(conn, user_id, year, month)
    conn.close()

    return render_template(
        'calendar.html',
        year=year,
        month=month,
        years=years,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
        cal_weeks=cal_weeks,
        profit_by_day=profit_by_day,
        month_summary=month_summary,
        total_days_worked=total_days_worked,
        avg_daily=avg_daily,
        forecast=forecast
    )


@app.route('/report')
@login_required
def report():
    user_id = session['user_id']
    conn = get_db()

    years_rows = conn.execute('''
        SELECT DISTINCT year FROM monthly_summary WHERE user_id = ? AND grand_total > 0 ORDER BY year DESC
    ''', (user_id,)).fetchall()
    years = [r['year'] for r in years_rows]

    selected_year = request.args.get('year', years[0] if years else datetime.date.today().year, type=int)

    report_data = conn.execute('''
        SELECT * FROM monthly_summary
        WHERE user_id = ? AND year = ? AND grand_total > 0
        ORDER BY month
    ''', (user_id, selected_year)).fetchall()

    year_total_row = conn.execute('''
        SELECT
            COALESCE(SUM(dispensing_plus_daily_total), 0) as dpd,
            COALESCE(SUM(non_insurance_total), 0) as nim,
            COALESCE(SUM(grand_total), 0) as grand
        FROM monthly_summary
        WHERE user_id = ? AND year = ?
    ''', (user_id, selected_year)).fetchone()

    year_total = {
        'dpd': year_total_row['dpd'],
        'nim': year_total_row['nim'],
        'grand': year_total_row['grand']
    }

    report_data_json = {
        'labels': [f"{r['month']}월" for r in report_data],
        'dpd': [r['dispensing_plus_daily_total'] for r in report_data],
        'nim': [r['non_insurance_total'] for r in report_data]
    }

    conn.close()

    return render_template('report.html',
                           years=years,
                           selected_year=selected_year,
                           report_data=report_data,
                           year_total=year_total,
                           report_data_json=report_data_json)


@app.route('/export/<int:year>')
@login_required
def export_csv(year):
    user_id = session['user_id']
    pharmacy = session.get('pharmacy_name', '약국')
    conn = get_db()
    rows = conn.execute('''
        SELECT * FROM monthly_summary WHERE user_id = ? AND year = ? ORDER BY month
    ''', (user_id, year)).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([f'[{pharmacy}] {year}년 순익 리포트'])
    writer.writerow(['연도', '월', '조제+일매순익', '비보험약가차액', '전체합계', '전월대비'])
    for r in rows:
        writer.writerow([r['year'], r['month'], r['dispensing_plus_daily_total'],
                         r['non_insurance_total'], r['grand_total'], r['prev_month_diff']])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'{pharmacy}_순익_{year}.csv'
    )


@app.route('/trend')
@login_required
def trend():
    user_id = session['user_id']
    conn = get_db()

    rows = conn.execute('''
        SELECT year, month, grand_total, dispensing_plus_daily_total, non_insurance_total
        FROM monthly_summary
        WHERE user_id = ? AND grand_total > 0
        ORDER BY year, month
    ''', (user_id,)).fetchall()

    years_dict = {}
    for r in rows:
        yr = r['year']
        if yr not in years_dict:
            years_dict[yr] = [0] * 12
        years_dict[yr][r['month'] - 1] = r['grand_total']

    yearly_data = [
        {'year': yr, 'data': data}
        for yr, data in sorted(years_dict.items())
        if any(v > 0 for v in data)
    ]

    dow_rows = conn.execute('''
        SELECT day_of_week, AVG(total) as avg_total, COUNT(*) as cnt
        FROM daily_profit
        WHERE user_id = ? AND total > 0
        GROUP BY day_of_week
    ''', (user_id,)).fetchall()

    day_order = ['월', '화', '수', '목', '금', '토']
    dow_dict = {r['day_of_week']: int(r['avg_total']) for r in dow_rows}
    day_of_week_data = {
        'labels': day_order,
        'values': [dow_dict.get(d, 0) for d in day_order]
    }

    all_months = conn.execute('''
        SELECT year, month, grand_total, prev_month_diff
        FROM monthly_summary
        WHERE user_id = ? AND grand_total > 0
        ORDER BY year DESC, month DESC
        LIMIT 24
    ''', (user_id,)).fetchall()

    growth_labels = []
    growth_values = []
    for r in reversed(all_months):
        growth_labels.append(f"{r['year']}.{r['month']:02d}")
        prev_total = r['grand_total'] - r['prev_month_diff']
        rate = round((r['prev_month_diff'] / prev_total) * 100, 1) if prev_total > 0 else 0
        growth_values.append(rate)

    growth_data = {'labels': growth_labels, 'values': growth_values}

    ratio_data = {
        'labels': [f"{r['year']}.{r['month']:02d}" for r in rows],
        'dispensing': [r['dispensing_plus_daily_total'] for r in rows],
        'non_insurance': [r['non_insurance_total'] for r in rows]
    }

    heatmap_data = []
    if rows:
        all_totals = [r['grand_total'] for r in rows if r['grand_total'] > 0]
        min_val = min(all_totals) if all_totals else 0
        max_val = max(all_totals) if all_totals else 1

        for yr, data in sorted(years_dict.items()):
            months = []
            for val in data:
                if val > 0 and max_val > min_val:
                    intensity = (val - min_val) / (max_val - min_val)
                    r_c = int(220 - intensity * 180)
                    g_c = int(240 - intensity * 80)
                    b_c = int(255 - intensity * 60)
                    color = f'rgb({r_c},{g_c},{b_c})'
                    text_color = '#fff' if intensity > 0.5 else '#333'
                else:
                    color = '#f8f9fa'
                    text_color = '#ccc'
                months.append({'value': val, 'color': color, 'text_color': text_color})
            heatmap_data.append({'year': yr, 'months': months})

    conn.close()

    return render_template('trend.html',
                           yearly_data=yearly_data,
                           day_of_week_data=day_of_week_data,
                           growth_data=growth_data,
                           ratio_data=ratio_data,
                           heatmap_data=heatmap_data)


@app.route('/download_template')
@login_required
def download_template():
    """표준 엑셀 양식 다운로드"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "일별순익_입력양식"

    headers = ["날짜(YYYY-MM-DD)", "요일", "조제료(원)", "일매순익(원)", "비보험약가차액(원)", "메모"]
    ws.append(headers)

    # 샘플 데이터 3행
    sample_rows = [
        ["2026-09-15", "화", 572890, 205997, 156934, "샘플 데이터 1"],
        ["2026-09-16", "수", 316570, 185322, 287476, "샘플 데이터 2"],
        ["2026-09-17", "목", 190010, 200664, 227933, "샘플 데이터 3"]
    ]
    for row in sample_rows:
        ws.append(row)

    header_fill = openpyxl.styles.PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    header_font = openpyxl.styles.Font(name="맑은 고딕", size=11, bold=True, color="FFFFFF")

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = openpyxl.styles.Alignment(horizontal="center", vertical="center")

    for col in ws.columns:
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = 20

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='약국순익_표준업로드양식.xlsx'
    )


@app.route('/upload_excel', methods=['POST'])
@login_required
def upload_excel():
    user_id = session['user_id']
    if 'excel_file' not in request.files:
        flash('파일을 선택해 주세요.', 'warning')
        return redirect(url_for('dashboard'))

    file = request.files['excel_file']
    if file.filename == '':
        flash('파일을 선택해 주세요.', 'warning')
        return redirect(url_for('dashboard'))

    password = request.form.get('excel_password', '').strip()
    file_bytes = file.read()

    try:
        # 암호화 파일인지 검사
        if file_bytes.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1') or password:
            try:
                decrypted = io.BytesIO()
                office_file = msoffcrypto.OfficeFile(io.BytesIO(file_bytes))
                office_file.load_key(password=password or '7581')
                office_file.decrypt(decrypted)
                decrypted.seek(0)
                wb = openpyxl.load_workbook(decrypted, data_only=True)
            except Exception:
                wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        else:
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)

        conn = get_db()
        cursor = conn.cursor()
        imported_count = 0
        touched_months = set()

        # [모드 1] 표준 템플릿 양식 감지
        first_ws = wb.active
        first_row_vals = [str(first_ws.cell(1, c).value or '') for c in range(1, 7)]
        is_standard_template = any('날짜' in v for v in first_row_vals) and any('조제' in v for v in first_row_vals)

        if is_standard_template:
            for r in range(2, first_ws.max_row + 1):
                raw_date = first_ws.cell(r, 1).value
                if not raw_date:
                    continue
                date_str = str(raw_date)[:10].strip()
                if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
                    continue

                dow = str(first_ws.cell(r, 2).value or '')
                def s_int(c):
                    v = first_ws.cell(r, c).value
                    try: return int(v) if v else 0
                    except: return 0

                disp = s_int(3)
                daily = s_int(4)
                nim = s_int(5)
                memo = str(first_ws.cell(r, 6).value or '')

                dpd = disp + daily
                tot = dpd + nim

                cursor.execute('''
                    INSERT OR REPLACE INTO daily_profit
                    (user_id, date, day_of_week, dispensing_fee, daily_net_profit,
                     dispensing_plus_daily, non_insurance_margin, total, memo, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                ''', (user_id, date_str, dow, disp, daily, dpd, nim, tot, memo))
                imported_count += 1
                touched_months.add((int(date_str[:4]), int(date_str[5:7])))

            # 변경된 월 통계 자동 재계산
            for yr, mo in touched_months:
                recalc_monthly_summary(conn, user_id, yr, mo)

            conn.commit()
            conn.close()
            flash(f'🎉 표준 엑셀 데이터 가져오기 완료! (총 {imported_count}일치 데이터가 등록되었습니다)', 'success')
            return redirect(url_for('dashboard'))

        # [모드 2] 기존 순익표 (주표 + 합계) 시트 구조 처리
        # 합계 시트 (2번째 시트)
        if len(wb.sheetnames) >= 2:
            ws_sum = wb[wb.sheetnames[1]]
            for r in range(2, ws_sum.max_row + 1):
                yr = ws_sum.cell(r, 1).value
                mo_str = str(ws_sum.cell(r, 2).value or '')
                if yr is None or not isinstance(yr, (int, float)):
                    continue
                m_match = re.search(r'(\d+)', mo_str)
                if not m_match:
                    continue
                def s_int(c):
                    v = ws_sum.cell(r, c).value
                    try: return int(v) if v else 0
                    except: return 0
                dpd = s_int(3)
                nim = s_int(4)
                grand = s_int(5)
                diff = s_int(6)
                if dpd == 0 and nim == 0 and grand == 0:
                    continue
                cursor.execute('''
                    INSERT OR REPLACE INTO monthly_summary
                    (user_id, year, month, dispensing_plus_daily_total, non_insurance_total, grand_total, prev_month_diff)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, int(yr), int(m_match.group(1)), dpd, nim, grand, diff))

        # 주표 시트 (1번째 시트)
        ws_week = wb[wb.sheetnames[0]]
        current_year = 2022
        current_month = 10
        current_start_day = 17
        day_offset = 0
        DAY_NAMES = ['월', '화', '수', '목', '금', '토']

        imported_count = 0
        for r in range(1, ws_week.max_row + 1):
            cell_a = ws_week.cell(r, 1).value
            if cell_a is None:
                day_offset = 0
                continue
            cell_a_str = str(cell_a).strip()
            week_match = re.search(r'(\d+)\s*월?\s*(\d+)\s*일?\s*~', cell_a_str)
            if week_match:
                new_month = int(week_match.group(1))
                current_start_day = int(week_match.group(2))
                day_offset = 0
                if new_month < current_month and new_month <= 2 and current_month >= 11:
                    current_year += 1
                current_month = new_month
                continue

            if cell_a_str in DAY_NAMES:
                def s_int(c):
                    v = ws_week.cell(r, c).value
                    try: return int(v) if v else 0
                    except: return 0
                d_fee = s_int(2)
                d_net = s_int(3)
                dpd = s_int(4)
                nim = s_int(5)
                tot = s_int(6)
                if d_fee == 0 and d_net == 0 and nim == 0 and tot == 0:
                    day_offset += 1
                    continue
                if dpd == 0 and (d_fee > 0 or d_net > 0):
                    dpd = d_fee + d_net
                if tot == 0:
                    tot = dpd + nim

                day = current_start_day + day_offset
                day_offset += 1
                act_mo = current_month
                act_yr = current_year
                max_d = calendar.monthrange(act_yr, act_mo)[1]
                if day > max_d:
                    day -= max_d
                    act_mo += 1
                    if act_mo > 12:
                        act_mo = 1
                        act_yr += 1
                try:
                    dt = datetime.date(act_yr, act_mo, day)
                    cursor.execute('''
                        INSERT OR REPLACE INTO daily_profit
                        (user_id, date, day_of_week, dispensing_fee, daily_net_profit,
                         dispensing_plus_daily, non_insurance_margin, total, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                    ''', (user_id, dt.isoformat(), cell_a_str, d_fee, d_net, dpd, nim, tot))
                    imported_count += 1
                except ValueError:
                    pass

        conn.commit()
        conn.close()
        flash(f'🎉 엑셀 데이터 가져오기 완료! (총 {imported_count}일치 순익 데이터가 등록되었습니다)', 'success')
    except Exception as e:
        flash(f'엑셀 가져오기 실패: {str(e)}', 'danger')

# ==========================================
# 🛡️ 관리자 전용 콘솔 라우트
# ==========================================

@app.route('/admin')
@admin_required
def admin_dashboard():
    try:
        users = get_all_users_stats()
        
        total_users = len(users)
        total_records = sum(int(u['total_entries'] or 0) for u in users)
        total_profit = sum(int(u['total_profit'] or 0) for u in users)
        
        return render_template(
            'admin.html',
            users=users,
            total_users=total_users,
            total_records=total_records,
            total_profit=total_profit
        )
    except Exception as e:
        flash(f'관리자 콘솔 로딩 중 오류: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))


@app.route('/admin/switch_user/<int:target_user_id>')
@admin_required
def admin_switch_user(target_user_id):
    """관리자가 특정 약국의 계정으로 전환하여 데이터를 점검/둘러보기"""
    conn = get_db()
    target_user = conn.execute('SELECT * FROM users WHERE id = ?', (target_user_id,)).fetchone()
    conn.close()

    if not target_user:
        flash('존재하지 않는 회원입니다.', 'danger')
        return redirect(url_for('admin_dashboard'))

    # 원래 관리자의 user_id를 기억 (이미 스위칭 중이 아니었을 때만 저장)
    if 'original_admin_id' not in session:
        session['original_admin_id'] = session['user_id']
        session['original_admin_user'] = session['username']

    session['user_id'] = target_user['id']
    session['username'] = target_user['username']
    session['pharmacy_name'] = target_user['pharmacy_name']

    flash(f"🔍 [{target_user['pharmacy_name']}] 계정으로 화면이 전환되었습니다. 관리자 복귀는 상단 배너를 클릭하세요.", 'info')
    return redirect(url_for('dashboard'))


@app.route('/admin/switch_back')
def admin_switch_back():
    """관리자 원래 계정으로 복귀"""
    if 'original_admin_id' in session:
        admin_id = session['original_admin_id']
        conn = get_db()
        admin_user = conn.execute('SELECT * FROM users WHERE id = ?', (admin_id,)).fetchone()
        conn.close()

        if admin_user:
            session['user_id'] = admin_user['id']
            session['username'] = admin_user['username']
            session['pharmacy_name'] = admin_user['pharmacy_name']

        session.pop('original_admin_id', None)
        session.pop('original_admin_user', None)
        flash('관리자(admin) 계정으로 안전하게 복귀하였습니다.', 'success')

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_user/<int:target_user_id>', methods=['POST'])
@admin_required
def admin_delete_user(target_user_id):
    """테스트 계정 또는 회원 데이터 삭제"""
    conn = get_db()
    target_user = conn.execute('SELECT * FROM users WHERE id = ?', (target_user_id,)).fetchone()
    conn.close()

    if not target_user:
        flash('해당 회원을 찾을 수 없습니다.', 'danger')
        return redirect(url_for('admin_dashboard'))

    if target_user['username'] == 'admin' or target_user['id'] == 1:
        flash('관리자(admin) 본인 계정은 삭제할 수 없습니다.', 'danger')
        return redirect(url_for('admin_dashboard'))

    p_name = target_user['pharmacy_name']
    delete_user_and_data(target_user_id)
    flash(f"'{p_name}' 계정 및 등록된 모든 데이터가 삭제되었습니다.", 'warning')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/export_backup')
@admin_required
def admin_export_backup():
    """관리자 전용: 전체 약국 회원 및 순익 데이터 원클릭 엑셀 백업 다운로드"""
    try:
        from urllib.parse import quote
        conn = get_db()
        wb = openpyxl.Workbook()

        # 1. 회원 목록 시트
        ws_users = wb.active
        ws_users.title = "약국회원목록"
        ws_users.append(["회원번호(ID)", "아이디", "약국명", "가입일시"])
        users = conn.execute("SELECT id, username, pharmacy_name, created_at FROM users ORDER BY id").fetchall()
        for u in users:
            ws_users.append([u['id'], u['username'], u['pharmacy_name'], str(u['created_at'])[:19] if u['created_at'] else ''])

        # 2. 일별 순익 데이터 시트
        ws_daily = wb.create_sheet(title="일별순익전체")
        ws_daily.append(["회원ID", "약국명", "날짜", "요일", "조제료", "일매순익", "조제+일매", "비보험약가차액", "전체합계", "메모"])
        daily_rows = conn.execute('''
            SELECT d.user_id, u.pharmacy_name, d.date, d.day_of_week, 
                   d.dispensing_fee, d.daily_net_profit, d.dispensing_plus_daily, 
                   d.non_insurance_margin, d.total, d.memo
            FROM daily_profit d
            LEFT JOIN users u ON d.user_id = u.id
            ORDER BY d.user_id, d.date
        ''').fetchall()
        for r in daily_rows:
            ws_daily.append([
                r['user_id'], r['pharmacy_name'], r['date'], r['day_of_week'],
                r['dispensing_fee'], r['daily_net_profit'], r['dispensing_plus_daily'],
                r['non_insurance_margin'], r['total'], r['memo'] or ''
            ])

        # 3. 월별 요약 시트
        ws_monthly = wb.create_sheet(title="월별요약전체")
        ws_monthly.append(["회원ID", "약국명", "연도", "월", "조제+일매합계", "비보험합계", "전체합계", "전월대비증감"])
        monthly_rows = conn.execute('''
            SELECT m.user_id, u.pharmacy_name, m.year, m.month,
                   m.dispensing_plus_daily_total, m.non_insurance_total, m.grand_total, m.prev_month_diff
            FROM monthly_summary m
            LEFT JOIN users u ON m.user_id = u.id
            ORDER BY m.user_id, m.year, m.month
        ''').fetchall()
        for m in monthly_rows:
            ws_monthly.append([
                m['user_id'], m['pharmacy_name'], m['year'], m['month'],
                m['dispensing_plus_daily_total'], m['non_insurance_total'], m['grand_total'], m['prev_month_diff']
            ])

        conn.close()

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f"약국순익관리_전체DB백업_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        encoded_filename = quote(filename)

        response = send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded_filename}"
        return response

    except Exception as e:
        flash(f'DB 백업 파일 생성 중 오류 발생: {str(e)}', 'danger')
        return redirect(url_for('admin_dashboard'))


if __name__ == '__main__':
    init_db()
    print("\n=== 약국 순익 관리 시스템 (다중 약국 온라인 SaaS) 시작 ===")
    print("접속 주소: http://localhost:5000\n")
    app.run(debug=True, host='0.0.0.0', port=5000)
