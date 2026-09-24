"""
약국 순익 관리 시스템 - 다중 약국 지원 온라인 SaaS 버전
"""
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session, g
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db, init_db, recalc_monthly_summary, get_all_users_stats, delete_user_and_data, get_pool_status
from profit_analysis import (get_month_forecast, get_yoy_day_comparison,
    get_profit_balance_diagnosis, get_ai_narrative_briefing, generate_annual_narrative_report,
    get_business_schedule, korea_today)
from functools import wraps
import secrets
import datetime
import calendar
import json
import csv
import io
import os
import re
import time
import threading
from urllib.parse import quote

app = Flask(__name__)
app.jinja_env.trim_blocks = True
app.jinja_env.lstrip_blocks = True
from profit_display import install as install_profit_display
install_profit_display(app)
from auth_config import require_secret_key
app.secret_key = require_secret_key()
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 86400  # 정적 에셋 24시간 브라우저 캐싱

# 🔒 약국장 전용 1인 단독 모드: 신규 가입 차단 (환경변수 ALLOW_REGISTRATION=1 로 필요 시 즉시 재개 가능)
ALLOW_REGISTRATION = os.environ.get('ALLOW_REGISTRATION', '0') == '1'

# ⚡ 사용자별 초고속 인메모리 스마트 캐시 (1인 전용 최적화: 1시간 TTL, 데이터 변경 시 즉시 갱신)
_USER_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 3600  # 1시간 유효 (데이터 변경 시 invalidate_user_cache 즉시 호출로 실시간 정합성 보장)


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
            WHERE user_id = ?
            ORDER BY year, month
        ''', (user_id,)).fetchall()

        from extra_profit import monthly_totals, merge_monthly_summaries
        data = merge_monthly_summaries(rows, monthly_totals(conn, user_id))
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


def get_cached_business_schedule(conn=None, user_id=None):
    """영업일 설정 캐시. 저장 시 사용자 캐시 전체가 무효화되어 즉시 갱신된다."""
    now = time.time()
    with _CACHE_LOCK:
        entry = (_USER_CACHE.get(user_id) or {}).get('business_schedule')
        if entry and (now - entry['ts'] < _CACHE_TTL):
            return entry['data']

    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True
    try:
        data = get_business_schedule(conn, user_id)
        with _CACHE_LOCK:
            bucket = _USER_CACHE.setdefault(user_id, {})
            bucket['business_schedule'] = {'ts': now, 'data': data}
        return data
    finally:
        if should_close:
            conn.close()


# ⚡ 관리자 콘솔 스마트 캐시
_ADMIN_CACHE = {}


def get_cached_calculator_settings(conn=None, user_id=None):
    """사용자의 계산기 설정값 캐시 (캐시 히트 시 DB 연결/쿼리 0회, 0ms 즉시 반환)"""
    now = time.time()
    with _CACHE_LOCK:
        if user_id in _USER_CACHE:
            entry = _USER_CACHE[user_id].get('calculator_settings')
            if entry and (now - entry['ts'] < _CACHE_TTL):
                return entry['data']

    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True
    try:
        row = conn.execute('SELECT * FROM user_calculator_settings WHERE user_id = ?', (user_id,)).fetchone()
        data = dict(row) if row else None
        if data:
            for k in ('otc_margin_rate', 'card_fee_rate'):
                if k in data and data[k] is not None:
                    data[k] = float(data[k])
        with _CACHE_LOCK:
            if user_id not in _USER_CACHE:
                _USER_CACHE[user_id] = {}
            _USER_CACHE[user_id]['calculator_settings'] = {'ts': now, 'data': data}
        return data
    finally:
        if should_close:
            conn.close()


def get_cached_admin_stats():
    """관리자 콘솔 회원 목록 및 통계 캐시 (캐시 히트 시 DB 연결/쿼리 0회, 0ms 즉시 반환)"""
    now = time.time()
    with _CACHE_LOCK:
        entry = _ADMIN_CACHE.get('stats')
        if entry and (now - entry['ts'] < _CACHE_TTL):
            return entry['data']

    users = get_all_users_stats()
    with _CACHE_LOCK:
        _ADMIN_CACHE['stats'] = {'ts': now, 'data': users}
    return users


def invalidate_admin_cache():
    """회원 정보 또는 데이터 변경 시 관리자 캐시 무효화"""
    with _CACHE_LOCK:
        _ADMIN_CACHE.clear()


def invalidate_user_cache(user_id=None):
    """데이터 변경 시 해당 사용자의 인메모리 캐시 및 관리자 캐시 즉시 무효화 (실시간 정합성 보장)"""
    with _CACHE_LOCK:
        if user_id is None:
            _USER_CACHE.clear()
        elif user_id in _USER_CACHE:
            _USER_CACHE.pop(user_id, None)
        _ADMIN_CACHE.clear()


# Schema initialization is an explicit operator step: python manage_db.py init.
from extra_profit import install as install_extra_profit
install_extra_profit(app)
from response_security import install as install_response_security
install_response_security(app)
from response_compress import install as install_response_compress
install_response_compress(app)
from csrf_protection import install as install_csrf_protection
install_csrf_protection(app)


def bootstrap_app_extensions(app_module=None):
    """로컬 실행 및 운영 환경(Gunicorn) 모두에서 일관된 보안 헤더, 연결 풀 가드,
    일장부 전용 회계 락, 캐시 정합성 및 성능 메트릭 확장을 적용합니다."""
    import sys
    current_module = app_module or sys.modules[__name__]
    from response_security import install as install_response_security
    from response_compress import install as install_response_compress
    from pool_guard import install as install_pool_guard
    from daily_only import install as install_daily_only
    from daily_labels import install as install_daily_labels
    from cache_coherence import install as install_cache_coherence
    from runtime_metrics import install as install_metrics
    import database

    install_response_security(current_module.app)
    install_response_compress(current_module.app)
    install_pool_guard(database, current_module.app)
    install_daily_only(current_module)
    install_daily_labels(current_module)
    install_cache_coherence(current_module)
    install_metrics(current_module)
    warm_up_cache(1)


def warm_up_cache(user_id=1):
    """서버 구동 직후 약국장 핵심 데이터를 백그라운드에서 사전 캐싱 (콜드 스타트 0초 달성)"""
    if os.environ.get('PYTEST_CURRENT_TEST'):
        return

    def _worker():
        try:
            time.sleep(0.5)  # 서버 및 커넥션 풀 초기화 대기
            with app.app_context():
                conn = get_db()
                try:
                    now = datetime.datetime.now()
                    get_cached_monthly_summary(conn, user_id)
                    get_cached_current_month_dailies(conn, user_id, now.year, now.month)
                    get_cached_dow_avg(conn, user_id)
                    get_cached_business_schedule(conn, user_id)
                    get_cached_calculator_settings(conn, user_id)
                finally:
                    conn.close()
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True, name="CacheWarmupThread")
    t.start()


@app.teardown_appcontext
def shutdown_session(exception=None):
    """요청 종료 시 미반납 트랜잭션 및 DB 리소스 안전 정리 (커넥션 풀 누수 원천 방지)"""
    conn = getattr(g, '_database', None)
    if conn is not None:
        try:
            if exception:
                conn.rollback()
            conn.close()
        except Exception:
            pass
        g._database = None



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
    return {'year': now.year, 'month': now.month, 'dispensing_plus_daily': 0, 'non_insurance': 0, 'extra_profit_total': 0, 'grand_total': 0, 'diff': 0}


def load_analysis_rows(conn, user_id, year, month):
    """One user-scoped snapshot for forecast, comparisons and weekly totals."""
    today = korea_today()
    start = datetime.date(year, month, 1) - datetime.timedelta(days=364)
    end = datetime.date(year, month, calendar.monthrange(year, month)[1])
    recent_start = today - datetime.timedelta(days=55)
    recent_end = today + datetime.timedelta(days=6 - today.weekday())
    return [dict(r) for r in conn.execute(
        "SELECT date, day_of_week, dispensing_fee, daily_net_profit, "
        "dispensing_plus_daily, non_insurance_margin, total "
        "FROM daily_profit WHERE user_id = ? AND "
        "(date BETWEEN ? AND ? OR date BETWEEN ? AND ?) ORDER BY date",
        (user_id, start.isoformat(), end.isoformat(), recent_start.isoformat(), recent_end.isoformat())
    ).fetchall()]


def get_recent_weeks_profit_stats(conn, user_id, num_weeks=4, rows=None):
    """
    최근 4주간 주차별 순익 현황 및 이번 주 이익 집계 (x월 1~4주차)
    - 한국 표준 목요일 기준 x월 n주차 명칭 자동 산출
    - 주차별 조제료, 일매순익, 비보험약가차액, 주간 총 순익, 영업일수, 일평균
    - 전주 대비 증감액/증감율, 이번 주 진행중 상태 및 동기간 비교
    """
    today = korea_today()
    this_monday = today - datetime.timedelta(days=today.weekday())
    this_sunday = this_monday + datetime.timedelta(days=6)

    start_date = this_monday - datetime.timedelta(weeks=num_weeks - 1)
    end_date = this_sunday

    if rows is None:
        rows = conn.execute('''
            SELECT date, day_of_week, dispensing_fee, daily_net_profit, non_insurance_margin, total
            FROM daily_profit
            WHERE user_id = ? AND date BETWEEN ? AND ?
            ORDER BY date ASC
        ''', (user_id, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))).fetchall()

    date_map = {r['date']: r for r in rows}
    extra_map = {r['date']: int(r['amount'] or 0) for r in conn.execute('''
        SELECT date, SUM(amount) AS amount FROM extra_profit
        WHERE user_id = ? AND date BETWEEN ? AND ? GROUP BY date
    ''', (user_id, start_date.isoformat(), end_date.isoformat())).fetchall()}

    weeks = []
    prev_week_dict = None

    for idx in range(num_weeks - 1, -1, -1):
        w_monday = this_monday - datetime.timedelta(weeks=idx)
        w_sunday = w_monday + datetime.timedelta(days=6)
        w_thursday = w_monday + datetime.timedelta(days=3)

        # 목요일 기준 월 및 주차
        target_month = w_thursday.month
        month_first_day = w_thursday.replace(day=1)
        first_thursday = month_first_day + datetime.timedelta(days=(3 - month_first_day.weekday() + 7) % 7)
        if w_thursday < first_thursday:
            prev_month_last = month_first_day - datetime.timedelta(days=1)
            target_month = prev_month_last.month
            pm_first = prev_month_last.replace(day=1)
            pm_first_thu = pm_first + datetime.timedelta(days=(3 - pm_first.weekday() + 7) % 7)
            week_num = (w_thursday - pm_first_thu).days // 7 + 1
        else:
            week_num = (w_thursday - first_thursday).days // 7 + 1

        week_label = f"{target_month}월 {week_num}주차"

        disp_sum = 0
        daily_sum = 0
        nim_sum = 0
        extra_sum = 0
        tot_sum = 0
        entered_days = 0

        cur_d = w_monday
        while cur_d <= w_sunday:
            d_str = cur_d.strftime('%Y-%m-%d')
            if d_str in date_map:
                r = date_map[d_str]
                t = int(r['total'] or 0)
                disp = int(r['dispensing_fee'] or 0)
                daily = int(r['daily_net_profit'] or 0)
                nim = int(r['non_insurance_margin'] or 0)
                disp_sum += disp
                daily_sum += daily
                nim_sum += nim
                tot_sum += t
                if t > 0 or disp > 0:
                    entered_days += 1
            misc = extra_map.get(d_str, 0)
            extra_sum += misc
            tot_sum += misc
            cur_d += datetime.timedelta(days=1)

        daily_avg = int(tot_sum / entered_days) if entered_days > 0 else 0

        is_current = (idx == 0)

        diff = None
        growth_pct = None
        if prev_week_dict is not None and prev_week_dict['total_sum'] > 0:
            diff = tot_sum - prev_week_dict['total_sum']
            growth_pct = float(round((diff / float(prev_week_dict['total_sum'])) * 100, 1))

        week_info = {
            'week_label': week_label,
            'start_date': w_monday.strftime('%m.%d'),
            'end_date': w_sunday.strftime('%m.%d'),
            'date_range': f"{w_monday.strftime('%m.%d')} ~ {w_sunday.strftime('%m.%d')}",
            'is_current': is_current,
            'dispensing_sum': disp_sum,
            'daily_sum': daily_sum,
            'disp_plus_daily': disp_sum + daily_sum,
            'nim_sum': nim_sum,
            'extra_sum': extra_sum,
            'total_sum': tot_sum,
            'entered_days': entered_days,
            'daily_avg': daily_avg,
            'diff': diff,
            'growth_pct': growth_pct
        }

        weeks.append(week_info)
        prev_week_dict = week_info

    this_week = weeks[-1] if weeks else None

    # 이번 주와 지난주 동기간(월요일~오늘 요일) 직접 비교 계산
    if this_week and len(weeks) >= 2:
        last_week = weeks[-2]
        days_passed = today.weekday() + 1
        lw_monday = this_monday - datetime.timedelta(weeks=1)
        lw_same_period_sum = 0
        for d_off in range(days_passed):
            target_d = (lw_monday + datetime.timedelta(days=d_off)).strftime('%Y-%m-%d')
            if target_d in date_map:
                lw_same_period_sum += int(date_map[target_d]['total'] or 0)
            lw_same_period_sum += extra_map.get(target_d, 0)

        this_week['last_week_same_period'] = lw_same_period_sum
        if lw_same_period_sum > 0:
            s_diff = this_week['total_sum'] - lw_same_period_sum
            this_week['same_period_diff'] = s_diff
            this_week['same_period_pct'] = float(round((s_diff / float(lw_same_period_sum)) * 100, 1))
        else:
            this_week['same_period_diff'] = None
            this_week['same_period_pct'] = None

    chart_data = {
        'labels': [w['week_label'] for w in weeks],
        'totals': [w['total_sum'] for w in weeks],
        'disp_plus_daily': [w['disp_plus_daily'] for w in weeks],
        'non_insurance': [w['nim_sum'] for w in weeks],
        'extra_profit_total': [w['extra_sum'] for w in weeks]
    }

    return {
        'weeks': weeks,
        'this_week': this_week,
        'chart_data': chart_data
    }


# ==========================================
# ⚡ 킵얼라이브(Keep-Alive) 찌르기 전용 초경량 엔드포인트
# ==========================================

@app.route('/ping')
@app.route('/health')
def ping():
    """cron-job.org / UptimeRobot 찌르기 전용 초경량 엔드포인트 (출력 크기 2바이트: output too large 에러 영구 방지)"""
    return 'ok', 200, {'Content-Type': 'text/plain'}


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
            if hasattr(conn, 'use_autocommit_reads'):
                conn.use_autocommit_reads()
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

    return render_template('login.html', allow_registration=ALLOW_REGISTRATION)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if not ALLOW_REGISTRATION and not app.config.get('TESTING'):
        flash('현재 약국장 전용 비공개 모드로 운영 중입니다. 신규 회원가입은 마감되었습니다.', 'info')
        return redirect(url_for('login'))

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

        invalidate_admin_cache()

        session['user_id'] = new_id
        session['username'] = username
        session['pharmacy_name'] = pharmacy_name

        flash(f'🎉 {pharmacy_name} 계정이 생성되었습니다! 순익 관리를 시작하세요.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('register.html')


@app.route('/logout', methods=['POST'])
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
    now = time.time()
    dashboard_cache_key = f"dashboard_ctx:{korea_today().isoformat()}"
    with _CACHE_LOCK:
        cached = (_USER_CACHE.get(user_id) or {}).get(dashboard_cache_key)
        if cached and (now - cached['ts'] < _CACHE_TTL):
            return render_template('dashboard.html', **cached['data'])

    conn = get_db()
    try:
        if hasattr(conn, 'use_autocommit_reads'):
            conn.use_autocommit_reads()
        # [초고속 스마트 캐시 1] monthly_summary 전체를 캐시에서 조회 (캐시 히트 시 DB 0ms)
        rows = get_cached_monthly_summary(conn, user_id)

        if rows:
            last_r = rows[-1]
            current_month = {
                'year': int(last_r['year']),
                'month': int(last_r['month']),
                'dispensing_plus_daily': int(last_r['dispensing_plus_daily_total'] or 0),
                'non_insurance': int(last_r['non_insurance_total'] or 0),
                'extra_profit_total': int(last_r.get('extra_profit_total') or 0),
                'grand_total': int(last_r['grand_total'] or 0),
                'diff': int(last_r['prev_month_diff'] or 0)
            }
        else:
            now_dt = korea_today()
            current_month = {'year': now_dt.year, 'month': now_dt.month, 'dispensing_plus_daily': 0, 'non_insurance': 0, 'extra_profit_total': 0, 'grand_total': 0, 'diff': 0}

        monthly_data = {
            'labels': [f"{r['year']}.{r['month']:02d}" for r in rows],
            'totals': [int(r['grand_total']) for r in rows],
            'dispensing_daily': [int(r['dispensing_plus_daily_total']) for r in rows],
            'non_insurance': [int(r['non_insurance_total']) for r in rows],
            'extra_profit_total': [int(r.get('extra_profit_total') or 0) for r in rows]
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
        analysis_rows = load_analysis_rows(conn, user_id, cur_year, cur_month)
        cur_month_rows = [r for r in analysis_rows if r['date'].startswith(f'{cur_year:04d}-{cur_month:02d}-')]
        schedule = get_cached_business_schedule(conn, user_id)

        # [초고속 스마트 캐시 3] 요일별 평균 캐시 조회 (캐시 히트 시 DB 0ms)
        dow_avg = None

        # 작년 동월 총합 (이미 rows에 있으므로 쿼리 0회!)
        last_year_total = 0
        for r in rows:
            if int(r['year']) == cur_year - 1 and int(r['month']) == cur_month:
                last_year_total = int(r['grand_total'] or 0)
                break

        # 최신일 레코드 (당월 데이터가 있으면 인메모리에서 바로 추출, 쿼리 0회)
        if cur_month_rows:
            latest_row = next((r for r in reversed(cur_month_rows) if r['date'] <= korea_today().isoformat()), None)
        else:
            latest_row = conn.execute(
                'SELECT * FROM daily_profit WHERE user_id = ? AND date <= ? ORDER BY date DESC LIMIT 1',
                (user_id, korea_today().isoformat())
            ).fetchone()

        # 당월 누적 합계 (인메모리 즉시 계산)
        cur_cum = sum(int(r['total'] or 0) for r in cur_month_rows) if cur_month_rows else (int(latest_row['total'] or 0) if latest_row else 0)

        # 헬퍼 호출 (사전 로딩 데이터 활용으로 DB 쿼리 최소화)
        forecast = get_month_forecast(conn, user_id, cur_year, cur_month,
                                      entered_rows=cur_month_rows, dow_avg=dow_avg, last_year_total=last_year_total,
                                      schedule=schedule, history_rows=analysis_rows,
                                      month_summary_row=rows[-1] if rows else {})
        # A summary-only selected month may fall back to a much older daily row.
        comparison_rows = analysis_rows if latest_row and latest_row in cur_month_rows else None
        yoy_day = get_yoy_day_comparison(conn, user_id, today_row=latest_row, comparison_rows=comparison_rows)
        balance = get_profit_balance_diagnosis(conn, user_id, cur_year, cur_month,
                                               entered_rows=cur_month_rows,
                                               month_summary_row=rows[-1] if rows else {})
        narrative_briefing = get_ai_narrative_briefing(conn, user_id, current_month, forecast,
                                                       latest_row=latest_row, cur_cum=cur_cum, cur_month_rows=cur_month_rows, comparison_rows=comparison_rows)
        weekly_stats = get_recent_weeks_profit_stats(conn, user_id, rows=analysis_rows)

        dashboard_ctx = {
            'current_month': current_month,
            'monthly_data': monthly_data,
            'current_month_data': current_month,
            'year_compare': year_compare,
            'forecast': forecast,
            'yoy_day': yoy_day,
            'balance': balance,
            'narrative_briefing': narrative_briefing,
            'weekly_stats': weekly_stats
        }

        # 완성된 금융 컨텍스트를 캐시해 반복 대시보드 요청의 원격 DB 왕복을 제거한다.
        with _CACHE_LOCK:
            bucket = _USER_CACHE.setdefault(user_id, {})
            bucket[dashboard_cache_key] = {'ts': now, 'data': dashboard_ctx}
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
                recalc_monthly_summary(conn, user_id, dt.year, dt.month)
                invalidate_user_cache(user_id)
                flash(f'{date_str} ({dow}) 순익이 저장되었습니다. 합계: {total:,}원', 'success')
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    # A disconnected server cannot roll back; retain the original error.
                    pass
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


@app.route('/business-schedule', methods=['POST'])
@login_required
def save_business_schedule():
    token = request.form.get('csrf_token', '')
    if not token or not secrets.compare_digest(token, session.get('schedule_csrf', '')):
        return '화면을 새로고침한 후 다시 저장해 주세요.', 400
    try:
        weekdays = sorted(set(int(v) for v in request.form.getlist('weekdays')))
        if any(v < 0 or v > 6 for v in weekdays):
            raise ValueError('요일 범위를 확인해 주세요.')
        def dates(field):
            text = request.form.get(field, '')
            if len(text) > 12000:
                raise ValueError('한 번에 입력할 수 있는 날짜 수를 초과했습니다.')
            values = sorted(set(v for v in re.split(r'[,\s]+', text.strip()) if v))
            for value in values:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    raise ValueError('날짜는 YYYY-MM-DD 형식으로 입력해 주세요.')
                datetime.date.fromisoformat(value)
            return values
        closed, opened = dates('closed_dates'), dates('open_dates')
        if set(closed) & set(opened):
            raise ValueError('같은 날짜를 휴무와 영업에 동시에 지정할 수 없습니다.')
        year = int(request.form.get('year', korea_today().year))
        month = int(request.form.get('month', korea_today().month))
        if not 2 <= year <= 9998 or not 1 <= month <= 12:
            raise ValueError('연월을 확인해 주세요.')
    except (ValueError, TypeError):
        return '입력 오류: 요일(0~6), 날짜(YYYY-MM-DD), 휴무·영업 중복 여부를 확인해 주세요. 뒤로 가서 수정할 수 있습니다.', 400
    settings = json.dumps({'weekdays': weekdays, 'closed_dates': closed, 'open_dates': opened})
    conn = get_db()
    try:
        conn.execute("""INSERT INTO business_schedules (user_id, settings_json) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET settings_json = excluded.settings_json""",
            (session['user_id'], settings))
        conn.commit()
    finally:
        conn.close()
    invalidate_user_cache(session['user_id'])
    flash('영업 일정이 저장되었습니다. 예측과 미입력일을 다시 계산했습니다.', 'success')
    return redirect(url_for('calendar_view', year=year, month=month))


@app.route('/calendar')
@login_required
def calendar_view():
    user_id = session['user_id']
    now = time.time()
    requested_year = request.args.get('year', type=int)
    requested_month = request.args.get('month', type=int)
    calendar_cache_key = (
        f"calendar_ctx:{requested_year if requested_year is not None else 'default'}:"
        f"{requested_month if requested_month is not None else 'default'}:{korea_today().isoformat()}"
    )
    with _CACHE_LOCK:
        cached = (_USER_CACHE.get(user_id) or {}).get(calendar_cache_key)
        if cached and (now - cached['ts'] < _CACHE_TTL):
            session.setdefault('schedule_csrf', secrets.token_urlsafe(32))
            return render_template('calendar.html', schedule_csrf=session['schedule_csrf'], **cached['data'])

    conn = get_db()
    try:
        if hasattr(conn, 'use_autocommit_reads'):
            conn.use_autocommit_reads()
        # [초고속 스마트 캐시 1] 월별 요약 캐시에서 연도 목록 및 최신 월 도출 (DB 연결/쿼리 0회, 0ms)
        m_rows = get_cached_monthly_summary(conn, user_id)
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
        if not 2 <= year <= 9998 or not 1 <= month <= 12:
            return '올바른 연월을 입력해 주세요.', 400

        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

        analysis_rows = load_analysis_rows(conn, user_id, year, month)
        rows = [r for r in analysis_rows if r['date'].startswith(f'{year:04d}-{month:02d}-')]

        profit_by_day = {}
        for r in rows:
            d_num = int(r['date'].split('-')[2])
            profit_by_day[d_num] = r

        cal_weeks = calendar.Calendar(firstweekday=calendar.SUNDAY).monthdayscalendar(year, month)

        # 당월 월별 요약 (인메모리 캐시에서 즉시 추출, 쿼리 0회)
        month_summary = next((r for r in m_rows if int(r['year']) == year and int(r['month']) == month), None)

        total_days_worked = len(rows)
        avg_daily = int(month_summary['grand_total']) // total_days_worked if (month_summary and total_days_worked > 0) else 0

        dow_avg = None
        last_year_total = next((int(r['grand_total']) for r in m_rows if int(r['year']) == year - 1 and int(r['month']) == month), 0)

        business_schedule = get_cached_business_schedule(conn, user_id)
        forecast = get_month_forecast(conn, user_id, year, month, entered_rows=rows,
            last_year_total=last_year_total, schedule=business_schedule,
            history_rows=analysis_rows, month_summary_row=month_summary or {})

    finally:
        conn.close()
    calendar_ctx = {
        'year': year,
        'month': month,
        'years': years,
        'prev_year': prev_year,
        'prev_month': prev_month,
        'next_year': next_year,
        'next_month': next_month,
        'cal_weeks': cal_weeks,
        'profit_by_day': profit_by_day,
        'month_summary': month_summary,
        'total_days_worked': total_days_worked,
        'avg_daily': avg_daily,
        'business_schedule': business_schedule,
        'forecast': forecast,
    }
    with _CACHE_LOCK:
        bucket = _USER_CACHE.setdefault(user_id, {})
        bucket[calendar_cache_key] = {'ts': now, 'data': calendar_ctx}

    session.setdefault('schedule_csrf', secrets.token_urlsafe(32))
    return render_template('calendar.html', schedule_csrf=session['schedule_csrf'], **calendar_ctx)


@app.route('/report')
@login_required
def report():
    user_id = session['user_id']
    # [초고속 스마트 캐시 연산] 캐시 히트 시 DB 연결/쿼리 0회! (0ms 즉각 반환)
    raw_rows = get_cached_monthly_summary(None, user_id)
    all_rows = sorted(raw_rows, key=lambda r: (-int(r['year']), int(r['month'])))

    years = sorted(list(set(int(r['year']) for r in all_rows)), reverse=True)
    today = korea_today()
    selected_year = request.args.get('year', years[0] if years else today.year, type=int)
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
                'extra_profit_total': int(r.get('extra_profit_total') or 0),
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
        'extra_profit_total': sum(r['extra_profit_total'] for r in report_data),
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

    # 전년 비교는 같은 기간끼리만 계산한다. 진행 중인 현재 월은 완료월 비교에서 제외한다.
    previous_by_month = {
        int(r['month']): int(r['grand_total'] or 0)
        for r in all_rows if int(r['year']) == last_year
    }
    comparable_rows = [
        r for r in report_data
        if not (selected_year == today.year and int(r['month']) >= today.month)
    ]
    comparison_months = [int(r['month']) for r in comparable_rows]
    comparison_complete = bool(comparison_months) and all(m in previous_by_month for m in comparison_months)
    comparison_current_total = sum(int(r['grand_total']) for r in comparable_rows) if comparison_complete else None
    comparison_previous_total = (
        sum(previous_by_month[m] for m in comparison_months) if comparison_complete else None
    )
    yoy_growth_pct = None
    yoy_diff = None
    yoy_label = None
    yoy_badge_label = None
    if comparison_previous_total is not None and comparison_previous_total > 0:
        yoy_diff = comparison_current_total - comparison_previous_total
        yoy_growth_pct = round((yoy_diff / float(comparison_previous_total)) * 100, 1)
        contiguous = comparison_months == list(range(1, max(comparison_months) + 1))
        if selected_year == today.year:
            yoy_label = (
                f"전년 동기간 증감률 (1~{max(comparison_months)}월)"
                if contiguous else "전년 동일 완료월 증감률"
            )
            yoy_badge_label = "전년동기간"
        else:
            yoy_label = "전년 동일 입력월 증감률"
            yoy_badge_label = "전년동월"

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
        {'quarter': 1, 'name': '1분기 (1~3월)', 'months': [1, 2, 3], 'total': 0, 'dpd': 0, 'nim': 0, 'extra_profit_total': 0},
        {'quarter': 2, 'name': '2분기 (4~6월)', 'months': [4, 5, 6], 'total': 0, 'dpd': 0, 'nim': 0, 'extra_profit_total': 0},
        {'quarter': 3, 'name': '3분기 (7~9월)', 'months': [7, 8, 9], 'total': 0, 'dpd': 0, 'nim': 0, 'extra_profit_total': 0},
        {'quarter': 4, 'name': '4분기 (10~12월)', 'months': [10, 11, 12], 'total': 0, 'dpd': 0, 'nim': 0, 'extra_profit_total': 0}
    ]
    for r in report_data:
        m = r['month']
        q_idx = (m - 1) // 3
        quarters[q_idx]['total'] += r['grand_total']
        quarters[q_idx]['dpd'] += r['dispensing_plus_daily_total']
        quarters[q_idx]['nim'] += r['non_insurance_total']
        quarters[q_idx]['extra_profit_total'] += r['extra_profit_total']

    # 연간 줄글 분석 생성
    narrative_paragraphs = generate_annual_narrative_report(
        selected_year, report_data, year_total, last_year_total, best_month, worst_month, quarters,
        comparison_current_total=comparison_current_total,
        comparison_previous_total=comparison_previous_total,
        comparison_label=yoy_label,
        use_period_comparison=True,
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
                           yoy_label=yoy_label,
                           yoy_badge_label=yoy_badge_label,
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
        rows = [r for r in get_cached_monthly_summary(conn, user_id) if int(r['year']) == year]
        from profit_components import load_monthly_components, attach_components
        grouped = load_monthly_components(conn, user_id)
        detailed_rows = attach_components(rows, grouped)
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([f'[{pharmacy}] {year}년 순익 리포트'])
    writer.writerow(['연도', '월', '조제료', '일매순익', '비보험마진', '잡이익', '전체합계', '전월대비', '세부자료 상태'])
    for r in detailed_rows:
        known = r['breakdown_available']
        writer.writerow([r['year'], r['month'],
                         r['dispensing_fee'] if known else '',
                         r['daily_net_profit'] if known else '',
                         r['non_insurance_margin'] if known else '',
                         r['extra_profit_total'],
                         r['grand_total'], r['prev_month_diff'],
                         '확인됨' if known else '세부자료 확인 필요'])

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
        'non_insurance': [int(r['non_insurance_total']) for r in rows],
        'totals': [int(r['grand_total']) for r in rows]
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

    # ⚡ 캐시된 설정값 조회 (캐시 히트 시 DB 연결/쿼리 0회, 0.001초 반환)
    saved_settings = get_cached_calculator_settings(None, user_id)
    if saved_settings:
        settings = dict(saved_settings)
    else:
        settings = defaults

    # ⚡ 현재 월 실적 조회 (원클릭 자동 불러오기용) - 캐시된 당월 일별 데이터 활용
    today = datetime.date.today()
    cur_month_rows = get_cached_current_month_dailies(None, user_id, today.year, today.month)
    disp = sum(int(r.get('dispensing_fee') or 0) for r in cur_month_rows)
    daily = sum(int(r.get('daily_net_profit') or 0) for r in cur_month_rows)
    nim = sum(int(r.get('non_insurance_margin') or 0) for r in cur_month_rows)
    days = len(cur_month_rows)

    cur_stats = {
        'has_data': (disp > 0 or daily > 0),
        'disp': disp,
        'daily': daily,
        'nim': nim,
        'days': days
    }
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
        invalidate_user_cache(user_id)
    finally:
        conn.close()
    return jsonify({'success': True, 'message': '계산기 설정값이 안전하게 저장되었습니다.'})


@app.route('/download_template')
@login_required
def download_template():
    """표준 엑셀 양식 다운로드"""
    import openpyxl
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
        import openpyxl
        # 암호화 파일인지 검사
        if file_bytes.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1') or password:
            try:
                import msoffcrypto
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
                for yr, mo in sorted(touched_months):
                    recalc_monthly_summary(conn, user_id, yr, mo, commit=False)

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
    return redirect(url_for('dashboard'))

# ==========================================
# 🛡️ 관리자 전용 콘솔 라우트
# ==========================================

@app.route('/admin')
@admin_required
def admin_dashboard():
    try:
        users = get_cached_admin_stats()
        
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


@app.route('/admin/switch_user/<int:target_user_id>', methods=['POST'])
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


@app.route('/admin/switch_back', methods=['POST'])
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
    import openpyxl
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
@admin_required
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

    try:
        t2 = time.time()
        cur = conn.execute("SELECT 1")
        try:
            cur.fetchall()
        finally:
            cur.close()
        steps['query_select1_sec'] = round(time.time() - t2, 3)
    finally:
        t3 = time.time()
        conn.close()
        steps['conn_close_sec'] = round(time.time() - t3, 3)

    steps['pool_status'] = get_pool_status()
    with _CACHE_LOCK:
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
    conn = getattr(g, '_database', None)
    if conn is not None:
        try:
            conn.rollback()
        except Exception:
            pass
    return render_template(
        'error.html',
        error_code=500,
        error_title='시스템 일시 오류가 발생했습니다',
        error_message='요청을 처리하는 도중 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.'
    ), 500


if __name__ == '__main__':
    bootstrap_app_extensions()
    print("\n=== 약국 순익 관리 시스템 (다중 약국 온라인 SaaS) 시작 ===")
    print("접속 주소: http://localhost:5000\n")
    app.run(debug=True, host='0.0.0.0', port=5000)
