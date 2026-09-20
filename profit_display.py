"""Request-scoped template helpers for reconciled three-way and miscellaneous profit."""
from flask import g, session
from database import get_db
from extra_profit import monthly_totals
from profit_components import attach_components, load_monthly_components, total_components


def install(app):
    """Expose narrow template helpers without changing stored monetary formulas."""
    if app.extensions.get('three_profit_display_installed'):
        return

    @app.context_processor
    def inject_three_profit_helpers():
        if 'user_id' not in session:
            return {}

        def grouped():
            if not hasattr(g, '_three_profit_grouped'):
                conn = get_db()
                try:
                    g._three_profit_grouped = load_monthly_components(conn, session['user_id'])
                finally:
                    conn.close()
            return g._three_profit_grouped

        def extras():
            if not hasattr(g, '_extra_profit_grouped'):
                conn = get_db()
                try:
                    g._extra_profit_grouped = monthly_totals(conn, session['user_id'])
                finally:
                    conn.close()
            return g._extra_profit_grouped

        def annotate(rows):
            extra = extras()
            return [dict(row, extra_profit_total=extra.get((int(row['year']), int(row['month'])), 0))
                    for row in rows]

        def components_for(year, month, combined, non_insurance, grand):
            row = {'year': int(year), 'month': int(month),
                   'dispensing_plus_daily_total': combined,
                   'non_insurance_total': non_insurance,
                   'grand_total': grand}
            return attach_components(annotate([row]), grouped())[0]

        def period_components(year, rows, months=None):
            subset = [dict(row, year=int(year)) for row in rows
                      if months is None or int(row['month']) in months]
            return total_components(attach_components(annotate(subset), grouped()))

        def three_way_series(labels, combined, non_insurance, totals):
            series = {'labels': list(labels), 'dispensing_fee': [],
                      'daily_net_profit': [], 'non_insurance_margin': [],
                      'extra_profit_total': [], 'totals': list(totals)}
            if not (len(labels) == len(combined) == len(non_insurance) == len(totals)):
                raise ValueError('Monthly chart series must have equal lengths')
            for label, dpd, nim, grand in zip(labels, combined, non_insurance, totals):
                year_text, month_text = str(label).split('.')
                split = components_for(int(year_text), int(month_text), dpd, nim, grand)
                for field in ('dispensing_fee', 'daily_net_profit', 'non_insurance_margin'):
                    series[field].append(split[field])
                series['extra_profit_total'].append(split['extra_profit_total'])
            return series

        return {'components_for': components_for,
                'period_components': period_components,
                'three_way_series': three_way_series}

    app.extensions['three_profit_display_installed'] = True
