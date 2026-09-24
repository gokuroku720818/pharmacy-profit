"""Observed-profit analysis. No tax or standalone calculator logic lives here."""
import calendar
import datetime as dt
import json
from statistics import mean

from database import get_db

DAY_NAMES = '월화수목금토일'
FIELDS = [('dispensing_fee', '조제료'), ('daily_net_profit', '일매순익'),
          ('non_insurance_margin', '비보험 차액')]


def korea_today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()


def get_business_schedule(conn, user_id):
    row = conn.execute('SELECT settings_json FROM business_schedules WHERE user_id = ?', (user_id,)).fetchone()
    return json.loads(row['settings_json']) if row else {
        'weekdays': [0, 1, 2, 3, 4, 5], 'closed_dates': [], 'open_dates': []}


def is_business_day(day, schedule):
    iso = day.isoformat()
    if iso in schedule['closed_dates']:
        return False
    return iso in schedule['open_dates'] or day.weekday() in schedule['weekdays']


def get_month_forecast(conn, user_id, year, month, entered_rows=None, dow_avg=None,
                       last_year_total=None, as_of=None, schedule=None, history_rows=None, month_summary_row=None):
    """Eight-week weekday scenarios, with past gaps explicitly excluded.

    low/high are observed weekday minima/maxima, NOT confidence intervals.
    Keep dow_avg in the call signature for older callers, but never use lifetime averages.
    """
    needs_db = (
        history_rows is None or schedule is None or entered_rows is None
        or month_summary_row is None or last_year_total is None
    )
    owned = conn is None and needs_db
    if owned:
        conn = get_db()
        if hasattr(conn, 'use_autocommit_reads'):
            conn.use_autocommit_reads()
    try:
        today = as_of or korea_today()
        start = dt.date(year, month, 1)
        end = dt.date(year, month, calendar.monthrange(year, month)[1])
        # Historical views must not learn from later observations.
        cutoff = min(today, end)
        history_start = cutoff - dt.timedelta(days=55)
        history = conn.execute('''SELECT date, total FROM daily_profit
            WHERE user_id = ? AND date BETWEEN ? AND ? ORDER BY date''',
            (user_id, history_start.isoformat(), cutoff.isoformat())).fetchall() if history_rows is None else [
                r for r in history_rows if history_start.isoformat() <= r['date'] <= cutoff.isoformat()]
        schedule = schedule if schedule is not None else get_business_schedule(conn, user_id)
        if entered_rows is None:
            entered_rows = conn.execute('''SELECT date,total FROM daily_profit
                WHERE user_id = ? AND date BETWEEN ? AND ?''',
                (user_id, start.isoformat(), end.isoformat())).fetchall()
        observed = {r['date']: int(r['total'] or 0) for r in entered_rows
                    if start.isoformat() <= r['date'] <= min(today, end).isoformat()}
        future_count = sum(r['date'] > today.isoformat() for r in entered_rows)
        samples = {i: [] for i in range(7)}
        for row in history:
            day = dt.date.fromisoformat(row['date'])
            # Explicitly marked closures are not zero-profit operating days.
            if row['date'] not in schedule['closed_dates']:
                samples[day.weekday()].append(int(row['total'] or 0))
        stats = {DAY_NAMES[i]: {'count': len(values), 'mean': round(mean(values)),
                                'low': min(values), 'high': max(values)}
                 for i, values in samples.items() if values}
        missing, pending, unsupported = [], [], []
        sums = {'mean': 0, 'low': 0, 'high': 0}
        for n in range(1, end.day + 1):
            day = dt.date(year, month, n)
            if not is_business_day(day, schedule) or day.isoformat() in observed:
                continue
            if day < today:
                missing.append(day.isoformat())
            else:
                pending.append(day.isoformat())
                item = stats.get(DAY_NAMES[day.weekday()])
                if not item:
                    unsupported.append(day.isoformat())
                else:
                    for key in sums:
                        sums[key] += item[key]
        current = sum(observed.values())
        summary = conn.execute('SELECT grand_total FROM monthly_summary WHERE user_id = ? AND year = ? AND month = ?',
                               (user_id, year, month)).fetchone() if month_summary_row is None else month_summary_row
        summary_only = not observed and not future_count and bool(summary)
        if summary_only:
            current = int(summary['grand_total'] or 0)
            missing = []  # Daily detail is unavailable; the monthly total may already include it.
        can_estimate = not unsupported and not (summary_only and end >= today)
        total = current + sums['mean'] if can_estimate else None
        if last_year_total is None:
            row = conn.execute('''SELECT grand_total FROM monthly_summary
                WHERE user_id = ? AND year = ? AND month = ?''', (user_id, year-1, month)).fetchone()
            last_year_total = int(row['grand_total'] or 0) if row else 0
        comparable = total is not None and not missing and not future_count and not summary_only and last_year_total > 0
        return {
            'year': year, 'month': month, 'as_of': today.isoformat(), 'is_past': end < today,
            'current_total': current, 'entered_count': len(observed), 'summary_only': summary_only,
            'remaining_business_days': len(pending), 'missing_dates': missing,
            'missing_count': len(missing), 'future_entry_count': future_count,
            'unsupported_dates': unsupported, 'unsupported_count': len(unsupported),
            'expected_additional': sums['mean'] if can_estimate else None,
            'forecast_total': total, 'forecast_low': current + sums['low'] if can_estimate else None,
            'forecast_high': current + sums['high'] if can_estimate else None,
            'last_year_total': last_year_total,
            'yoy_growth_pct': round((total-last_year_total)/last_year_total*100, 1) if comparable else None,
            'weekday_samples': stats, 'sample_count': sum(s['count'] for s in stats.values()),
            'history_start': history_start.isoformat(), 'history_end': cutoff.isoformat(),
            'low_sample': any(stats[DAY_NAMES[dt.date.fromisoformat(d).weekday()]]['count'] < 4
                              for d in pending if d not in unsupported),
            'closed_dates': [dt.date(year, month, n).isoformat() for n in range(1, end.day+1)
                             if not is_business_day(dt.date(year, month, n), schedule)],
        }
    finally:
        if owned:
            conn.close()


