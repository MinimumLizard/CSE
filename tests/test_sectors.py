import pytest

from cse.config import ROOT
from cse.sectors import INDUSTRY_GROUPS, classify, load_overrides

# Raw labels exactly as returned by companyProfile on 2026-10-02.
@pytest.mark.parametrize("label,expected", [
    ("FOOD BEVERAGE & TOBACCO", "Food, Beverage & Tobacco"),
    ("Food Beverage & Tobacco", "Food, Beverage & Tobacco"),
    ("Food, Beverage & Tobacco", "Food, Beverage & Tobacco"),
    ("Insurance ", "Insurance"),
    ("FOOD & STAPLES RETAILING", "Food & Staples Retailing"),
    (" Investment Banking & Brokerage", "Diversified Financials"),
    ("- Property & Casualty Insurance", "Insurance"),
    ("45103010 - Application Software", "Software & Services"),
    ("Real Estate (6010)", "Real Estate Management & Development"),
    ("Real Estate Management&Development", "Real Estate Management & Development"),
    ("Independent Power Producers & Energy Traders", "Utilities"),
    ("Industrials", None),          # GICS sector, spans several groups: not guessed
    ("Trading", None),
    (None, None),
])
def test_label_mapping(label, expected):
    assert classify("X.N0000", label, {}) == expected


def test_override_wins_and_is_validated(tmp_path):
    assert classify("MSL.N0000", "Services", {"MSL.N0000": "Transportation"}) == "Transportation"
    bad = tmp_path / "o.yaml"
    bad.write_text("overrides:\n  MSL.N0000: Shipping\n")
    with pytest.raises(ValueError, match="industry groups"):
        load_overrides(bad)


def test_repo_overrides_are_valid():
    assert set(load_overrides(ROOT / "config" / "sector_overrides.yaml").values()) <= set(INDUSTRY_GROUPS)
