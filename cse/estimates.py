"""Universe filter and risk/return estimates for the portfolio lab (docs/METHODS.md §8).

Inputs are repo files only: prices.csv, indices.csv, corporate_actions.csv (+ review file),
sector labels from cached companyProfile responses, and config/portfolio.yaml.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

from .config import EQUITY_SUFFIXES
from .returns import check_adjustment, total_returns, weekly_returns, weekly_sum, wednesdays

SUFFIX_KIND = {"R": "rights", "W": "warrant", "P": "preference share", "U": "fund units",
               "D": "no equity prices (debt issuer)"}
MARKET_INDEX = "ASI"   # ASPI (price index) until TRI history covers the window (decision D4)


@dataclass
class Universe:
    symbols: list[str]
    dropped: list[dict]                         # {"symbol", "name", "reason"}
    stats: pd.DataFrame                         # per-equity liquidity/history stats


@dataclass
class Estimates:
    session: str
    weeks: list[str]                            # Wednesday dates of the weekly returns
    symbols: list[str]
    sectors: dict[str, str]
    returns: pd.DataFrame                       # T × N weekly total returns
    market: pd.Series                           # weekly market (ASPI) returns
    cov: np.ndarray                             # annualised Ledoit-Wolf covariance
    shrinkage: float
    beta: pd.Series                             # Dimson beta
    beta_parts: pd.DataFrame                    # slope on current and lagged market
    er: pd.Series                               # CAPM expected return
    vol: pd.Series                              # sqrt(diag(cov))
    amihud: pd.Series
    median_turnover: pd.Series
    turnover_source: pd.Series                  # "real" / "estimated" / "mixed"
    tri: dict[str, pd.DataFrame] = field(default_factory=dict)
    rf: float = 0.0


def trading_sessions(indices: pd.DataFrame) -> list[str]:
    return sorted(indices.loc[indices["index"] == MARKET_INDEX, "date"].unique())


def daily_turnover(hist: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Real turnover where present, otherwise the close × volume estimate (decision D2)."""
    real = pd.to_numeric(hist["turnover"], errors="coerce")
    est = pd.to_numeric(hist["turnover_est"], errors="coerce")
    return real.fillna(est), real.notna()


def build_tris(prices: pd.DataFrame, actions: list[dict], symbols: list[str], withholding: float,
               history_adjusted: bool) -> dict[str, pd.DataFrame]:
    by_sym: dict[str, list[dict]] = {}
    for a in actions:
        by_sym.setdefault(a["symbol"], []).append(a)
    out = {}
    for sym, hist in prices[prices["symbol"].isin(symbols)].groupby("symbol"):
        closes = hist.set_index("date")["close"].astype(float).sort_index()
        acts = sorted(by_sym.get(sym, []), key=lambda a: a["ex_date"])
        check_adjustment(closes, acts, history_adjusted)
        out[sym] = total_returns(closes, acts, withholding, history_adjusted)
    return out


def select_universe(securities: list, prices: pd.DataFrame, sessions: list[str], tris: dict,
                    sectors: dict[str, str | None], excluded: set[str], cfg: dict, session: str,
                    weeks_all: list[str]) -> Universe:
    """Apply the brief's filters in order and record a reason for every dropped security."""
    window_start = (dt.date.fromisoformat(session) - dt.timedelta(weeks=52)).isoformat()
    win = [s for s in sessions if window_start < s <= session]
    dropped, keep, stats = [], [], []
    by_sym = {s: g.set_index("date").sort_index() for s, g in prices.groupby("symbol")}
    for sec in sorted(securities, key=lambda s: s.symbol):
        sym = sec.symbol
        if not sym.endswith(EQUITY_SUFFIXES):
            kind = SUFFIX_KIND.get(sym.split(".")[1][0] if "." in sym else "", "not an ordinary share")
            dropped.append({"symbol": sym, "name": sec.name, "reason": f"not an ordinary share ({kind})"})
            continue
        hist = by_sym.get(sym)
        tri = tris.get(sym)
        n_weeks = 0
        if tri is not None:
            n_weeks = int(weekly_returns(tri["tri"], weeks_all).notna().sum())
        traded = hist.index.isin(win).sum() / len(win) if hist is not None and win else 0.0
        if hist is not None:
            to, is_real = daily_turnover(hist)
            med = float(to.reindex(win).fillna(0).median()) if win else 0.0
            real_share = float(is_real.reindex(win).fillna(False).mean()) if win else 0.0
        else:
            med, real_share = 0.0, 0.0
        stats.append({"symbol": sym, "weeks": n_weeks, "traded_share": traded, "median_turnover": med,
                      "real_turnover_share": real_share, "sector": sectors.get(sym)})
        reason = None
        if sym in excluded:
            reason = "excluded (conflict of interest)"
        elif n_weeks < cfg["min_history_weeks"]:
            reason = f"history {n_weeks} weeks < {cfg['min_history_weeks']}"
        elif traded < cfg["min_traded_share"]:
            reason = f"traded on {traded:.0%} of sessions < {cfg['min_traded_share']:.0%}"
        elif med < cfg["min_median_turnover_lkr"]:
            reason = f"median daily turnover Rs {med / 1e6:.2f} mn < Rs {cfg['min_median_turnover_lkr'] / 1e6:.2f} mn"
        elif not sectors.get(sym):
            reason = "sector unclassified (add it to config/sector_overrides.yaml)"
        if reason:
            dropped.append({"symbol": sym, "name": sec.name, "reason": reason})
        else:
            keep.append(sym)
    return Universe(keep, dropped, pd.DataFrame(stats).set_index("symbol") if stats else pd.DataFrame())