def get_yoy_day_comparison(conn, user_id, date_str=None, today_row=None, comparison_rows=None):
    if today_row is None:
        if date_str:
            today_row = conn.execute('SELECT * FROM daily_profit WHERE user_id = ? AND date = ?',
                                     (user_id, date_str)).fetchone()
        else:
            today_row = conn.execute('''SELECT * FROM daily_profit WHERE user_id = ? AND date <= ?
                ORDER BY date DESC LIMIT 1''', (user_id, korea_today().isoformat())).fetchone()
    if not today_row:
        return None
    date = dt.date.fromisoformat(today_row['date'])
    previous_date = (date - dt.timedelta(days=364)).isoformat()
    previous = conn.execute('SELECT * FROM daily_profit WHERE user_id = ? AND date = ?',
                            (user_id, previous_date)).fetchone() if comparison_rows is None else next(
                                (r for r in comparison_rows if r['date'] == previous_date), None)
    value = int(today_row['total'] or 0)
    old = int(previous['total'] or 0) if previous else None
    return {'current_date': date.isoformat(), 'current_dow': DAY_NAMES[date.weekday()],
            'current_total': value, 'current_dispensing': int(today_row['dispensing_fee'] or 0),
            'current_daily': int(today_row['daily_net_profit'] or 0),
            'current_non_insurance': int(today_row['non_insurance_margin'] or 0),
            'yoy_date': previous_date, 'yoy_total': old,
            'yoy_dispensing': int(previous['dispensing_fee'] or 0) if previous else None,
            'yoy_daily': int(previous['daily_net_profit'] or 0) if previous else None,
            'diff': value-old if old is not None else None,
            'growth_pct': round((value-old)/old*100, 1) if old and old > 0 else None}


