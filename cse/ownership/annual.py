"""Who owns the parents? Passages from each company's latest annual report.

    python -m cse.ownership.annual        (resumable; a re-run only fetches reports it hasn't seen)

Annual reports state the company's parent and ultimate parent (LKAS 1 para 138(c)) and often its
ultimate controlling party (LKAS 24), directors' indirect holdings ("through X (Pvt) Ltd") and
related-party descriptions ("a company wholly owned by the Chairman"). The reports are large, so
only the passages that mention these are kept, each with its line number, in
data/raw/ownership/annual/<report id>.json; the PDF stays on cdn.cse.lk (linked).

Nothing here changes the ownership graph. `cse.ownership.parents` turns the passages into
suggestions, and only links you confirm in config/ownership_parents.yaml are used.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

from .. import storage
from ..config import ROOT
from ..fetch import CDN, Paths, log_run
from ..http import USER_AGENT, CseClient
from ..models import ms_to_date
from .collect import companies, own_dir, pdf_to_text

INDEX_COLS = ["id", "code", "symbol", "title", "period_date", "uploaded_utc", "url", "pages", "words",
              "passages", "status", "fetched_utc"]
KEYS = re.compile(
    r"ultimate\s+(?:parent|holding|controlling|beneficial)|parent\s+(?:company|entity|undertaking|enterprise)"
    r"|holding\s+company\s+(?:is|of)|wholly[\s-]+owned\b|indirect\s+holding|indirectly\s+held"
    r"|controlling\s+(?:shareholder|entity|party|interest)|beneficial\s+owner", re.I)
STRONG = re.compile(r"ultimate|wholly[\s-]+owned|indirect", re.I)
WINDOW = 5
MAX_PASSAGES = 60


def passages(text: str) -> list[dict]:
    """Lines around each mention, merged where they overlap. Strong mentions (ultimate parent,
    wholly owned by, indirect holding) are kept first when there are too many."""
    lines = text.splitlines()
    hits = [i for i, ln in enumerate(lines) if KEYS.search(ln)]
    spans: list[list[int]] = []
    for i in hits:
        a, b = max(0, i - WINDOW), min(len(lines), i + WINDOW + 1)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    out = []
    for a, b in spans:
        body = "\n".join(re.sub(r"\s{3,}", "   ", ln.strip()) for ln in lines[a:b] if ln.strip())
        out.append({"line": a + 1, "text": body, "strong": bool(STRONG.search(body))})
    out.sort(key=lambda p: (not p["strong"], p["line"]))
    return sorted(out[:MAX_PASSAGES], key=lambda p: p["line"])


def pick_annual(fin: dict) -> dict | None:
    items = [x for x in (fin.get("infoAnnualData") or []) if x.get("path")]
    return max(items, key=lambda x: (x.get("manualDate") or 0, x.get("uploadedDate") or 0)) if items else None


def run(root: Path = ROOT, client: CseClient | None = None, only: list[str] | None = None) -> str:
    if not shutil.which("pdftotext"):
        raise SystemExit("pdftotext not found: install poppler-utils")
    paths = Paths(root)
    base = own_dir(root)
    index = base / "annual_reports.csv"
    known = {r["id"] for r in storage.read_rows(index)}
    web = requests.Session()
    web.headers["User-Agent"] = USER_AGENT
    comps = companies(paths)
    if only:
        comps = {c: s for c, s in comps.items() if c in only or s in only}
    new, failed = 0, 0
    for i, (code, symbol) in enumerate(sorted(comps.items()), 1):
        saved = base / "financials" / f"{symbol}.json"
        if saved.exists():                          # the weekly collector saves it; no second call
            fin = json.loads(saved.read_bytes())
        else:
            client = client or CseClient()
            fin = client.call("financials", symbol=symbol).data or {}
            storage.atomic_write_bytes(saved, json.dumps(fin).encode())
        rep = pick_annual(fin)
        if not rep or str(rep["id"]) in known:
            continue
        rid = str(rep["id"])
        url = CDN + quote(str(rep["path"]).lstrip("/"), safe="/")
        row = {"id": rid, "code": code, "symbol": symbol, "title": (rep.get("fileText") or "").strip(),
               "period_date": ms_to_date(rep["manualDate"]).isoformat() if rep.get("manualDate") else "",
               "uploaded_utc": dt.datetime.fromtimestamp((rep.get("uploadedDate") or 0) / 1000, dt.timezone.utc)
               .strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(rep.get("uploadedDate"), (int, float)) else "",
               "url": url, "pages": 0, "words": 0, "passages": 0, "status": "",
               "fetched_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        content = None
        for attempt in range(3):
            try:
                r = web.get(url, timeout=300)
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
            ps = passages(text)
            row.update(pages=pages, words=len(text.split()), passages=len(ps),
                       status="ok" if len(text.split()) > 2000 else "no_text")
            storage.atomic_write_bytes(base / "annual" / f"{rid}.json", json.dumps(
                {"id": rid, "code": code, "symbol": symbol, "url": url, "title": row["title"],
                 "period_date": row["period_date"], "passages": ps}, indent=1, ensure_ascii=False).encode())
        storage.append(index, INDEX_COLS, [row], key=("id",))
        known.add(rid)
        new += 1
        if i % 25 == 0:
            print(f"  {i}/{len(comps)} companies, {new} new annual reports", file=sys.stderr)
    msg = f"ownership annual: {len(comps)} companies, {new} new annual reports ({failed} download failures)"
    log_run(paths, "ownership-annual", "ok", None, msg)
    return msg


if __name__ == "__main__":
    print(run())
