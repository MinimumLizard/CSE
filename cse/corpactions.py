"""Corporate actions: collect from the CSE's structured announcement records and turn them into
data/history/corporate_actions.csv.

Sources (docs/API_NOTES.md §4 Q2):
- getAnnouncementByCompany: per-company announcement list over a date range.
- getAnnouncementById: typed detail (CashDividendWithDates, ScripDividendWithDates, ShareSplits,
  RightsIssue, ...).
- getGeneralAnnouncementById: the "(DATES)" follow-ups the typed endpoint answers with 204
  (split trading dates, rights XR dates).

Rules:
- Amounts and ratios come only from numeric API fields, or from text that states them
  explicitly. Nothing is inferred. A row whose amount or ratio was read from free text, or
  whose fields are incomplete or contradictory, is marked `needs_review`, and it is not used
  until it is confirmed in config/corporate_actions_review.yaml.
- The CSV is append-only and de-duplicated on `id`.

`python -m cse.corpactions` collects the history window (resumable; raw responses are cached
under data/raw/announcements/) and then rebuilds the CSV from everything cached.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import storage
from .config import EQUITY_SUFFIXES, ROOT
from .fetch import CDN, Paths, log_run
from .http import CseClient
from .models import SecurityList, validate

CA_COLS = ["id", "symbol", "type", "ex_date", "amount_per_share", "ratio_new", "ratio_held",
           "factor", "subscription_price", "status", "note", "source_url"]
TYPES = ("cash_dividend", "subdivision", "scrip", "rights")
CA_KEYWORDS = ("DIVIDEND", "SUB-DIVISION", "SUBDIVISION", "SUB DIVISION", "RIGHTS", "SCRIP",
               "CAPITALI", "CONSOLIDATION", "BONUS")


def is_ca_category(category: str) -> bool:
    c = (category or "").upper()
    return any(k in c for k in CA_KEYWORDS) and "DEBENTURE" not in c


def cache_dir(paths: Paths) -> Path:
    return paths.root / "data" / "raw" / "announcements"


# --- collection ------------------------------------------------------------------------------

def _cached_call(client: CseClient, path: Path, endpoint: str, **params):
    """Return parsed JSON (or None for 204/empty), fetching and caching on first use."""
    if path.exists():
        body = path.read_bytes()
        return json.loads(body) if body.strip() else None
    r = client.call(endpoint, **params)
    storage.atomic_write_bytes(path, r.raw)
    return r.data


def collect(client: CseClient, paths: Paths, from_date: str, to_date: str) -> int:
    """Cache every company's announcement list and every CA-related detail record."""
    securities = validate(SecurityList, client.call("allSecurityCode").data, "allSecurityCode").root
    companies: dict[str, str] = {}
    for s in sorted(securities, key=lambda s: (not s.symbol.endswith(".N0000"), s.symbol)):
        if s.symbol.endswith(EQUITY_SUFFIXES):
            companies.setdefault(s.symbol.split(".")[0], s.symbol)
    root = cache_dir(paths)
    n_detail = 0
    for i, (code, symbol) in enumerate(sorted(companies.items()), 1):
        lst = _cached_call(client, root / "byCompany" / f"{symbol}_{from_date}_{to_date}.json",
                           "getAnnouncementByCompany", symbol=symbol, fromDate=from_date, toDate=to_date)
        for a in (lst or {}).get("reqCompanyAnnouncement", []):
            if not is_ca_category(a.get("announcementCategory", "")):
                continue
            aid = a["announcementId"]
            typed = _cached_call(client, root / "getAnnouncementById" / f"{aid}.json",
                                 "getAnnouncementById", announcementId=aid)
            if not typed:
                _cached_call(client, root / "getGeneralAnnouncementById" / f"{aid}.json",
                             "getGeneralAnnouncementById", announcementId=aid)
            n_detail += 1
        if i % 25 == 0:
            print(f"  {i}/{len(companies)} companies, {n_detail} CA announcements", file=sys.stderr)
    return n_detail


