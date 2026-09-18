"""
약국 순익 관리 시스템 - 다중 약국 지원 온라인 SaaS 버전
"""
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db, init_db, recalc_monthly_summary, get_all_users_stats, delete_user_and_data, get_pool_status
from functools import wraps
import datetime
import calendar
import json
import csv
import io
import os
import re
import time
import threading
import openpyxl
import msoffcrypto
from urllib.parse import quote

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'pharmacy-profit-saas-super-secret-key-2026')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 86400  # 정적 에셋 24시간 브라우저 캐싱

# ⚡ 사용자별 초고속 인메모리 스마트 캐시 (조회 99%, 변경 1% SaaS 구조에 최적화)
_USER_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 300  # 5분 유효


def get_cached_monthly_summary(conn=None, user_id=None):
    """사용자의 월별 요약 전체 목록 캐시 (캐시 히트 시 DB 연결/쿼리 0회, 0ms 즉시 반환)"""
    now = time.time()
    with _CACHE_LOCK:
        if user_id in _USER_CACHE:
            entry = _USER_CACHE[user_id].get('monthly_summary')
            if entry and (now - entry['ts'] < _CACHE_TTL):
                return entry['data']

    # 캐시 미스 시에만 DB 연결 생성
    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True
    try:
        rows = conn.execute('''
            SELECT year, month, dispensing_plus_daily_total, non_insurance_total, grand_total, prev_month_diff
            FROM monthly_summary
            WHERE user_id = ? AND grand_total > 0
            ORDER BY year, month
        ''', (user_id,)).fetchall()

        data = [dict(r) for r in rows]
        with _CACHE_LOCK:
            if user_id not in _USER_CACHE:
                _USER_CACHE[user_id] = {}
            _USER_CACHE[user_id]['monthly_summary'] = {'ts': now, 'data': data}
        return data
    finally:
        if should_close:
            conn.close()


def get_cached_current_month_dailies(conn=None, user_id=None, cur_year=None, cur_month=None):
    """사용자의 당월 일별 데이터 캐시 (캐시 히트 시 DB 연결/쿼리 0회, 0ms 즉시 반환)"""
    now = time.time()
    cache_key = f'daily_{cur_year}_{cur_month:02d}'
    with _CACHE_LOCK:
        if user_id in _USER_CACHE:
            entry = _USER_CACHE[user_id].get(cache_key)
            if entry and (now - entry['ts'] < _CACHE_TTL):
                return entry['data']

    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True
    try:
        date_prefix = f"{cur_year}-{cur_month:02d}"
        cur_month_rows = conn.execute('''
            SELECT date, day_of_week, dispensing_fee, daily_net_profit, non_insurance_margin, total
            FROM daily_profit
            WHERE user_id = ? AND date LIKE ?
            ORDER BY date ASC
        ''', (user_id, date_prefix + '%')).fetchall()

        data = [dict(r) for r in cur_month_rows]
        with _CACHE_LOCK:
            if user_id not in _USER_CACHE:
                _USER_CACHE[user_id] = {}
            _USER_CACHE[user_id][cache_key] = {'ts': now, 'data': data}
        return data
    finally:
        if should_close:
            conn.close()


def get_cached_dow_avg(conn=None, user_id=None):
    """사용자의 요일별 평균 캐시 (캐시 히트 시 DB 연결/쿼리 0회, 0ms 즉시 반환)"""
    now = time.time()
    with _CACHE_LOCK:
        if user_id in _USER_CACHE:
            entry = _USER_CACHE[user_id].get('dow_avg')
            if entry and (now - entry['ts'] < _CACHE_TTL):
                return entry['data']

    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True
    try:
        dow_rows = conn.execute('''
            SELECT day_of_week, AVG(total) as avg_total
            FROM daily_profit
            WHERE user_id = ? AND total > 0
            GROUP BY day_of_week
        ''', (user_id,)).fetchall()

        data = {r['day_of_week']: int(r['avg_total'] or 0) for r in dow_rows}
        with _CACHE_LOCK:
            if user_id not in _USER_CACHE:
                _USER_CACHE[user_id] = {}
            _USER_CACHE[user_id]['dow_avg'] = {'ts': now, 'data': data}
        return data
    finally:
        if should_close:
            conn.close()


def invalidate_user_cache(user_id=None):
    """데이터 변경 시 해당 사용자의 인메모리 캐시 즉시 무효화 (실시간 정합성 보장)"""
    with _CACHE_LOCK:
        if user_id is None:
            _USER_CACHE.clear()
        elif user_id in _USER_CACHE:
            _USER_CACHE.pop(user_id, None)


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
    try:
        row = conn.execute(
            'SELECT * FROM monthly_summary WHERE user_id = ? AND grand_total > 0 ORDER BY year DESC, month DESC LIMIT 1',
            (user_id,)
        ).fetchone()
    finally:
        if should_close:
            conn.close()

    if row:
        return {
            'year': row['year'],
            'month': row['month'],
            'dispensing_plus_daily': int(row['dispensing_plus_daily_total'] or 0),
            'non_insurance': int(row['non_insurance_total'] or 0),
            'grand_total': int(row['grand_total'] or 0),
            'diff': int(row['prev_month_diff'] or 0)
        }
    now = datetime.date.today()
    return {'year': now.year, 'month': now.month, 'dispensing_plus_daily': 0, 'non_insurance': 0, 'grand_total': 0, 'diff': 0}


def get_month_forecast(conn, user_id, year, month, entered_rows=None, dow_avg=None, last_year_total=None):
    """1. 이번 달 최종 순익 자동 예측기 (사전 로딩 데이터 지원으로 쿼리 0~1회 최소화)"""
    date_prefix = f"{year}-{month:02d}"

    if entered_rows is None:
        entered_rows = conn.execute(
            'SELECT date, day_of_week, total FROM daily_profit WHERE user_id = ? AND date LIKE ?',
            (user_id, date_prefix + '%')
        ).fetchall()

    current_total = sum(int(r['total'] or 0) for r in entered_rows)
    entered_days = set(int(r['date'].split('-')[2]) for r in entered_rows)

    if dow_avg is None:
        dow_rows = conn.execute(
            'SELECT day_of_week, AVG(total) as avg_total FROM daily_profit WHERE user_id = ? AND total > 0 GROUP BY day_of_week',
            (user_id,)
        ).fetchall()
        dow_avg = {r['day_of_week']: int(r['avg_total'] or 0) for r in dow_rows}

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

    if last_year_total is None:
        prev_year_summary = conn.execute(
            'SELECT grand_total FROM monthly_summary WHERE user_id = ? AND year = ? AND month = ?',
            (user_id, year - 1, month)
        ).fetchone()
        last_year_total = int(prev_year_summary['grand_total'] or 0) if prev_year_summary else 0

    yoy_growth_pct = float(round(((forecast_total - last_year_total) / float(last_year_total) * 100), 1)) if last_year_total > 0 else 0.0

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


