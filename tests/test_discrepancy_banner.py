def test_daily_total_discrepancy_warning_is_suppressed():
    from daily_monthly_ledger import filter_sitewide_warning

    message = ('주의: 일별 total 합계와 조제료·일매순익·비보험마진 합계가 서로 다른 날짜가 있습니다. '
               '공식 월합계는 일별 total을 사용하고 차액은 월별 합계 검증 화면에서 별도로 표시합니다.')
    assert filter_sitewide_warning(message, 'warning') is None
