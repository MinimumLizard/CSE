"""Computations behind Panels A and B. Pure functions over the history CSVs and the latest
raw session, so every number on the site can be reproduced from files in the repo.

Definitions are documented in docs/METHODS.md (§1 Terminal).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .models import CompanyInfo, SectorList, TradeSummary, validate

ASPI = "ASI"
SL20 = "S&P SL20"
WINDOWS = {"1w": 5, "1m": 21, "3m": 63}   # in market sessions (ASPI calendar)
AVG_SESSIONS = 20
JUMP = 0.40                               # one-bar move treated as a likely unadjusted corporate action


def _prefer_daily(df: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """Same key from both sources: keep the daily row (it carries real turnover)."""
    if df.empty:
        return df
    df = df.assign(_rank=(df["source"] != "daily").astype(int)).sort_values(key + ["_rank"])
    return df.drop_duplicates(key, keep="first").drop(columns="_rank").reset_index(drop=True)


def load_prices(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "symbol", "close", "volume", "turnover", "turnover_est", "source"])
    df = pd.read_csv(path, dtype={"date": str, "symbol": str, "source": str})
    return _prefer_daily(df, ["symbol", "date"])


def load_indices(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "index", "value", "change", "source"])
    df = pd.read_csv(path, dtype={"date": str, "index": str, "source": str})
    return _prefer_daily(df, ["index", "date"])


def load_market(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, dtype={"date": str, "source": str})
    return _prefer_daily(df, ["date"]).sort_values("date").reset_index(drop=True)


@dataclass
class Session:
    date: str
    trade: TradeSummary
    sectors: SectorList
    infos: dict[str, CompanyInfo]


def load_session(raw_dir: Path) -> Session:
    def j(name):
        return json.loads((raw_dir / name).read_bytes())
    infos = {}
    info_dir = raw_dir / "companyInfoSummery"
    if info_dir.exists():
        for p in sorted(info_dir.glob("*.json")):
            infos[p.stem] = validate(CompanyInfo, json.loads(p.read_bytes()), str(p))
    return Session(
        date=raw_dir.name,
        trade=validate(TradeSummary, j("tradeSummary.json"), "tradeSummary"),
        sectors=validate(SectorList, j("allSectors.json"), "allSectors"),
        infos=infos,
    )


def session_calendar(indices: pd.DataFrame) -> list[str]:
    """Market sessions = dates on which ASPI has a value."""
    return sorted(indices.loc[indices["index"] == ASPI, "date"].unique())


def last_on_or_before(series: pd.Series, date: str) -> float | None:
    """`series` indexed by ISO date string, sorted. Last value on or before `date`."""
    s = series.loc[:date]
    return float(s.iloc[-1]) if len(s) else None


def has_jump(closes: pd.Series, since: str) -> bool:
    s = closes.loc[since:]
    if len(s) < 2:
        return False
    return bool((s.pct_change().abs() > JUMP).any())


def relative_returns(closes: pd.Series, aspi: pd.Series, calendar: list[str]) -> dict[str, dict]:
    """Price return of the stock minus ASPI return over 5 / 21 / 63 sessions.

    Uses the last traded price on or before each end of the window. Returns, per window,
    {"value": float | None, "needed": int | None, "flag": str | None}.
    """
    out = {}
    end = calendar[-1] if calendar else None
    for label, k in WINDOWS.items():
        if len(calendar) < k + 1:
            out[label] = {"value": None, "needed": k + 1 - len(calendar), "flag": None}
            continue
        start = calendar[-1 - k]
        p0, p1 = last_on_or_before(closes, start), last_on_or_before(closes, end)
        a0, a1 = last_on_or_before(aspi, start), last_on_or_before(aspi, end)
        if p0 is None or p1 is None or a0 is None or a1 is None:
            out[label] = {"value": None, "needed": None, "flag": "no trade before window"}
            continue
        flag = "unadjusted corporate action in window" if has_jump(closes, start) else None
        out[label] = {"value": (p1 / p0 - 1) - (a1 / a0 - 1), "needed": None, "flag": flag,
                      "stock": p1 / p0 - 1, "aspi": a1 / a0 - 1}
    return out


def volume_vs_average(volumes: pd.Series, calendar: list[str], today_volume: float | None) -> dict:
    """Today's volume / mean volume over the previous 20 sessions (0 on no-trade days)."""
    if len(calendar) < AVG_SESSIONS + 1:
        return {"value": None, "needed": AVG_SESSIONS + 1 - len(calendar)}
    prior = calendar[-1 - AVG_SESSIONS:-1]
    avg = volumes.reindex(prior).fillna(0).mean()
    if not avg:
        return {"value": None, "needed": None, "avg": 0.0}
    return {"value": (today_volume or 0) / avg, "needed": None, "avg": float(avg)}


def range_position(price: float | None, lo: float | None, hi: float | None) -> float | None:
    if price is None or lo is None or hi is None or hi <= lo:
        return None
    return (price - lo) / (hi - lo)


def market_vs_average(market: pd.DataFrame, column: str) -> dict:
    """Latest session's market total vs the mean of the previous 20 sessions (daily data only)."""
    daily = market[market["source"] == "daily"] if not market.empty else market
    if daily.empty:
        return {"value": None, "avg": None, "ratio": None, "needed": AVG_SESSIONS + 1}
    today = float(daily[column].iloc[-1])
    if len(daily) < AVG_SESSIONS + 1:
        return {"value": today, "avg": None, "ratio": None, "needed": AVG_SESSIONS + 1 - len(daily)}
    avg = float(daily[column].iloc[-1 - AVG_SESSIONS:-1].mean())
    return {"value": today, "avg": avg, "ratio": today / avg if avg else None, "needed": None}