def get_yoy_day_comparison(conn, user_id, date_str=None, today_row=None):
    """2. 작년 오늘 vs 올해 오늘 1:1 맞춤 비교 (사전 로딩 데이터 지원)"""
    if today_row is None:
        if not date_str:
            latest = conn.execute('SELECT date FROM daily_profit WHERE user_id = ? ORDER BY date DESC LIMIT 1', (user_id,)).fetchone()
            if not latest:
                return None
            date_str = latest['date']

        today_row = conn.execute('SELECT * FROM daily_profit WHERE user_id = ? AND date = ?', (user_id, date_str)).fetchone()
        if not today_row:
            return None
    else:
        date_str = today_row['date']

    target_dt = datetime.date.fromisoformat(date_str)
    yoy_dt = target_dt - datetime.timedelta(days=364)
    yoy_date_str = yoy_dt.isoformat()

    yoy_row = conn.execute('SELECT * FROM daily_profit WHERE user_id = ? AND date = ?', (user_id, yoy_date_str)).fetchone()

    if not yoy_row:
        yoy_row = conn.execute(
            'SELECT * FROM daily_profit WHERE user_id = ? AND date LIKE ? AND day_of_week = ? ORDER BY date LIMIT 1',
            (user_id, f"{target_dt.year - 1}-{target_dt.month:02d}%", today_row['day_of_week'])
        ).fetchone()

    last_year_total = int(yoy_row['total'] or 0) if yoy_row else 0
    last_year_date = yoy_row['date'] if yoy_row else yoy_date_str

    diff = int(today_row['total'] or 0) - last_year_total
    growth_pct = float(round((diff / float(last_year_total) * 100), 1)) if last_year_total > 0 else 0.0

    return {
        'current_date': date_str,
        'current_dow': today_row['day_of_week'],
        'current_total': int(today_row['total'] or 0),
        'current_dispensing': int(today_row['dispensing_fee'] or 0),
        'current_daily': int(today_row['daily_net_profit'] or 0),
        'current_non_insurance': int(today_row['non_insurance_margin'] or 0),
        'yoy_date': last_year_date,
        'yoy_total': last_year_total,
        'diff': diff,
        'growth_pct': growth_pct
    }


def get_profit_balance_diagnosis(conn, user_id, year, month, entered_rows=None, month_summary_row=None):
    """3. 순익 황금비율 진단 (사전 로딩 데이터 활용 시 쿼리 0회)"""
    if entered_rows is not None and len(entered_rows) > 0:
        disp = sum(int(r['dispensing_fee'] or 0) for r in entered_rows)
        daily = sum(int(r['daily_net_profit'] or 0) for r in entered_rows)
        nim = sum(int(r['non_insurance_margin'] or 0) for r in entered_rows)
        total = sum(int(r['total'] or 0) for r in entered_rows)
    else:
        date_prefix = f"{year}-{month:02d}"
        row = conn.execute('''
            SELECT 
                COALESCE(SUM(dispensing_fee), 0) as disp,
                COALESCE(SUM(daily_net_profit), 0) as daily,
                COALESCE(SUM(non_insurance_margin), 0) as nim,
                COALESCE(SUM(total), 0) as total
            FROM daily_profit WHERE user_id = ? AND date LIKE ?
        ''', (user_id, date_prefix + '%')).fetchone()

        total = int(row['total'] or 0)
        disp = int(row['disp'] or 0)
        daily = int(row['daily'] or 0)
        nim = int(row['nim'] or 0)

    if total == 0:
        s_row = month_summary_row or conn.execute('SELECT * FROM monthly_summary WHERE user_id = ? AND year = ? AND month = ?', (user_id, year, month)).fetchone()
        if s_row and s_row['grand_total'] > 0:
            total = int(s_row['grand_total'] or 0)
            nim = int(s_row['non_insurance_total'] or 0)
            dpd = int(s_row['dispensing_plus_daily_total'] or 0)
            disp = int(dpd * 0.6)
            daily = dpd - disp

    if total > 0:
        disp_pct = float(round((disp / total) * 100, 1))
        daily_pct = float(round((daily / total) * 100, 1))
        nim_pct = float(round((nim / total) * 100, 1))
    else:
        disp_pct, daily_pct, nim_pct = 0.0, 0.0, 0.0

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


