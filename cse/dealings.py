"""Directors' share dealings: who bought or sold what, usually disclosed within 1-4 market days.

    python -m cse.dealings              backfill details not yet saved, then rebuild the table
    python -m cse.dealings --offline    rebuild only (the daily job saves new details itself)

Listed companies must announce every dealing by a director in the company's shares, in the
director's own name or through a "relevant interest" account (a company or relative the director
is connected with). The API serves these as structured records (dType "DealingsByDirectors"):
director, nature of directorship, own/related account, and per transaction its type, date,
quantity and price.

Sources, all real API responses kept in the repo:
- data/raw/<session>/getAnnouncementById/<id>.json   saved by the daily fetch for new announcements;
- data/raw/dealings/<id>.json                          the one-off backfill of older notices, whose
  ids come from the per-company announcement lists in data/raw/announcements/byCompany/.

Output (derived, rebuilt in full every run): data/dealings/dealings.csv, one row per transaction.
`side` is buy / sell / other from the free-text transaction type; anything not clearly a purchase
or a sale (gifts, transfers, inheritance, unrecognised wording) is `other` and never counted as
buying or selling.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

from . import storage
from .config import ROOT
from .fetch import CDN, Paths, log_run
from .http import CseClient

COLS = ["announcement_id", "announced", "symbol", "company", "director", "director_role", "account_type",
        "account", "trade_date", "side", "trans_type", "quantity", "price", "value", "lag_days", "url"]
BUY = re.compile(r"pu?r?chas|a[cq]{1,2}ui|\bbuy|bought|subscri|allot\w*\W+rights|rights\W+(?:issue\W+)?(?:allot|subscri)", re.I)
SELL = re.compile(r"\bsale\b|\bsell|\bsold|dispos", re.I)
OTHER = re.compile(r"gift|transfer|inherit|bequest|transmission|donat", re.I)
CATEGORY = re.compile(r"DEALING|RELEVANT INTEREST", re.I)


def side_of(trans_type: str) -> str:
    t = trans_type or ""
    if OTHER.search(t):
        return "other"
    buy, sell = bool(BUY.search(t)), bool(SELL.search(t))
    return "buy" if buy and not sell else "sell" if sell and not buy else "other"


def _date(s: str | None) -> dt.date | None:
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime((s or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def weekdays_between(a: dt.date, b: dt.date) -> int:
    """Market-day lag approximated by weekdays (holidays not removed)."""
    if b < a:
        return -weekdays_between(b, a)
    return sum(1 for k in range(1, (b - a).days + 1) if (a + dt.timedelta(k)).weekday() < 5)


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def parse(aid: str, data: dict) -> list[dict]:
    b = (data or {}).get("reqBaseAnnouncement") or {}
    if b.get("dType") != "DealingsByDirectors" or not b.get("directorTransactions"):
        return []
    docs = (data or {}).get("reqAnnouncementDocs") or []
    url = (CDN + docs[0]["fileUrl"].lstrip("/")).replace(" ", "%20") if docs and docs[0].get("fileUrl") else ""
    announced = _date(b.get("dateOfAnnouncement")) or _date(b.get("dateOfNotification"))
    related = bool(b.get("accTypeRelInterest"))
    director = re.sub(r"\s+", " ", b.get("dircetorsName") or "").strip()
    account = re.sub(r"\s+", " ", b.get("relInterestAccountName") or "").strip() if related else director
    rows = []
    for t in b["directorTransactions"]:
        td = _date(t.get("transactionDate"))
        q, p = _num(t.get("quantity")), _num(t.get("price"))
        rows.append({
            "announcement_id": aid, "announced": announced.isoformat() if announced else "",
            "symbol": (b.get("symbol") or "").strip(), "company": (b.get("companyName") or "").strip(),
            "director": director, "director_role": (b.get("natureOfDir") or "").strip(),
            "account_type": "related" if related else "own", "account": account or director,
            "trade_date": td.isoformat() if td else "", "side": side_of(t.get("transType") or ""),
            "trans_type": (t.get("transType") or "").strip(), "quantity": q, "price": p,
            "value": q * p if q is not None and p is not None else None,
            "lag_days": weekdays_between(td, announced) if td and announced else None, "url": url,
        })
    return rows


def raw_files(root: Path) -> dict[str, Path]:
    """announcement id -> saved detail; the daily job's copy wins over the backfill's."""
    out = {f.stem: f for f in sorted((root / "data" / "raw" / "dealings").glob("*.json"))}
    for f in sorted((root / "data" / "raw").glob("*/getAnnouncementById/*.json")):
        out[f.stem] = f
    return out


def candidate_ids(root: Path) -> set[str]:
    ids = {r["id"] for r in storage.read_rows(Paths(root).announcements) if CATEGORY.search(r["category"])}
    for f in (root / "data" / "raw" / "announcements" / "byCompany").glob("*.json"):
        for a in json.loads(f.read_bytes()).get("reqCompanyAnnouncement") or []:
            if CATEGORY.search(a.get("announcementCategory") or ""):
                ids.add(str(a["announcementId"]))
    return ids


def backfill(root: Path, client: CseClient) -> int:
    have = raw_files(root)
    todo = sorted(candidate_ids(root) - set(have), key=int)
    out = root / "data" / "raw" / "dealings"
    for i, aid in enumerate(todo, 1):
        r = client.call("getAnnouncementById", announcementId=int(aid))
        # 204 / empty bodies are saved too, so a re-run doesn't ask again
        storage.atomic_write_bytes(out / f"{aid}.json", r.raw if r.status == 200 and r.raw else b"{}")
        if i % 100 == 0:
            print(f"  {i}/{len(todo)} dealing notices", file=sys.stderr)
    return len(todo)


def rebuild(root: Path) -> list[dict]:
    rows = []
    for aid, f in raw_files(root).items():
        try:
            data = json.loads(f.read_bytes() or b"{}")
        except json.JSONDecodeError:
            continue
        rows += parse(aid, data)
    rows.sort(key=lambda r: (r["trade_date"] or r["announced"], r["announced"], int(r["announcement_id"])))
    out = root / "data" / "dealings" / "dealings.csv"
    storage.atomic_write_bytes(out, (",".join(COLS) + "\n" + storage._serialise(COLS, rows)).encode())
    return rows


def run(root: Path = ROOT, offline: bool = False) -> str:
    n = 0 if offline else backfill(root, CseClient())
    rows = rebuild(root)
    sides = {s: sum(1 for r in rows if r["side"] == s) for s in ("buy", "sell", "other")}
    msg = (f"dealings: {n} notices fetched; {len(rows)} transactions "
           f"({sides['buy']} buys, {sides['sell']} sells, {sides['other']} other)")
    log_run(Paths(root), "dealings", "ok", None, msg)
    return msg


if __name__ == "__main__":
    print(run(offline="--offline" in sys.argv))
