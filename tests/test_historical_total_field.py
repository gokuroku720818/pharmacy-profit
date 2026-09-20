"""Legacy daily total cells must not hide otherwise fully reconciled components."""

from profit_components import attach_components, total_components


def test_legacy_daily_total_disagrees_but_all_monthly_components_and_monthly_total_match():
    # Independently imported historical Excel can carry an incorrect daily total
    # even though each category reconciles exactly with the official monthly ledger.
    monthly = [{
        'year': 2026, 'month': 8, 'dispensing_plus_daily_total': 500,
        'non_insurance_total': 50, 'grand_total': 550,
        'extra_profit_total': 0,
    }]
    grouped = {(2026, 8): {
        'dispensing_fee': 400, 'daily_net_profit': 100,
        'non_insurance_margin': 50, 'grand_total': 540,
    }}
    [result] = attach_components(monthly, grouped)
    assert result['breakdown_available'] is True
    assert (result['dispensing_fee'], result['daily_net_profit'], result['non_insurance_margin']) == (400, 100, 50)
    assert result['grand_total'] == 550
    assert total_components([result]) == {
        'dispensing_fee': 400, 'daily_net_profit': 100, 'non_insurance_margin': 50,
    }


def test_invalid_monthly_total_stays_unverified_even_if_component_fields_match():
    monthly = [{
        'year': 2026, 'month': 7, 'dispensing_plus_daily_total': 500,
        'non_insurance_total': 50, 'grand_total': 580,
        'extra_profit_total': 0,
    }]
    grouped = {(2026, 7): {
        'dispensing_fee': 400, 'daily_net_profit': 100,
        'non_insurance_margin': 50, 'grand_total': 550,
    }}
    [result] = attach_components(monthly, grouped)
    assert result['breakdown_available'] is False
    assert result['dispensing_fee'] is None
    assert result['grand_total'] == 580