def get_ai_narrative_briefing(conn, user_id, current_month, forecast, latest_row=None, cur_cum=None, cur_month_rows=None):
    """대시보드: 시적이고 감성적인 AI 경영 브리핑 리포트 생성 (고속 인메모리 연계)"""
    if latest_row is None:
        latest_row = conn.execute(
            'SELECT * FROM daily_profit WHERE user_id = ? ORDER BY date DESC LIMIT 1',
            (user_id,)
        ).fetchone()

    if not latest_row:
        return None

    dt = datetime.date.fromisoformat(latest_row['date'])
    dow = latest_row['day_of_week']
    today_total = int(latest_row['total'] or 0)
    disp = int(latest_row['dispensing_fee'] or 0)
    daily = int(latest_row['daily_net_profit'] or 0)
    nim = int(latest_row['non_insurance_margin'] or 0)

    # 1. 지난주 같은 요일 비교 (7일 전) - 당월 일별 데이터에서 우선 인메모리 탐색 (쿼리 0회)
    prev_week_date = (dt - datetime.timedelta(days=7)).isoformat()
    pw_row = None
    if cur_month_rows:
        for r in cur_month_rows:
            if r['date'] == prev_week_date:
                pw_row = r
                break
    if pw_row is None:
        pw_row = conn.execute(
            'SELECT total FROM daily_profit WHERE user_id = ? AND date = ?',
            (user_id, prev_week_date)
        ).fetchone()

    # 2. 이번 달 누적 (인메모리 계산값 우선 활용)
    if cur_cum is None:
        if cur_month_rows:
            cur_cum = sum(int(r['total'] or 0) for r in cur_month_rows if r['date'] <= latest_row['date'])
        else:
            cur_month_prefix = f"{dt.year}-{dt.month:02d}"
            cur_cum_row = conn.execute(
                'SELECT COALESCE(SUM(total), 0) as cum FROM daily_profit WHERE user_id = ? AND date >= ? AND date <= ?',
                (user_id, f"{cur_month_prefix}-01", latest_row['date'])
            ).fetchone()
            cur_cum = int(cur_cum_row['cum'] or 0) if cur_cum_row else today_total

    # 3. 지난달(전월) 및 작년 동기 누적 1회 통합 쿼리 (쿼리 2회 -> 1회 통합)
    prev_m_year = dt.year if dt.month > 1 else dt.year - 1
    prev_m_month = dt.month - 1 if dt.month > 1 else 12
    prev_m_prefix = f"{prev_m_year}-{prev_m_month:02d}"
    prev_m_target_day = min(dt.day, calendar.monthrange(prev_m_year, prev_m_month)[1])

    last_y_prefix = f"{dt.year - 1}-{dt.month:02d}"

    cum_rows = conn.execute('''
        SELECT 
            COALESCE(SUM(CASE WHEN date >= ? AND date <= ? THEN total ELSE 0 END), 0) as prev_cum,
            COALESCE(SUM(CASE WHEN date >= ? AND date <= ? THEN total ELSE 0 END), 0) as last_y_cum
        FROM daily_profit 
        WHERE user_id = ? AND (
            (date >= ? AND date <= ?) OR 
            (date >= ? AND date <= ?)
        )
    ''', (
        f"{prev_m_prefix}-01", f"{prev_m_prefix}-{prev_m_target_day:02d}",
        f"{last_y_prefix}-01", f"{last_y_prefix}-{dt.day:02d}",
        user_id,
        f"{prev_m_prefix}-01", f"{prev_m_prefix}-{prev_m_target_day:02d}",
        f"{last_y_prefix}-01", f"{last_y_prefix}-{dt.day:02d}"
    )).fetchone()
    prev_cum = int(cum_rows['prev_cum'] or 0) if cum_rows else 0
    last_y_cum = int(cum_rows['last_y_cum'] or 0) if cum_rows else 0

    paragraphs = []

    # 단락 1: 오늘 성과 및 지난주 동요일 비교
    p1 = f"<strong>오늘 우리 약국의 순익은 {today_total:,}원입니다.</strong>"
    if pw_row and pw_row['total'] and int(pw_row['total']) > 0:
        pw_total = int(pw_row['total'])
        pw_diff = today_total - pw_total
        pw_pct = float(round((pw_diff / float(pw_total)) * 100, 1))
        dir_txt = "증가하며" if pw_diff >= 0 else "기록하며"
        badge_color = "text-success" if pw_diff >= 0 else "text-danger"
        p1 += f" 지난주 같은 {dow}요일({pw_total:,}원) 대비 <strong class='{badge_color}'>{pw_pct:+,}% {dir_txt}</strong> 주간 흐름을 힘차게 견인했습니다."
    else:
        p1 += f" 이번 주 {dow}요일 순익 흐름을 안정적으로 이어갔습니다."
    p1 += f" 조제료 {disp:,}원과 함께 매약 순익 {daily:,}원, 비보험 약가차액 {nim:,}원이 조화롭게 어우러진 하루입니다."
    paragraphs.append(p1)

    # 단락 2: 지난달 및 작년 비교
    p2_items = []
    if prev_cum > 0:
        m_diff = cur_cum - prev_cum
        m_pct = float(round((m_diff / float(prev_cum)) * 100, 1))
        m_status = "앞서 달리고 있으며" if m_diff >= 0 else "조금 신중한 흐름이며"
        m_color = "text-success" if m_diff >= 0 else "text-danger"
        p2_items.append(f"<strong>지난달({prev_m_month}월) 같은 시점 누적 대비 <span class='{m_color}'>{m_pct:+,}%</span></strong> {m_status}")

    if last_y_cum > 0:
        y_diff = cur_cum - last_y_cum
        y_pct = float(round((y_diff / float(last_y_cum)) * 100, 1))
        y_status = "더 단단해진 성장세" if y_diff >= 0 else "안정적인 방어선"
        y_color = "text-success" if y_diff >= 0 else "text-danger"
        p2_items.append(f"<strong>작년 {dt.month}월 동기 대비 <span class='{y_color}'>{y_pct:+,}%</span></strong> {y_status}를 보여줍니다")

    if p2_items:
        paragraphs.append("시야를 넓혀보면, " + ", ".join(p2_items) + ".")

    # 단락 3: 월말 고지 전망
    forecast_total = forecast.get('forecast_total', 0) if forecast else 0
    if forecast_total > 0:
        forecast_man = int(forecast_total / 10000)
        paragraphs.append(f"현재 페이스를 유지한다면 이번 {dt.month}월은 <strong>월말 예상 순익 약 {forecast_man:,}만 원 고지</strong>를 달성할 것으로 전망됩니다. 오늘도 묵묵히 자리를 지키며 일궈내신 소중한 성과입니다.")

    return {
        'date_title': f"{dt.month}월 {dt.day}일 ({dow})",
        'paragraphs': paragraphs
    }


