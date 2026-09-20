"""UI contract: every financial breakdown uses the three underlying components.

These checks use only repository source; they do not access production data.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path):
    return (ROOT / path).read_text(encoding='utf-8')


def test_dashboard_calendar_report_show_three_components():
    for path in ('templates/dashboard.html', 'templates/calendar.html', 'templates/report.html'):
        template = source(path)
        for label in ('조제료', '일매순익', '비보험마진'):
            assert label in template, (path, label)
        assert 'components_for(' in template, path
    assert 'period_components(' in source('templates/report.html')


def test_dashboard_and_trend_charts_consume_three_series():
    chart_source = source('static/js/charts.js')
    for key in ('monthlyData.dispensing_fee', 'monthlyData.daily_net_profit',
                'weeklyData.dispensing', 'weeklyData.daily', 'initThreeWayTrendChart'):
        assert key in chart_source, key
    assert 'three_way_series(' in source('templates/dashboard.html')
    assert 'three_way_series(' in source('templates/trend.html')


def test_csv_has_three_separate_columns_and_no_fabricated_history():
    app_source = source('app.py')
    assert "'연도', '월', '조제료', '일매순익', '비보험마진'" in app_source
    assert 'attach_components(rows, grouped)' in app_source
    assert '세부자료 확인 필요' in app_source
    assert 'install_profit_display(app)' in app_source


def test_calculator_and_core_math_are_not_part_of_ui_patch():
    # Guard our implementation scope: three-way presentation helpers are read-only.
    helpers = source('profit_display.py') + source('profit_components.py')
    assert 'INSERT INTO' not in helpers
    assert 'UPDATE daily_profit' not in helpers
    assert 'DELETE FROM' not in helpers
