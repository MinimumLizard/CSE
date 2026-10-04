"""Forward-only live record of the model portfolios (docs/METHODS.md §9).

Files (append-only, never restated; every row carries the `series` label):
  record/nav.csv       date, series, portfolio, nav, cash, holdings_value, note
  record/holdings.csv  date, series, portfolio, symbol, shares, price, value, weight, target_weight, flag
  record/trades.csv    date, series, portfolio, symbol, action, shares, price, value, cost, reason

One update per session. If the record already has the session, nothing is written, so a second
run on the same day leaves the files byte-identical.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from . import storage

NAV_COLS = ["date", "series", "portfolio", "nav", "cash", "holdings_value", "note"]
HOLD_COLS = ["date", "series", "portfolio", "symbol", "shares", "price", "value", "weight", "target_weight", "flag"]
TRADE_COLS = ["date", "series", "portfolio", "symbol", "action", "shares", "price", "value", "cost", "reason"]
TRADED = ("min_variance", "risk_parity", "max_sharpe", "equal_weight")
MARKET = "market"
MY_BOOK = "my_book"
INDEX_UNIT = "ASI"


@dataclass
class State:
    shares: dict[str, float]
    targets: dict[str, float]
    flags: set[str]
    cash: float


@dataclass
class Update:
    session: str
    nav: list[dict] = field(default_factory=list)
    holdings: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    status: str = ""


class RecordPaths:
    def __init__(self, root: Path):
        self.dir = root / "record"
        self.nav = self.dir / "nav.csv"
        self.holdings = self.dir / "holdings.csv"
        self.trades = self.dir / "trades.csv"


def band(target: float, cfg: dict) -> float:
    return max(cfg["band_relative"] * target, cfg["band_absolute"])


def is_scheduled(session: str, sessions: list[str], months: list[int]) -> bool:
    """First trading session of a month listed in `rebalance_months`."""
    month = session[:7]
    first = next((s for s in sessions if s[:7] == month), None)
    return first == session and int(session[5:7]) in months


def last_state(rp: RecordPaths, series: str) -> tuple[str | None, dict[str, State], dict[str, float]]:
    navs = [r for r in storage.read_rows(rp.nav) if r["series"] == series]
    if not navs:
        return None, {}, {}
    last = max(r["date"] for r in navs)
    holds = [r for r in storage.read_rows(rp.holdings) if r["series"] == series and r["date"] == last]
    states, navmap = {}, {}
    for r in navs:
        if r["date"] == last:
            states[r["portfolio"]] = State({}, {}, set(), float(r["cash"] or 0))
            navmap[r["portfolio"]] = float(r["nav"])
    for h in holds:
        st = states[h["portfolio"]]
        if float(h["shares"]):
            st.shares[h["symbol"]] = float(h["shares"])
        if h["target_weight"] not in ("", None):
            st.targets[h["symbol"]] = float(h["target_weight"])
        if h["flag"] == "1":
            st.flags.add(h["symbol"])
    return last, states, navmap


class Pricer:
    """Last traded close on or before the session, corrected for share-count events that went
    ex after that close (so a split that hasn't traded yet doesn't inflate the value)."""

    def __init__(self, closes: dict[str, dict[str, float]], actions: list[dict], session: str):
        self.closes, self.session = closes, session
        self.actions = {}
        for a in actions:
            self.actions.setdefault(a["symbol"], []).append(a)

    def last(self, sym: str) -> tuple[str, float] | None:
        series = self.closes.get(sym) or {}
        dates = [d for d in series if d <= self.session]
        if not dates:
            return None
        d = max(dates)
        return d, series[d]

    def price(self, sym: str) -> float | None:
        got = self.last(sym)
        if not got:
            return None
        d, p = got
        for a in self.actions.get(sym, []):
            if d < a["ex_date"] <= self.session:
                if a["type"] in ("subdivision", "scrip") and a.get("factor"):
                    p = p / a["factor"]
                elif a["type"] == "rights":
                    n, m, s = a["ratio_held"], a["ratio_new"], a["subscription_price"]
                    p = (n * p + m * s) / (n + m)
                elif a["type"] == "cash_dividend":
                    p = p - a["amount_per_share"]
        return p


def _buy_shares(value: float, price: float, cost: float) -> int:
    return max(0, math.floor(value / (price * (1 + cost))))


def execute(st: State, targets: dict[str, float], names: list[str], pricer: Pricer, cfg: dict,
            portfolio: str, session: str, series: str, reason: str, min_trade: float) -> list[dict]:
    """Trade `names` to their target weights at today's price. Sells first, then buys limited by cash."""
    c = cfg["cost_per_side"]
    total = st.cash + sum(sh * (pricer.price(s) or 0) for s, sh in st.shares.items())
    sells, buys = [], []
    for s in names:
        p = pricer.price(s)
        if not p:
            continue
        diff = targets.get(s, 0.0) * total - st.shares.get(s, 0) * p
        if abs(diff) < min_trade:
            continue
        (buys if diff > 0 else sells).append((s, p, diff))
    trades = []
    for s, p, diff in sells:
        n = min(st.shares.get(s, 0), round(-diff / p)) if targets.get(s, 0) > 0 else st.shares.get(s, 0)
        if n <= 0:
            continue
        value = n * p
        st.shares[s] = st.shares.get(s, 0) - n
        st.cash += value - value * c
        trades.append({"date": session, "series": series, "portfolio": portfolio, "symbol": s, "action": "sell",
                       "shares": n, "price": p, "value": value, "cost": value * c, "reason": reason})
    for s, p, diff in buys:
        n = _buy_shares(min(diff, st.cash), p, c)
        if n <= 0:
            continue
        value = n * p
        st.shares[s] = st.shares.get(s, 0) + n
        st.cash -= value + value * c
        trades.append({"date": session, "series": series, "portfolio": portfolio, "symbol": s, "action": "buy",
                       "shares": n, "price": p, "value": value, "cost": value * c, "reason": reason})
    st.shares = {s: n for s, n in st.shares.items() if n > 0}
    return trades