# --- parsing ---------------------------------------------------------------------------------

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20}


def parse_date(v) -> str | None:
    """API dates come as '01 Jul 2026' strings or epoch-ms numbers (midnight Sri Lanka time)."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        from .models import ms_to_date
        return ms_to_date(int(v)).isoformat()
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(str(v).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_rights_text(text: str | None) -> tuple[float, float] | None:
    """'Three (3) new Ordinary Voting Shares for every Fifty (50) existing ...' -> (3, 50).

    Uses the numerals in parentheses when present (they are unambiguous), else number words.
    Returns (new, held) or None when the text doesn't state both.
    """
    if not text:
        return None
    t = text.lower()
    m = re.search(r"\((\d+(?:\.\d+)?)\)[^()]*?new.*?every[^()]*?\((\d+(?:\.\d+)?)\)", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    if ":" in t or "every" not in t:
        return None
    head, _, tail = t.partition("every")
    if "preference" in head:                       # preference shares aren't an ordinary rights issue
        return None
    a = re.findall(r"\d+(?:\.\d+)?", head)        # '06 new ordinary shares for every 01 existing'
    b = re.findall(r"\d+(?:\.\d+)?", tail)
    if len(a) == 1 and b:
        return float(a[0]), float(b[0])
    m = re.search(r"(\w+)\s+(?:\(\w+\)\s+)?new.*?every\s+(\w+)", t)
    if m and m.group(1) in _WORDS and m.group(2) in _WORDS:
        return float(_WORDS[m.group(1)]), float(_WORDS[m.group(2)])
    return None


def parse_proportion(v) -> tuple[float | None, str]:
    """Scrip proportion = existing shares per 1 new share. Returns (held_per_new, how).

    The API field is sometimes a number and sometimes text: '1 for 136.0022997317',
    '1 share for every 115.38 shares', 'one scrip issue for every 80.41807', '1for115.25',
    'One (1) new share ... for each existing ... (36.97479001)'. Text is accepted only in the
    form "1 new for N" with exactly one other number N; anything else returns (None, 'unparsed').
    """
    n = _num(v)
    if n is not None:
        return (n, "numeric") if n > 0 else (None, "unparsed")
    text = str(v or "").strip()
    if ":" in text:                       # "1:5" doesn't say which side is new; never guessed
        return None, "unparsed"
    if not re.match(r"^(1\b|1for|one\b)", text, re.I):
        return None, "unparsed"
    others = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text) if float(x) != 1.0]
    return (others[0], "text") if len(others) == 1 and others[0] > 1 else (None, "unparsed")


def scrip_check(issued, held_per_new: float, shares_now: float | None) -> bool | None:
    """Shares issued × proportion ≈ shares outstanding before the issue. `shares_now` (current
    quantity issued) includes the scrip shares, so the expected ratio is about 1 / (1 + 1/N).
    Returns None when there's nothing to check against."""
    issued = _num(issued)
    if not issued or not shares_now:
        return None
    ratio = issued * held_per_new / shares_now
    return 0.85 <= ratio <= 1.05


@dataclass
class Record:
    """One cached announcement: list row + detail (typed or general)."""
    aid: int
    company_symbol: str          # the symbol used to query (voting line)
    category: str
    date: str
    detail: dict | None          # reqBaseAnnouncement
    docs: list
    source: str                  # "typed" | "general" | "none"

    @property
    def code(self) -> str:
        return self.company_symbol.split(".")[0]

    @property
    def url(self) -> str:
        if not self.docs:
            return ""
        from urllib.parse import quote
        d = self.docs[0]
        base = d.get("baseUrl") if str(d.get("baseUrl") or "").startswith("https://") else CDN
        return base.rstrip("/") + "/" + quote(str(d.get("fileUrl", "")).lstrip("/"), safe="/")


