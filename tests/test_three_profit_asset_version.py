"""Stale browser JS must not break the new three-component dashboard/trend."""
from pathlib import Path


def test_chart_asset_url_is_versioned_for_three_component_release():
    base = (Path(__file__).resolve().parents[1] / 'templates' / 'base.html').read_text(encoding='utf-8')
    assert "url_for('static', filename='js/charts.js', v='three-profit-20260920')" in base
