"""Long-only, fully invested model portfolios with cvxpy (docs/METHODS.md §8).

Constraints for minimum variance, risk parity and maximum Sharpe:
  0 ≤ w_i ≤ cap_i,  cap_i = min(max_weight, participation × median daily turnover_i × days_to_build
                                             / portfolio_size)
  Σ_{i ∈ sector} w_i ≤ max_sector_weight,   Σ w_i = 1.
If they can't all hold, `check_feasible` names the binding constraint and nothing is solved.
"""
from __future__ import annotations

from dataclasses import dataclass

import cvxpy as cp
import numpy as np
import pandas as pd

SOLVER = "CLARABEL"


class Infeasible(RuntimeError):
    """The constraints admit no fully invested portfolio. The message names what binds."""


@dataclass
class Problem:
    symbols: list[str]
    sectors: list[str]
    cov: np.ndarray
    er: np.ndarray
    rf: float
    caps: np.ndarray
    liq_caps: np.ndarray
    max_weight: float
    max_sector_weight: float

    @property
    def sector_names(self) -> list[str]:
        return sorted(set(self.sectors))

    def sector_matrix(self) -> np.ndarray:
        return np.array([[1.0 if s == name else 0.0 for s in self.sectors] for name in self.sector_names])


def make_problem(est, cfg: dict) -> Problem:
    med = est.median_turnover.reindex(est.symbols).to_numpy()
    liq = cfg["participation"] * med * cfg["days_to_build"] / cfg["portfolio_size_lkr"]
    caps = np.minimum(cfg["max_weight"], liq)
    return Problem(symbols=list(est.symbols), sectors=[est.sectors[s] for s in est.symbols],
                   cov=np.asarray(est.cov), er=est.er.reindex(est.symbols).to_numpy(), rf=est.rf,
                   caps=caps, liq_caps=liq, max_weight=cfg["max_weight"],
                   max_sector_weight=cfg["max_sector_weight"])


def check_feasible(p: Problem) -> None:
    """Σ caps ≥ 1 and Σ_sectors min(sector cap, Σ caps in sector) ≥ 1 are necessary and, for
    these box + sector constraints, sufficient."""
    n_liq = int((p.liq_caps < p.max_weight).sum())
    total = float(p.caps.sum())
    if total < 1 - 1e-9:
        raise Infeasible(
            f"per-stock caps sum to {total:.3f} < 1: max_weight {p.max_weight:.0%} across {len(p.symbols)} "
            f"stocks allows {min(1, p.max_weight * len(p.symbols)):.0%}, and the liquidity cap is tighter "
            f"than max_weight for {n_liq} of them. Binding: "
            + ("max_weight (too few stocks)" if p.max_weight * len(p.symbols) < 1 else "liquidity caps"))
    S = p.sector_matrix()
    reach = np.minimum(p.max_sector_weight, S @ p.caps)
    if reach.sum() < 1 - 1e-9:
        raise Infeasible(
            f"sector caps bind: with max_sector_weight {p.max_sector_weight:.0%} and the per-stock caps, "
            f"the {len(p.sector_names)} sectors can hold at most {reach.sum():.3f} in total")


def _constraints(p: Problem, w, scale=1.0):
    """Linear constraints on w; `scale` homogenises them (w = y / κ formulations)."""
    S = p.sector_matrix()
    return [w >= 0, w <= p.caps * scale, S @ w <= p.max_sector_weight * scale]


def _solve(prob: cp.Problem, what: str):
    prob.solve(solver=SOLVER)
    if prob.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
        raise Infeasible(f"{what}: solver status {prob.status}")


def _clean(w: np.ndarray) -> np.ndarray:
    w = np.where(w < 1e-7, 0.0, w)
    return w / w.sum()


def min_variance(p: Problem) -> np.ndarray:
    w = cp.Variable(len(p.symbols))
    _solve(cp.Problem(cp.Minimize(cp.quad_form(w, cp.psd_wrap(p.cov))),
                      _constraints(p, w) + [cp.sum(w) == 1]), "minimum variance")
    return _clean(w.value)