def load_records(paths: Paths) -> list[Record]:
    root = cache_dir(paths)
    out: dict[int, Record] = {}
    for lst_path in sorted((root / "byCompany").glob("*.json")):
        symbol = lst_path.name.split("_")[0]
        body = lst_path.read_bytes()
        lst = json.loads(body) if body.strip() else {}
        for a in lst.get("reqCompanyAnnouncement", []):
            if not is_ca_category(a.get("announcementCategory", "")):
                continue
            aid = int(a["announcementId"])
            detail, docs, source = None, [], "none"
            for sub, name in (("getAnnouncementById", "typed"), ("getGeneralAnnouncementById", "general")):
                p = root / sub / f"{aid}.json"
                if p.exists() and p.read_bytes().strip():
                    j = json.loads(p.read_bytes())
                    if j and j.get("reqBaseAnnouncement"):
                        detail, docs, source = j["reqBaseAnnouncement"], j.get("reqAnnouncementDocs") or [], name
                        break
            date = parse_date(a.get("dateOfAnnouncement")) or ""
            out[aid] = Record(aid, symbol, a.get("announcementCategory", "").strip(), date, detail, docs, source)
    # Announcements the daily job saw (detail saved under data/raw/<session>/).
    classes = None
    for a in storage.read_rows(paths.announcements):
        if not a["id"].isdigit() or int(a["id"]) in out or not is_ca_category(a["category"]):
            continue
        aid = int(a["id"])
        detail, docs, source = None, [], "none"
        for sub, name in (("getAnnouncementById", "typed"), ("getGeneralAnnouncementById", "general")):
            hits = sorted((paths.root / "data" / "raw").glob(f"*/{sub}/{aid}.json"))
            if hits:
                j = json.loads(hits[-1].read_bytes())
                if j and j.get("reqBaseAnnouncement"):
                    detail, docs, source = j["reqBaseAnnouncement"], j.get("reqAnnouncementDocs") or [], name
                    break
        if classes is None:
            classes = share_classes(paths)
        syms = classes.get(a["symbol"], [])
        voting = next((x for x in syms if x.endswith(".N0000")), syms[0] if syms else None)
        if voting:
            out[aid] = Record(aid, voting, a["category"], a["date"], detail, docs, source)
    return sorted(out.values(), key=lambda r: (r.date, r.aid))


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def parse(records: list[Record], share_classes: dict[str, list[str]],
          shares_out: dict[str, float] | None = None) -> list[dict]:
    """Turn cached records into corporate_actions rows. `share_classes`: code -> symbols;
    `shares_out`: symbol -> current quantity issued (for the scrip share-count check)."""
    rows: list[dict] = []
    by_code: dict[str, list[Record]] = {}
    for r in records:
        by_code.setdefault(r.code, []).append(r)

    def row(rec, symbol, typ, **kw):
        base = {"id": f"{rec.aid}:{symbol}", "symbol": symbol, "type": typ, "ex_date": None,
                "amount_per_share": None, "ratio_new": None, "ratio_held": None, "factor": None,
                "subscription_price": None, "status": "confirmed", "note": "", "source_url": rec.url}
        base.update(kw)
        return base

    for code, recs in by_code.items():
        classes = share_classes.get(code, [])
        voting = next((s for s in classes if s.endswith(".N0000")), None)
        nonvoting = next((s for s in classes if s.endswith(".X0000")), None)
        for rec in recs:
            d = rec.detail or {}
            dtype = d.get("dType") or ""
            cat = rec.category.upper()

            # Cash dividend with dates (typed). Separate rows per share class.
            if dtype == "CashDividendWithDates":
                xd = parse_date(d.get("xd"))
                for sym, key, flag in ((voting, "votingDivPerShare", "votingShare"),
                                       (nonvoting, "nonVotingDivPerShare", "noneVotingShare")):
                    amt = _num(d.get(key))
                    if amt is None and sym == voting:
                        amt = _num(d.get("divPerShare"))
                    if not sym or not d.get(flag, sym == voting) or not amt:
                        continue
                    status, note = ("confirmed", "") if xd else ("needs_review", "no XD date in record")
                    rows.append(row(rec, sym, "cash_dividend", ex_date=xd, amount_per_share=amt,
                                    status=status, note=note))
                continue

            # Scrip dividend with dates (typed). Proportion = existing shares per 1 new share.
            if dtype == "ScripDividendWithDates":
                xd = parse_date(d.get("xd"))
                for sym, pkey, ikey in ((voting, "votingPropotion", "votingNumSharesIssued"),
                                        (nonvoting, "nonVotingPropotion", "nonVotingNumSharesIssued")):
                    if not sym or d.get(pkey) in (None, ""):
                        continue
                    prop, how = parse_proportion(d.get(pkey))
                    check = scrip_check(d.get(ikey), prop, (shares_out or {}).get(sym)) if prop else None
                    ok = bool(xd and prop and check is not False and (how == "numeric" or check))
                    note = "" if how == "numeric" else f"proportion from field text {str(d.get(pkey)).strip()[:60]!r}"
                    if check is not None:
                        note += ("; " if note else "") + f"share-count check {'passed' if check else 'FAILED'}"
                    if not xd:
                        note += "; no XD date"
                    rows.append(row(rec, sym, "scrip", ex_date=xd, ratio_new=1.0 if prop else None, ratio_held=prop,
                                    factor=(prop + 1) / prop if prop else None,
                                    status="confirmed" if ok else "needs_review", note=note.strip("; ")))
                continue

            # Announced without dates: if no dated record exists within 120 days either side (the
            # undated one can precede it, or follow it, e.g. COMB's post-AGM share-number notice),
            # list it for review (amount/proportion known, ex-date missing) instead of dropping it.
            if dtype in ("ScripDividendToBeNotified", "CashDividendDatesToBeNotified"):
                want = "ScripDividendWithDates" if dtype.startswith("Scrip") else "CashDividendWithDates"
                if rec.date:
                    d0 = dt.date.fromisoformat(rec.date)
                    lo, hi = (d0 - dt.timedelta(days=120)).isoformat(), (d0 + dt.timedelta(days=120)).isoformat()
                else:
                    lo, hi = "", "9999"
                if any((x.detail or {}).get("dType") == want and lo <= x.date <= hi for x in recs):
                    continue
                if dtype.startswith("Scrip"):
                    prop, _ = parse_proportion(d.get("votingPropotion"))
                    if voting and prop:
                        rows.append(row(rec, voting, "scrip", ratio_new=1.0, ratio_held=prop, factor=(prop + 1) / prop,
                                        status="needs_review", note="no dated scrip record found: add ex_date when confirming"))
                else:
                    amt = _num(d.get("votingDivPerShare")) or _num(d.get("divPerShare"))
                    if voting and amt:
                        rows.append(row(rec, voting, "cash_dividend", amount_per_share=amt, status="needs_review",
                                        note="no dated dividend record found: add ex_date when confirming"))
                continue

            # Share sub-division: ratio from share counts (typed); ex-date from the (DATES) record.
            if dtype == "ShareSplits":
                dates = next((x for x in recs if x.source == "general" and "SUB" in x.category.upper()
                              and "DIVISION" in x.category.upper() and x.date >= rec.date), None)
                ex = parse_date((dates.detail or {}).get("tradingCommencement")) if dates else None
                for sym, ek, rk in ((voting, "votingExistingNumOfShares", "votingResultingNumOfShares"),
                                    (nonvoting, "nonVotingExistingNumOfShares", "nonVotingResultingNumOfShares")):
                    e, r_ = _num(d.get(ek)), _num(d.get(rk))
                    if not sym or not e or not r_:
                        continue
                    f = r_ / e
                    ok = ex is not None
                    # The dates record's id is part of the row id, so when the dates arrive later a
                    # new, complete row is appended (the earlier needs_review row is then superseded).
                    rid = f"{rec.aid}+{dates.aid}:{sym}" if dates else f"{rec.aid}:{sym}"
                    rows.append(row(rec, sym, "subdivision", id=rid, ex_date=ex, ratio_new=f - 1, ratio_held=1.0,
                                    factor=f, status="confirmed" if ok else "needs_review",
                                    note="ex-date = trading commencement after the split" if ok
                                    else "split dates not yet announced"))
                continue

            # Rights: price from the typed record, XR from the (DATES) record; ratio is text.
            if rec.source == "general" and "RIGHTS" in cat and d.get("xr"):
                xr = parse_date(d.get("xr"))
                typed = [x for x in recs if (x.detail or {}).get("dType") == "RightsIssue"
                         and x.date <= rec.date and _num(x.detail.get("votingShareConsideration"))]
                price_rec = typed[-1] if typed else None
                for sym, pkey, ckey in ((voting, "votingProportion", "votingShareConsideration"),
                                        (nonvoting, "nonVotingProportion", "nonVotingShareConsideration")):
                    text = d.get(pkey) or ((price_rec.detail or {}).get(
                        "votingShrsPropToBeIssued" if sym == voting else "nonVotingShrsPropToBeIssued") if price_rec else None)
                    if not sym or not text:
                        continue
                    ratio = parse_rights_text(text)
                    s_price = _num((price_rec.detail or {}).get(ckey)) if price_rec else None
                    rows.append(row(rec, sym, "rights", ex_date=xr,
                                    ratio_new=ratio[0] if ratio else None, ratio_held=ratio[1] if ratio else None,
                                    subscription_price=s_price, status="needs_review",
                                    note=f"ratio read from text: {text.strip()[:90]!r}"
                                         + ("" if s_price else "; subscription price not found")))
                continue

            if dtype and dtype not in ("RightsIssue",) and voting:
                rows.append(row(rec, voting, "scrip", status="needs_review",
                                note=f"unrecognised record type {dtype!r} ({rec.category}): check the PDF"))
                continue

            # Anything else CA-like without a structured parser: list it for review, no numbers.
            if any(k in cat for k in ("CAPITALI", "CONSOLIDATION", "BONUS")) and voting:
                rows.append(row(rec, voting, "scrip" if "CONSOLIDATION" not in cat else "subdivision",
                                status="needs_review", note=f"unstructured: {rec.category}"))
    return rows


