"""Optimiser constraints on real weekly returns (30 liquid CSE stocks, Oct 2025 - Sep 2026)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.covariance import LedoitWolf

from cse import optimize
from cse.estimates import dimson_beta

FIX = Path(__file__).parent / "fixtures"
R = pd.read_csv(FIX / "weekly_returns_30.csv", index_col=0)
META = pd.read_csv(FIX / "weekly_returns_30_meta.csv").set_index("symbol").loc[R.columns]
COV = LedoitWolf().fit(R.to_numpy()).covariance_ * 52
RF = 0.099


def problem(max_weight=0.10, max_sector=0.30, size=5_000_000, er=None, symbols=None):
    syms = symbols or list(R.columns)
    idx = [list(R.columns).index(s) for s in syms]
    med = META.loc[syms, "median_turnover"].to_numpy()
    liq = 0.20 * med * 10 / size
    beta = er if er is not None else np.array([1.0 + 0.02 * i for i in range(len(syms))])
    return optimize.Problem(symbols=syms, sectors=META.loc[syms, "sector"].tolist(), cov=COV[np.ix_(idx, idx)],
                            er=RF + beta * 0.06, rf=RF, caps=np.minimum(max_weight, liq), liq_caps=liq,
                            max_weight=max_weight, max_sector_weight=max_sector)


@pytest.mark.parametrize("solver", [optimize.min_variance, optimize.risk_parity, optimize.max_sharpe])
def test_all_constraints_hold_and_weights_sum_to_one(solver):
    p = problem()
    w = solver(p)
    rep = optimize.constraint_report(w, p)
    assert rep["ok"], rep
    assert w.sum() == pytest.approx(1.0, abs=1e-9)
    assert (w >= 0).all() and (w <= 0.10 + 1e-6).all()
    S = p.sector_matrix()
    assert (S @ w <= 0.30 + 1e-6).all()


def test_liquidity_cap_binds_when_portfolio_is_large():
    p = problem(size=1_000_000_000)          # Rs 1 bn: liquidity is tighter than max_weight for most names
    assert (p.liq_caps < 0.10).sum() >= 20
    optimize.check_feasible(p)
    for solver in (optimize.min_variance, optimize.risk_parity, optimize.max_sharpe):
        w = solver(p)
        assert (w <= p.liq_caps + 1e-6).all() and optimize.constraint_report(w, p)["ok"]


def test_min_variance_is_the_lowest_variance_point():
    p = problem()
    w_mv = optimize.min_variance(p)
    v_mv = w_mv @ p.cov @ w_mv
    for w in (optimize.risk_parity(p), optimize.max_sharpe(p), np.full(len(p.symbols), 1 / len(p.symbols))):
        assert v_mv <= w @ p.cov @ w + 1e-10


def test_max_sharpe_beats_the_others_on_sharpe():
    p = problem()
    sharpe = lambda w: (p.er @ w - RF) / np.sqrt(w @ p.cov @ w)
    best = sharpe(optimize.max_sharpe(p))
    for w in (optimize.min_variance(p), optimize.risk_parity(p)):
        assert best >= sharpe(w) - 1e-6


def test_risk_parity_equalises_contributions_when_no_cap_binds():
    p = problem(max_weight=1.0, max_sector=1.0)
    w = optimize.risk_parity(p)
    rc = optimize.risk_contributions(w, p.cov)
    assert rc.sum() == pytest.approx(1.0)
    assert rc.max() - rc.min() < 1e-4


def test_frontier_is_monotone():
    pts = optimize.frontier(problem(), points=10)
    assert len(pts) >= 8
    ers = [x["er"] for x in pts]
    vols = [x["vol"] for x in pts]
    assert ers == sorted(ers) and all(b >= a - 1e-9 for a, b in zip(vols, vols[1:]))


def test_infeasible_max_weight_is_named():
    p = problem(symbols=list(R.columns)[:8])       # 8 × 10% = 80% < 100%
    with pytest.raises(optimize.Infeasible, match="max_weight"):
        optimize.check_feasible(p)


def test_infeasible_sector_cap_is_named():
    banks = META.index[META["sector"] == "Banks"].tolist()
    p = problem(symbols=banks, max_weight=0.5)       # every stock is a bank; 30% sector cap
    with pytest.raises(optimize.Infeasible, match="sector caps bind"):
        optimize.check_feasible(p)


def test_infeasible_liquidity_is_named():
    p = problem(size=1e12)                            # absurd size: liquidity caps tiny
    with pytest.raises(optimize.Infeasible, match="liquidity caps"):
        optimize.check_feasible(p)


def test_dimson_beta_sums_current_and_lagged_slopes():
    m = R.mean(axis=1)                                 # an equal-weight market built from the same data
    y = 0.7 * m + 0.3 * m.shift(1).fillna(0)
    b, b0, b1 = dimson_beta(y, m)
    assert b0 == pytest.approx(0.7, abs=1e-6) and b1 == pytest.approx(0.3, abs=0.05)
    assert b == pytest.approx(b0 + b1)


# --- ownership group cap (groups are the real ones derived from data/ownership on 2026-10-07) -------

GROUPS = pd.read_csv(FIX / "weekly_returns_30_groups.csv").set_index("symbol").loc[R.columns, "group"]


def grouped(cap, **kw):
    p = problem(**kw)
    p.groups, p.max_group_weight = GROUPS.loc[p.symbols].tolist(), cap
    return p


@pytest.mark.parametrize("solver", [optimize.min_variance, optimize.risk_parity, optimize.max_sharpe])
def test_group_cap_holds(solver):
    p = grouped(0.12, max_weight=0.25, max_sector=0.6)
    w = solver(p)
    rep = optimize.constraint_report(w, p)
    assert rep["ok"] and rep["max_group_over"] <= 1e-6, rep
    by_group = pd.Series(w, index=p.groups).groupby(level=0).sum()
    assert by_group.max() <= 0.12 + 1e-6
    assert "KDDPERERA" in p.group_names                 # six of the 30 stocks trace to one holder


def test_group_cap_binds_where_it_should():
    free = optimize.max_sharpe(problem(max_weight=0.25, max_sector=0.6))
    by_group = pd.Series(free, index=GROUPS.tolist()).groupby(level=0).sum()
    top = by_group.idxmax()
    capped_at = float(by_group.max()) / 2
    p = grouped(capped_at, max_weight=0.25, max_sector=0.6)
    w = optimize.max_sharpe(p)
    assert pd.Series(w, index=p.groups).groupby(level=0).sum()[top] <= capped_at + 1e-6


def test_infeasible_group_cap_is_named():
    syms = GROUPS[GROUPS == "KDDPERERA"].index.tolist()
    p = grouped(0.5, max_weight=1.0, max_sector=1.0, symbols=syms)   # every stock in one group
    with pytest.raises(optimize.Infeasible, match="group caps bind"):
        optimize.check_feasible(p)


def test_no_groups_means_no_group_constraint():
    p = problem()
    assert p.group_names == [] and optimize.constraint_report(optimize.min_variance(p), p)["max_group_over"] == -1.0
