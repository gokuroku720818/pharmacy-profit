"""Real production dialect, only disposable transaction-local synthetic tables."""
import datetime as dt

import database
from test_analysis import service
from test_postgres_integration import postgres_pool


def test_shared_miscellaneous_ranges_on_postgresql(service, postgres_pool):
    connection = database.get_db()
    try:
        connection.execute('CREATE TEMP TABLE extra_profit (user_id integer, date varchar(10), amount bigint)')
        for user, day, amount in [(321, '2022-07-01', 7), (321, '2024-12-01', 999),
                                  (321, '2026-09-01', 11), (322, '2026-09-01', 999)]:
            connection.execute('INSERT INTO extra_profit VALUES (?, ?, ?)', (user, day, amount))
        result = service.get_recent_weeks_profit_stats(connection, 321, rows=[],
            extra_range=(dt.date(2022, 7, 1), dt.date(2022, 8, 31)))
        assert result['extra_by_date'] == {'2022-07-01': 7, '2026-09-01': 11}
        assert result['weeks'][1]['extra_sum'] == 11
    finally:
        connection.close()