def max_sharpe(p: Problem) -> np.ndarray:
    """Homogenised form: min y'Σy s.t. (μ − rf)'y = 1, y = κ·w with the constraints scaled by κ."""
    excess = p.er - p.rf
    if (excess <= 0).all():
        raise Infeasible("maximum Sharpe: no stock has a positive expected excess return")
    y, k = cp.Variable(len(p.symbols)), cp.Variable(nonneg=True)
    _solve(cp.Problem(cp.Minimize(cp.quad_form(y, cp.psd_wrap(p.cov))),
                      _constraints(p, y, k) + [cp.sum(y) == k, excess @ y == 1]), "maximum Sharpe")
    return _clean(y.value / k.value)


def risk_parity(p: Problem) -> np.ndarray:
    """Equal risk contribution via the convex log-barrier formulation (Spinu 2013):
    min ½ y'Σy − (1/n) Σ log y_i, then w = y / Σy. The caps enter as homogeneous linear
    constraints on y (y_i ≤ cap_i Σy, sector Σ ≤ cap Σy). Where a cap binds, contributions
    can't all be equal; the actual contributions are reported."""
    n = len(p.symbols)
    y = cp.Variable(n)
    t = cp.sum(y)
    obj = 0.5 * cp.quad_form(y, cp.psd_wrap(p.cov)) - cp.sum(cp.log(y)) / n
    _solve(cp.Problem(cp.Minimize(obj), [y >= 1e-9, y <= p.caps * t,
                                         p.sector_matrix() @ y <= p.max_sector_weight * t]), "risk parity")
    return _clean(y.value / y.value.sum())


def max_return(p: Problem) -> float:
    w = cp.Variable(len(p.symbols))
    _solve(cp.Problem(cp.Maximize(p.er @ w), _constraints(p, w) + [cp.sum(w) == 1]), "maximum return")
    return float(p.er @ w.value)


def frontier(p: Problem, points: int = 25) -> list[dict]:
    """Constrained efficient frontier: minimum variance at target expected returns from the
    minimum-variance portfolio's return up to the highest feasible return."""
    w0 = min_variance(p)
    lo, hi = float(p.er @ w0), max_return(p)
    out = []
    for target in np.linspace(lo, hi, points):
        w = cp.Variable(len(p.symbols))
        prob = cp.Problem(cp.Minimize(cp.quad_form(w, cp.psd_wrap(p.cov))),
                          _constraints(p, w) + [cp.sum(w) == 1, p.er @ w >= target - 1e-10])
        prob.solve(solver=SOLVER)
        if prob.status in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
            wv = np.clip(w.value, 0, None)
            out.append({"er": float(p.er @ wv), "vol": float(np.sqrt(wv @ p.cov @ wv))})
    return out


def risk_contributions(w: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Share of portfolio variance from each holding: w_i (Σw)_i / w'Σw (sums to 1)."""
    var = float(w @ cov @ w)
    return w * (cov @ w) / var if var > 0 else np.zeros_like(w)


def summary(name: str, w: np.ndarray, p: Problem) -> dict:
    vol = float(np.sqrt(w @ p.cov @ w))
    er = float(p.er @ w)
    rc = risk_contributions(w, p.cov)
    sector = pd.Series(w, index=p.sectors).groupby(level=0).sum()
    holdings = [{"symbol": s, "weight": float(wi), "risk_contribution": float(ri), "cap": float(c),
                 "sector": sec, "er": float(e)}
                for s, wi, ri, c, sec, e in zip(p.symbols, w, rc, p.caps, p.sectors, p.er) if wi > 1e-6]
    holdings.sort(key=lambda h: -h["weight"])
    return {"name": name, "er": er, "vol": vol, "sharpe": (er - p.rf) / vol if vol else None,
            "holdings": holdings, "sectors": {k: float(v) for k, v in sector[sector > 1e-6].sort_values(ascending=False).items()},
            "n": len(holdings)}


def constraint_report(w: np.ndarray, p: Problem, tol: float = 1e-6) -> dict:
    """Used by tests and shown on the page: every constraint, checked numerically."""
    S = p.sector_matrix()
    return {"sum": float(w.sum()), "min": float(w.min()),
            "max_over_cap": float((w - p.caps).max()),
            "max_sector_over": float((S @ w - p.max_sector_weight).max()),
            "ok": bool(abs(w.sum() - 1) < tol and w.min() >= -tol and (w - p.caps).max() <= tol
                       and (S @ w - p.max_sector_weight).max() <= tol)}
