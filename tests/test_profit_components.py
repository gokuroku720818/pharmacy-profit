"""Synthetic-only proof of 3-part pharmacy profit reporting.

Only data consistent with existing monthly_summary may be split. In particular,
monthly-only Excel imports must never be guessed into dispensing/daily amounts.
"""
import sqlite3

import profit_components


def sample_db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE daily_profit (
        user_id INTEGER, date TEXT, dispensing_fee INTEGER,
        daily_net_profit INTEGER, non_insurance_margin INTEGER, total INTEGER)''')
    conn.executemany('INSERT INTO daily_profit VALUES (?, ?, ?, ?, ?, ?)', [
        (1, '2026-08-01', 100, 40, 20, 160),
        (1, '2026-08-02', 300, 60, 30, 390),
        (1, '2026-09-01', 200, 80, 70, 350),
        (2, '2026-08-01', 99999, 99999, 99999, 299997),
    ])
    return conn


def summary(year=2026, month=8, dpd=500, nim=50, total=550):
    return {'year': year, 'month': month,
            'dispensing_plus_daily_total': dpd,
            'non_insurance_total': nim, 'grand_total': total}


def test_grouped_components_split_actual_daily_data_and_isolate_users():
    conn = sample_db()
    try:
        grouped = profit_components.load_monthly_components(conn, 1)
    finally:
        conn.close()
    assert grouped[(2026, 8)] == {
        'dispensing_fee': 400, 'daily_net_profit': 100,
        'non_insurance_margin': 50, 'grand_total': 550,
    }
    assert grouped[(2026, 9)]['dispensing_fee'] == 200
    assert len(grouped) == 2


def test_verified_split_matches_existing_month_and_preserves_total():
    details = profit_components.attach_components([summary()], {
        (2026, 8): {'dispensing_fee': 400, 'daily_net_profit': 100,
                    'non_insurance_margin': 50, 'grand_total': 550}
    })
    assert details[0]['breakdown_available'] is True
    assert [details[0][key] for key in ('dispensing_fee', 'daily_net_profit', 'non_insurance_margin')] == [400, 100, 50]
    assert details[0]['grand_total'] == 550


def test_monthly_only_import_has_unknown_not_fabricated_component_values():
    details = profit_components.attach_components([summary()], {})
    assert details[0]['breakdown_available'] is False
    assert details[0]['dispensing_fee'] is None
    assert details[0]['daily_net_profit'] is None
    assert details[0]['non_insurance_margin'] is None
    assert details[0]['grand_total'] == 550


def test_inconsistent_daily_sum_does_not_show_invented_monthly_split():
    details = profit_components.attach_components([summary()], {
        (2026, 8): {'dispensing_fee': 350, 'daily_net_profit': 100,
                    'non_insurance_margin': 50, 'grand_total': 500}
    })
    assert details[0]['breakdown_available'] is False
    assert details[0]['dispensing_fee'] is None
    assert details[0]['grand_total'] == 550


def test_component_totals_unknown_if_any_month_unverifiable():
    known = {'breakdown_available': True, 'dispensing_fee': 400,
             'daily_net_profit': 100, 'non_insurance_margin': 50}
    missing = {'breakdown_available': False, 'dispensing_fee': None,
               'daily_net_profit': None, 'non_insurance_margin': None}
    assert profit_components.total_components([known]) == {
        'dispensing_fee': 400, 'daily_net_profit': 100, 'non_insurance_margin': 50}
    assert profit_components.total_components([known, missing]) is None
    assert profit_components.total_components([]) is None
