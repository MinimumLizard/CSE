"""Portfolio lab job: `python -m cse.lab` (after `cse.fetch`, before `cse.build`).

Reads only repo files. Writes data/lab/<session>.json and data/lab/latest.json (everything
Panel D shows) and appends the live record (record/*.csv) unless --dry-run.

Outcomes, all recorded in the JSON and data/runs.csv:
  ok          estimates, portfolios and the record updated
  waiting     not enough history yet for `min_history_weeks` (nothing traded)
  infeasible  constraints can't all hold; the message names the binding one (nothing traded)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import corpactions, metrics, optimize, record, storage
from .config import EQUITY_SUFFIXES, ROOT, load_universe
from .estimates import MARKET_INDEX, all_wednesdays, build_tris, estimate, select_universe, trading_sessions
from .fetch import Paths, log_run
from .models import SecurityList, validate
from .sectors import classify, load_overrides

PORTFOLIO_LABELS = {"min_variance": "Minimum variance", "risk_parity": "Risk parity",
                    "max_sharpe": "Maximum Sharpe", "equal_weight": "Equal weight",
                    "market": "Market (ASPI)", "my_book": "My book"}


def load_config(root: Path) -> dict:
    cfg = yaml.safe_load((root / "config" / "portfolio.yaml").read_text())
    required = ["series", "portfolio_size_lkr", "record_notional_lkr", "risk_free_annual", "equity_risk_premium",
                "cost_per_side", "dividend_withholding", "max_weight", "max_sector_weight", "min_history_weeks",
                "min_traded_share", "min_median_turnover_lkr", "participation", "days_to_build",
                "rebalance_months", "band_relative", "band_absolute", "min_trade_lkr", "history_adjusted"]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise ValueError(f"config/portfolio.yaml is missing {missing}")
    cfg.setdefault("my_book", {})
    return cfg


def latest_securities(paths: Paths):
    sessions = sorted(p for p in (paths.root / "data" / "raw").iterdir() if (p / "allSecurityCode.json").exists())
    return validate(SecurityList, json.loads((sessions[-1] / "allSecurityCode.json").read_bytes()),
                    "allSecurityCode").root


def sector_map(paths: Paths, symbols: list[str]) -> tuple[dict[str, str | None], dict[str, str | None]]:
    """symbol -> industry group (None if unclassified), plus the raw labels, from the most recent
    cached companyProfile response for each symbol."""
    overrides = load_overrides(paths.root / "config" / "sector_overrides.yaml")
    labels: dict[str, str | None] = {}
    for d in sorted((paths.root / "data" / "raw").glob("*/companyProfile")):
        for f in d.glob("*.json"):
            info = (json.loads(f.read_bytes()).get("reqComSumInfo") or [{}])
            labels[f.stem] = (info[0] or {}).get("sector") if info else None
    return {s: classify(s, labels.get(s), overrides) for s in symbols}, labels


def _jsonable(x):
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def compute(out: dict, universe, tris, prices, indices, sectors, cfg, session, weeks_all):
    """Estimates, feasibility, the three optimised portfolios, equal weight and the frontier.
    Fills `out` for the page; raises optimize.Infeasible naming the binding constraint."""
    est = estimate(universe, tris, prices, indices, sectors, cfg, session, weeks_all)
    prob = optimize.make_problem(est, cfg)
    out.update(stocks=[{"symbol": s, "sector": est.sectors[s], "er": est.er[s], "vol": est.vol[s], "beta": est.beta[s],
                        "beta_current": est.beta_parts.loc[s, "b0"], "beta_lag": est.beta_parts.loc[s, "b1"],
                        "amihud": est.amihud[s], "median_turnover": est.median_turnover[s],
                        "turnover_source": est.turnover_source[s], "cap": float(c), "liq_cap": float(lc)}
                       for s, c, lc in zip(prob.symbols, prob.caps, prob.liq_caps)],
               weeks=[est.weeks[0], est.weeks[-1]], n_weeks=len(est.weeks), shrinkage=est.shrinkage, rf=est.rf)
    optimize.check_feasible(prob)
    w = {"min_variance": optimize.min_variance(prob), "risk_parity": optimize.risk_parity(prob),
         "max_sharpe": optimize.max_sharpe(prob)}
    for name, wv in w.items():
        rep = optimize.constraint_report(wv, prob)
        if not rep["ok"]:
            raise RuntimeError(f"{name} violates constraints: {rep}")
    w["equal_weight"] = np.full(len(prob.symbols), 1 / len(prob.symbols))
    out["portfolios"] = {k: {**optimize.summary(PORTFOLIO_LABELS[k], v, prob),
                             "constraints": optimize.constraint_report(v, prob) if k != "equal_weight" else None}
                         for k, v in w.items()}
    out["frontier"] = optimize.frontier(prob)
    return est, prob, w


PREVIEW_MIN_WEEKS = 26


def run(root: Path = ROOT, dry_run: bool = False, out_dir: Path | None = None,
        min_history_override: int | None = None) -> dict:
    paths = Paths(root)
    cfg = load_config(root)
    if min_history_override:
        cfg["min_history_weeks"] = min_history_override
    uni_cfg = load_universe(root / "config" / "universe.yaml")
    prices = metrics.load_prices(paths.prices)
    indices = metrics.load_indices(paths.indices)
    sessions = trading_sessions(indices)
    session = storage.last_daily_session(paths.prices) or sessions[-1]
    securities = latest_securities(paths)
    equities = [s.symbol for s in securities if s.symbol.endswith(EQUITY_SUFFIXES)]

    if not dry_run:
        corpactions.rebuild(paths)            # picks up CA announcements the daily job saw
    ca_rows = storage.read_rows(paths.history / "corporate_actions.csv")
    reviews = corpactions.load_reviews(root / "config" / "corporate_actions_review.yaml")
    actions = corpactions.effective(ca_rows, reviews)
    needs_review = [{**r, "affects_holdings": False} for r in corpactions.pending_review(ca_rows, reviews)]

    sectors, labels = sector_map(paths, equities)
    tris = build_tris(prices, actions, equities, cfg["dividend_withholding"], cfg["history_adjusted"])
    weeks_all = all_wednesdays(sessions, session)
    universe = select_universe(securities, prices, sessions, tris, sectors, set(uni_cfg.excluded), cfg,
                               session, weeks_all)
    max_weeks = int(universe.stats["weeks"].max()) if not universe.stats.empty else 0

    out = {"session": session, "series": cfg["series"] + ("-dryrun" if dry_run else ""),
           "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "config": {k: v for k, v in cfg.items() if k != "my_book"}, "market_proxy": "ASPI (price index)",
           "universe": universe.symbols, "dropped": universe.dropped,
           "needs_review": needs_review, "actions_used": len(actions),
           "labels": PORTFOLIO_LABELS, "weeks_available": max_weeks}

    def finish(status: str, message: str) -> dict:
        out.update(status=status, message=message)
        if not dry_run or out_dir:
            d = out_dir or (root / "data" / "lab")
            d.mkdir(parents=True, exist_ok=True)
            body = json.dumps(_jsonable(out), indent=1, sort_keys=False).encode()
            storage.atomic_write_bytes(d / f"{session}.json", body)
            storage.atomic_write_bytes(d / "latest.json", body)
        if not dry_run:
            log_run(paths, "lab", status, session, message)
        return out

    closes = {s: g.set_index("date")["close"].astype(float).to_dict() for s, g in prices.groupby("symbol")}
    pricer = record.Pricer(closes, actions, session)
    aspi = indices[indices["index"] == MARKET_INDEX].set_index("date")["value"].astype(float).to_dict()
    series = out["series"]

    def value_only(status: str, message: str) -> dict:
        """No valid targets today: still value an existing record and book its corporate actions."""
        up = record.update(root, series, session, sessions, None, pricer, actions, aspi, cfg, cfg.get("my_book"))
        out["record_status"] = up.status
        if not dry_run and up.nav:
            out["record_written"] = record.commit(root, up)
        return finish(status, message + (f" Record: {up.status}." if up.nav else ""))

    if len(universe.symbols) < 2:
        need = cfg["min_history_weeks"]
        if max_weeks >= PREVIEW_MIN_WEEKS:
            # Preview on the history that exists. Nothing is traded or recorded from it.
            pcfg = {**cfg, "min_history_weeks": max_weeks}
            puni = select_universe(securities, prices, sessions, tris, sectors, set(uni_cfg.excluded), pcfg,
                                   session, weeks_all)
            out["preview"] = {"weeks": max_weeks, "universe": len(puni.symbols)}
            try:
                compute(out, puni, tris, prices, indices, sectors, pcfg, session, weeks_all)
            except optimize.Infeasible as exc:
                out["preview"]["infeasible"] = str(exc)
        return value_only("waiting", f"universe has {len(universe.symbols)} stocks: the longest history is "
                                     f"{max_weeks} weekly returns and {need} are required (decision D1). "
                                     "The daily job adds one per week.")

    try:
        est, prob, w = compute(out, universe, tris, prices, indices, sectors, cfg, session, weeks_all)
    except optimize.Infeasible as exc:
        return value_only("infeasible", str(exc))
    targets = {k: {s: float(x) for s, x in zip(prob.symbols, v) if x > 1e-6} for k, v in w.items()}

    pre_weights = record.last_weights(root, series)
    up = record.update(root, series, session, sessions, targets, pricer, actions, aspi, cfg, cfg.get("my_book"))
    out["record_status"] = up.status
    if not dry_run:
        out["record_written"] = record.commit(root, up)

    # Trade lists at portfolio_size_lkr: today's executed trades and names flagged for the next close.
    held = {}
    for h in up.holdings:
        held.setdefault(h["portfolio"], {})[h["symbol"]] = h
    lists = {}
    for p in record.TRADED:
        rows = held.get(p, {})
        weights = {s: float(r["weight"]) for s, r in rows.items()}
        tgt = {s: float(r["target_weight"] or 0) for s, r in rows.items()}
        flagged = sorted(s for s, r in rows.items() if r["flag"] == "1")
        px = {s: r["price"] for s, r in rows.items() if r["price"]}
        today = [t for t in up.trades if t["portfolio"] == p and t["action"] in ("buy", "sell")]
        lists[p] = {"executed_today": [{"symbol": t["symbol"], "side": "add" if t["action"] == "buy" else "trim",
                                        "reason": t["reason"]} for t in today],
                    "next_close": record.trade_list(tgt, weights, flagged, px, est.median_turnover.to_dict(),
                                                    cfg, "next close")}
        if today and up.status.startswith(("inception", "updated")):
            pre = {} if up.status.startswith("inception") else pre_weights.get(p, {})
            lists[p]["today_scaled"] = record.trade_list(tgt, pre, sorted({t["symbol"] for t in today}), px,
                                                         est.median_turnover.to_dict(), cfg, "today")
    out["trade_lists"] = lists
    held_now = {h["symbol"] for h in up.holdings if h["portfolio"] in record.TRADED and float(h["shares"] or 0) > 0}
    for r in out["needs_review"]:
        # Only actions the record can still book matter here: ex-date today or later, or unknown.
        # Past ones affect the estimates only (and are corrected at the next re-estimation).
        r["affects_holdings"] = r["symbol"] in held_now and (not r["ex_date"] or r["ex_date"] >= session)
    return finish("ok", f"{len(universe.symbols)} stocks, {len(est.weeks)} weekly returns; {up.status}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="compute everything, write nothing to record/ or data/lab/")
    ap.add_argument("--out", type=Path, help="with --dry-run: write the JSON here instead")
    ap.add_argument("--min-history-weeks", type=int, help="override for a dry run (never use for the live record)")
    a = ap.parse_args()
    if a.min_history_weeks and not a.dry_run:
        raise SystemExit("--min-history-weeks is only allowed with --dry-run")
    try:
        res = run(dry_run=a.dry_run, out_dir=a.out, min_history_override=a.min_history_weeks)
    except Exception as exc:
        if not a.dry_run:
            log_run(Paths(ROOT), "lab", "failed", None, f"{type(exc).__name__}: {exc}")
        raise
    print(f"{res['status']}: {res['message']}")


if __name__ == "__main__":
    main()
