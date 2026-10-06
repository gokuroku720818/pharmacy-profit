"""기존 조회 결과만 사용하는 경영 판단. DB 접근·장부 변경·세액 계산 없음."""
import calendar
import datetime as dt
from fractions import Fraction

from profit_analysis import FIELDS, is_business_day, korea_today


def _period(rows, extras, start, end, schedule, today):
    """0원 입력도 영업 기록으로 세며 미입력·휴무·미래 기록을 구분한다."""
    dates = {r['date']: r for r in rows if start.isoformat() <= r['date'] <= end.isoformat()}
    expected = []
    day = start
    while day <= end:
        if is_business_day(day, schedule):
            expected.append(day.isoformat())
        day += dt.timedelta(days=1)
    unentered = [day for day in expected if day not in dates]
    components = {field: sum(int(r[field] or 0) for r in dates.values()) for field, _ in FIELDS}
    daily_total = sum(int(r['total'] or 0) for r in dates.values())
    extra_total = sum(int(amount) for day, amount in extras.items()
                      if start.isoformat() <= day <= end.isoformat())
    outside = sum(not is_business_day(dt.date.fromisoformat(day), schedule) for day in dates)
    discrepancy = daily_total - sum(components.values())
    # 날짜별 차액이 상쇄되어도 일치한 기록으로 오인하지 않는다.
    discrepancy_days = sum(int(r['total'] or 0) != sum(int(r[f] or 0) for f, _ in FIELDS)
                           for r in dates.values())
    return {'start': start.isoformat(), 'end': end.isoformat(),
            'daily_total': daily_total, 'extra_total': extra_total,
            'total': daily_total + extra_total, 'components': components,
            'entered_count': len(dates), 'expected_days': len(expected),
            'entered_business_days': len(dates) - outside,
            'outside_schedule_count': outside,
            'missing_dates': [day for day in unentered if day < today.isoformat()],
            'awaiting_today': today.isoformat() in unentered,
            'complete': bool(expected) and not unentered and not outside,
            'discrepancy': discrepancy, 'discrepancy_days': discrepancy_days,
            'has_records': bool(dates) or any(start.isoformat() <= day <= end.isoformat() for day in extras)}


def build_management_review(rows, extra_by_date, year, month, schedule, as_of=None):
    """동기간 항목 증감·잡이익 제외 실적·영업일수 효과를 인메모리에서 계산한다."""
    today = as_of or korea_today()
    start = dt.date(year, month, 1)
    end = dt.date(year, month, calendar.monthrange(year, month)[1])
    cutoff = min(today, end)
    previous_end = start - dt.timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    if start > today:
        previous_cutoff = previous_start - dt.timedelta(days=1)
    elif end < today:
        previous_cutoff = previous_end
    else:
        previous_cutoff = previous_start.replace(day=min(cutoff.day, previous_end.day))
    current = _period(rows, extra_by_date, start, cutoff, schedule, today)
    previous = _period(rows, extra_by_date, previous_start, previous_cutoff, schedule, today)
    components = [{'label': label, 'current': current['components'][field],
                   'previous': previous['components'][field],
                   'diff': current['components'][field] - previous['components'][field]}
                  for field, label in FIELDS]
    components.append({'label': '잡이익', 'current': current['extra_total'],
                       'previous': previous['extra_total'],
                       'diff': current['extra_total'] - previous['extra_total']})
    day_effects = None
    if current['complete'] and previous['complete'] and not (
            current['discrepancy_days'] or previous['discrepancy_days']):
        now_days, old_days = current['entered_business_days'], previous['entered_business_days']
        now_avg = Fraction(current['daily_total'], now_days)
        old_avg = Fraction(previous['daily_total'], old_days)
        days_effect = round(old_avg * (now_days - old_days))
        daily_diff = current['daily_total'] - previous['daily_total']
        day_effects = {'current_days': now_days, 'previous_days': old_days,
                      'current_average': round(now_avg), 'previous_average': round(old_avg),
                      'days_effect': days_effect, 'average_effect': daily_diff - days_effect,
                      'daily_total_diff': daily_diff}
    observed_dates = {r['date'] for r in rows if start.isoformat() <= r['date'] <= cutoff.isoformat()}
    remaining = sum(is_business_day(start + dt.timedelta(days=n), schedule)
                    and start + dt.timedelta(days=n) >= today
                    and (start + dt.timedelta(days=n)).isoformat() not in observed_dates
                    for n in range(end.day))
    return {'year': year, 'month': month, 'as_of': today.isoformat(),
            'current': current, 'previous': previous, 'components': components,
            'has_comparison': current['has_records'] and previous['has_records'],
            'total_diff': current['total'] - previous['total'],
            'discrepancy_diff': current['discrepancy'] - previous['discrepancy'],
            'day_effects': day_effects, 'remaining_business_days': remaining,
            'can_set_goal': start <= today <= end,
            'target_ready': not current['missing_dates'] and not current['outside_schedule_count']
                            and not current['discrepancy_days'],
            'future_entry_count': sum(cutoff.isoformat() < r['date'] <= end.isoformat()
                                      for r in rows if r['date'] >= start.isoformat())}
