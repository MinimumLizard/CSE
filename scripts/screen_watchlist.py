"""Whole-market screen used to propose watchlist additions (Step 0 side task).

Two stages:
  python scripts/screen_watchlist.py fetch    # polite, resumable API pull -> data/raw/<session>/
  python scripts/screen_watchlist.py report   # pure computation from those files -> docs/WATCHLIST_SCREEN.md

Only endpoints verified in docs/API_NOTES.md are used. Every number in the report is
computed from the saved raw responses; nothing is typed in by hand.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cse.sectors import classify, load_overrides  # noqa: E402

BASE = "https://www.cse.lk/api/"
UA = "cse-terminal/0.1 (personal research dashboard; github.com/minimumlizard/cse)"
ROOT = Path(__file__).resolve().parents[1]
SLT = dt.timezone(dt.timedelta(hours=5, minutes=30))
EQUITY_SUFFIXES = (".N0000", ".X0000")

# Symbols from the user's watchlist screenshots and config/universe.yaml draft.
USER_LIST = {
    "BANKS": ["NTB.N0000", "DFCC.N0000", "COMB.N0000", "HNB.N0000", "NDB.N0000"],
    "PLAYS": ["RIL.N0000", "LOLC.N0000", "HAYC.N0000", "AGST.N0000", "LOFC.N0000"],
    "LOLC": ["BIL.N0000", "BRWN.N0000"],
    "FUNDAMENTALS": ["CARS.N0000", "HAYL.N0000", "AEL.N0000", "JKH.N0000", "CIC.N0000"],
    "RESEARCHING": ["WIND.N0000", "PKME.N0000", "LVEN.N0000", "CHOT.N0000", "FCT.N0000", "CALT.N0000", "CALH.N0000"],
    "PUNTS": ["BFL.N0000", "DOCK.N0000", "UDPL.N0000", "SPEN.N0000", "SCAP.N0000"],
    "CONFIG": ["ALLI.N0000", "LHCL.N0000"],
}
EXCLUDED = {"NTB.N0000", "LVEN.N0000"}

session = requests.Session()
session.headers["User-Agent"] = UA


def call(method: str, endpoint: str, data: dict | None = None) -> bytes:
    for attempt in range(3):
        try:
            if method == "GET":
                r = session.get(BASE + endpoint, timeout=30)
            else:
                r = session.post(BASE + endpoint, data=data or {}, timeout=30)
            time.sleep(1)
            r.raise_for_status()
            json.loads(r.content)  # must be JSON
            return r.content
        except (requests.RequestException, ValueError) as exc:
            wait = 2 ** (attempt + 1)
            print(f"  {endpoint} {data} failed ({exc}); retry in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"giving up on {endpoint} {data}")


def ms_to_date(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, SLT).strftime("%Y-%m-%d")


def fetch() -> Path:
    dms = json.loads(call("POST", "dailyMarketSummery"))
    session_date = ms_to_date(dms[0][0]["tradeDate"])
    out = ROOT / "data" / "raw" / session_date
    out.mkdir(parents=True, exist_ok=True)
    (out / "dailyMarketSummery.json").write_bytes(json.dumps(dms).encode())
    for method, ep in [("POST", "tradeSummary"), ("GET", "allSecurityCode"), ("POST", "allSectors"),
                       ("POST", "marketSummery"), ("POST", "aspiData"), ("POST", "snpData"), ("POST", "aspi/year")]:
        (out / f"{ep.replace('/', '_')}.json").write_bytes(call(method, ep))
    (out / "chartData_1_p5.json").write_bytes(call("POST", "chartData", {"chartId": 1, "period": 5}))
    (out / "chartData_40_p5.json").write_bytes(call("POST", "chartData", {"chartId": 40, "period": 5}))

    securities = json.loads((out / "allSecurityCode.json").read_bytes())
    equities = [s for s in securities if s["symbol"].endswith(EQUITY_SUFFIXES)]
    for sub in ("companyChartDataByStock", "companyInfoSummery", "companyProfile"):
        (out / sub).mkdir(exist_ok=True)
    for i, sec in enumerate(equities, 1):
        sym = sec["symbol"]
        jobs = [
            ("companyChartDataByStock", {"stockId": sec["id"], "period": 5}),
            ("companyInfoSummery", {"symbol": sym}),
            ("companyProfile", {"symbol": sym}),
        ]
        for ep, data in jobs:
            path = out / ep / f"{sym}.json"
            if not path.exists():
                path.write_bytes(call("POST", ep, data))
        if i % 25 == 0:
            print(f"{i}/{len(equities)}")
    print(f"saved to {out}")
    return out


def load(path: Path):
    return json.loads(path.read_bytes())


def short(symbol: str) -> str:
    """LOLC.N0000 -> LOLC, HNB.X0000 -> HNB.X (keep the non-voting marker)."""
    code, _, suffix = symbol.partition(".")
    return code if suffix == "N0000" else f"{code}.{suffix[0]}"


def fmt_rs(v: float | None) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1e9:
        return f"{v / 1e9:,.1f} bn"
    if abs(v) >= 10e6:
        return f"{v / 1e6:,.1f} mn"
    return f"{v / 1e6:,.2f} mn"


def report(session_date: str) -> None:
    raw = ROOT / "data" / "raw" / session_date
    securities = load(raw / "allSecurityCode.json")
    trade = {r["symbol"]: r for r in load(raw / "tradeSummary.json")["reqTradeSummery"]}
    aspi = load(raw / "chartData_1_p5.json")
    sessions = sorted({ms_to_date(x["d"]) for x in aspi})
    n_sess = len(sessions)
    aspi_ret = aspi[-1]["v"] / aspi[0]["v"] - 1
    user_syms = {s for v in USER_LIST.values() for s in v}
    user_cos = {s.split(".")[0] for s in user_syms}
    overrides = load_overrides(ROOT / "config" / "sector_overrides.yaml")
    unmatched: dict[str, list[str]] = {}

    rows = []
    missing: list[str] = []
    for sec in securities:
        sym = sec["symbol"]
        if not sym.endswith(EQUITY_SUFFIXES):
            continue
        if not all((raw / ep / f"{sym}.json").exists()
                   for ep in ("companyChartDataByStock", "companyInfoSummery", "companyProfile")):
            missing.append(sym)
            continue
        chart = (load(raw / "companyChartDataByStock" / f"{sym}.json").get("chartData") or [])
        info = load(raw / "companyInfoSummery" / f"{sym}.json")
        prof = load(raw / "companyProfile" / f"{sym}.json")
        si = info.get("reqSymbolInfo") or {}
        beta = (info.get("reqSymbolBetaInfo") or {}).get("triASIBetaValue")
        comsum = (prof.get("reqComSumInfo") or [{}])[0] if prof.get("reqComSumInfo") else {}
        if not classify(sym, comsum.get("sector"), overrides):
            unmatched.setdefault(repr(comsum.get("sector")), []).append(sym)
        by_day = {ms_to_date(x["t"]): x for x in chart}
        in_window = [by_day[d] for d in sessions if d in by_day]
        # Turnover is not in the history endpoint; close x volume is a labelled ESTIMATE.
        est_turnover = [by_day[d]["p"] * by_day[d]["q"] if d in by_day else 0.0 for d in sessions]
        jumps = [
            (ms_to_date(b["t"]), a["p"], b["p"])
            for a, b in zip(chart, chart[1:])
            if a["p"] and abs(b["p"] / a["p"] - 1) > 0.40
        ]
        ret_1y = (chart[-1]["p"] / chart[0]["p"] - 1) if len(chart) > 1 and chart[0]["p"] else None
        rows.append({
            "symbol": sym,
            "name": sec["name"],
            "sector": classify(sym, comsum.get("sector"), overrides) or "Unclassified",
            "board": comsum.get("boardType") or "—",
            "traded_share": len(in_window) / n_sess if n_sess else 0.0,
            "median_turnover_est": statistics.median(est_turnover) if est_turnover else 0.0,
            "turnover_today": trade.get(sym, {}).get("turnover"),
            "mcap": si.get("marketCap"),
            "beta": beta,
            "ret_1y": ret_1y,
            "jumps": jumps,
            "last": chart[-1]["p"] if chart else None,
            "last_date": ms_to_date(chart[-1]["t"]) if chart else None,
            "mine": sym in user_syms,
            "mine_co": sym.split(".")[0] in user_cos,   # another share class of a company you follow
            "excluded": sym in EXCLUDED,
        })

    def tier(r):
        if r["traded_share"] >= 0.95 and r["median_turnover_est"] >= 10e6:
            return "A"  # deep: trades almost every day, >= Rs 10 mn median
        if r["traded_share"] >= 0.80 and r["median_turnover_est"] >= 1e6:
            return "B"  # passes the Phase 2 draft liquidity filters
        return "C"

    for r in rows:
        r["tier"] = tier(r)

    lines: list[str] = []
    w = lines.append
    w(f"# Whole-market screen — session {session_date}\n")
    w("Generated by `python scripts/screen_watchlist.py report` from raw API responses in "
      f"`data/raw/{session_date}/`. Re-run to reproduce every number.\n")
    w("**This is a liquidity / size / sector-coverage screen, not a valuation screen and not advice.** "
      "The CSE API exposes no per-stock P/E, P/BV, dividend yield or earnings (see API_NOTES.md), so "
      "nothing here says a stock is cheap or good. It says which names are large and liquid enough to "
      "matter, which sectors your list does not cover, and which of your names would fail the Phase 2 "
      "liquidity filters.\n")
    w("## Method\n")
    w(f"- Universe: every `.N0000` (voting) and `.X0000` (non-voting) security in `allSecurityCode` "
      f"({len(rows)} securities).")
    w(f"- Window: the {n_sess} ASPI sessions {sessions[0]} → {sessions[-1]} returned by "
      "`chartData` (chartId 1, period 5) — the maximum history the public API gives.")
    w("- **Traded share** = sessions with a trade in `companyChartDataByStock` ÷ ASPI sessions.")
    w("- **Median turnover (est.)** = median over all sessions of close × volume, with 0 on no-trade days. "
      "The history endpoint has no turnover field, so this is an *estimate* (close ≠ VWAP). "
      "Today's actual turnover from `tradeSummary` is shown alongside as a check.")
    w("- **Market cap** from `companyInfoSummery.reqSymbolInfo.marketCap`; **beta** is the CSE-published "
      "`triASIBetaValue` (vs. the total-return ASI; CSE does not document the estimation window).")
    w(f"- **1y price change** is on *unadjusted* prices (no dividends; splits not adjusted). ASPI over the "
      f"same window: {aspi_ret:+.1%}. Names with a >40% one-day move are flagged ⚠ — usually an "
      "unadjusted sub-division, scrip or rights issue, so their 1y figure is meaningless.")
    w("- Tiers: **A** traded ≥95% of sessions and median turnover ≥ Rs 10 mn; **B** ≥80% and ≥ Rs 1 mn "
      "(the draft Phase 2 filters); **C** everything else.")
    w("- Sector from `companyProfile`, mapped to the 20 S&P/CSE industry groups by `cse/sectors.py` "
      "(normalised names, GICS sub-industry aliases, then the per-symbol proposals in "
      "`config/sector_overrides.yaml`, which you should confirm). Unclassified: "
      + ("; ".join(f"{k} ({', '.join(v)})" for k, v in sorted(unmatched.items())) or "none") + ".\n")

    if missing:
        w(f"**Incomplete fetch: {len(missing)} securities skipped** ({', '.join(missing[:10])}…). "
          "Re-run `fetch` to complete.\n")
    counts = {t: sum(1 for r in rows if r["tier"] == t) for t in "ABC"}
    w(f"Result: {counts['A']} tier A, {counts['B']} tier B, {counts['C']} tier C.\n")

    def row_md(r):
        flag = (" ★" if r["mine"] else "") + (" ⚠" if r["jumps"] else "") + (" ⛔excluded" if r["excluded"] else "")
        ret = "—" if r["ret_1y"] is None or r["jumps"] else f"{r['ret_1y']:+.0%}"
        beta = "—" if r["beta"] is None else f"{r['beta']:.2f}"
        return (f"| {r['symbol']}{flag} | {r['name'][:34]} | {r['sector']} | {r['tier']} | {r['traded_share']:.0%} | "
                f"{fmt_rs(r['median_turnover_est'])} | {fmt_rs(r['turnover_today'])} | {fmt_rs(r['mcap'])} | "
                f"{beta} | {ret} |")

    header = ("| Symbol | Name | Sector | Tier | Traded | Med. turnover (est.) | Turnover today | Mkt cap | Beta | 1y px |\n"
              "|---|---|---|---|---|---|---|---|---|---|")

    w("## 1. Your current list, assessed\n")
    for group, syms in USER_LIST.items():
        w(f"**{group}**\n")
        w(header)
        for s in syms:
            r = next((x for x in rows if x["symbol"] == s), None)
            if r:
                w(row_md(r))
        w("")

    from cse.sectors import INDUSTRY_GROUPS
    sectors = sorted(set(INDUSTRY_GROUPS) | {r["sector"] for r in rows})
    w("## 2. Sector coverage\n")
    w("| Sector | Tier A/B names | On your list | Largest tier A/B names not on your list |")
    w("|---|---|---|---|")
    for sec in sectors:
        names = [r for r in rows if r["sector"] == sec and r["tier"] in "AB"]
        mine = [short(r["symbol"]) for r in rows if r["sector"] == sec and r["mine"]]
        others = sorted((r for r in names if not r["mine_co"] and not r["excluded"]),
                        key=lambda r: -(r["mcap"] or 0))[:4]
        w(f"| {sec} | {len(names)} | {', '.join(mine) or '**none**'} | "
          f"{', '.join(short(r['symbol']) for r in others) or '—'} |")
    w("")

    w("## 3. Rule-based candidates\n")
    w("★ = on your list, ⚠ = >40% one-day move in the window (likely unadjusted corporate action), "
      "⛔ = in `excluded`.\n")
    w("Mechanical rules, no judgement: (i) in each sector, the tier A name with the highest median "
      "turnover from a company not already on your list (any share class) and not excluded; (ii) the 10 highest-turnover tier A names not on your "
      "list; (iii) names on your list that fail the draft Phase 2 liquidity filters (tier C).\n")
    w("**(i) Best-liquidity name per sector, not on your list**\n")
    w(header)
    for sec in sectors:
        cands = sorted((r for r in rows if r["sector"] == sec and r["tier"] == "A"
                        and not r["mine_co"] and not r["excluded"]), key=lambda r: -r["median_turnover_est"])
        if cands:
            w(row_md(cands[0]))
    w("")
    w("**(ii) Most liquid names not on your list**\n")
    w(header)
    for r in sorted((r for r in rows if r["tier"] == "A" and not r["mine_co"] and not r["excluded"]),
                    key=lambda r: -r["median_turnover_est"])[:10]:
        w(row_md(r))
    w("")
    w("**(iii) Your names that fail the draft Phase 2 liquidity filters**\n")
    w(header)
    for r in sorted((r for r in rows if r["mine"] and r["tier"] == "C"), key=lambda r: r["median_turnover_est"]):
        w(row_md(r))
    w("")

    w("## 3b. Top 25 by median turnover (est.), whole market\n")
    w(header)
    for r in sorted(rows, key=lambda r: -r["median_turnover_est"])[:25]:
        w(row_md(r))
    w("")

    w("## 4. Top 25 by market cap, whole market\n")
    w(header)
    for r in sorted(rows, key=lambda r: -(r["mcap"] or 0))[:25]:
        w(row_md(r))
    w("")

    w("## 5. All tier A and B names by sector\n")
    for sec in sectors:
        names = sorted((r for r in rows if r["sector"] == sec and r["tier"] in "AB"),
                       key=lambda r: -r["median_turnover_est"])
        if not names:
            continue
        w(f"**{sec}**\n")
        w(header)
        for r in names:
            w(row_md(r))
        w("")

    w("## 6. Securities with no trades in the window\n")
    dead = [r for r in rows if r["traded_share"] == 0]
    w(", ".join(f"{r['symbol']}" for r in dead) or "none")
    w("")
    (ROOT / "docs" / "WATCHLIST_SCREEN.md").write_text("\n".join(lines))
    json.dump(rows, open(ROOT / "docs" / "watchlist_screen_rows.json", "w"), indent=1, default=str)
    print("wrote docs/WATCHLIST_SCREEN.md")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in {"fetch", "report"}:
        raise SystemExit(__doc__)
    if sys.argv[1] == "fetch":
        fetch()
    else:
        report(sys.argv[2] if len(sys.argv) > 2 else sorted(p.name for p in (ROOT / "data" / "raw").iterdir())[-1])
