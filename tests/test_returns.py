"""Corporate-action formulas, each against a hand-worked example built from a REAL event and
the REAL closes around it (tests/fixtures/2026-10-02/companyChartDataByStock)."""
import pandas as pd
import pytest

from cse.models import ms_to_date
from cse.returns import (AdjustmentMismatch, check_adjustment, sample_on_or_before, total_returns,
                         weekly_returns, weekly_sum)
from tests.conftest import load


def closes(symbol):
    rows = load(f"companyChartDataByStock/{symbol}.json")["chartData"]
    return pd.Series({ms_to_date(r["t"]).isoformat(): r["p"] for r in rows}).sort_index()


def ret_on(df, date):
    return df.loc[date, "ret"]


def test_no_actions_is_plain_price_return():
    c = closes("LOLC.N0000")
    df = total_returns(c, [])
    assert df["ret"].iloc[1:].tolist() == pytest.approx((c / c.shift(1) - 1).iloc[1:].tolist())
    assert df["tri"].iloc[-1] == pytest.approx(c.iloc[-1] / c.iloc[0])


def test_cash_dividend_cic_2026():
    """CIC final dividend Rs 0.50, XD 01 Jul 2026 (announcement 37168).
    Cum close 30 Jun 31.00, ex close 01 Jul 31.80:
      R = (31.80 + 0.50) / 31.00 − 1 = 32.30 / 31.00 − 1 = 0.0419355"""
    a = [{"symbol": "CIC.N0000", "type": "cash_dividend", "ex_date": "2026-07-01", "amount_per_share": 0.5}]
    df = total_returns(closes("CIC.N0000"), a)
    assert ret_on(df, "2026-07-01") == pytest.approx(32.30 / 31.00 - 1)
    # With 15% withholding: (31.80 + 0.50 × 0.85) / 31.00 − 1 = 32.225 / 31.00 − 1
    df = total_returns(closes("CIC.N0000"), a, withholding=0.15)
    assert ret_on(df, "2026-07-01") == pytest.approx(32.225 / 31.00 - 1)


def test_subdivision_cic_2025():
    """CIC 1:5 sub-division (32457: 291,600,000 → 1,458,000,000 voting shares, F = 5); trading
    resumed 22 Oct 2025 (33380). Last close before 13 Oct 170.00, first after 34.20:
      a = 1 held, b = 4 new → F = (1 + 4) / 1 = 5
      R = 34.20 × 5 / 170.00 − 1 = 171 / 170 − 1 = 0.0058824"""
    a = [{"symbol": "CIC.N0000", "type": "subdivision", "ex_date": "2025-10-22", "factor": 5.0}]
    df = total_returns(closes("CIC.N0000"), a)
    assert ret_on(df, "2025-10-22") == pytest.approx(171 / 170 - 1)
    raw = closes("CIC.N0000")
    assert raw["2025-10-22"] / raw["2025-10-13"] - 1 == pytest.approx(-0.79882, abs=1e-5)  # what unadjusted would show


def test_scrip_comb_2026():
    """COMB scrip, XD 02 Apr 2026 (36113): 1 new share per 108.2352945432 held.
      F = (a + b) / a = 109.2352945432 / 108.2352945432 = 1.0092391
      cum 31 Mar 204.25, ex 02 Apr 201.75: R = 201.75 × F / 204.25 − 1"""
    a_held, b_new = 108.2352945432, 1.0
    f = (a_held + b_new) / a_held
    assert f == pytest.approx(1.0092391, abs=1e-7)
    act = [{"symbol": "COMB.N0000", "type": "scrip", "ex_date": "2026-04-02", "factor": f}]
    df = total_returns(closes("COMB.N0000"), act)
    assert ret_on(df, "2026-04-02") == pytest.approx(201.75 * f / 204.25 - 1)
    assert ret_on(df, "2026-04-02") == pytest.approx(-0.0031141, abs=1e-6)


