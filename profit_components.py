"""Read-only three-component monthly presentation with conservative reconciliation.

monthly_summary historically stores dispensing + OTC combined; some Excel imports
have only that aggregate and no daily source records. Never invent a split.
This module does not mutate tables, calculator settings or monetary formulas.
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


def attach_components(summary_rows, grouped):
    """Copy summaries, adding components ONLY when source sums exactly match.

    None denotes unavailable/contradictory historical breakdown; it is not
    numeric zero. Report totals remain the stored monthly grand_total.
    """
    details = []
    for summary in summary_rows:
        item = dict(summary)
        source = grouped.get((int(item['year']), int(item['month'])))
        combined = int(item['dispensing_plus_daily_total'] or 0)
        nim = int(item['non_insurance_total'] or 0)
        grand = int(item['grand_total'] or 0)
        verified = bool(source is not None and
                        source['dispensing_fee'] + source['daily_net_profit'] == combined and
                        source['non_insurance_margin'] == nim and
                        source['grand_total'] == grand and
                        combined + nim == grand)
        item['breakdown_available'] = verified
        if verified:
            item.update({key: source[key] for key in COMPONENT_KEYS})
        else:
            item.update({key: None for key in COMPONENT_KEYS})
        details.append(item)
    return details


def total_components(rows):
    """Only report a whole-period three-way split when all months reconcile."""
    if not rows or any(not row['breakdown_available'] for row in rows):
        return None
    return {key: sum(int(row[key]) for row in rows) for key in COMPONENT_KEYS}
