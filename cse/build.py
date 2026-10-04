"""Render the static site: `python -m cse.build` -> site/index.html.

Everything is computed here from data/history/*.csv and the latest data/raw/<session>/
directory and embedded in the page at build time. The browser makes no API calls.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import metrics, storage
from .config import ROOT, load_universe
from .fetch import Paths
from .models import SLT
from .tags import tag

ANNOUNCEMENT_DAYS = 90


# --- formatting (Jinja filters) ---------------------------------------------------------------

MINUS = "−"


def _bad(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def f_rs(v) -> str:
    """LKR as Rs mn / Rs bn."""
    if _bad(v):
        return "—"
    v = float(v)
    sign = MINUS if v < 0 else ""
    a = abs(v)
    if a >= 1e9:
        return f"{sign}Rs {a / 1e9:,.2f} bn"
    return f"{sign}Rs {a / 1e6:,.1f} mn" if a >= 1e7 else f"{sign}Rs {a / 1e6:,.2f} mn"


def f_num(v, dp: int = 2) -> str:
    if _bad(v):
        return "—"
    s = f"{abs(float(v)):,.{dp}f}"
    return (MINUS + s) if float(v) < 0 else s


def f_signed(v, dp: int = 2) -> str:
    if _bad(v):
        return "—"
    v = float(v)
    s = f"{abs(v):,.{dp}f}"
    return ("+" if v > 0 else MINUS if v < 0 else "±") + s


def f_spct(v, dp: int = 2) -> str:
    """Signed percentage from a fraction (0.0123 -> +1.23%)."""
    return "—" if _bad(v) else f_signed(float(v) * 100, dp) + "%"


def f_cls(v) -> str:
    if _bad(v) or float(v) == 0:
        return "flat"
    return "up" if float(v) > 0 else "dn"


def f_vol(v) -> str:
    if _bad(v):
        return "—"
    v = float(v)
    if v >= 1e6:
        return f"{v / 1e6:,.2f} mn"
    if v >= 1e3:
        return f"{v / 1e3:,.1f} k"
    return f"{v:,.0f}"


# --- context ---------------------------------------------------------------------------------

def _series(indices: pd.DataFrame, name: str) -> dict:
    s = indices[indices["index"] == name].sort_values("date")
    return {"dates": s["date"].tolist(), "values": [round(float(v), 2) for v in s["value"]]}


def _run_status(paths: Paths) -> dict:
    runs = [r for r in storage.read_rows(paths.runs) if r["command"] == "fetch"]
    last = runs[-1] if runs else None
    last_ok = next((r for r in reversed(runs) if r["status"] in ("ok", "no_new_session")), None)
    failed = bool(last and last["status"] == "failed")
    return {"last": last, "last_ok": last_ok, "failed": failed}


def build_context(root: Path = ROOT) -> dict:
    paths = Paths(root)
    universe = load_universe(root / "config" / "universe.yaml")
    sessions = sorted(p.name for p in (root / "data" / "raw").iterdir()
                      if p.is_dir() and (p / "tradeSummary.json").exists() and (p / "allSectors.json").exists())
    if not sessions:
        raise SystemExit("no data/raw/<session>/ with tradeSummary.json; run `python -m cse.fetch` first")
    stored = storage.last_daily_session(paths.prices)
    session_name = stored if stored in sessions else sessions[-1]
    sess = metrics.load_session(paths.raw(session_name))

    prices = metrics.load_prices(paths.prices)
    indices = metrics.load_indices(paths.indices)
    market = metrics.load_market(paths.market)
    calendar = metrics.session_calendar(indices)
    aspi_series = indices[indices["index"] == metrics.ASPI].set_index("date")["value"].sort_index()

    excluded = set(universe.excluded)
    excluded_codes = {s.split(".")[0] for s in excluded}
    watch_codes = {s.split(".")[0] for s in universe.watchlist}
    trade_rows = sess.trade.reqTradeSummery
    by_sym = {r.symbol: r for r in trade_rows}

    # Panel A ---------------------------------------------------------------------------------
    sec_by_id = {s.sectorId: s for s in sess.sectors.root}
    tri = {name: indices[(indices["index"] == name) & (indices["date"] == sess.date)]
           for name in ("ASTRI", "S&P SL20 TRI")}
    headline = []
    for sid, label in ((1, "ASPI"), (40, "S&P SL20")):
        s = sec_by_id[sid]
        headline.append({"label": label, "value": s.indexValue, "change": s.change, "pct": s.percentage / 100,
                         "series": _series(indices, s.symbol)})
    for name, df in tri.items():
        if not df.empty:
            r = df.iloc[-1]
            ch = None if pd.isna(r["change"]) else float(r["change"])
            prev = float(r["value"]) - ch if ch is not None else None
            headline.append({"label": "ASPI TRI" if name == "ASTRI" else name, "value": float(r["value"]),
                             "change": ch, "pct": (ch / prev) if ch is not None and prev else None, "series": None})

    stats = {k: metrics.market_vs_average(market, k) for k in ("turnover", "volume", "trades")}
    mrow = market[market["date"] == sess.date].iloc[-1].to_dict() if not market.empty and (market["date"] == sess.date).any() else {}
    foreign = {"buy": mrow.get("foreign_buy"), "sell": mrow.get("foreign_sell"),
               "net": (mrow["foreign_buy"] - mrow["foreign_sell"]) if mrow else None}
    valuation = {k: mrow.get(k) for k in ("per", "pbv", "dy", "market_cap")}

    equities = [r for r in trade_rows if r.symbol.endswith((".N0000", ".X0000"))]
    breadth = {"up": sum(r.change > 0 for r in equities), "down": sum(r.change < 0 for r in equities),
               "flat": sum(r.change == 0 for r in equities), "traded": len(equities),
               "listed": mrow.get("listed"), "traded_all": mrow.get("traded")}

    sectors = [{"name": s.name, "symbol": s.symbol, "value": s.indexValue, "change": s.change,
                "pct": s.percentage / 100, "turnover": s.sectorTurnoverToday}
               for s in sess.sectors.root if s.sectorId not in (1, 40)]
    sectors.sort(key=lambda r: -(r["turnover"] or 0))

    def trow(r):
        return {"symbol": r.symbol, "name": r.name, "price": r.closingPrice, "change": r.change,
                "pct": r.percentageChange / 100, "turnover": r.turnover, "volume": r.sharevolume,
                "excluded": r.symbol in excluded, "mine": r.symbol in universe.watchlist}

    floor = universe.gainers_losers_min_turnover_lkr
    liquid = [r for r in trade_rows if r.turnover >= floor]
    top_turnover = [trow(r) for r in sorted(trade_rows, key=lambda r: -r.turnover)[:10]]
    gainers = [trow(r) for r in sorted((r for r in liquid if r.percentageChange > 0), key=lambda r: -r.percentageChange)[:10]]
    losers = [trow(r) for r in sorted((r for r in liquid if r.percentageChange < 0), key=lambda r: r.percentageChange)[:10]]

    # Panel B ---------------------------------------------------------------------------------
    book = []
    year_ago = calendar[-min(len(calendar), 240)] if calendar else sess.date
    for group, syms in universe.groups.items():
        rows = []
        for sym in syms:
            info = sess.infos.get(sym)
            si = info.reqSymbolInfo if info else None
            t = by_sym.get(sym)
            hist = prices[prices["symbol"] == sym].set_index("date").sort_index()
            closes = hist["close"].astype(float) if not hist.empty else pd.Series(dtype=float)
            volumes = hist["volume"].astype(float) if not hist.empty else pd.Series(dtype=float)
            price = t.closingPrice if t else (si.lastTradedPrice if si else None)
            lo, hi = (si.p12LowPrice, si.p12HiPrice) if si else (None, None)
            rows.append({
                "symbol": sym, "name": t.name if t else (si.name if si else sym), "excluded": sym in excluded,
                "traded": t is not None, "price": price,
                "change": t.change if t else None, "pct": (t.percentageChange / 100) if t else None,
                "lo": lo, "hi": hi, "pos": metrics.range_position(price, lo, hi),
                "range_flag": metrics.has_jump(closes, year_ago),
                "vol": metrics.volume_vs_average(volumes, calendar, t.sharevolume if t else 0),
                "rel": metrics.relative_returns(closes, aspi_series, calendar),
                "mcap": (t.marketCap if t and t.marketCap else (si.marketCap if si else None)),
                "beta": info.reqSymbolBetaInfo.triASIBetaValue if info and info.reqSymbolBetaInfo else None,
                "turnover": t.turnover if t else None,
            })
        book.append({"group": group, "rows": rows})

    # Panel C ---------------------------------------------------------------------------------
    cutoff = (dt.date.fromisoformat(sess.date) - dt.timedelta(days=ANNOUNCEMENT_DAYS)).isoformat()
    anns = [r for r in storage.read_rows(paths.announcements) if r["date"] >= cutoff]
    anns.sort(key=lambda r: (r["date"], r["id"].lstrip("F").zfill(12)), reverse=True)
    announcements = [{**r, "tag": tag(r["category"], r["title"]),
                      "mine": r["symbol"] in watch_codes, "excluded": r["symbol"] in excluded_codes}
                     for r in anns]

    now = dt.datetime.now(SLT)
    return {
        "session": sess.date,
        "built_slt": now.strftime("%Y-%m-%d %H:%M"),
        "built_iso": now.isoformat(timespec="seconds"),
        "run": _run_status(paths),
        "headline": headline, "stats": stats, "foreign": foreign, "valuation": valuation,
        "breadth": breadth, "sectors": sectors, "top_turnover": top_turnover,
        "gainers": gainers, "losers": losers, "floor": floor,
        "book": book, "windows": list(metrics.WINDOWS), "avg_sessions": metrics.AVG_SESSIONS,
        "announcements": announcements, "announcement_days": ANNOUNCEMENT_DAYS,
        "calendar_len": len(calendar), "calendar_start": calendar[0] if calendar else None,
        "tags": ["board", "dealings", "dividend", "capital", "results", "other"],
    }


def render(context: dict, out: Path) -> None:
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)
    env.filters.update(rs=f_rs, num=f_num, signed=f_signed, spct=f_spct, cls=f_cls, vol=f_vol)
    charts = {h["label"]: h["series"] for h in context["headline"] if h["series"]}
    html = env.get_template("index.html.j2").render(
        **context, charts_json=json.dumps(charts, separators=(",", ":")),
        meta_json=json.dumps({"session": context["session"], "built": context["built_iso"]}))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)


def main() -> None:
    out = ROOT / "site" / "index.html"
    render(build_context(ROOT), out)
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