def get_profit_balance_diagnosis(conn, user_id, year, month, entered_rows=None, month_summary_row=None):
    if entered_rows is None:
        start = dt.date(year, month, 1)
        next_month = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        entered_rows = conn.execute(
            'SELECT * FROM daily_profit WHERE user_id = ? AND date >= ? AND date < ?',
            (user_id, start.isoformat(), next_month.isoformat())
        ).fetchall()
    entered_rows = [r for r in entered_rows if r['date'] <= korea_today().isoformat()]
    values = [sum(int(r[field] or 0) for r in entered_rows) for field, _ in FIELDS]
    total = sum(values)
    has_breakdown = bool(entered_rows)
    summary = month_summary_row
    if not has_breakdown and summary is None:
        summary = conn.execute('SELECT * FROM monthly_summary WHERE user_id = ? AND year = ? AND month = ?',
                               (user_id, year, month)).fetchone()
    if not has_breakdown:
        total = int(summary['grand_total'] or 0) if summary else 0
        values = [None, None, int(summary['non_insurance_total'] or 0) if summary else 0]
    percentages = [round(v/total*100, 1) if v is not None and total > 0 else 0.0 for v in values]
    return dict(zip(['disp_val', 'daily_val', 'nim_val'], values),
                **dict(zip(['disp_pct', 'daily_pct', 'nim_pct'], percentages)),
                total=total, has_breakdown=has_breakdown,
                can_chart=has_breakdown and total > 0 and all(v >= 0 for v in values),
                combined_val=int(summary['dispensing_plus_daily_total'] or 0) if summary else sum(v or 0 for v in values[:2]),
                status='info' if has_breakdown else 'secondary',
                status_text='입력 실적 기준' if has_breakdown else '세부 자료 없음',
                comment='입력된 일별 수익의 구성입니다. 비중만으로 경영 안정성이나 적정성을 판단하지 않습니다.'
                        if has_breakdown else '월 합계만 있어 조제료와 일매순익을 나눌 수 없습니다. 임의 비율을 적용하지 않습니다.')


def compare_periods(current, previous):
    if not current or not previous:
        return None
    now_total = sum(int(r['total'] or 0) for r in current)
    old_total = sum(int(r['total'] or 0) for r in previous)
    now_avg, old_avg = now_total/len(current), old_total/len(previous)
    components = []
    for field, label in FIELDS:
        now = sum(int(r[field] or 0) for r in current)
        old = sum(int(r[field] or 0) for r in previous)
        components.append({'label': label, 'current': now, 'previous': old, 'diff': now-old})
    return {'current_total': now_total, 'previous_total': old_total, 'diff': now_total-old_total,
            'growth_pct': round((now_total-old_total)/old_total*100, 1) if old_total > 0 else None,
            'current_days': len(current), 'previous_days': len(previous),
            'current_avg': round(now_avg), 'previous_avg': round(old_avg),
            'daily_avg_diff': round(now_avg-old_avg), 'components': components}


def get_ai_narrative_briefing(conn, user_id, current_month, forecast, latest_row=None,
                              cur_cum=None, cur_month_rows=None, comparison_rows=None):
    if latest_row is None:
        latest_row = conn.execute('''SELECT * FROM daily_profit WHERE user_id = ? AND date <= ?
            ORDER BY date DESC LIMIT 1''', (user_id, korea_today().isoformat())).fetchone()
    if not latest_row or latest_row['date'] > korea_today().isoformat():
        return None
    day = dt.date.fromisoformat(latest_row['date'])
    start = day.replace(day=1)
    previous_month_end = start-dt.timedelta(days=1)
    previous_start = previous_month_end.replace(day=1)
    previous_end = previous_start.replace(day=min(day.day, previous_month_end.day))
    if cur_month_rows is None:
        cur_month_rows = conn.execute('SELECT * FROM daily_profit WHERE user_id = ? AND date BETWEEN ? AND ?',
                                      (user_id, start.isoformat(), day.isoformat())).fetchall()
    current = [r for r in cur_month_rows if start.isoformat() <= r['date'] <= day.isoformat()]
    previous = conn.execute('SELECT * FROM daily_profit WHERE user_id = ? AND date BETWEEN ? AND ?',
                            (user_id, previous_start.isoformat(), previous_end.isoformat())).fetchall() if comparison_rows is None else [
                                r for r in comparison_rows if previous_start.isoformat() <= r['date'] <= previous_end.isoformat()]
    comparison = compare_periods(current, previous)
    total = int(latest_row['total'] or 0)
    paragraphs = [f"최근 입력일 {day.isoformat()} ({DAY_NAMES[day.weekday()]})의 합계는 <strong>{total:,}원</strong>입니다."]
    week_row = conn.execute('SELECT total FROM daily_profit WHERE user_id = ? AND date = ?',
                            (user_id, (day-dt.timedelta(days=7)).isoformat())).fetchone() if comparison_rows is None else next(
                                (r for r in comparison_rows if r['date'] == (day-dt.timedelta(days=7)).isoformat()), None)
    if week_row:
        change = total-int(week_row['total'] or 0)
        direction = '증가' if change > 0 else '감소' if change < 0 else '동일'
        paragraphs.append(f'지난주 동요일 대비 {abs(change):,}원 {direction}입니다.')
    if comparison:
        c = comparison
        direction = '증가' if c['diff'] > 0 else '감소' if c['diff'] < 0 else '동일'
        paragraphs.append(f"전월 {previous_start.isoformat()}~{previous_end.isoformat()} 입력 실적 대비 "
                          f"이번 달 1~{day.day}일 합계는 <strong>{abs(c['diff']):,}원 {direction}</strong>입니다. "
                          f"입력일수는 이번 달 {c['current_days']}일, 전월 {c['previous_days']}일입니다.")
        changes = ' · '.join(f"{v['label']} {v['diff']:+,}원" for v in c['components'])
        paragraphs.append(f'항목별 증감: {changes}.')
        paragraphs.append(f"입력일 기준 일평균은 {c['previous_avg']:,}원 → {c['current_avg']:,}원 "
                          f"({c['daily_avg_diff']:+,}원)입니다. 미입력일은 분모에서 제외하며, 실제 영업일수·요일 구성 차이는 남아 있습니다.")
    else:
        paragraphs.append('전월 같은 기간의 일별 자료가 부족해 항목별 증감과 일평균을 비교할 수 없습니다.')
    paragraphs.append('처방 건수 자료가 없어 조제료 변화가 처방 건수 때문인지 건당 조제료 때문인지는 구분할 수 없습니다.')
    if forecast and forecast['year'] == day.year and forecast['month'] == day.month:
        if forecast['missing_count']:
            paragraphs.append(f"지정 영업일 중 과거 {forecast['missing_count']}일이 미입력입니다. 누락일은 0원이나 예상 실적으로 채우지 않았습니다.")
        if forecast['forecast_total'] is not None and not forecast['is_past']:
            paragraphs.append(f"최근 8주 동요일 평균 기준 잠정 월말 합계는 {forecast['forecast_total']:,}원입니다. "
                              '입력 누락분은 제외되어 있으며, 휴무 설정과 자료 보완에 따라 달라집니다.')
    return {'date_title': f'{day.month}월 {day.day}일 ({DAY_NAMES[day.weekday()]})',
            'paragraphs': paragraphs, 'month_comparison': comparison}