def load_reviews(path: Path) -> tuple[dict[str, dict], set[str]]:
    """config/corporate_actions_review.yaml -> ({id: corrections}, {rejected ids})."""
    if not path.exists():
        return {}, set()
    data = yaml.safe_load(path.read_text()) or {}
    confirm = {str(k): (v or {}) for k, v in (data.get("confirm") or {}).items()}
    unknown = {k for v in confirm.values() for k in v} - set(CA_COLS)
    if unknown:
        raise ValueError(f"{path}: unknown fields {sorted(unknown)}")
    return confirm, {str(x) for x in (data.get("reject") or [])}


def _base(row_id: str) -> str:
    """'32457+33380:CIC.N0000' -> '32457:CIC.N0000' (first announcement id + symbol)."""
    head, _, sym = row_id.partition(":")
    return head.split("+")[0] + ":" + sym


def superseded(rows: list[dict]) -> set[str]:
    """Ids of needs_review rows replaced by a different, complete row for the same announcement
    and symbol (e.g. a split first seen without dates, later completed by its dates record)."""
    complete: dict[str, set[str]] = {}
    for r in rows:
        if r["status"] != "needs_review" and r["ex_date"]:
            complete.setdefault(_base(r["id"]), set()).add(r["id"])
    return {r["id"] for r in rows
            if r["status"] == "needs_review" and complete.get(_base(r["id"]), set()) - {r["id"]}}


