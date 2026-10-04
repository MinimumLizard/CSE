"""Total returns from unadjusted closes and corporate actions (docs/METHODS.md §8).

For consecutive traded closes P_{t0}, P_{t1}, with every corporate action whose ex-date falls
in (t0, t1]:

    F = product of share-count factors (sub-division / scrip: (a + b) / a)
    D = sum of cash dividends per share
    base = TERP = (n × P_{t0} + m × S) / (n + m)   if a rights issue (m new per n at S) goes ex
         = P_{t0}                                  otherwise
    R = (P_{t1} × F + D × (1 − withholding)) / base − 1

With no actions this is plain P_{t1} / P_{t0} − 1. Applying actions to the first traded close
on or after the ex-date (not the calendar ex-date) handles illiquid names that skip the ex-date.

`history_adjusted=True` means the price source already reflects splits/scrip/rights, so F and
TERP are NOT applied again (dividends still are). `check_adjustment` enforces that the setting
matches the data, so double adjustment (or none at all) can't pass silently.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


class AdjustmentMismatch(RuntimeError):
    """The price series contradicts the `history_adjusted` setting at a share-count event."""


def _events(actions: list[dict], t0: str, t1: str) -> list[dict]:
    return [a for a in actions if t0 < a["ex_date"] <= t1]


def check_adjustment(closes: pd.Series, actions: list[dict], history_adjusted: bool) -> None:
    """At every confirmed share-count event with factor ≥ 1.5, the raw close must drop by roughly
    the factor if prices are unadjusted, and must NOT drop if they are adjusted."""
    dates = list(closes.index)
    for a in actions:
        f = a.get("factor")
        if a["type"] not in ("subdivision", "scrip") or not f or f < 1.5:
            continue
        before = [d for d in dates if d < a["ex_date"]]
        after = [d for d in dates if d >= a["ex_date"]]
        if not before or not after:
            continue
        raw = float(closes[after[0]]) / float(closes[before[-1]])
        # Log-distance to "dropped by f" vs "didn't drop": whichever is nearer wins.
        dropped = abs(math.log(raw) + math.log(f)) < abs(math.log(raw))
        if dropped and history_adjusted:
            raise AdjustmentMismatch(
                f"{a['symbol']} {a['type']} ×{f:g} on {a['ex_date']}: raw close moved {raw:.3f}× "
                "(an unadjusted drop) but history_adjusted is true")
        if not dropped and not history_adjusted:
            raise AdjustmentMismatch(
                f"{a['symbol']} {a['type']} ×{f:g} on {a['ex_date']}: raw close moved {raw:.3f}×, "
                "so prices already look adjusted; refusing to adjust them a second time")


def total_returns(closes: pd.Series, actions: list[dict], withholding: float = 0.0,
                  history_adjusted: bool = False) -> pd.DataFrame:
    """Per-traded-day total returns and a total-return index (TRI, first close = 1).

    `closes`: unadjusted closes indexed by ISO date (traded days only), sorted.
    `actions`: effective corporate actions for this symbol (cse.corpactions.effective).
    """
    closes = closes.astype(float).sort_index()
    dates = list(closes.index)
    rets, notes = [np.nan], [""]
    for t0, t1 in zip(dates, dates[1:]):
        p0, p1 = float(closes[t0]), float(closes[t1])
        f, d, rights, applied = 1.0, 0.0, None, []
        for a in _events(actions, t0, t1):
            if a["type"] == "cash_dividend" and a.get("amount_per_share"):
                d += a["amount_per_share"]
                applied.append(f"div {a['amount_per_share']:g}")
            elif a["type"] in ("subdivision", "scrip") and a.get("factor") and not history_adjusted:
                f *= a["factor"]
                applied.append(f"{a['type']} ×{a['factor']:.6g}")
            elif a["type"] == "rights" and not history_adjusted:
                rights = a
                applied.append(f"rights {a['ratio_new']:g}:{a['ratio_held']:g} @ {a['subscription_price']:g}")
        base = p0
        if rights is not None:
            n, m, s = rights["ratio_held"], rights["ratio_new"], rights["subscription_price"]
            base = (n * p0 + m * s) / (n + m)
        rets.append((p1 * f + d * (1 - withholding)) / base - 1)
        notes.append("; ".join(applied))
    out = pd.DataFrame({"close": closes.values, "ret": rets, "events": notes}, index=dates)
    out["tri"] = (1 + out["ret"].fillna(0)).cumprod()
    return out


def wednesdays(start: str, end: str) -> list[str]:
    return [d.date().isoformat() for d in pd.date_range(start, end, freq="W-WED")]


def sample_on_or_before(level: pd.Series, dates: list[str]) -> pd.Series:
    """Last value on or before each date (NaN before the first observation)."""
    level = level.sort_index()
    idx = np.searchsorted(np.array(level.index, dtype=object), np.array(dates, dtype=object), side="right") - 1
    vals = [float(level.iloc[i]) if i >= 0 else np.nan for i in idx]
    return pd.Series(vals, index=dates)


def weekly_returns(level: pd.Series, weds: list[str]) -> pd.Series:
    """Wednesday-to-Wednesday returns of a level series (TRI or index), using the last value on
    or before each Wednesday. The first Wednesday has no return; weeks before the first
    observation are NaN."""
    s = sample_on_or_before(level, weds)
    return (s / s.shift(1) - 1).iloc[1:]


def weekly_sum(daily: pd.Series, weds: list[str]) -> pd.Series:
    """Sum of a daily series over (previous Wednesday, Wednesday]."""
    daily = daily.sort_index()
    out = {}
    for w0, w1 in zip(weds, weds[1:]):
        out[w1] = float(daily[(daily.index > w0) & (daily.index <= w1)].sum())
    return pd.Series(out)