# ==========================================
# 🔑 인증 라우트 (회원가입, 로그인, 로그아웃)
# ==========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        conn = get_db()
        try:
            user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        finally:
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
        pharmacy_name = request.form.get('pharmacy_name', '').strip()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not pharmacy_name or not username or not password:
            flash('약국명, 아이디, 비밀번호를 모두 입력해주세요.', 'warning')
            return redirect(url_for('register'))

        conn = get_db()
        try:
            exist = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()

            if exist:
                flash('이미 존재하는 아이디입니다. 다른 아이디를 사용해 주세요.', 'warning')
                return redirect(url_for('register'))

            p_hash = generate_password_hash(password)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (username, password_hash, pharmacy_name)
                VALUES (?, ?, ?)
            ''', (username, p_hash, pharmacy_name))
            conn.commit()
            new_id = cursor.lastrowid
        finally:
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

    # ⚡ 대시보드 스마트 캐시 확인: 캐시 히트 시 DB 연결/쿼리 0회, 0.01초 즉시 렌더링!
    now = time.time()
    with _CACHE_LOCK:
        if user_id in _USER_CACHE:
            entry = _USER_CACHE[user_id].get('dashboard_ctx')
            if entry and (now - entry['ts'] < _CACHE_TTL):
                return render_template('dashboard.html', **entry['ctx'])

    conn = get_db()
    try:
        # [초고속 스마트 캐시 1] monthly_summary 전체를 캐시에서 조회 (캐시 히트 시 DB 0ms)
        rows = get_cached_monthly_summary(conn, user_id)

        if rows:
            last_r = rows[-1]
            current_month = {
                'year': int(last_r['year']),
                'month': int(last_r['month']),
                'dispensing_plus_daily': int(last_r['dispensing_plus_daily_total'] or 0),
                'non_insurance': int(last_r['non_insurance_total'] or 0),
                'grand_total': int(last_r['grand_total'] or 0),
                'diff': int(last_r['prev_month_diff'] or 0)
            }
        else:
            now_dt = datetime.date.today()
            current_month = {'year': now_dt.year, 'month': now_dt.month, 'dispensing_plus_daily': 0, 'non_insurance': 0, 'grand_total': 0, 'diff': 0}

        monthly_data = {
            'labels': [f"{r['year']}.{r['month']:02d}" for r in rows],
            'totals': [int(r['grand_total']) for r in rows],
            'dispensing_daily': [int(r['dispensing_plus_daily_total']) for r in rows],
            'non_insurance': [int(r['non_insurance_total']) for r in rows]
        }

        years_data = {}
        for r in rows:
            yr = int(r['year'])
            if yr not in years_data:
                years_data[yr] = [0] * 12
            years_data[yr][int(r['month']) - 1] = int(r['grand_total'])

        year_compare = {
            'labels': [f'{m}월' for m in range(1, 13)],
            'datasets': [
                {'label': f'{yr}년', 'data': [int(v) for v in data]}
                for yr, data in sorted(years_data.items())
                if any(v > 0 for v in data)
            ]
        }

        # [초고속 스마트 캐시 2] 당월 daily_profit 데이터 캐시 조회 (캐시 히트 시 DB 0ms)
        cur_year = current_month['year']
        cur_month = current_month['month']
        cur_month_rows = get_cached_current_month_dailies(conn, user_id, cur_year, cur_month)

        # [초고속 스마트 캐시 3] 요일별 평균 캐시 조회 (캐시 히트 시 DB 0ms)
        dow_avg = get_cached_dow_avg(conn, user_id)

        # 작년 동월 총합 (이미 rows에 있으므로 쿼리 0회!)
        last_year_total = 0
        for r in rows:
            if int(r['year']) == cur_year - 1 and int(r['month']) == cur_month:
                last_year_total = int(r['grand_total'] or 0)
                break

        # 최신일 레코드 (당월 데이터가 있으면 인메모리에서 바로 추출, 쿼리 0회)
        if cur_month_rows:
            latest_row = cur_month_rows[-1]
        else:
            latest_row = conn.execute(
                'SELECT * FROM daily_profit WHERE user_id = ? ORDER BY date DESC LIMIT 1',
                (user_id,)
            ).fetchone()

        # 당월 누적 합계 (인메모리 즉시 계산)
        cur_cum = sum(int(r['total'] or 0) for r in cur_month_rows) if cur_month_rows else (int(latest_row['total'] or 0) if latest_row else 0)

        # 헬퍼 호출 (사전 로딩 데이터 활용으로 DB 쿼리 최소화)
        forecast = get_month_forecast(conn, user_id, cur_year, cur_month,
                                      entered_rows=cur_month_rows, dow_avg=dow_avg, last_year_total=last_year_total)
        yoy_day = get_yoy_day_comparison(conn, user_id, today_row=latest_row)
        balance = get_profit_balance_diagnosis(conn, user_id, cur_year, cur_month,
                                               entered_rows=cur_month_rows,
                                               month_summary_row=rows[-1] if rows else None)
        narrative_briefing = get_ai_narrative_briefing(conn, user_id, current_month, forecast,
                                                       latest_row=latest_row, cur_cum=cur_cum, cur_month_rows=cur_month_rows)

        dashboard_ctx = {
            'current_month': current_month,
            'monthly_data': monthly_data,
            'current_month_data': current_month,
            'year_compare': year_compare,
            'forecast': forecast,
            'yoy_day': yoy_day,
            'balance': balance,
            'narrative_briefing': narrative_briefing
        }

        # 캐시 저장 (대시보드 컨텍스트 및 순익입력 화면 데이터 동시 사전적재)
        recent_preload = [dict(r) for r in reversed(cur_month_rows[-20:])] if cur_month_rows else []
        with _CACHE_LOCK:
            if user_id not in _USER_CACHE:
                _USER_CACHE[user_id] = {}
            _USER_CACHE[user_id]['dashboard_ctx'] = {'ts': now, 'ctx': dashboard_ctx}
            _USER_CACHE[user_id]['input_cache'] = {'ts': now, 'recent': recent_preload, 'yoy_day': yoy_day}
    finally:
        conn.close()

    return render_template('dashboard.html', **dashboard_ctx)


@app.route('/input', methods=['GET', 'POST'])
@login_required
def input_sales():
    user_id = session['user_id']

    if request.method == 'POST':
        conn = get_db()
        try:
            date_str = request.form.get('date', '')
            try:
                dispensing = int(request.form.get('dispensing_fee') or 0)
                daily_net = int(request.form.get('daily_net_profit') or 0)
                non_insurance = int(request.form.get('non_insurance_margin') or 0)
                dt = datetime.datetime.strptime(date_str, '%Y-%m-%d')
            except (ValueError, TypeError):
                flash('날짜 또는 금액을 올바르게 입력해주세요.', 'danger')
                return redirect(url_for('input_sales'))
            memo = request.form.get('memo', '')

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
                invalidate_user_cache(user_id)
                flash(f'{date_str} ({dow}) 순익이 저장되었습니다. 합계: {total:,}원', 'success')
            except Exception as e:
                flash(f'저장 실패: {str(e)}', 'danger')

            referrer = request.referrer or ''
            if 'calendar' in referrer:
                return redirect(url_for('calendar_view', year=dt.year, month=dt.month))
            return redirect(url_for('input_sales'))
        finally:
            conn.close()

    # GET: ⚡ 초고속 스마트 캐시 활용 (DB 연결 0회, 0ms 즉시 응답!)
    now = time.time()
    recent = None
    yoy_day = None
    with _CACHE_LOCK:
        if user_id in _USER_CACHE:
            entry = _USER_CACHE[user_id].get('input_cache')
            if entry and (now - entry['ts'] < _CACHE_TTL):
                recent = entry['recent']
                yoy_day = entry['yoy_day']

    if recent is None:
        conn = get_db()
        try:
            recent_raw = conn.execute('''
                SELECT * FROM daily_profit WHERE user_id = ? ORDER BY date DESC LIMIT 20
            ''', (user_id,)).fetchall()
            recent = [dict(r) for r in recent_raw]
            yoy_day = get_yoy_day_comparison(conn, user_id)
            with _CACHE_LOCK:
                if user_id not in _USER_CACHE:
                    _USER_CACHE[user_id] = {}
                _USER_CACHE[user_id]['input_cache'] = {'ts': now, 'recent': recent, 'yoy_day': yoy_day}
        finally:
            conn.close()

    today = datetime.date.today().isoformat()
    return render_template('input.html', today=today, recent_sales=recent, yoy_day=yoy_day)


@app.route('/calendar')
@login_required
def calendar_view():
    user_id = session['user_id']
    # [초고속 스마트 캐시 1] 월별 요약 캐시에서 연도 목록 및 최신 월 도출 (DB 연결/쿼리 0회, 0ms)
    m_rows = get_cached_monthly_summary(None, user_id)
    years = sorted(list(set(int(r['year']) for r in m_rows)), reverse=True)

    if m_rows:
        last_m = m_rows[-1]
        default_year = int(last_m['year'])
        default_month = int(last_m['month'])
    else:
        today_dt = datetime.date.today()
        default_year, default_month = today_dt.year, today_dt.month

    year = request.args.get('year', default_year, type=int)
    month = request.args.get('month', default_month, type=int)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    # [초고속 스마트 캐시 2] 당월 데이터는 캐시에서 0ms 반환 (과거 타 월 조회 시에만 get_db 호출)
    today_now = datetime.date.today()
    if year == today_now.year and month == today_now.month:
        rows = get_cached_current_month_dailies(None, user_id, year, month)
    else:
        conn = get_db()
        try:
            date_prefix = f"{year}-{month:02d}"
            rows_raw = conn.execute('SELECT * FROM daily_profit WHERE user_id = ? AND date LIKE ?', (user_id, date_prefix + '%')).fetchall()
            rows = [dict(r) for r in rows_raw]
        finally:
            conn.close()

    profit_by_day = {}
    for r in rows:
        d_num = int(r['date'].split('-')[2])
        profit_by_day[d_num] = r

    calendar.setfirstweekday(calendar.SUNDAY)
    cal_weeks = calendar.monthcalendar(year, month)

    # 당월 월별 요약 (인메모리 캐시에서 즉시 추출, 쿼리 0회)
    month_summary = next((r for r in m_rows if int(r['year']) == year and int(r['month']) == month), None)

    total_days_worked = len(rows)
    avg_daily = int(month_summary['grand_total']) // total_days_worked if (month_summary and total_days_worked > 0) else 0

    dow_avg = get_cached_dow_avg(None, user_id)
    last_year_total = next((int(r['grand_total']) for r in m_rows if int(r['year']) == year - 1 and int(r['month']) == month), 0)

    # 사전 로딩 인자 주입으로 DB 쿼리 0회 즉시 예측 계산
    forecast = get_month_forecast(None, user_id, year, month, entered_rows=rows, dow_avg=dow_avg, last_year_total=last_year_total)

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


def generate_annual_narrative_report(year, report_data, year_total, last_year_total, best_month, worst_month, quarterly_data):
    """연간 경영 결산 줄글 브리핑 리포트 생성"""
    if not report_data or year_total['grand'] == 0:
        return None

    months_count = len(report_data)
    avg_monthly = int(year_total['grand']) // months_count if months_count > 0 else 0
    grand_man = int(year_total['grand'] / 10000)
    avg_man = int(avg_monthly / 10000)

    paragraphs = []

    # 1. 연간 총평 및 전년 대비 성장률
    p1 = f"<strong>{year}년 우리 약국의 연간 누적 순익은 총 {int(year_total['grand']):,}원(약 {grand_man:,}만 원)</strong>으로, <strong>월평균 {int(avg_monthly):,}원(약 {avg_man:,}만 원)</strong>의 결실을 거두었습니다."
    if last_year_total and last_year_total['grand'] > 0:
        yoy_diff = int(year_total['grand']) - int(last_year_total['grand'])
        yoy_pct = round((yoy_diff / float(last_year_total['grand'])) * 100, 1)
        dir_txt = "성장하며 견고한 상승 궤도" if yoy_diff >= 0 else "안정적인 방어선"
        color = "text-success" if yoy_diff >= 0 else "text-danger"
        p1 += f" 이는 전년도({year-1}년) 총순익 대비 <strong class='{color}'>{yoy_pct:+,}% {dir_txt}</strong>를 증명해낸 값진 성과입니다."
    else:
        p1 += f" 한 해 동안 흔들림 없는 경영 안정성을 확보하며 탄탄한 실적 기반을 다졌습니다."
    paragraphs.append(p1)

    # 2. 골든 먼스(최고의 달) 분석
    if best_month:
        best_val = int(best_month['grand_total'])
        best_man = int(best_val / 10000)
        p2 = f"1년 중 가장 눈부신 성과를 일궈낸 <strong>골든 먼스(Golden Month)는 👑 {best_month['month']}월({best_val:,}원, 약 {best_man:,}만 원)</strong>이었습니다."
        if worst_month and worst_month['month'] != best_month['month']:
            worst_val = int(worst_month['grand_total'])
            worst_man = int(worst_val / 10000)
            p2 += f" 상대적으로 숨을 고른 달은 {worst_month['month']}월({worst_val:,}원, 약 {worst_man:,}만 원)이었으나, 연중 큰 부침 없이 월별 실적 방어선이 훌륭하게 작동했습니다."
        paragraphs.append(p2)

    # 3. 수익 포트폴리오 및 알짜 비보험 기여도
    if year_total['grand'] > 0:
        nim_pct = round((float(year_total['nim']) / float(year_total['grand'])) * 100, 1)
    else:
        nim_pct = 0
    dpd_pct = round(100.0 - nim_pct, 1)
    p3 = f"수익 구성을 들여다보면, 조제료 및 매약 순익이 <strong>{int(year_total['dpd']):,}원({dpd_pct}%)</strong>으로 든든한 기초 체력을 뒷받침했습니다. 아울러 비보험 약가차액이 <strong>{int(year_total['nim']):,}원({nim_pct}%)</strong>의 알짜 마진을 창출하며 약국 수익 다각화의 핵심 엔진 역할을 톡톡히 해냈습니다."
    paragraphs.append(p3)

    # 4. 분기별 실적 흐름 및 제언
    best_q = max(quarterly_data, key=lambda q: q['total']) if quarterly_data else None
    if best_q and best_q['total'] > 0 and year_total['grand'] > 0:
        q_pct = round((float(best_q['total']) / float(year_total['grand'])) * 100, 1)
        p4 = f"분기별 흐름에서는 <strong>{best_q['quarter']}분기({int(best_q['total']):,}원, 연간의 {q_pct}%)</strong>의 모멘텀이 가장 강했습니다. 앞으로도 환절기 처방 호조와 함께 매약 상담 기회를 적극 연계하신다면 더욱 높은 수익 고지를 안정적으로 유지하실 수 있을 것입니다."
        paragraphs.append(p4)

    return paragraphs


@app.route('/report')
@login_required
def report():
    user_id = session['user_id']
    # [초고속 스마트 캐시 연산] 캐시 히트 시 DB 연결/쿼리 0회! (0ms 즉각 반환)
    raw_rows = get_cached_monthly_summary(None, user_id)
    all_rows = sorted(raw_rows, key=lambda r: (-int(r['year']), int(r['month'])))

    years = sorted(list(set(int(r['year']) for r in all_rows)), reverse=True)
    selected_year = request.args.get('year', years[0] if years else datetime.date.today().year, type=int)
    last_year = selected_year - 1

    report_data = []
    year_dpd = 0
    year_nim = 0
    year_grand = 0

    last_year_dpd = 0
    last_year_nim = 0
    last_year_grand = 0
    last_12m = [0] * 12

    for r in all_rows:
        yr = int(r['year'])
        mo = int(r['month'])
        dpd = int(r['dispensing_plus_daily_total'] or 0)
        nim = int(r['non_insurance_total'] or 0)
        gt = int(r['grand_total'] or 0)
        diff = int(r['prev_month_diff'] or 0)

        if yr == selected_year:
            report_data.append({
                'month': mo,
                'dispensing_plus_daily_total': dpd,
                'non_insurance_total': nim,
                'grand_total': gt,
                'prev_month_diff': diff
            })
            year_dpd += dpd
            year_nim += nim
            year_grand += gt
        elif yr == last_year:
            last_year_dpd += dpd
            last_year_nim += nim
            last_year_grand += gt
            if 1 <= mo <= 12:
                last_12m[mo - 1] = gt

    report_data.sort(key=lambda x: x['month'])

    year_total = {
        'dpd': year_dpd,
        'nim': year_nim,
        'grand': year_grand
    }

    last_year_total = None
    if last_year_grand > 0:
        last_year_total = {
            'dpd': last_year_dpd,
            'nim': last_year_nim,
            'grand': last_year_grand
        }

    # 전년 대비 성장률
    yoy_growth_pct = None
    yoy_diff = 0
    if last_year_total and last_year_total['grand'] > 0:
        yoy_diff = year_total['grand'] - last_year_total['grand']
        yoy_growth_pct = round((yoy_diff / float(last_year_total['grand'])) * 100, 1)

    # 최고/최저 실적 달
    best_month = max(report_data, key=lambda r: r['grand_total']) if report_data else None
    worst_month = min(report_data, key=lambda r: r['grand_total']) if report_data else None
    months_count = len(report_data)
    avg_monthly = (year_total['grand'] // months_count) if months_count > 0 else 0

    # 비보험 및 조제 비중
    nim_pct = round((float(year_total['nim']) / float(year_total['grand'])) * 100, 1) if year_total['grand'] > 0 else 0
    dpd_pct = round(100.0 - nim_pct, 1)

    # 분기별 실적 집계 (Q1~Q4)
    quarters = [
        {'quarter': 1, 'name': '1분기 (1~3월)', 'months': [1, 2, 3], 'total': 0, 'dpd': 0, 'nim': 0},
        {'quarter': 2, 'name': '2분기 (4~6월)', 'months': [4, 5, 6], 'total': 0, 'dpd': 0, 'nim': 0},
        {'quarter': 3, 'name': '3분기 (7~9월)', 'months': [7, 8, 9], 'total': 0, 'dpd': 0, 'nim': 0},
        {'quarter': 4, 'name': '4분기 (10~12월)', 'months': [10, 11, 12], 'total': 0, 'dpd': 0, 'nim': 0}
    ]
    for r in report_data:
        m = r['month']
        q_idx = (m - 1) // 3
        quarters[q_idx]['total'] += r['grand_total']
        quarters[q_idx]['dpd'] += r['dispensing_plus_daily_total']
        quarters[q_idx]['nim'] += r['non_insurance_total']

    # 연간 줄글 분석 생성
    narrative_paragraphs = generate_annual_narrative_report(
        selected_year, report_data, year_total, last_year_total, best_month, worst_month, quarters
    )

    # 12개월 전체 배열 (작년 vs 올해 콤보 차트용)
    cur_12m = [0] * 12
    for r in report_data:
        cur_12m[r['month'] - 1] = int(r['grand_total'])

    chart_payload = {
        'labels': [f'{m}월' for m in range(1, 13)],
        'cur_year': cur_12m,
        'cur_dpd': [int(r['dispensing_plus_daily_total']) for r in report_data],
        'cur_nim': [int(r['non_insurance_total']) for r in report_data],
        'last_year': last_12m,
        'has_last_year': any(v > 0 for v in last_12m),
        'quarters_labels': [q['name'] for q in quarters],
        'quarters_totals': [int(q['total']) for q in quarters],
        'composition': [int(year_total['dpd']), int(year_total['nim'])]
    }

    return render_template('report.html',
                           years=years,
                           selected_year=selected_year,
                           report_data=report_data,
                           year_total=year_total,
                           last_year_total=last_year_total,
                           yoy_growth_pct=yoy_growth_pct,
                           yoy_diff=yoy_diff,
                           best_month=best_month,
                           worst_month=worst_month,
                           avg_monthly=avg_monthly,
                           nim_pct=nim_pct,
                           dpd_pct=dpd_pct,
                           quarters=quarters,
                           narrative_paragraphs=narrative_paragraphs,
                           chart_payload=chart_payload)


@app.route('/export/<int:year>')
@login_required
def export_csv(year):
    user_id = session['user_id']
    pharmacy = session.get('pharmacy_name', '약국')
    conn = get_db()
    try:
        rows = conn.execute('''
            SELECT * FROM monthly_summary WHERE user_id = ? AND year = ? ORDER BY month
        ''', (user_id, year)).fetchall()
    finally:
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
    # [초고속 스마트 캐시] monthly_summary 및 요일별 평균 캐시 조회 (DB 연결/쿼리 0회, 0ms 즉시 반환)
    rows = get_cached_monthly_summary(None, user_id)

    years_dict = {}
    for r in rows:
        yr = r['year']
        if yr not in years_dict:
            years_dict[yr] = [0] * 12
        years_dict[yr][r['month'] - 1] = int(r['grand_total'])

    yearly_data = [
        {'year': yr, 'data': [int(v) for v in data]}
        for yr, data in sorted(years_dict.items())
        if any(v > 0 for v in data)
    ]

    dow_dict = get_cached_dow_avg(None, user_id)
    day_order = ['월', '화', '수', '목', '금', '토']
    day_of_week_data = {
        'labels': day_order,
        'values': [dow_dict.get(d, 0) for d in day_order]
    }

    # 중복 쿼리 제거: 이미 조회된 rows의 최근 24개월을 인메모리에서 바로 활용
    all_months = rows[-24:] if len(rows) > 24 else rows

    growth_labels = []
    growth_values = []
    for r in all_months:
        growth_labels.append(f"{r['year']}.{r['month']:02d}")
        prev_total = int(r['grand_total']) - int(r['prev_month_diff'] or 0)
        rate = float(round((int(r['prev_month_diff'] or 0) / prev_total) * 100, 1)) if prev_total > 0 else 0.0
        growth_values.append(rate)

    growth_data = {'labels': growth_labels, 'values': growth_values}

    ratio_data = {
        'labels': [f"{r['year']}.{r['month']:02d}" for r in rows],
        'dispensing': [int(r['dispensing_plus_daily_total']) for r in rows],
        'non_insurance': [int(r['non_insurance_total']) for r in rows]
    }

    heatmap_data = []
    if rows:
        all_totals = [int(r['grand_total']) for r in rows if r['grand_total'] > 0]
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
                months.append({'value': int(val), 'color': color, 'text_color': text_color})
            heatmap_data.append({'year': yr, 'months': months})

    return render_template('trend.html',
                           yearly_data=yearly_data,
                           day_of_week_data=day_of_week_data,
                           growth_data=growth_data,
                           ratio_data=ratio_data,
                           heatmap_data=heatmap_data)


# ==========================================
# 🧮 약국 순수익 계산기 라우트
# ==========================================

@app.route('/calculator', methods=['GET'])
@login_required
def calculator():
    user_id = session['user_id']
    conn = get_db()
    try:
        # 기존 저장된 계산기 설정값 조회
        row = conn.execute('SELECT * FROM user_calculator_settings WHERE user_id = ?', (user_id,)).fetchone()

        defaults = {
            'dispensing_fee': 15000000,
            'dispensing_cut': 0,
            'non_insurance_fee': 500000,
            'non_insurance_margin': 1500000,
            'otc_calc_mode': 'direct',
            'monthly_otc_net_profit': 8000000,
            'daily_otc_sales': 1000000,
            'work_days': 25,
            'otc_margin_rate': 0.35,
            'monthly_drug_cost': 45000000,
            'pharmacist_salary': 4000000,
            'staff_salary': 2500000,
            'meal_cost': 300000,
            'rent_cost': 3300000,
            'maintenance_cost': 300000,
            'supplies_cost': 0,
            'software_cost': 110000,
            'barcode_cost': 0,
            'electricity_cost': 250000,
            'communication_cost': 50000,
            'water_purifier_cost': 30000,
            'security_cost': 70000,
            'tax_accountant_cost': 150000,
            'association_fee': 50000,
            'card_fee_rate': 0.02
        }

        if row:
            settings = dict(row)
            # Decimal→float 변환 (PostgreSQL NUMERIC 타입 대응)
            for k in ('otc_margin_rate', 'card_fee_rate'):
                if k in settings and settings[k] is not None:
                    settings[k] = float(settings[k])
        else:
            settings = defaults

        # 현재 월 실적 조회 (원클릭 자동 불러오기용)
        today = datetime.date.today()
        cur_month_prefix = f"{today.year}-{today.month:02d}"
        stats_row = conn.execute('''
            SELECT 
                COALESCE(SUM(dispensing_fee), 0) as disp,
                COALESCE(SUM(daily_net_profit), 0) as daily,
                COALESCE(SUM(non_insurance_margin), 0) as nim,
                COUNT(*) as days
            FROM daily_profit
            WHERE user_id = ? AND date LIKE ?
        ''', (user_id, cur_month_prefix + '%')).fetchone()

        cur_stats = {
            'has_data': stats_row and (int(stats_row['disp'] or 0) > 0 or int(stats_row['daily'] or 0) > 0),
            'disp': int(stats_row['disp'] or 0) if stats_row else 0,
            'daily': int(stats_row['daily'] or 0) if stats_row else 0,
            'nim': int(stats_row['nim'] or 0) if stats_row else 0,
            'days': int(stats_row['days'] or 0) if stats_row else 0
        }
    finally:
        conn.close()
    return render_template('calculator.html', settings=settings, cur_stats=cur_stats)


@app.route('/api/calculator/save', methods=['POST'])
@login_required
def save_calculator_settings():
    user_id = session['user_id']
    data = request.get_json() or {}
    conn = get_db()
    try:
        fields = [
            'dispensing_fee', 'dispensing_cut', 'non_insurance_fee', 'non_insurance_margin',
            'otc_calc_mode', 'monthly_otc_net_profit',
            'daily_otc_sales', 'work_days', 'otc_margin_rate', 'monthly_drug_cost',
            'pharmacist_salary', 'staff_salary', 'meal_cost', 'rent_cost', 'maintenance_cost',
            'supplies_cost', 'software_cost', 'barcode_cost', 'electricity_cost',
            'communication_cost', 'water_purifier_cost', 'security_cost', 'tax_accountant_cost',
            'association_fee', 'card_fee_rate'
        ]

        existing = conn.execute('SELECT id FROM user_calculator_settings WHERE user_id = ?', (user_id,)).fetchone()
        if existing:
            set_clause = ', '.join([f"{f} = ?" for f in fields])
            params = [data.get(f, 0) for f in fields] + [user_id]
            conn.execute(f"UPDATE user_calculator_settings SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", params)
        else:
            col_clause = 'user_id, ' + ', '.join(fields)
            val_clause = '?, ' + ', '.join(['?' for _ in fields])
            params = [user_id] + [data.get(f, 0) for f in fields]
            conn.execute(f"INSERT INTO user_calculator_settings ({col_clause}) VALUES ({val_clause})", params)

        conn.commit()
    finally:
        conn.close()
    return jsonify({'success': True, 'message': '계산기 설정값이 안전하게 저장되었습니다.'})


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
        try:
            cursor = conn.cursor()
            imported_count = 0
            touched_months = set()

            def s_int(v):
                try:
                    return int(v) if v else 0
                except (ValueError, TypeError):
                    return 0

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
                    disp = s_int(first_ws.cell(r, 3).value)
                    daily = s_int(first_ws.cell(r, 4).value)
                    nim = s_int(first_ws.cell(r, 5).value)
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
                invalidate_user_cache(user_id)
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
                    dpd = s_int(ws_sum.cell(r, 3).value)
                    nim = s_int(ws_sum.cell(r, 4).value)
                    grand = s_int(ws_sum.cell(r, 5).value)
                    diff = s_int(ws_sum.cell(r, 6).value)
                    if dpd == 0 and nim == 0 and grand == 0:
                        continue
                    cursor.execute('''
                        INSERT OR REPLACE INTO monthly_summary
                        (user_id, year, month, dispensing_plus_daily_total, non_insurance_total, grand_total, prev_month_diff)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (user_id, int(yr), int(m_match.group(1)), dpd, nim, grand, diff))

            # 주표 시트 (1번째 시트)
            ws_week = wb[wb.sheetnames[0]]
            
            # 연도 자동 추론: 1) 합계 시트 최소 연도 -> 2) 파일명 내 연도 -> 3) 기본 2022년
            inferred_year = None
            if len(wb.sheetnames) >= 2:
                sum_years = []
                for r in range(2, ws_sum.max_row + 1):
                    yr_v = ws_sum.cell(r, 1).value
                    if isinstance(yr_v, (int, float)) and 2000 <= int(yr_v) <= 2099:
                        sum_years.append(int(yr_v))
                if sum_years:
                    inferred_year = min(sum_years)

            if not inferred_year:
                fn_match = re.search(r'(20\d{2})', file.filename or '')
                inferred_year = int(fn_match.group(1)) if fn_match else 2022

            current_year = inferred_year
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
                    d_fee = s_int(ws_week.cell(r, 2).value)
                    d_net = s_int(ws_week.cell(r, 3).value)
                    dpd = s_int(ws_week.cell(r, 4).value)
                    nim = s_int(ws_week.cell(r, 5).value)
                    tot = s_int(ws_week.cell(r, 6).value)
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
            invalidate_user_cache(user_id)
            flash(f'🎉 엑셀 데이터 가져오기 완료! (총 {imported_count}일치 순익 데이터가 등록되었습니다)', 'success')
        finally:
            conn.close()
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
    try:
        target_user = conn.execute('SELECT * FROM users WHERE id = ?', (target_user_id,)).fetchone()
    finally:
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
@login_required
def admin_switch_back():
    """관리자 원래 계정으로 복귀"""
    if 'original_admin_id' in session:
        admin_id = session['original_admin_id']
        conn = get_db()
        try:
            admin_user = conn.execute('SELECT * FROM users WHERE id = ?', (admin_id,)).fetchone()
        finally:
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
    try:
        target_user = conn.execute('SELECT * FROM users WHERE id = ?', (target_user_id,)).fetchone()
    finally:
        conn.close()

    if not target_user:
        flash('해당 회원을 찾을 수 없습니다.', 'danger')
        return redirect(url_for('admin_dashboard'))

    if target_user['username'] == 'admin' or target_user['id'] == 1:
        flash('관리자(admin) 본인 계정은 삭제할 수 없습니다.', 'danger')
        return redirect(url_for('admin_dashboard'))

    p_name = target_user['pharmacy_name']
    delete_user_and_data(target_user_id)
    invalidate_user_cache(target_user_id)
    flash(f"'{p_name}' 계정 및 등록된 모든 데이터가 삭제되었습니다.", 'warning')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/export_backup')
