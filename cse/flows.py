"""Money flows: where the big owners' money is moving (docs/METHODS.md §11).

    python -m cse.flows      -> data/flows/latest.json (page input, rebuilt every run, not committed)

Two feeds, joined on the ownership tracker's entities (cse.ownership.names: same normalisation,
your aliases in config/ownership_aliases.yaml):

1. **Dealings** (fast, 1-4 market days behind): directors' trades and trades by their "relevant
   interest" accounts, from data/dealings/dealings.csv. The *actor* is the account that traded:
   the director, or the related company/person named on the notice.
2. **Quarterly list changes** (slow, about 1-2 months behind, but covers holders who aren't
   directors): consecutive verified top-20 lists of the same company and share class
   (data/ownership/holdings.csv). A holder's change is classified from both its share count and its
   fraction of the class:
     - shares and fraction both changed  -> traded (bought / sold);
     - fraction changed, shares didn't   -> diluted or concentrated by others (not a trade);
     - shares changed, fraction didn't   -> corporate action (split, bonus, scrip; not a trade);
     - entered the list                  -> bought at least (its fraction - the previous list's
                                            smallest fraction);
     - left the list                     -> sold at least (its fraction - the new list's smallest).
   Values are the fraction change x the class's latest market capitalisation, so quarters compare
   at one price and splits don't distort them.

Nothing here predicts anything. It shows who moved money where, from public disclosures, as early
as the disclosures allow.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

from . import storage
from .config import ROOT, load_universe
from .ownership import analyse, groups, names

WINDOWS = (30, 90, 365)
LARGE_LKR = 10_000_000


def actor_entity(account: str, market, aliases) -> names.Entity:
    """The ownership entity behind a dealing's account name ('CT Holdings PLC - Common Directors'
    -> C T Holdings PLC; 'Mr X (Spouse)' -> Mr X's spouse is not Mr X, so only the suffix forms
    that name the account holder itself are stripped)."""
    # '<company> - Directors', '<company>- Directors / Shareholders', '<company> - Common Directors'
    name = re.sub(r"\s*[-–(]\s*(?:common\s+)?(?:directors?|shareholders?)"
                  r"(?:\s*[/&,]\s*(?:common\s+)?(?:directors?|shareholders?))*\s*\)?\s*$", "", account, flags=re.I)
    return analyse.entity_for(name.strip(" -,"), market, aliases)


def _f(x) -> float:
    return float(x) if x not in (None, "") else 0.0


GENERIC_ACCOUNT = re.compile(r"^\W*(?:shareholders?|n/?a|nil|none|-+|as per (?:the )?attach\w*|attached|"
                             r"see attach\w*|related part(?:y|ies))\W*$", re.I)


def dealings_rows(root: Path, market, aliases, group_of: dict, group_label: dict) -> list[dict]:
    out = []
    for r in storage.read_rows(root / "data" / "dealings" / "dealings.csv"):
        account = r["account"] if r["account"] and not GENERIC_ACCOUNT.match(r["account"]) else r["director"]
        e = actor_entity(account, market, aliases)
        target_group = group_of.get(r["symbol"], "LISTED:" + r["symbol"])
        actor_group = group_of.get(e.symbol, "LISTED:" + e.symbol) if e.type == "listed" else e.key
        out.append({**r, "actor_key": e.key, "actor": e.name, "actor_type": e.type,
                    "actor_group": actor_group, "actor_group_name": group_label.get(actor_group, e.name),
                    "target_group": target_group, "target_group_name": group_label.get(target_group, r["symbol"]),
                    "in_group": actor_group == target_group, "value": _f(r["value"]),
                    "date": r["trade_date"] or r["announced"]})
    return out


def quarterly_changes(root: Path, market) -> list[dict]:
    rows = [h for h in storage.read_rows(root / "data" / "ownership" / "holdings.csv")
            if h["table_status"] in analyse.USABLE and h["fraction"]]
    uploaded = {r["id"]: r["uploaded_utc"] for r in storage.read_rows(root / "data" / "raw" / "ownership" / "reports.csv")}
    # one table per (symbol, period): the latest upload
    tables: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for h in rows:
        tables[(h["symbol"], h["period_date"])][h["report_id"]].append(h)
    lists: dict[str, list[tuple[str, list[dict]]]] = defaultdict(list)
    for (sym, period), by_rep in tables.items():
        rid = max(by_rep, key=lambda r: uploaded.get(r, ""))
        lists[sym].append((period, by_rep[rid]))
    out = []
    for sym, seq in lists.items():
        seq.sort()
        cap = market.mcap.get(sym) or 0.0
        for (p0, t0), (p1, t1) in zip(seq, seq[1:]):
            gap = (dt.date.fromisoformat(p1) - dt.date.fromisoformat(p0)).days
            if not 40 <= gap <= 130:                    # adjacent quarters only
                continue
            def agg(t):
                d: dict[str, dict] = {}
                for h in t:
                    x = d.setdefault(h["owner_key"], {"shares": 0.0, "frac": 0.0, "name": h["owner_name"],
                                                      "type": h["owner_type"]})
                    x["shares"] += _f(h["shares"])
                    x["frac"] += _f(h["fraction"])
                return d
            a, b = agg(t0), agg(t1)
            cut0, cut1 = min(_f(h["fraction"]) for h in t0), min(_f(h["fraction"]) for h in t1)
            for k in set(a) | set(b):
                x0, x1 = a.get(k), b.get(k)
                ref = x1 or x0
                if x0 and x1:
                    dfrac, dsh = x1["frac"] - x0["frac"], x1["shares"] - x0["shares"]
                    frac_moved = abs(dfrac) > max(2e-5, 1e-3 * x0["frac"])
                    shares_moved = abs(dsh) > 0.5
                    if not frac_moved and not shares_moved:
                        continue
                    kind = ("bought" if dfrac > 0 else "sold") if frac_moved and shares_moved else \
                        ("concentrated" if dfrac > 0 else "diluted") if frac_moved else "corporate action"
                    traded = frac_moved and shares_moved
                elif x1:
                    dfrac, kind, traded = max(0.0, x1["frac"] - cut0), "entered list", True
                else:
                    dfrac, kind, traded = -max(0.0, x0["frac"] - cut1), "left list", True
                out.append({"symbol": sym, "code": sym.split(".")[0], "from": p0, "to": p1, "owner_key": k,
                            "owner": ref["name"], "owner_type": ref["type"], "kind": kind, "traded": traded,
                            "frac_from": x0["frac"] if x0 else None, "frac_to": x1["frac"] if x1 else None,
                            "dfrac": dfrac, "value": dfrac * cap if traded else 0.0})
    return out


def build(root: Path = ROOT) -> dict:
    market = analyse.load_market(root)
    aliases = names.load_aliases(root / "config" / "ownership_aliases.yaml")
    gmap, glabel = groups.group_of(root) if (root / "data" / "ownership" / "companies.csv").exists() else ({}, {})
    group_of = {code: g for code, g in gmap.items()}
    deals = dealings_rows(root, market, aliases, group_of, glabel)
    changes = quarterly_changes(root, market)
    watch = set()
    try:
        watch = {s.split(".")[0] for s in load_universe(root / "config" / "universe.yaml").watchlist}
    except Exception:  # noqa: BLE001 - the page still works without a watchlist
        pass
    asof = max((d["date"] for d in deals if d["date"]), default=dt.date.today().isoformat())
    end = dt.date.fromisoformat(asof)

    def window(days):
        start = (end - dt.timedelta(days=days)).isoformat()
        return [d for d in deals if d["date"] and d["date"] > start and d["side"] in ("buy", "sell")]

    actors = {}
    for w in WINDOWS:
        per: dict[str, dict] = {}
        for d in window(w):
            a = per.setdefault(d["actor_key"], {"key": d["actor_key"], "name": d["actor"], "type": d["actor_type"],
                                                "group": d["actor_group_name"], "buy": 0.0, "sell": 0.0, "n": 0,
                                                "codes": defaultdict(float)})
            v = d["value"] if d["side"] == "buy" else -d["value"]
            a["buy" if v > 0 else "sell"] += abs(v)
            a["n"] += 1
            a["codes"][d["symbol"]] += v
        for a in per.values():
            a["net"] = a["buy"] - a["sell"]
            a["codes"] = dict(sorted(a["codes"].items(), key=lambda kv: -abs(kv[1]))[:6])
        actors[w] = sorted(per.values(), key=lambda a: -abs(a["net"]))

    companies = {}
    for w in WINDOWS:
        per = {}
        for d in window(w):
            c = per.setdefault(d["symbol"], {"symbol": d["symbol"], "company": d["company"], "buy": 0.0, "sell": 0.0,
                                             "buyers": {}, "sellers": {}, "watch": d["symbol"] in watch})
            if d["side"] == "buy":
                c["buy"] += d["value"]
                c["buyers"].setdefault(d["actor_key"], d["actor"])     # one entry per owner, not per spelling
            else:
                c["sell"] += d["value"]
                c["sellers"].setdefault(d["actor_key"], d["actor"])
        for c in per.values():
            c["net"] = c["buy"] - c["sell"]
            c["cluster"] = len(c["buyers"]) >= 2 and c["net"] > 0
            c["buyers"], c["sellers"] = sorted(c["buyers"].values()), sorted(c["sellers"].values())
        companies[w] = sorted(per.values(), key=lambda c: -abs(c["net"]))

    recent = sorted((d for d in deals if d["date"]), key=lambda d: (d["date"], d["announced"]), reverse=True)
    feed = []
    for d in recent[:150]:
        flags = []
        if d["value"] >= LARGE_LKR:
            flags.append("large")
        if d["symbol"] in watch:
            flags.append("watchlist")
        if d["in_group"]:
            flags.append("own group")
        if d["account_type"] == "related":
            flags.append("related account")
        feed.append({k: d[k] for k in ("date", "announced", "lag_days", "symbol", "company", "actor", "actor_type",
                                       "actor_group_name", "director", "account_type", "side", "trans_type",
                                       "quantity", "price", "value", "url")} | {"flags": flags})

    # per-actor detail for the drill-down: every dealing in the last year + quarterly changes
    detail: dict[str, dict] = {}
    for d in deals:
        if not d["date"] or d["date"] <= (end - dt.timedelta(days=365)).isoformat():
            continue
        x = detail.setdefault(d["actor_key"], {"name": d["actor"], "type": d["actor_type"],
                                                "group": d["actor_group_name"], "trades": [], "quarters": []})
        x["trades"].append([d["date"], d["symbol"], d["side"], _f(d["quantity"]), _f(d["price"]), d["value"],
                            d["trans_type"], d["url"]])
    for c in changes:
        if not c["traded"]:
            continue
        x = detail.setdefault(c["owner_key"], {"name": c["owner"], "type": c["owner_type"], "group": c["owner"],
                                                "trades": [], "quarters": []})
        x["quarters"].append([c["to"], c["code"], c["kind"], c["dfrac"], c["value"]])
    q_owner: dict[str, dict] = {}
    for c in changes:
        if not c["traded"]:
            continue
        o = q_owner.setdefault(c["owner_key"], {"key": c["owner_key"], "name": c["owner"], "type": c["owner_type"],
                                                "in": 0.0, "out": 0.0, "codes": defaultdict(float)})
        o["in" if c["value"] > 0 else "out"] += abs(c["value"])
        o["codes"][c["code"]] += c["value"]
    for o in q_owner.values():
        o["net"] = o["in"] - o["out"]
        o["codes"] = dict(sorted(o["codes"].items(), key=lambda kv: -abs(kv[1]))[:6])
    periods = sorted({c["to"] for c in changes})
    lags = [float(d["lag_days"]) for d in deals if d["lag_days"] not in ("", None)]
    return {"asof": asof, "built_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "price_session": market.session, "n_dealings": len(deals),
            "n_trades": sum(1 for d in deals if d["side"] in ("buy", "sell")),
            "lag_median": statistics.median(lags) if lags else None,
            "actors": {str(w): v[:60] for w, v in actors.items()},
            "companies": {str(w): v[:40] for w, v in companies.items()},
            "feed": feed, "detail": detail,
            "quarter_owners": sorted(q_owner.values(), key=lambda o: -abs(o["net"]))[:60],
            "quarter_changes": sorted((c for c in changes if c["traded"]), key=lambda c: -abs(c["value"]))[:80],
            "quarter_periods": periods, "watch": sorted(watch)}


def write(result: dict, root: Path = ROOT) -> str:
    storage.atomic_write_bytes(root / "data" / "flows" / "latest.json",
                               json.dumps(result, separators=(",", ":"), default=float).encode())
    return (f"flows: {result['n_trades']} dealings (as of {result['asof']}), "
            f"{len(result['quarter_changes'])} largest quarterly stake changes")


def render(root: Path, out: Path) -> bool:
    """site/flows.html from data/flows/latest.json; False when there's nothing to show yet."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from .build import METHODS_URL
    path = root / "data" / "flows" / "latest.json"
    if not path.exists():
        return False
    d = json.loads(path.read_bytes())
    built = dt.datetime.strptime(d["built_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)
    html = env.get_template("flows.html.j2").render(
        d=d, d_json=json.dumps(d, separators=(",", ":")).replace("</", "<\\/"),
        built_slt=built.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M"),
        methods_url=METHODS_URL + "#11-money-flows")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return True


if __name__ == "__main__":
    print(write(build(ROOT), ROOT))
