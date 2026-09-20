"""Read-only three-component monthly presentation with conservative reconciliation.

monthly_summary historically stores dispensing + OTC combined; some Excel imports
have only that aggregate and no daily source records. Never invent a split.
Miscellaneous gains live in a separate ledger and affect the DISPLAY grand total.
"""


COMPONENT_KEYS = ('dispensing_fee', 'daily_net_profit', 'non_insurance_margin')


def load_monthly_components(conn, user_id):
    """Aggregate all of one user's daily rows in one portable SQL query."""
    rows = conn.execute('''
        SELECT SUBSTR(date, 1, 4) AS year_text,
               SUBSTR(date, 6, 2) AS month_text,
               SUM(COALESCE(dispensing_fee, 0)) AS dispensing_fee,
               SUM(COALESCE(daily_net_profit, 0)) AS daily_net_profit,
               SUM(COALESCE(non_insurance_margin, 0)) AS non_insurance_margin,
               SUM(COALESCE(total, 0)) AS grand_total
        FROM daily_profit
        WHERE user_id = ?
        GROUP BY SUBSTR(date, 1, 4), SUBSTR(date, 6, 2)
    ''', (user_id,)).fetchall()
    return {
        (int(row['year_text']), int(row['month_text'])): {
            key: int(row[key] or 0) for key in (*COMPONENT_KEYS, 'grand_total')
        }
        for row in rows
    }


def attach_components(summary_rows, grouped, *, present_source=False):
    """Attach source-derived component amounts without modifying ledger totals.

    The default strict mode leaves unverified splits unknown (e.g. for legacy
    exports). UI may opt into present_source to show *actual daily-source sums*
    even when independently imported monthly totals disagree. Callers must
    disclose the provenance and discrepancy; the source values are never
    presented as a mathematically reconciled split in this mode.
    """
    details = []
    for summary in summary_rows:
        item = dict(summary)
        source = grouped.get((int(item['year']), int(item['month'])))
        combined = int(item['dispensing_plus_daily_total'] or 0)
        nim = int(item['non_insurance_total'] or 0)
        grand = int(item['grand_total'] or 0)
        extra = int(item.get('extra_profit_total') or 0)
        verified = bool(source is not None and
                        source['dispensing_fee'] + source['daily_net_profit'] == combined and
                        source['non_insurance_margin'] == nim and
                        combined + nim + extra == grand)
        available = verified or (present_source and source is not None)
        item['extra_profit_total'] = extra
        item['breakdown_available'] = available
        item['breakdown_verified'] = verified
        item['breakdown_source'] = '일별 기록 기준' if source is not None else None
        item['breakdown_difference'] = (
            grand - extra - sum(int(source[key]) for key in COMPONENT_KEYS)
            if source is not None else None
        )
        item.update({key: int(source[key]) if available else None for key in COMPONENT_KEYS})
        details.append(item)
    return details


def total_components(rows):
    """Only total a whole period when every month has actual source figures."""
    if not rows or any(not row['breakdown_available'] for row in rows):
        return None
    return {key: sum(int(row[key]) for row in rows) for key in COMPONENT_KEYS}
