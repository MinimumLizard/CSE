"""Map companyProfile sector labels onto the 20 S&P/CSE GICS industry-group indices.

The API's labels are inconsistent (docs/API_NOTES.md Q5): case, commas and spacing vary,
some labels are GICS *sub-industries* or raw GICS codes, and a few are legacy CSE sector
names. Resolution order:
  1. config/sector_overrides.yaml (per symbol; judgement calls, edited by the user)
  2. exact match to an index name after normalising case, punctuation and spacing
  3. LABEL_ALIASES: GICS sub-industry -> its industry group (objective, per GICS)
Anything else is unclassified; callers decide whether that is fatal (Phase 2: it is).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

INDUSTRY_GROUPS = (
    "Energy", "Materials", "Capital Goods", "Commercial & Professional Services", "Transportation",
    "Automobiles & Components", "Consumer Durables & Apparel", "Consumer Services", "Retailing",
    "Food & Staples Retailing", "Food, Beverage & Tobacco", "Household & Personal Products",
    "Health Care Equipment & Services", "Banks", "Diversified Financials", "Insurance",
    "Software & Services", "Telecommunication Services", "Utilities",
    "Real Estate Management & Development",
)


def key(label: str) -> str:
    return re.sub(r"[^A-Z]", "", re.sub(r"^\s*[\d\s-]+", "", label or "").upper())


_BY_KEY = {key(g): g for g in INDUSTRY_GROUPS}

# GICS sub-industry (or sector-level code) -> GICS industry group. Only unambiguous cases.
LABEL_ALIASES = {
    key("Diversified Financial"): "Diversified Financials",
    key("Diversified Financial Services"): "Diversified Financials",
    key("Investment Banking & Brokerage"): "Diversified Financials",
    key("Consumer Finance"): "Diversified Financials",
    key("Property & Casualty Insurance"): "Insurance",
    key("Multi-line Insurance"): "Insurance",
    key("Application Software"): "Software & Services",
    key("Independent Power Producers & Energy Traders"): "Utilities",
    key("Real Estate Development"): "Real Estate Management & Development",
    key("Real Estate (6010)"): "Real Estate Management & Development",
    key("Real Estate Management&Development"): "Real Estate Management & Development",
}


def load_overrides(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    out = {}
    for sym, group in (data.get("overrides") or {}).items():
        if group not in INDUSTRY_GROUPS:
            raise ValueError(f"{path}: {sym} -> {group!r} is not one of the 20 industry groups")
        out[sym] = group
    return out


def classify(symbol: str, label: str | None, overrides: dict[str, str]) -> str | None:
    if symbol in overrides:
        return overrides[symbol]
    k = key(label or "")
    return _BY_KEY.get(k) or LABEL_ALIASES.get(k)
