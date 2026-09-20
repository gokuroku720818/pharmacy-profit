"""Retire obsolete independent-monthly-ledger labels on financial displays.

Only presentation strings are changed. Daily totals, discrepancy warnings,
finance records and the standalone calculator are left untouched.
"""
from functools import wraps


_REPORT_REPLACEMENTS = (
    ('일별 기록의 세 수익 항목 + 별도 입력 잡이익 기준 구성비입니다. 월장부 전체순익은 별도 기준입니다.',
     '일별 세 수익 항목과 잡이익의 구성비입니다. 일별 total과 세 항목 합계가 다르면 차액을 별도 표시합니다.'),
    ('일별 기록+잡이익 기준 합계:', '세 수익 항목+잡이익 합계:'),
    ('월장부 전체순익:', '일별 total+잡이익 월합계:'),
    ('서로 다른 원본의 차액:', '일별 total-세 항목 차이:'),
    ('비보험 알짜 마진율 (월장부 기준)', '비보험 순익 비중 (일별 기록 기준)'),
    ('월별 순익 상세 장부', '월별 자동 집계'),
    ('월장부 순익 기준', '일별 기록 자동 집계 기준'),
    ('월장부', '일별 자동 집계'),
)

_OLD_RECONCILIATION = ('이전 월장부 원본은 삭제하거나 수정하지 않았고 '
                       '공식 계산에는 사용하지 않습니다.')
_NEW_RECONCILIATION = ('월별 합계는 일별 기록으로 자동 생성되며 '
                       '별도로 입력하는 월별 장부는 없습니다.')


def install(module):
    """Update only the two affected financial pages once per worker."""
    if getattr(module, '_daily_labels_installed', False):
        return
    old_render = module.render_template

    @wraps(old_render)
    def render_without_old_month_ledger(template_name, *args, **context):
        html = old_render(template_name, *args, **context)
        if template_name == 'report.html':
            for obsolete, replacement in _REPORT_REPLACEMENTS:
                html = html.replace(obsolete, replacement)
        return html

    module.render_template = render_without_old_month_ledger
    old_reconciliation = module.app.view_functions['historical_reconciliation']

    @wraps(old_reconciliation)
    def reconciliation_without_old_month_ledger(*args, **kwargs):
        response = old_reconciliation(*args, **kwargs)
        if (hasattr(response, 'status_code') and response.status_code == 200
                and response.mimetype == 'text/html'):
            page = response.get_data(as_text=True)
            response.set_data(page.replace(_OLD_RECONCILIATION, _NEW_RECONCILIATION))
        return response

    module.app.view_functions['historical_reconciliation'] = reconciliation_without_old_month_ledger
    module._daily_labels_installed = True