def pending_review(rows: list[dict], reviews: tuple[dict[str, dict], set[str]]) -> list[dict]:
    gone = superseded(rows)
    confirm, reject = reviews
    return [r for r in rows if r["status"] == "needs_review" and r["id"] not in gone
            and r["id"] not in confirm and r["id"] not in reject]


def effective(rows: list[dict], reviews: tuple[dict[str, dict], set[str]]) -> list[dict]:
    """Rows usable for returns: confirmed rows, plus needs_review rows the user confirmed
    (with any corrections applied). Rejected ids are dropped."""
    confirm, reject = reviews
    gone = superseded(rows)
    out = []
    for r in rows:
        r = dict(r)
        if r["id"] in reject or r["id"] in gone:
            continue
        if r["status"] == "needs_review":
            if r["id"] not in confirm:
                continue
            r.update({k: v for k, v in confirm[r["id"]].items()})
            r["status"] = "confirmed (reviewed)"
        out.append(r)
    # One event per (symbol, type, ex-date): an amended or repeated announcement replaces the
    # earlier one instead of being counted twice. The latest announcement id wins.
    latest: dict[tuple, dict] = {}
    for r in sorted(out, key=lambda r: int(r["id"].split(":")[0].split("+")[0])):
        latest[(r["symbol"], r["type"], r["ex_date"])] = r
    out = list(latest.values())
    for r in out:
        for k in ("amount_per_share", "ratio_new", "ratio_held", "factor", "subscription_price"):
            r[k] = _num(r[k]) if r[k] not in (None, "") else None
        if r["type"] in ("subdivision", "scrip") and r["factor"] is None and r["ratio_new"] and r["ratio_held"]:
            r["factor"] = (r["ratio_held"] + r["ratio_new"]) / r["ratio_held"]
        if r["ex_date"] in ("", None):
            raise ValueError(f"corporate action {r['id']} is usable but has no ex_date")
    return out


