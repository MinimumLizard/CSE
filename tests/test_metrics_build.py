import pandas as pd
import pytest

from cse import backfill, build, fetch, metrics
from tests.conftest import load


def _series(name):
    chart = load(name)
    return chart


def test_prefer_daily_over_backfill():
    df = pd.DataFrame([
        {"date": "2026-10-02", "symbol": "LOLC.N0000", "close": 439.25, "source": "backfill"},
        {"date": "2026-10-02", "symbol": "LOLC.N0000", "close": 439.25, "source": "daily"},
    ])
    out = metrics._prefer_daily(df, ["symbol", "date"])
    assert len(out) == 1 and out.iloc[0]["source"] == "daily"


def test_relative_returns_need_enough_sessions():
    cal = ["2026-09-28", "2026-09-29", "2026-09-30"]
    s = pd.Series([1.0, 1.0, 1.0], index=cal)
    out = metrics.relative_returns(s, s, cal)
    assert out["1w"] == {"value": None, "needed": 3, "flag": None}
    assert out["3m"]["needed"] == 61


def test_relative_returns_on_real_history(root, client):
    fetch.run(client, root)
    backfill.run(client, root)
    p = fetch.Paths(root)
    prices = metrics.load_prices(p.prices)
    idx = metrics.load_indices(p.indices)
    cal = metrics.session_calendar(idx)
    assert len(cal) == 240
    aspi = idx[idx["index"] == "ASI"].set_index("date")["value"].sort_index()
    lolc = prices[prices.symbol == "LOLC.N0000"].set_index("date")["close"].sort_index()
    rel = metrics.relative_returns(lolc, aspi, cal)
    start = cal[-1 - 5]
    expected = (lolc.loc[:cal[-1]].iloc[-1] / lolc.loc[:start].iloc[-1] - 1) - (aspi.iloc[-1] / aspi.loc[:start].iloc[-1] - 1)
    assert rel["1w"]["value"] == pytest.approx(expected)
    cic = prices[prices.symbol == "CIC.N0000"].set_index("date")["close"].sort_index()
    assert metrics.has_jump(cic, cal[0])            # the unadjusted 1:5 split is detected and flagged


def test_volume_vs_average_counts_no_trade_days_as_zero():
    cal = [f"2026-09-{d:02d}" for d in range(1, 23)]          # 22 sessions
    vols = pd.Series({cal[-2]: 200.0})                        # traded once in the prior 20
    out = metrics.volume_vs_average(vols, cal, 100.0)
    assert out["avg"] == pytest.approx(10.0) and out["value"] == pytest.approx(10.0)
    assert metrics.volume_vs_average(vols, cal[:10], 1.0)["needed"] == 11


def test_range_position():
    assert metrics.range_position(15, 10, 20) == 0.5
    assert metrics.range_position(15, None, 20) is None


def test_build_renders_page(root, client):
    fetch.run(client, root)
    backfill.run(client, root)
    ctx = build.build_context(root)
    assert ctx["session"] == "2026-10-02"
    labels = [h["label"] for h in ctx["headline"]]
    assert labels[:2] == ["ASPI", "S&P SL20"] and "ASPI TRI" in labels
    assert ctx["stats"]["turnover"]["needed"] == 19            # 2 daily sessions so far, 21 required
    out = root / "site" / "index.html"
    build.render(ctx, out)
    html = out.read_text()
    for needle in ["Session <b>2026-10-02</b>", "A · Market tape", "B · My book", "C · Disclosures",
                   "20,812.64", "LOLC", "RIGHTS ISSUE", "chart.umd.min.js", "19 more sessions needed"]:
        assert needle in html, needle
    assert "⛔" in html                                          # NTB excluded flag shows in top turnover


def test_formatters_pair_colour_with_sign():
    assert build.f_signed(1.5) == "+1.50" and build.f_signed(-1.5) == "−1.50" and build.f_signed(0) == "±0.00"
    assert build.f_spct(0.0123) == "+1.23%"
    assert build.f_rs(2.4e9) == "Rs 2.40 bn" and build.f_rs(5.5e6) == "Rs 5.50 mn" and build.f_rs(None) == "—"