def apply_actions(st: State, actions: list[dict], since: str, session: str, cfg: dict, portfolio: str,
                  series: str) -> list[dict]:
    """Corporate actions going ex in (since, session] on shares held at `since`."""
    rows = []
    for a in sorted(actions, key=lambda a: a["ex_date"]):
        s = a["symbol"]
        held = st.shares.get(s, 0)
        if not held or not (since < a["ex_date"] <= session):
            continue
        base = {"date": session, "series": series, "portfolio": portfolio, "symbol": s, "price": None, "cost": 0.0}
        if a["type"] == "cash_dividend":
            cash = held * a["amount_per_share"] * (1 - cfg["dividend_withholding"])
            st.cash += cash
            rows.append({**base, "action": "dividend", "shares": held, "value": cash,
                         "reason": f"ex {a['ex_date']} Rs {a['amount_per_share']:g}/sh net of {cfg['dividend_withholding']:.0%} WHT"})
        elif a["type"] in ("subdivision", "scrip"):
            new = math.floor(held * a["factor"])
            st.shares[s] = new
            rows.append({**base, "action": a["type"], "shares": new - held, "value": 0.0,
                         "reason": f"ex {a['ex_date']} factor {a['factor']:.6g}; fractions dropped"})
        elif a["type"] == "rights":
            entitled = math.floor(held * a["ratio_new"] / a["ratio_held"])
            take = min(entitled, math.floor(max(st.cash, 0) / a["subscription_price"]))
            st.shares[s] = held + take
            st.cash -= take * a["subscription_price"]
            rows.append({**base, "action": "rights", "shares": take, "price": a["subscription_price"],
                         "value": take * a["subscription_price"],
                         "reason": f"ex {a['ex_date']} {a['ratio_new']:g} for {a['ratio_held']:g} @ {a['subscription_price']:g}"
                                   + ("" if take == entitled else f"; {entitled - take} not taken up (cash)")})
    return rows


def _value(st: State, pricer: Pricer) -> tuple[float, dict[str, tuple[float, float]]]:
    vals = {}
    for s, n in st.shares.items():
        p = pricer.price(s) or 0.0
        vals[s] = (p, n * p)
    return sum(v for _, v in vals.values()), vals


def _snapshot(up: Update, st: State, pricer: Pricer, portfolio: str, series: str, cfg: dict, note: str) -> None:
    hv, vals = _value(st, pricer)
    nav = hv + st.cash
    up.nav.append({"date": up.session, "series": series, "portfolio": portfolio, "nav": nav, "cash": st.cash,
                   "holdings_value": hv, "note": note})
    for s in sorted(set(st.shares) | set(st.targets)):
        p, v = vals.get(s, (pricer.price(s), 0.0))
        w = v / nav if nav else 0.0
        t = st.targets.get(s, 0.0)
        flag = abs(w - t) > band(t, cfg)
        up.holdings.append({"date": up.session, "series": series, "portfolio": portfolio, "symbol": s,
                            "shares": st.shares.get(s, 0), "price": p, "value": v, "weight": w,
                            "target_weight": t, "flag": "1" if flag else "0"})


