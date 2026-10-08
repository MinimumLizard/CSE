"""Collect the latest interim report of every listed company, as text.

`python -m cse.ownership.collect` (resumable; a re-run only fetches reports it hasn't seen).

- `financials` (per company) lists report PDFs; the newest quarterly one is used, falling back to
  the newest annual report for companies without quarterly filings.
- PDFs are downloaded to a temporary file and converted with `pdftotext -layout`; only the text
  is kept, in data/raw/ownership/text/<report id>.txt (the PDF stays on cdn.cse.lk, linked).
- data/raw/ownership/reports.csv indexes every report collected (append-only, keyed on id).
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import requests

from .. import storage
from ..config import EQUITY_SUFFIXES, ROOT
from ..fetch import CDN, Paths, log_run
from ..http import USER_AGENT, CseClient
from ..models import SecurityList, ms_to_date, validate

REPORT_COLS = ["id", "code", "symbol", "kind", "title", "period_date", "uploaded_utc", "url", "pages",
               "words", "status", "fetched_utc"]


def own_dir(root: Path) -> Path:
    return root / "data" / "raw" / "ownership"


def companies(paths: Paths) -> dict[str, str]:
    """code -> voting symbol (or the only class), from the latest saved security list."""
    sessions = sorted(p for p in (paths.root / "data" / "raw").iterdir() if (p / "allSecurityCode.json").exists())
    secs = validate(SecurityList, json.loads((sessions[-1] / "allSecurityCode.json").read_bytes()), "sec").root
    out: dict[str, str] = {}
    for s in sorted(secs, key=lambda s: (not s.symbol.endswith(".N0000"), s.symbol)):
        if s.symbol.endswith(EQUITY_SUFFIXES):
            out.setdefault(s.symbol.split(".")[0], s.symbol)
    return out


def pick_report(fin: dict) -> tuple[str, dict] | None:
    for kind, key in (("quarterly", "infoQuarterlyData"), ("annual", "infoAnnualData")):
        items = [x for x in (fin.get(key) or []) if x.get("path")]
        if items:
            return kind, max(items, key=lambda x: (x.get("manualDate") or 0, x.get("uploadedDate") or 0))
    return None


def history_reports(fin: dict, since: str) -> list[tuple[str, dict]]:
    """Every quarterly report with a period date on or after `since` (YYYY-MM-DD), one per period:
    the latest upload wins when a period was filed twice (e.g. a corrected report)."""
    by_period: dict[str, dict] = {}
    for x in (fin.get("infoQuarterlyData") or []):
        if not x.get("path") or not x.get("manualDate"):
            continue
        period = ms_to_date(x["manualDate"]).isoformat()
        if since <= period <= dt.date.today().isoformat():
            cur = by_period.get(period)
            if cur is None or (x.get("uploadedDate") or 0) > (cur.get("uploadedDate") or 0):
                by_period[period] = x
    return [("quarterly", by_period[p]) for p in sorted(by_period)]


def pdf_to_text(content: bytes) -> tuple[str, int]:
    with tempfile.TemporaryDirectory() as d:
        pdf = Path(d) / "r.pdf"
        pdf.write_bytes(content)
        txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, timeout=300)
        info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=60).stdout
    pages = next((int(l.split()[-1]) for l in info.splitlines() if l.startswith("Pages:")), 0)
    return txt.stdout, pages


def run(root: Path = ROOT, client: CseClient | None = None, only: list[str] | None = None,
        since: str | None = None) -> str:
    """Collect each company's latest report; with `since`, also every earlier quarterly report from
    that period on (the ownership history behind data/ownership/changes.csv)."""
    if not shutil.which("pdftotext"):
        raise SystemExit("pdftotext not found: install poppler-utils")
    paths = Paths(root)
    client = client or CseClient()
    web = requests.Session()
    web.headers["User-Agent"] = USER_AGENT
    base = own_dir(root)
    index = base / "reports.csv"
    known = {r["id"] for r in storage.read_rows(index)}
    comps = companies(paths)
    if only:
        comps = {c: s for c, s in comps.items() if c in only or s in only}
    new, failed = 0, 0
    for i, (code, symbol) in enumerate(sorted(comps.items()), 1):
        saved = base / "financials" / f"{symbol}.json"
        if since and saved.exists():                # a history pass re-uses this week's listing
            fin = json.loads(saved.read_bytes())
        else:
            fin = client.call("financials", symbol=symbol).data or {}
            storage.atomic_write_bytes(saved, json.dumps(fin).encode())
        picked = [p for p in [pick_report(fin)] if p] + (history_reports(fin, since) if since else [])
        for kind, rep in picked:
            if str(rep["id"]) in known:
                continue
            new, failed = _fetch_one(base, index, web, code, symbol, kind, rep, new, failed)
            known.add(str(rep["id"]))
        if i % 25 == 0:
            print(f"  {i}/{len(comps)} companies, {new} new reports", file=sys.stderr)
    msg = f"ownership collect: {len(comps)} companies, {new} new reports ({failed} download failures)"
    log_run(paths, "ownership-collect", "ok", None, msg)
    return msg


def _fetch_one(base, index, web, code, symbol, kind, rep, new, failed):
    """Download one report PDF, keep its text, index it. Returns the updated (new, failed) counts."""
    rid = str(rep["id"])
    url = CDN + quote(str(rep["path"]).lstrip("/"), safe="/")
    row = {"id": rid, "code": code, "symbol": symbol, "kind": kind, "title": (rep.get("fileText") or "").strip(),
           "period_date": ms_to_date(rep["manualDate"]).isoformat() if rep.get("manualDate") else "",
           "uploaded_utc": dt.datetime.fromtimestamp((rep.get("uploadedDate") or 0) / 1000, dt.timezone.utc)
           .strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(rep.get("uploadedDate"), (int, float)) else "",
           "url": url, "pages": 0, "words": 0, "status": "",
           "fetched_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    content = None
    for attempt in range(3):
        try:
            r = web.get(url, timeout=120)
            time.sleep(1)
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                content = r.content
                break
        except requests.RequestException:
            pass
        time.sleep(2 ** (attempt + 1))
    if content is None:
        row["status"] = "download_failed"
        failed += 1
    else:
        text, pages = pdf_to_text(content)
        row.update(pages=pages, words=len(text.split()), status="ok" if len(text.split()) > 200 else "no_text")
        storage.atomic_write_bytes(base / "text" / f"{rid}.txt", text.encode())
    storage.append(index, REPORT_COLS, [row], key=("id",))
    new += 1
    return new, failed


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="also collect every quarterly report from this period on (YYYY-MM-DD)")
    print(run(since=ap.parse_args().since))