def test_rights_hayl_2026():
    """HAYL rights: 3 new per 50 held at Rs 200 (35376), XR 18 Mar 2026 (35920).
      TERP = (n × P_cum + m × S) / (n + m) = (50 × 205.00 + 3 × 200) / 53 = 10,850 / 53 = 204.716981
      R = P_ex / TERP − 1 = 203.00 / 204.716981 − 1 = −0.0083871"""
    act = [{"symbol": "HAYL.N0000", "type": "rights", "ex_date": "2026-03-18", "ratio_new": 3.0,
            "ratio_held": 50.0, "subscription_price": 200.0}]
    df = total_returns(closes("HAYL.N0000"), act)
    terp = (50 * 205.00 + 3 * 200) / 53
    assert terp == pytest.approx(204.716981, abs=1e-6)
    assert ret_on(df, "2026-03-18") == pytest.approx(203.00 / terp - 1)
    assert ret_on(df, "2026-03-18") == pytest.approx(-0.0083871, abs=1e-7)


def test_other_days_have_factor_one_and_no_dividend():
    a = [{"symbol": "CIC.N0000", "type": "subdivision", "ex_date": "2025-10-22", "factor": 5.0}]
    c = closes("CIC.N0000")
    df = total_returns(c, a)
    plain = c / c.shift(1) - 1
    others = [d for d in df.index[1:] if d != "2025-10-22"]
    assert df.loc[others, "ret"].tolist() == pytest.approx(plain[others].tolist())


def test_action_lands_on_first_trade_after_ex_date():
    """An ex-date on a non-trading day is applied to the next traded close."""
    c = closes("CIC.N0000")
    a = [{"symbol": "CIC.N0000", "type": "subdivision", "ex_date": "2025-10-18", "factor": 5.0}]  # a Saturday
    assert ret_on(total_returns(c, a), "2025-10-22") == pytest.approx(171 / 170 - 1)


# --- no double adjustment -------------------------------------------------------------------

SPLIT = [{"symbol": "CIC.N0000", "type": "subdivision", "ex_date": "2025-10-22", "factor": 5.0}]


def test_unadjusted_history_is_adjusted_once():
    check_adjustment(closes("CIC.N0000"), SPLIT, history_adjusted=False)   # the real data: passes
    with pytest.raises(AdjustmentMismatch, match="history_adjusted is true"):
        check_adjustment(closes("CIC.N0000"), SPLIT, history_adjusted=True)


def test_already_adjusted_history_is_not_adjusted_again():
    """Simulate an adjusted source by dividing CIC's real pre-split closes by 5."""
    c = closes("CIC.N0000")
    adj = c.where(c.index >= "2025-10-22", c / 5)
    with pytest.raises(AdjustmentMismatch, match="refusing to adjust them a second time"):
        check_adjustment(adj, SPLIT, history_adjusted=False)
    check_adjustment(adj, SPLIT, history_adjusted=True)
    df = total_returns(adj, SPLIT, history_adjusted=True)
    assert ret_on(df, "2025-10-22") == pytest.approx(34.2 / 34.0 - 1)       # no second ×5
    assert df["events"].loc["2025-10-22"] == ""


# --- weekly sampling ------------------------------------------------------------------------

def test_weekly_uses_last_close_on_or_before_wednesday():
    c = closes("LOLC.N0000")
    weds = ["2026-09-16", "2026-09-23", "2026-09-30"]
    s = sample_on_or_before(c, weds)
    for w in weds:
        assert s[w] == c.loc[:w].iloc[-1]
    r = weekly_returns(c, weds)
    assert list(r.index) == weds[1:]
    assert r["2026-09-30"] == pytest.approx(s["2026-09-30"] / s["2026-09-23"] - 1)


def test_weekly_sum_window():
    c = closes("LOLC.N0000")
    s = weekly_sum(c, ["2026-09-16", "2026-09-23"])
    assert s["2026-09-23"] == pytest.approx(c[(c.index > "2026-09-16") & (c.index <= "2026-09-23")].sum())
