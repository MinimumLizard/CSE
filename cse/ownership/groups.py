"""Ownership groups for the portfolio lab's group exposure cap (docs/METHODS.md §10.5).

A stock's group is found by following its largest voting block upward:

  company → largest voting block holder (≥ 20 %, not a fund/institution or unidentified nominee)
          → if that holder is itself a listed company, repeat from it
          → stop at a holder that isn't listed, at a listed company with no such block, or at a loop.

Two stocks are in the same group when they end at the same holder. This is deliberately wider than
the page's "control groups" (> 50 %): a 43 % holder such as Milford Exports in Melstacorp decides
that company in practice, and for a risk cap grouping too widely is the safe error. Funds and other
institutions are not group heads, because holding 20 % of several companies doesn't make them move
together.

Input: data/ownership/companies.csv (committed by the weekly ownership run), so every group is
reproducible from the repo. A company with no verified shareholder table is its own group.
"""
from __future__ import annotations

from pathlib import Path

from .. import storage

NOT_HEADS = {"institution", "nominee"}


def chains(root: Path) -> dict[str, list[str]]:
    """company code -> [code, next holder key, ...]; the last element is the group head."""
    rows = {r["code"]: r for r in storage.read_rows(root / "data" / "ownership" / "companies.csv")}
    out: dict[str, list[str]] = {}
    for code in rows:
        path, cur = [code], code
        while True:
            r = rows.get(cur)
            if (not r or r["control_level"] not in ("controlled", "influence")
                    or r["controller_type"] in NOT_HEADS or not r["controller_key"]):
                break
            k = r["controller_key"]
            if k.startswith("LISTED:"):
                nxt = k[7:]
                if nxt in path:
                    break                      # cross-holding loop: stop at the last company reached
                path.append(nxt)
                cur = nxt
                continue
            path.append(k)
            break
        out[code] = path
    return out


def group_of(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(company code -> group key, group key -> display name)."""
    rows = {r["code"]: r for r in storage.read_rows(root / "data" / "ownership" / "companies.csv")}
    names: dict[str, str] = {}
    for r in rows.values():
        if r["controller_key"]:
            names.setdefault(r["controller_key"], r["controller_name"])
    groups, labels = {}, {}
    for code, path in chains(root).items():
        head = path[-1]
        key = head if head not in rows else "LISTED:" + head
        groups[code] = key
        labels[key] = rows[head]["name"] if head in rows else names.get(head, head)
    return groups, labels


def for_symbols(root: Path, symbols: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """symbol -> group key (both share classes of a company share its group), plus labels.
    Without ownership data both are empty, and the optimiser applies no group constraint."""
    if not (root / "data" / "ownership" / "companies.csv").exists():
        return {}, {}
    groups, labels = group_of(root)
    out = {s: groups.get(s.split(".")[0], "LISTED:" + s.split(".")[0]) for s in symbols}
    return out, labels
