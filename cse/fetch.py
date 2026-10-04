"""Daily data job: `python -m cse.fetch`.

1. Pull every endpoint the site needs, sequentially and politely (cse.http).
2. Validate every response (cse.models). Any failure aborts the run before anything is written.
3. Take the session date from the API (dailyMarketSummery.tradeDate), not the clock. If it
   equals the last stored daily session, append nothing and report "no new session".
4. Write untouched responses to data/raw/<session>/ and append to data/history/*.csv.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from . import storage
from .config import ROOT, load_universe, resolve
from .http import ApiError, CseClient
from .models import (AnnouncementDetail, ApprovedAnnouncements, CompanyInfo, DailyMarketSummary,
                     FinancialAnnouncements, IndexTick, MarketStatus, MarketSummery, SectorList,
                     SecurityList, TradeSummary, ms_to_date, validate)

CDN = "https://cdn.cse.lk/"
TRI_INDICES = {"triasi": "ASTRI", "spt": "S&P SL20 TRI"}


@dataclass
class Paths:
    root: Path

    @property
    def history(self) -> Path:
        return self.root / "data" / "history"

    @property
    def prices(self) -> Path:
        return self.history / "prices.csv"

    @property
    def indices(self) -> Path:
        return self.history / "indices.csv"

    @property
    def market(self) -> Path:
        return self.history / "market.csv"

    @property
    def announcements(self) -> Path:
        return self.history / "announcements.csv"

    @property
    def runs(self) -> Path:
        return self.root / "data" / "runs.csv"

    def raw(self, session: str) -> Path:
        return self.root / "data" / "raw" / session


@dataclass
class Snapshot:
    session: str
    raw: dict[str, bytes] = field(default_factory=dict)
    prices: list[dict] = field(default_factory=list)
    indices: list[dict] = field(default_factory=list)
    market: list[dict] = field(default_factory=list)
    announcements: list[dict] = field(default_factory=list)


def _norm_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def doc_url(detail: AnnouncementDetail | None) -> str:
    if not detail or not detail.reqAnnouncementDocs:
        return ""
    d = detail.reqAnnouncementDocs[0]
    base = d.baseUrl if (d.baseUrl or "").startswith("https://") else CDN  # never emit a non-https href
    return base.rstrip("/") + "/" + quote(d.fileUrl.lstrip("/"), safe="/")


def announcement_detail(client: CseClient, aid: int) -> tuple[AnnouncementDetail | None, str | None, bytes]:
    """Typed detail first; "dates" and general items return 204 there and are served by
    getGeneralAnnouncementById instead (docs/API_NOTES.md §4 Q2). Returns (detail, endpoint, raw)."""
    for endpoint in ("getAnnouncementById", "getGeneralAnnouncementById"):
        r = client.call(endpoint, announcementId=aid)
        if r.status == 200 and r.data:
            return validate(AnnouncementDetail, r.data, f"{endpoint} {aid}"), endpoint, r.raw
    return None, None, b""


def log_run(paths: Paths, command: str, status: str, session: str | None, message: str) -> None:
    row = {"run_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "command": command, "status": status, "session": session, "message": message[:300]}
    content, _ = storage.plan_append(paths.runs, storage.RUN_COLS, [row], key=storage.RUN_COLS)
    storage.atomic_write_bytes(paths.runs, content)


def collect(client: CseClient, paths: Paths, universe_path: Path | None = None) -> Snapshot | None:
    """Fetch and validate everything in memory. Returns None when there is no new session."""
    universe = load_universe(universe_path or paths.root / "config" / "universe.yaml")
    raw: dict[str, bytes] = {}

    def get(endpoint, model, name=None, **params):
        r = client.call(endpoint, **params)
        obj = validate(model, r.data, f"{endpoint} {params or ''}".strip())
        raw[name or f"{endpoint.replace('/', '_')}.json"] = r.raw
        return obj

    status = get("marketStatus", MarketStatus)
    securities = get("allSecurityCode", SecurityList).root
    resolve(universe, securities)  # exits naming any unknown symbol
    dms = get("dailyMarketSummery", DailyMarketSummary)
    session = dms.latest.session.isoformat()

    last = storage.last_daily_session(paths.prices)
    if last == session:
        return None
    if last and session < last:
        raise ApiError(f"API session {session} is older than the last stored session {last}")
    if "OPEN" in status.status.upper() and "CLOSE" not in status.status.upper():
        raise ApiError(f"market status is {status.status!r}; refusing to snapshot an open session")

    trade = get("tradeSummary", TradeSummary)
    newest_trade = max(ms_to_date(r.lastTradedTime) for r in trade.reqTradeSummery).isoformat()
    if newest_trade != session:
        raise ApiError(f"tradeSummary is for {newest_trade} but dailyMarketSummery says {session}")
    sectors = get("allSectors", SectorList)
    get("aspiData", IndexTick)
    get("snpData", IndexTick)
    get("marketSummery", MarketSummery)

    for sym in universe.all_symbols:
        info = get("companyInfoSummery", CompanyInfo, f"companyInfoSummery/{sym}.json", symbol=sym)
        if info.reqSymbolInfo.symbol != sym:
            raise ApiError(f"companyInfoSummery for {sym} returned {info.reqSymbolInfo.symbol}")

    snap = Snapshot(session=session, raw=raw)

    for r in trade.reqTradeSummery:
        snap.prices.append({
            "date": session, "symbol": r.symbol, "close": r.closingPrice,
            "previous_close": r.previousClose or None, "high": r.high, "low": r.low,
            "volume": r.sharevolume, "turnover": r.turnover, "turnover_est": None, "source": "daily",
        })

    for s in sectors.root:
        snap.indices.append({"date": session, "index": s.symbol, "value": s.indexValue,
                             "change": s.change, "source": "daily"})
    rows = dms.rows
    for i, row in enumerate(rows):
        prev = rows[i + 1] if i + 1 < len(rows) else None
        for attr, name in TRI_INDICES.items():
            v = getattr(row, attr)
            if v is None:
                continue
            pv = getattr(prev, attr) if prev else None
            snap.indices.append({"date": row.session.isoformat(), "index": name, "value": v,
                                 "change": (v - pv) if pv is not None else None, "source": "daily"})
        snap.market.append({
            "date": row.session.isoformat(), "turnover": row.marketTurnover,
            "volume": row.volumeOfTurnOverNumber, "trades": row.tradesNo,
            "foreign_buy": row.equityForeignPurchase, "foreign_sell": row.equityForeignSales,
            "listed": row.listedCompanyNumber, "traded": row.tradeCompanyNumber,
            "market_cap": row.marketCap, "aspi": row.asi, "spsl20": row.spp, "astri": row.triasi,
            "spsl20_tri": row.spt, "per": row.per, "pbv": row.pbv, "dy": row.dy, "source": "daily",
        })

    # Announcements: the list has no symbol or PDF, so fetch detail once per new announcementId.
    by_name: dict[str, str] = {}
    for s in sorted(securities, key=lambda s: (not s.symbol.endswith(".N0000"), s.symbol)):
        by_name.setdefault(_norm_name(s.name), s.symbol.split(".")[0])
    known = {r["id"] for r in storage.read_rows(paths.announcements)}
    approved = get("approvedAnnouncement", ApprovedAnnouncements)
    for a in approved.approvedAnnouncements:
        aid = str(a.announcementId)
        if aid in known:
            continue
        detail, endpoint, body = announcement_detail(client, a.announcementId)
        if detail:
            raw[f"{endpoint}/{aid}.json"] = body
        symbol = (detail.reqBaseAnnouncement.symbol if detail else None) or by_name.get(_norm_name(a.company), "")
        snap.announcements.append({
            "id": aid, "date": a.date.isoformat(), "symbol": symbol, "company": a.company.strip(),
            "title": (a.remarks or "").strip() or a.announcementCategory.strip(),
            "category": a.announcementCategory.strip(), "url": doc_url(detail),
        })
    fin = get("getFinancialAnnouncement", FinancialAnnouncements)
    for f in fin.reqFinancialAnnouncemnets:
        snap.announcements.append({
            "id": f"F{f.id}", "date": f.date.isoformat(), "symbol": f.symbol or by_name.get(_norm_name(f.name), ""),
            "company": f.name.strip(), "title": f.fileText.strip(), "category": "FINANCIAL REPORT",
            "url": CDN + quote(f.path.lstrip("/"), safe="/"),
        })
    return snap


def commit(snap: Snapshot, paths: Paths) -> dict[str, int]:
    """Write a fully validated snapshot. Plans every CSV first so header errors abort early."""
    plans = {
        "prices": (paths.prices, *storage.plan_append(paths.prices, storage.PRICE_COLS, snap.prices,
                                                       key=("date", "symbol", "source"))),
        "indices": (paths.indices, *storage.plan_append(paths.indices, storage.INDEX_COLS, snap.indices,
                                                         key=("date", "index", "source"))),
        "market": (paths.market, *storage.plan_append(paths.market, storage.MARKET_COLS, snap.market,
                                                       key=("date", "source"))),
        "announcements": (paths.announcements, *storage.plan_append(
            paths.announcements, storage.ANNOUNCEMENT_COLS, snap.announcements, key=("id",))),
    }
    raw_dir = paths.raw(snap.session)
    for rel, body in snap.raw.items():
        storage.atomic_write_bytes(raw_dir / rel, body)
    added = {}
    for name, (path, content, n) in plans.items():
        if n:
            storage.atomic_write_bytes(path, content)
        added[name] = n
    return added


def run(client: CseClient | None = None, root: Path = ROOT, universe_path: Path | None = None) -> str:
    paths = Paths(root)
    client = client or CseClient()
    try:
        snap = collect(client, paths, universe_path)
        if snap is None:
            msg = f"no new session (latest stored daily session {storage.last_daily_session(paths.prices)})"
            log_run(paths, "fetch", "no_new_session", storage.last_daily_session(paths.prices), msg)
            return msg
        added = commit(snap, paths)
        msg = f"session {snap.session}: " + ", ".join(f"{k} +{v}" for k, v in added.items()) + f" ({client.calls} API calls)"
        log_run(paths, "fetch", "ok", snap.session, msg)
        return msg
    except BaseException as exc:  # includes SymbolResolutionError (SystemExit)
        log_run(paths, "fetch", "failed", None, f"{type(exc).__name__}: {exc}")
        raise


def main() -> None:
    try:
        print(run())
    except SystemExit:
        raise
    except Exception as exc:
        traceback.print_exc()
        sys.exit(f"fetch failed: {exc}")


if __name__ == "__main__":
    main()
