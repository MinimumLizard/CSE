"""Load config/universe.yaml and resolve every symbol against the API's security list."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import Security

ROOT = Path(__file__).resolve().parents[1]
EQUITY_SUFFIXES = (".N0000", ".X0000")  # voting, non-voting (docs/API_NOTES.md §3)


class SymbolResolutionError(SystemExit):
    """Raised (and exits) when a configured symbol is not in allSecurityCode."""


@dataclass(frozen=True)
class Universe:
    groups: dict[str, list[str]]           # display group -> symbols, in config order
    excluded: list[str]
    gainers_losers_min_turnover_lkr: float = 1_000_000
    extra: dict = field(default_factory=dict)

    @property
    def watchlist(self) -> list[str]:
        seen: dict[str, None] = {}
        for syms in self.groups.values():
            for s in syms:
                seen.setdefault(s, None)
        return list(seen)

    @property
    def all_symbols(self) -> list[str]:
        return list(dict.fromkeys(self.watchlist + self.excluded))


def load_universe(path: Path | None = None) -> Universe:
    path = path or ROOT / "config" / "universe.yaml"
    cfg = yaml.safe_load(path.read_text()) or {}
    wl = cfg.get("watchlist") or []
    # Accept the brief's flat list or a mapping of display groups.
    groups = {"WATCHLIST": list(wl)} if isinstance(wl, list) else {str(k): list(v or []) for k, v in wl.items()}
    display = cfg.get("display") or {}
    return Universe(
        groups=groups,
        excluded=list(cfg.get("excluded") or []),
        gainers_losers_min_turnover_lkr=float(display.get("gainers_losers_min_turnover_lkr", 1_000_000)),
    )


def resolve(universe: Universe, securities: list[Security]) -> dict[str, Security]:
    """Map every configured symbol to its Security. Stop, naming the symbol, if one is unknown.

    No fuzzy matching and no guessed replacements, by design.
    """
    by_symbol = {s.symbol: s for s in securities}
    missing = [s for s in universe.all_symbols if s not in by_symbol]
    if missing:
        raise SymbolResolutionError(
            "config/universe.yaml: symbol(s) not found in the CSE security list (allSecurityCode): "
            + ", ".join(missing)
            + ". Check the exact symbol including suffix, e.g. LOLC.N0000."
        )
    return {s: by_symbol[s] for s in universe.all_symbols}