def generate_annual_narrative_report(year, report_data, year_total, last_year_total,
                                     best_month, worst_month, quarterly_data,
                                     comparison_current_total=None, comparison_previous_total=None,
                                     comparison_label=None, use_period_comparison=False):
    if not report_data:
        return None
    total = int(year_total['grand'])
    paragraphs = [f"{year}년 입력된 {len(report_data)}개월의 합계는 <strong>{total:,}원</strong>, "
                  f"입력월 평균은 {total//len(report_data):,}원입니다."]
    if use_period_comparison:
        if comparison_current_total is not None and comparison_previous_total:
            change = int(comparison_current_total)-int(comparison_previous_total)
            direction = '증가' if change > 0 else '감소' if change < 0 else '동일'
            label = comparison_label or '전년 동일 기간'
            paragraphs.append(
                f'{label} 기준으로 {abs(change):,}원 {direction}입니다. '
                '진행 중인 현재 월은 완료월 비교에서 제외합니다.'
            )
        elif last_year_total:
            paragraphs.append(
                '전년도 저장 자료는 있으나 같은 기간의 월 자료가 완전하지 않아 증감률을 계산하지 않았습니다.'
            )
    elif last_year_total:
        change = total-int(last_year_total['grand'])
        direction = '증가' if change > 0 else '감소' if change < 0 else '동일'
        paragraphs.append(f'전년도 저장 합계와 단순 비교하면 {abs(change):,}원 {direction}입니다. '
                          '비교 기간 및 입력 완성도가 다를 수 있으므로 동기간 성장률로 해석하지 마세요.')
    if best_month and worst_month:
        paragraphs.append(f"입력월 중 최대는 {best_month['month']}월 {int(best_month['grand_total']):,}원, "
                          f"최소는 {worst_month['month']}월 {int(worst_month['grand_total']):,}원입니다. 진행 중인 달이나 입력 누락은 비교에 영향을 줍니다.")
    paragraphs.append(f"조제+일매 합계 {int(year_total['dpd']):,}원, 비보험 차액 {int(year_total['nim']):,}원입니다. "
                      '이 합계만으로 경영 안정성이나 계절적 원인을 단정하지 않습니다.')
    return paragraphs