def dimson_beta(y: pd.Series, m: pd.Series) -> tuple[float, float, float]:
    """OLS of excess stock return on current and one-week-lagged excess market return.
    Returns (beta = b0 + b1, b0, b1)."""
    df = pd.DataFrame({"y": y, "m0": m, "m1": m.shift(1)}).dropna()
    X = np.column_stack([np.ones(len(df)), df["m0"], df["m1"]])
    coef, *_ = np.linalg.lstsq(X, df["y"].to_numpy(), rcond=None)
    return float(coef[1] + coef[2]), float(coef[1]), float(coef[2])


def estimate(universe: Universe, tris: dict, prices: pd.DataFrame, indices: pd.DataFrame,
             sectors: dict[str, str], cfg: dict, session: str, weeks_all: list[str]) -> Estimates:
    T = cfg["min_history_weeks"]
    weeks = weeks_all[-(T + 1):]                 # T returns need T + 1 Wednesdays
    syms = universe.symbols
    R = pd.DataFrame({s: weekly_returns(tris[s]["tri"], weeks) for s in syms})
    if R.isna().any().any():
        bad = R.columns[R.isna().any()].tolist()
        raise ValueError(f"incomplete weekly returns for {bad}")
    aspi = indices[indices["index"] == MARKET_INDEX].set_index("date")["value"].astype(float).sort_index()
    mkt = weekly_returns(aspi, weeks)
    rf_w = (1 + cfg["risk_free_annual"]) ** (1 / 52) - 1

    lw = LedoitWolf().fit(R.to_numpy())
    cov = lw.covariance_ * 52

    betas, parts = {}, []
    for s in syms:
        b, b0, b1 = dimson_beta(R[s] - rf_w, mkt - rf_w)
        betas[s] = b
        parts.append({"symbol": s, "b0": b0, "b1": b1})
    beta = pd.Series(betas)
    er = cfg["risk_free_annual"] + beta * cfg["equity_risk_premium"]

    amihud, med, src = {}, {}, {}
    for s in syms:
        hist = prices[prices["symbol"] == s].set_index("date").sort_index()
        to, is_real = daily_turnover(hist)
        wto = weekly_sum(to, weeks) / 1e6
        ratio = (R[s].abs() / wto).replace([np.inf], np.nan)
        amihud[s] = float(ratio[wto > 0].mean())
        med[s] = float(universe.stats.loc[s, "median_turnover"])
        rs = universe.stats.loc[s, "real_turnover_share"]
        src[s] = "real" if rs >= 0.999 else ("estimated" if rs == 0 else "mixed")
    return Estimates(
        session=session, weeks=weeks[1:], symbols=syms, sectors={s: sectors[s] for s in syms},
        returns=R, market=mkt, cov=cov, shrinkage=float(lw.shrinkage_), beta=beta,
        beta_parts=pd.DataFrame(parts).set_index("symbol"), er=er,
        vol=pd.Series(np.sqrt(np.diag(cov)), index=syms), amihud=pd.Series(amihud),
        median_turnover=pd.Series(med), turnover_source=pd.Series(src),
        tri={s: tris[s] for s in syms}, rf=cfg["risk_free_annual"],
    )


def all_wednesdays(sessions: list[str], session: str) -> list[str]:
    return wednesdays(sessions[0], session) if sessions else []