@admin_required
def admin_export_backup():
    """관리자 전용: 전체 약국 회원 및 순익 데이터 원클릭 엑셀 백업 다운로드"""
    conn = None
    try:
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
    finally:
        if conn:
            conn.close()


@app.route('/debug/perf')
def debug_perf():
    """서버 및 DB 커넥션 풀 성능 실시간 정밀 진단 API"""
    from database import get_pg_pool, get_database_url
    steps = {}

    t0 = time.time()
    pool = get_pg_pool()
    steps['get_pg_pool_sec'] = round(time.time() - t0, 3)

    t1 = time.time()
    conn = get_db()
    steps['get_db_total_sec'] = round(time.time() - t1, 3)

    t2 = time.time()
    cur = conn.execute("SELECT 1")
    cur.fetchall()
    steps['query_select1_sec'] = round(time.time() - t2, 3)

    t3 = time.time()
    conn.close()
    steps['conn_close_sec'] = round(time.time() - t3, 3)

    steps['pool_status'] = get_pool_status()
    steps['cache_stats'] = {
        'users_cached': len(_USER_CACHE),
        'cached_user_keys': {uid: list(data.keys()) for uid, data in _USER_CACHE.items()}
    }
    return jsonify(steps)


# ==========================================
# 🛡️ 전역 에러 핸들러
# ==========================================

@app.errorhandler(404)
def page_not_found(e):
    return render_template(
        'error.html',
        error_code=404,
        error_title='페이지를 찾을 수 없습니다',
        error_message='요청하신 주소가 잘못되었거나 변경된 페이지입니다.'
    ), 404


@app.errorhandler(500)
def internal_server_error(e):
    return render_template(
        'error.html',
        error_code=500,
        error_title='시스템 일시 오류가 발생했습니다',
        error_message='요청을 처리하는 도중 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.'
    ), 500


if __name__ == '__main__':
    init_db()
    print("\n=== 약국 순익 관리 시스템 (다중 약국 온라인 SaaS) 시작 ===")
    print("접속 주소: http://localhost:5000\n")
    app.run(debug=True, host='0.0.0.0', port=5000)