def update(root: Path, series: str, session: str, sessions: list[str], targets: dict[str, dict[str, float]],
           pricer: Pricer, actions: list[dict], aspi: dict[str, float], cfg: dict,
           my_book: dict[str, float] | None = None) -> Update:
    rp = RecordPaths(root)
    last, states, _ = last_state(rp, series)
    up = Update(session)
    if last == session:
        up.status = f"record already has {session} for {series}; nothing written"
        return up
    if last and last > session:
        raise RuntimeError(f"record for {series} is at {last}, after session {session}")
    N = cfg["record_notional_lkr"]
    c = cfg["cost_per_side"]

    if last is None:  # inception
        for p in TRADED:
            st = State({}, dict(targets[p]), set(), float(N))
            up.trades += execute(st, st.targets, list(st.targets), pricer, cfg, p, session, series, "inception", 0.0)
            _snapshot(up, st, pricer, p, series, cfg, "inception")
        units = N / aspi[session]
        up.nav.append({"date": session, "series": series, "portfolio": MARKET, "nav": N, "cash": 0.0,
                       "holdings_value": N, "note": f"inception; {units:.6f} ASPI units"})
        up.holdings.append({"date": session, "series": series, "portfolio": MARKET, "symbol": INDEX_UNIT,
                            "shares": units, "price": aspi[session], "value": N, "weight": 1.0,
                            "target_weight": 1.0, "flag": "0"})
        up.status = f"inception {session}"
    else:
        scheduled = is_scheduled(session, sessions, cfg["rebalance_months"])
        for p in TRADED:
            st = states[p]
            up.trades += apply_actions(st, actions, last, session, cfg, p, series)
            if scheduled:
                st.targets = dict(targets[p])
                names = sorted(set(st.targets) | set(st.shares))
                up.trades += execute(st, st.targets, names, pricer, cfg, p, session, series,
                                     "scheduled rebalance", cfg["min_trade_lkr"])
                note = "scheduled rebalance"
            elif st.flags:
                up.trades += execute(st, st.targets, sorted(st.flags), pricer, cfg, p, session, series,
                                     "drift band", cfg["min_trade_lkr"])
                note = f"drift trades: {', '.join(sorted(st.flags))}"
            else:
                note = ""
            _snapshot(up, st, pricer, p, series, cfg, note)
        mk = states[MARKET]
        units = mk.shares[INDEX_UNIT]
        nav = units * aspi[session]
        up.nav.append({"date": session, "series": series, "portfolio": MARKET, "nav": nav, "cash": 0.0,
                       "holdings_value": nav, "note": ""})
        up.holdings.append({"date": session, "series": series, "portfolio": MARKET, "symbol": INDEX_UNIT,
                            "shares": units, "price": aspi[session], "value": nav, "weight": 1.0,
                            "target_weight": 1.0, "flag": "0"})
        up.status = f"updated {last} → {session}" + (" (scheduled rebalance)" if scheduled else "")
    if my_book:
        st = State({s: float(n) for s, n in my_book.items()}, {}, set(), 0.0)
        hv, vals = _value(st, pricer)
        up.nav.append({"date": session, "series": series, "portfolio": MY_BOOK, "nav": hv, "cash": 0.0,
                       "holdings_value": hv, "note": "comparison only; shares from config"})
        for s, (p, v) in vals.items():
            up.holdings.append({"date": session, "series": series, "portfolio": MY_BOOK, "symbol": s,
                                "shares": st.shares[s], "price": p, "value": v, "weight": v / hv if hv else 0,
                                "target_weight": None, "flag": "0"})
    return up


def commit(root: Path, up: Update) -> dict[str, int]:
    rp = RecordPaths(root)
    plans = [(rp.nav, NAV_COLS, up.nav, ("date", "series", "portfolio")),
             (rp.holdings, HOLD_COLS, up.holdings, ("date", "series", "portfolio", "symbol")),
             (rp.trades, TRADE_COLS, up.trades, None)]
    out = {}
    staged = []
    for path, cols, rows, key in plans:
        content, n = storage.plan_append(path, cols, rows, key=key or cols)
        staged.append((path, content, n))
        out[path.name] = n
    for path, content, n in staged:
        if n:
            storage.atomic_write_bytes(path, content)
    return out


def trade_list(targets: dict[str, float], weights: dict[str, float], names: list[str], prices: dict[str, float],
               median_turnover: dict[str, float], cfg: dict, when: str) -> list[dict]:
    """Trades for `names` scaled to portfolio_size_lkr: (target − current weight) × size."""
    size, c = cfg["portfolio_size_lkr"], cfg["cost_per_side"]
    out = []
    for s in names:
        p = prices.get(s)
        if not p:
            continue
        amount = (targets.get(s, 0.0) - weights.get(s, 0.0)) * size
        if abs(amount) < cfg["min_trade_lkr"]:
            continue
        med = median_turnover.get(s)
        out.append({"symbol": s, "side": "add" if amount > 0 else "trim", "shares": round(abs(amount) / p),
                    "amount": abs(amount), "cost": abs(amount) * c,
                    "x_median_turnover": abs(amount) / med if med else None, "when": when})
    return sorted(out, key=lambda r: -r["amount"])
