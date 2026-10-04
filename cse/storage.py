"""Append-only CSV history files with de-duplication and atomic writes.

Existing rows are never modified or reordered: a write is (old bytes + new rows) written to a
temp file and swapped in with os.replace, so a crash can't leave a half-written file.
"""
from __future__ import annotations

import csv
import io
import math
import os
from pathlib import Path
from typing import Iterable, Sequence

PRICE_COLS = ["date", "symbol", "close", "previous_close", "high", "low", "volume",
              "turnover", "turnover_est", "source"]
INDEX_COLS = ["date", "index", "value", "change", "source"]
MARKET_COLS = ["date", "turnover", "volume", "trades", "foreign_buy", "foreign_sell",
               "listed", "traded", "market_cap", "aspi", "spsl20", "astri", "spsl20_tri",
               "per", "pbv", "dy", "source"]
ANNOUNCEMENT_COLS = ["id", "date", "symbol", "company", "title", "category", "url"]
RUN_COLS = ["run_at_utc", "command", "status", "session", "message"]


def fmt(v) -> str:
    """Stable, lossless text for CSV cells: ints without '.0', floats via repr, None as ''."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return repr(round(v, 8))
    return str(v)


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _serialise(cols: Sequence[str], rows: Iterable[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n", extrasaction="raise")
    for r in rows:
        w.writerow({c: fmt(r.get(c)) for c in cols})
    return buf.getvalue()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def plan_append(path: Path, cols: Sequence[str], rows: Iterable[dict], key: Sequence[str]) -> tuple[bytes, int]:
    """Return (full new file content, number of rows added) without touching disk.

    Rows whose key already exists in the file (or earlier in `rows`) are dropped.
    """
    existing = path.read_bytes() if path.exists() else b""
    if existing:
        header = existing.split(b"\n", 1)[0].decode()
        if header.split(",") != list(cols):
            raise ValueError(f"{path}: header {header!r} != expected {','.join(cols)!r}")
    seen = {tuple(r[k] for k in key) for r in read_rows(path)}
    new = []
    for r in rows:
        k = tuple(fmt(r.get(c)) for c in key)
        if k in seen:
            continue
        seen.add(k)
        new.append(r)
    if not new:
        return existing, 0
    head = b"" if existing else (",".join(cols) + "\n").encode()
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    return existing + head + _serialise(cols, new).encode(), len(new)


def append(path: Path, cols: Sequence[str], rows: Iterable[dict], key: Sequence[str]) -> int:
    content, n = plan_append(path, cols, rows, key)
    if n:
        atomic_write_bytes(path, content)
    return n


def last_daily_session(prices_path: Path) -> str | None:
    """Latest session written by the daily job (backfill rows don't count)."""
    dates = [r["date"] for r in read_rows(prices_path) if r["source"] == "daily"]
    return max(dates) if dates else None
