"""One-off history backfill: `python -m cse.backfill`.

Loads the maximum history the public API offers (1 year; docs/API_NOTES.md Q1) for every
ordinary equity (.N0000 / .X0000) and for ASPI, S&P SL20 and the 20 sector indices.

- Rows are marked source=backfill. Prices are UNADJUSTED (as served); adjustment for
  corporate actions happens downstream, never in this file.
- Turnover is not served by the history endpoint, so `turnover` is left empty and
  `turnover_est` = close x volume is stored separately (decision D2).
- `previous_close` is the previous bar's close in the same series (the stock's previous
  traded session), matching how the CSE defines previous close.
- A (date, symbol) already present from any source is never written again, so running this
  before or after `cse.fetch`, or twice, creates no duplicates.
- Resumable: validated raw responses are saved under data/raw/<session>/ as they arrive and
  reused on a re-run instead of being fetched again.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import storage
from .config import EQUITY_SUFFIXES, ROOT
from .fetch import Paths, log_run
from .http import ApiError, CseClient
from .models import (DailyMarketSummary, IndexChart, MarketStatus, SectorList, SecurityList, StockChart,
                     ms_to_date, validate)


def _cached(client: CseClient, path: Path, model, endpoint: str, **params):
    if path.exists():
        return validate(model, json.loads(path.read_bytes()), f"{path}")
    r = client.call(endpoint, **params)
    obj = validate(model, r.data, f"{endpoint} {params}")
    storage.atomic_write_bytes(path, r.raw)
    return obj


def price_rows(symbol: str, chart: StockChart) -> list[dict]:
    rows, prev = [], None
    for bar in sorted(chart.chartData, key=lambda b: b.t):
        rows.append({
            "date": ms_to_date(bar.t).isoformat(), "symbol": symbol, "close": bar.p,
            "previous_close": prev, "high": bar.h, "low": bar.l, "volume": bar.q,
            "turnover": None, "turnover_est": bar.p * bar.q, "source": "backfill",
        })
        prev = bar.p
    return rows


def index_rows(name: str, chart: IndexChart) -> list[dict]:
    rows, prev = [], None
    for pt in sorted(chart.root, key=lambda p: p.d):
        rows.append({"date": ms_to_date(pt.d).isoformat(), "index": name, "value": pt.v,
                     "change": (pt.v - prev) if prev is not None else None, "source": "backfill"})
        prev = pt.v
    return rows


def run(client: CseClient | None = None, root: Path = ROOT) -> str:
    paths = Paths(root)
    client = client or CseClient()
    try:
        status = validate(MarketStatus, client.call("marketStatus").data, "marketStatus")
        if "OPEN" in status.status.upper() and "CLOSE" not in status.status.upper():
            raise ApiError(f"market status is {status.status!r}; run the backfill after the close")
        session = validate(DailyMarketSummary, client.call("dailyMarketSummery").data,
                           "dailyMarketSummery").latest.session.isoformat()
        raw = paths.raw(session)
        securities = validate(SecurityList, client.call("allSecurityCode").data, "allSecurityCode").root
        sectors = validate(SectorList, client.call("allSectors").data, "allSectors").root
        equities = [s for s in securities if s.symbol.endswith(EQUITY_SUFFIXES)]

        prices: list[dict] = []
        for i, sec in enumerate(equities, 1):
            chart = _cached(client, raw / "companyChartDataByStock" / f"{sec.symbol}.json", StockChart,
                            "companyChartDataByStock", stockId=sec.id, period=5)
            prices += price_rows(sec.symbol, chart)
            if i % 50 == 0:
                print(f"  prices {i}/{len(equities)}", file=sys.stderr)
        indices: list[dict] = []
        for s in sectors:
            chart = _cached(client, raw / "chartData" / f"{s.sectorId}_p5.json", IndexChart,
                            "chartData", chartId=s.sectorId, period=5)
            indices += index_rows(s.symbol, chart)

        n_p = storage.append(paths.prices, storage.PRICE_COLS, prices, key=("date", "symbol"))
        n_i = storage.append(paths.indices, storage.INDEX_COLS, indices, key=("date", "index"))
        msg = (f"backfill to {session}: prices +{n_p} rows ({len(equities)} equities), "
               f"indices +{n_i} rows ({len(sectors)} indices), {client.calls} API calls")
        log_run(paths, "backfill", "ok", session, msg)
        return msg
    except BaseException as exc:
        log_run(paths, "backfill", "failed", None, f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    print(run())
