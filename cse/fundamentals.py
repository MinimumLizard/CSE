"""Company fundamentals from the interim reports we already hold (docs/METHODS.md §12).

    python -m cse.fundamentals     -> data/fundamentals/reports.csv, data/fundamentals/latest.csv

The CSE API serves no per-company earnings or book value (API_NOTES §5), so they are read from
each quarterly interim report's text (data/raw/ownership/text/). Every figure must reconcile
inside its own report or it isn't used:

- **EPS** (basic, first column): EPS x shares must equal the profit line in the same column
  (profit attributable to owners, or profit for the period) in Rs, Rs '000 or Rs mn, within
  2 % + the rounding of a 2-decimal EPS. The match also fixes the statement's unit.
- **Net assets per share** (first column): NAV/share x shares must equal equity attributable to
  owners (or total equity) the same way. Group figures are preferred over company-only ones.
- Shares are the report's own period-end count (from its verified shareholder table: shares /
  fraction), or the CSE's current count when the table isn't verified.

**Trailing-12-month EPS.** Reports differ in what their first column holds: the quarter, or the
year to date. It is taken from the column headers above the EPS line ("Quarter ended", "03 months
to" -> quarter; "Six months", "Nine months", "Year ended" -> year to date; "Period ended" alone is
unknown unless the report is the first quarter of the financial year, where both agree). The
financial year end comes from the company profile (finYearEnd 1 = 31 March, 2 = 31 December).
Quarterly EPS is the quarter column, or year-to-date minus the previous quarter's year-to-date
within the same financial year. TTM = the last four consecutive quarters, only when all four are
known. Nothing is estimated or annualised.

Outputs per company (latest.csv): price (latest close), NAV/share and P/B, TTM EPS and P/E, ROE
(TTM EPS / NAV per share), trailing dividends per share (confirmed corporate actions, last 365 days)
and dividend yield. Per-share figures in latest.csv are the reconciled totals (TTM profit, latest
equity) divided by today's issued shares, so P/E = market cap / TTM profit and P/B = market cap /
equity, the same construction as the CSE's market PER and PBV, and safe across splits and scrip
issues. The report's own EPS and NAV/share stay in reports.csv.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from pathlib import Path

from . import corpactions, metrics, storage
from .config import ROOT
from .fetch import Paths, log_run

TOL = 0.02
NUMTOK = re.compile(r"\(\s*-?[\d,]*\.?\d+\s*\)|-?\d[\d,]*\.?\d*")
EPS = re.compile(r"(?:basic\s+(?:and\s+diluted\s+)?)?(?:earnings?|profit|\(loss\)|loss)\s*(?:/\s*\(?(?:loss|profit)\)?\s*)?"
                 r"per\s+(?:ordinary\s+)?share|basic\s+eps\b", re.I)
NAV = re.compile(r"net\s+assets?\s+(?:value\s+)?per\s+(?:ordinary\s+)?share", re.I)
PROFIT = re.compile(r"(?:equity\s+holders|owners|shareholders)\s+of\s+the\s+(?:parent|company|bank|holding)|"
                    r"(?:net\s+)?(?:profit|\(loss\)|loss)\s*(?:/\s*\(?(?:loss|profit)\)?)?\s+(?:after\s+tax\s+)?"
                    r"for\s+the\s+(?:period|year|quarter)", re.I)
ATTRIB = re.compile(r"equity\s+holders|owners|shareholders", re.I)
EQUITY = re.compile(r"total\s+equity\s+attributable|equity\s+attributable\s+to\s+(?:the\s+)?(?:equity\s+holders|owners|"
                    r"shareholders)|^\s*total\s+(?:shareholders'?\s*)?equity\b", re.I)
QUARTER_HDR = re.compile(r"\bquarter\b|\bthree\s+months\b|\b0?3\s*months\b", re.I)
CUM_HDR = re.compile(r"\b(?:six|nine|twelve|0?6|0?9|12)\s*months\b|\byear\s+ended\b|\bhalf\s+year\b|\bcumulative\b", re.I)
UNITS = (1.0, 1e3, 1e6)


def numbers(text: str) -> list[float]:
    """Numbers on a statement line: '(1,234)' is negative; years like 2026 in headers are dropped by callers."""
    out = []
    for t in NUMTOK.findall(text):
        neg = t.strip().startswith("(") or t.strip().startswith("-")
        v = t.strip("() -").replace(",", "")
        if not v or v == ".":
            continue
        try:
            x = float(v)
        except ValueError:
            continue
        out.append(-x if neg else x)
    return out


def _labelled(lines: list[str], pat: re.Pattern, limit: int = 40) -> list[tuple[int, list[float], str]]:
    """(line index, numbers after the label, label line) for each line matching `pat`; when the numbers
    sit on the next line (labels wrapped above them), take those."""
    out = []
    for i, ln in enumerate(lines):
        m = pat.search(ln)
        if not m:
            continue
        v = numbers(ln[m.end():])
        if len(v) == 1 and v[0].is_integer() and 0 < v[0] < 100 and not re.search(r"\d\.\d", ln[m.end():]):
            v = []                                         # 'Earnings per share   9': a note reference
        if not v and i + 1 < len(lines) and not re.search(r"[A-Za-z]{4,}", lines[i + 1]):
            v = numbers(lines[i + 1])
        if not v:
            # 'Earnings per share' as a heading, numbers on a following '- basic' / 'Basic (Rs.)' line;
            # only numbers to the right of that label count (some layouts print other columns left of it)
            for k in range(i + 1, min(len(lines), i + 4)):
                b = re.search(r"^\s*[-–•]?\s*(?:\(?[a-z]\)\s*)?basic\b[^\d(\-]*", lines[k], re.I)
                if b:
                    v = numbers(lines[k][b.end():])
                    break
        v = [x for x in v if not (x.is_integer() and 1990 <= x <= 2040)]       # stray year headers
        if v:
            out.append((i, v, re.sub(r"\s{2,}", "  ", ln.strip())))
        if len(out) >= limit:
            break
    return out


def _tol(per_share: float) -> float:
    """2 % plus the rounding of the printed per-share figure (half a unit of its last decimal place)."""
    txt = f"{abs(per_share):.6f}".rstrip("0").rstrip(".")
    dp = len(txt.split(".")[1]) if "." in txt else 0
    return min(0.15, TOL + 0.5 * 10 ** -max(dp, 2) / max(abs(per_share), 1e-9))


def _reconcile(per_share: float, total: float, shares: float) -> float | None:
    """The unit (1, 1e3, 1e6) under which per_share x shares == total, or None."""
    if not per_share or not total or not shares or (per_share > 0) != (total > 0):
        return None
    for u in UNITS:
        r = per_share * shares / (total * u)
        if abs(r - 1) <= _tol(per_share):
            return u
    return None


def header_class(lines: list[str], i: int) -> str:
    """'quarter' / 'ytd' / 'unknown' for the first numeric column: the nearest line above (within 80)
    that names a period length decides; a bare 'period ended' header line is skipped over."""
    for k in range(i - 1, max(-1, i - 80), -1):
        ln = lines[k]
        q, c = QUARTER_HDR.search(ln), CUM_HDR.search(ln)
        if q and c:
            return "quarter" if q.start() < c.start() else "ytd"
        if q:
            return "quarter"
        if c:
            return "ytd"
    return "unknown"


def extract(text: str, shares: float) -> dict:
    lines = text.splitlines()
    out = {"eps": None, "eps_line": "", "profit": None, "unit": None, "eps_class": "", "nav": None, "nav_line": "",
           "equity": None, "nav_unit": None}
    profits = _labelled(lines, PROFIT, limit=80)
    for i, ev, label in _labelled(lines, EPS, limit=6):
        above = [p for p in profits if i - 60 <= p[0] < i]
        # attributable-to-owners first (EPS is on that), then the nearest profit line
        above.sort(key=lambda p: (not ATTRIB.search(p[2]), i - p[0]))
        for j, pv, plabel in above:
            u = _reconcile(ev[0], pv[0], shares)
            if u:
                out.update(eps=ev[0], eps_line=label, profit=pv[0] * u, unit=u, eps_class=header_class(lines, i),
                           profit_line=plabel)
                break
        if out["eps"] is not None:
            break
    navs = _labelled(lines, NAV, limit=8)
    navs.sort(key=lambda n: (not re.search(r"group|consolidated", n[2], re.I), bool(re.search(r"\bcompany\b", n[2], re.I)), n[0]))
    equities = _labelled(lines, EQUITY, limit=20)
    for i, nv, label in navs:
        for j, qv, qlabel in equities:
            u = _reconcile(nv[0], qv[0], shares)
            if u:
                out.update(nav=nv[0], nav_line=label, equity=qv[0] * u, nav_unit=u, equity_line=qlabel)
                break
        if out["nav"] is not None:
            break
    return out


def fiscal_quarter(period: str, fye: str) -> tuple[int, int] | None:
    """(fiscal year label, quarter 1-4) for a quarter-end date; fye '1' = 31 March, '2' = 31 December."""
    d = dt.date.fromisoformat(period)
    if (d.month, d.day) not in ((3, 31), (6, 30), (9, 30), (12, 31)):
        return None
    if fye == "2":
        return d.year, (d.month - 1) // 3 + 1
    q = {6: 1, 9: 2, 12: 3, 3: 4}[d.month]
    return (d.year + 1 if d.month >= 6 else d.year), q


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def quarter_end(title: str, period: str) -> str:
    """The quarter a report covers: the CSE's period date when it is a quarter end; otherwise the date in
    the title ('...Quarter ended 30th June 2026', '30/06/2025', '30.06.2025'), else the last quarter end
    at least 15 days before the CSE's date (some records carry the filing date)."""
    p = dt.date.fromisoformat(period)
    if (p.month, p.day) in ((3, 31), (6, 30), (9, 30), (12, 31)):
        return period
    n = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d\d)\b", title or "")
    if n:
        try:
            d = dt.date(int(n.group(3)), int(n.group(2)), int(n.group(1)))
            if (d.month, d.day) in ((3, 31), (6, 30), (9, 30), (12, 31)):
                return d.isoformat()
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(20\d\d)", title or "")
    if m and m.group(2)[:3].lower() in MONTHS:
        try:
            d = dt.date(int(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
            if (d.month, d.day) in ((3, 31), (6, 30), (9, 30), (12, 31)):
                return d.isoformat()
        except ValueError:
            pass
    d = dt.date.fromisoformat(period) - dt.timedelta(days=15)
    for y, mo, da in sorted(((y, mo, da) for y in (d.year - 1, d.year) for mo, da in ((3, 31), (6, 30), (9, 30), (12, 31))),
                            reverse=True):
        if dt.date(y, mo, da) <= d:
            return dt.date(y, mo, da).isoformat()
    return period


def report_shares(root: Path) -> dict[str, float]:
    """report id -> period-end shares (N + X) from its verified shareholder tables (shares / fraction)."""
    per: dict[str, dict[str, float]] = defaultdict(dict)
    for h in storage.read_rows(root / "data" / "ownership" / "holdings.csv"):
        if h["table_status"] in ("verified", "verified_total") and h["fraction"] and float(h["fraction"]) > 0.005:
            per[h["report_id"]].setdefault(h["class"], float(h["shares"]) / float(h["fraction"]))
    return {rid: sum(v.values()) for rid, v in per.items()}


def fyes(root: Path) -> dict[str, str]:
    out = {}
    for f in sorted((root / "data" / "raw").glob("*/companyProfile/*.json")):
        info = (json.loads(f.read_bytes()).get("reqComSumInfo") or [{}])[0] or {}
        if info.get("finYearEnd"):
            out[f.stem.split(".")[0]] = str(info["finYearEnd"])
    return out


def ttm(rows: list[dict], fye: str) -> tuple[float | None, str, str]:
    """(trailing-12-month profit attributable to owners, in Rs; the quarter it ends; how) from one
    company's verified report rows. Built from profit, not EPS, so a share split or bonus issue during
    the year doesn't mix old and new per-share figures."""
    by_q: dict[tuple[int, int], dict] = {}
    for r in rows:
        fq = fiscal_quarter(r["period_date"], fye)
        if fq and r["eps_status"] == "verified" and r["profit"] not in (None, ""):
            by_q[fq] = r
    quarterly: dict[tuple[int, int], float] = {}
    ytd: dict[tuple[int, int], float] = {}
    for (fy, q), r in sorted(by_q.items()):
        v, cls = float(r["profit"]), r["eps_class"]
        if q == 1:
            quarterly[(fy, q)] = ytd[(fy, q)] = v
        elif cls == "quarter":
            quarterly[(fy, q)] = v
        elif cls == "ytd":
            ytd[(fy, q)] = v
    for _ in range(4):                                  # year-to-date <-> quarter, within a financial year
        for (fy, q), v in list(ytd.items()):
            if q > 1 and (fy, q - 1) in ytd and (fy, q) not in quarterly:
                quarterly[(fy, q)] = v - ytd[(fy, q - 1)]
        for (fy, q), v in list(quarterly.items()):
            if q > 1 and (fy, q - 1) in ytd and (fy, q) not in ytd:
                ytd[(fy, q)] = ytd[(fy, q - 1)] + v
    if not quarterly:
        return None, "", ""
    last = max(quarterly)
    seq, (fy, q) = [], last
    for _ in range(4):
        if (fy, q) not in quarterly:
            return None, "", f"quarter {q} of FY{fy} not known"
        seq.append(quarterly[(fy, q)])
        fy, q = (fy, q - 1) if q > 1 else (fy - 1, 4)
    return sum(seq), by_q[last]["period_date"], "sum of 4 quarters' profit"


def dividends_ttm(root: Path, asof: str) -> dict[str, float]:
    rows = storage.read_rows(root / "data" / "history" / "corporate_actions.csv")
    acts = corpactions.effective(rows, corpactions.load_reviews(root / "config" / "corporate_actions_review.yaml"))
    start = (dt.date.fromisoformat(asof) - dt.timedelta(days=365)).isoformat()
    out: dict[str, float] = defaultdict(float)
    for a in acts:
        if a["type"] == "cash_dividend" and start < a["ex_date"] <= asof and a.get("amount_per_share"):
            out[a["symbol"]] += float(a["amount_per_share"])
    return out


def build(root: Path = ROOT) -> tuple[list[dict], list[dict]]:
    prices = metrics.load_prices(Paths(root).prices)
    last = prices.sort_values("date").groupby("symbol").tail(1).set_index("symbol")
    asof = str(prices["date"].max())
    reps = [r for r in storage.read_rows(root / "data" / "raw" / "ownership" / "reports.csv")
            if r["status"] == "ok" and r["period_date"] <= asof]             # a few CSE records carry future dates
    shares_by_rep = report_shares(root)
    from .ownership.analyse import load_market
    market = load_market(root)
    fy = fyes(root)
    out = []
    for r in sorted(reps, key=lambda r: (r["code"], r["period_date"], r["id"])):
        cur = (market.issued.get(r["code"] + ".N0000") or 0) + (market.issued.get(r["code"] + ".X0000") or 0)
        sh = shares_by_rep.get(r["id"]) or cur
        text = (root / "data" / "raw" / "ownership" / "text" / f"{r['id']}.txt").read_text(errors="replace")
        x = extract(text, sh)
        if x["eps"] is None and sh != cur and cur:
            x = extract(text, cur)                       # e.g. weighted shares near today's count
            sh = cur if x["eps"] is not None or x["nav"] is not None else sh
        out.append({"report_id": r["id"], "code": r["code"], "period_date": quarter_end(r["title"], r["period_date"]),
                    "fye": fy.get(r["code"], ""),
                    "shares": sh, "eps": x["eps"], "eps_class": x["eps_class"],
                    "eps_status": "verified" if x["eps"] is not None else "not found",
                    "profit": x["profit"], "unit": x["unit"], "eps_line": x["eps_line"][:160],
                    "nav": x["nav"], "nav_status": "verified" if x["nav"] is not None else "not found",
                    "equity": x["equity"], "nav_line": x["nav_line"][:160], "url": r["url"]})
    divs = dividends_ttm(root, asof)
    latest = []
    by_code: dict[str, list[dict]] = defaultdict(list)
    for r in out:
        by_code[r["code"]].append(r)
    for code, rows in sorted(by_code.items()):
        sym = code + ".N0000"
        price = float(last.loc[sym, "close"]) if sym in last.index else None
        navr = max((r for r in rows if r["nav_status"] == "verified"), key=lambda r: r["period_date"], default=None)
        profit, eps_to, how = ttm(rows, fy.get(code, "1"))
        # per-share figures on today's share count (the CSE's latest company summary), so that price / EPS =
        # market cap / profit and price / NAV = market cap / equity, as in the CSE's own market PER and PBV
        cur = (market.issued.get(code + ".N0000") or 0) + (market.issued.get(code + ".X0000") or 0)
        shares_now = cur or float(max(rows, key=lambda r: r["period_date"])["shares"] or 0)
        eps = profit / shares_now if profit is not None and shares_now else None
        nav = float(navr["equity"]) / shares_now if navr and shares_now else None
        d = divs.get(sym, 0.0)
        latest.append({"code": code, "price": price, "price_date": str(last.loc[sym, "date"]) if sym in last.index else "",
                       "shares": shares_now, "fye": {"1": "31 Mar", "2": "31 Dec"}.get(fy.get(code, ""), ""),
                       "nav": nav, "nav_period": navr["period_date"] if navr else "", "pb": price / nav if price and nav and nav > 0 else None,
                       "ttm_profit": profit, "ttm_eps": eps, "ttm_to": eps_to, "ttm_note": how,
                       "earnings_yield": eps / price if price and eps is not None else None,
                       "pe": price / eps if price and eps and eps / price > 0.002 else None,   # P/E above 500 is noise
                       "roe": eps / nav if eps is not None and nav and nav > 0 else None,
                       "div_ttm": d, "dy": d / price if price else None})
    return out, latest


REPORT_COLS = ["report_id", "code", "period_date", "fye", "shares", "eps", "eps_class", "eps_status", "profit", "unit",
               "eps_line", "nav", "nav_status", "equity", "nav_line", "url"]
LATEST_COLS = ["code", "price", "price_date", "shares", "fye", "nav", "nav_period", "pb", "ttm_profit", "ttm_eps", "ttm_to",
               "ttm_note", "earnings_yield", "pe", "roe", "div_ttm", "dy"]


def write(rows: list[dict], latest: list[dict], root: Path = ROOT) -> str:
    d = root / "data" / "fundamentals"
    storage.atomic_write_bytes(d / "reports.csv", (",".join(REPORT_COLS) + "\n" + storage._serialise(REPORT_COLS, rows)).encode())
    storage.atomic_write_bytes(d / "latest.csv", (",".join(LATEST_COLS) + "\n" + storage._serialise(LATEST_COLS, latest)).encode())
    n_pb = sum(1 for x in latest if x["pb"] is not None)
    n_pe = sum(1 for x in latest if x["ttm_eps"] is not None)
    msg = (f"fundamentals: {sum(r['eps_status'] == 'verified' for r in rows)}/{len(rows)} report EPS and "
           f"{sum(r['nav_status'] == 'verified' for r in rows)} NAV/share reconciled; {n_pb} companies with P/B, "
           f"{n_pe} with trailing-12-month EPS")
    log_run(Paths(root), "fundamentals", "ok", None, msg)
    return msg



def page_context(root: Path = ROOT) -> dict | None:
    """Rows for site/fundamentals.html, plus the market-level check against the CSE's own P/E, P/BV, DY."""
    path = root / "data" / "fundamentals" / "latest.csv"
    if not path.exists():
        return None
    from .config import load_universe
    from .ownership.analyse import load_market
    market = load_market(root)
    latest = storage.read_rows(path)
    reps = storage.read_rows(root / "data" / "fundamentals" / "reports.csv")
    src = {}
    for r in sorted(reps, key=lambda r: r["period_date"]):
        src[r["code"]] = r["url"]
    sectors = {}
    for f in sorted((root / "data" / "raw").glob("*/companyProfile/*.N0000.json")):
        info = (json.loads(f.read_bytes()).get("reqComSumInfo") or [{}])[0] or {}
        if info.get("sector"):
            sectors[f.stem.split(".")[0]] = info["sector"].strip()
    from . import sectors as sec
    overrides = sec.load_overrides(root / "config" / "sector_overrides.yaml")
    sectors = {c: sec.classify(c + ".N0000", lab, overrides) or lab for c, lab in sectors.items()}
    try:
        watch = {s.split(".")[0] for s in load_universe(root / "config" / "universe.yaml").watchlist}
    except Exception:  # noqa: BLE001
        watch = set()
    num = lambda v: float(v) if v not in (None, "") else None
    rows = []
    for x in latest:
        c = x["code"]
        rows.append({"code": c, "name": market.names.get(c, c), "sector": sectors.get(c, ""), "watch": c in watch,
                     "cap": market.company_cap(c), "price": num(x["price"]), "pe": num(x["pe"]),
                     "ey": num(x["earnings_yield"]), "pb": num(x["pb"]), "roe": num(x["roe"]), "dy": num(x["dy"]),
                     "ttm_to": x["ttm_to"], "nav_period": x["nav_period"], "fye": x["fye"],
                     "loss": num(x["ttm_eps"]) is not None and num(x["ttm_eps"]) <= 0, "url": src.get(c, "")})
    # market check: cap-weighted aggregates over the companies covered, vs the CSE's published figures
    mk = storage.read_rows(Paths(root).market)[-1]
    def agg(key):
        sel = [r for r in rows if r[key] is not None and r["price"]]
        cap = sum(r["cap"] for r in sel)
        return sel, cap
    e_sel, e_cap = agg("ey")
    b_sel, b_cap = [r for r in rows if r["pb"]], sum(r["cap"] for r in rows if r["pb"])
    d_sel, d_cap = agg("dy")
    total = sum(market.company_cap(c) for c in market.names)
    check = {
        "per": e_cap / sum(r["cap"] * r["ey"] for r in e_sel) if e_sel else None, "per_cse": num(mk.get("per")),
        "per_cov": e_cap / total if total else None,
        "pbv": b_cap / sum(r["cap"] / r["pb"] for r in b_sel) if b_sel else None, "pbv_cse": num(mk.get("pbv")),
        "pbv_cov": b_cap / total if total else None,
        "dy": sum(r["cap"] * r["dy"] for r in d_sel) / d_cap if d_cap else None, "dy_cse": num(mk.get("dy")),
        "date": mk.get("date"),
    }
    rows.sort(key=lambda r: -r["cap"])
    return {"rows": rows, "check": check, "n_pe": sum(1 for r in rows if r["pe"] or r["loss"]),
            "n_pb": sum(1 for r in rows if r["pb"]), "n": len(rows)}


if __name__ == "__main__":
    print(write(*build(ROOT), ROOT))