def share_classes(paths: Paths, securities=None) -> dict[str, list[str]]:
    if securities is None:
        sessions = sorted(p for p in (paths.root / "data" / "raw").iterdir() if (p / "allSecurityCode.json").exists())
        securities = validate(SecurityList, json.loads((sessions[-1] / "allSecurityCode.json").read_bytes()),
                              "allSecurityCode").root
    out: dict[str, list[str]] = {}
    for s in securities:
        if s.symbol.endswith(EQUITY_SUFFIXES):
            out.setdefault(s.symbol.split(".")[0], []).append(s.symbol)
    return out


def shares_outstanding(paths: Paths) -> dict[str, float]:
    """Latest `quantityIssued` per symbol from cached companyInfoSummery responses."""
    out = {}
    for f in sorted((paths.root / "data" / "raw").glob("*/companyInfoSummery/*.json")):
        q = (json.loads(f.read_bytes()).get("reqSymbolInfo") or {}).get("quantityIssued")
        if q:
            out[f.stem] = float(q)
    return out


def rebuild(paths: Paths) -> int:
    rows = parse(load_records(paths), share_classes(paths), shares_outstanding(paths))
    return storage.append(paths.history / "corporate_actions.csv", CA_COLS, rows, key=("id",))


def run(client: CseClient | None = None, root: Path = ROOT, from_date: str = "2025-06-01",
        to_date: str | None = None) -> str:
    paths = Paths(root)
    client = client or CseClient()
    to_date = to_date or dt.date.today().isoformat()
    n = collect(client, paths, from_date, to_date)
    added = rebuild(paths)
    msg = f"corporate actions: {n} CA announcements cached ({from_date}..{to_date}); +{added} rows"
    log_run(paths, "corpactions", "ok", None, msg)
    return msg


if __name__ == "__main__":
    print(run())
