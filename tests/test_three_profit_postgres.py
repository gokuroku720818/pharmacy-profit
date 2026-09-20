"""Verify production SQL dialect against disposable PostgreSQL, with temp data only."""

import database
from profit_components import load_monthly_components
from test_postgres_integration import postgres_pool


def test_postgresql_three_component_grouping_handles_real_cursor(postgres_pool):
    connection = database.get_db()
    try:
        connection.execute('''CREATE TEMP TABLE daily_profit (
            user_id integer, date varchar(10), dispensing_fee bigint,
            daily_net_profit bigint, non_insurance_margin bigint, total bigint)''')
        connection.execute('''INSERT INTO daily_profit
            (user_id, date, dispensing_fee, daily_net_profit, non_insurance_margin, total)
            VALUES (?, ?, ?, ?, ?, ?)''', (321, '2026-09-20', 120, 35, 45, 200))
        connection.execute('''INSERT INTO daily_profit
            (user_id, date, dispensing_fee, daily_net_profit, non_insurance_margin, total)
            VALUES (?, ?, ?, ?, ?, ?)''', (321, '2026-09-21', 80, 25, 15, 120))
        connection.execute('''INSERT INTO daily_profit
            (user_id, date, dispensing_fee, daily_net_profit, non_insurance_margin, total)
            VALUES (?, ?, ?, ?, ?, ?)''', (322, '2026-09-20', 999, 999, 999, 2997))
        grouped = load_monthly_components(connection, 321)
        assert grouped == {(2026, 9): {
            'dispensing_fee': 200,
            'daily_net_profit': 60,
            'non_insurance_margin': 60,
            'grand_total': 320,
        }}
    finally:
        connection.close()  # Roll back temporary table and synthetic rows.
