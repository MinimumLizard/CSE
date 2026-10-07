"""Who owns the Colombo Stock Exchange? Parse every collected report and build the ownership graph.

    python -m cse.ownership.analyse        -> data/ownership/*.csv, data/ownership/latest.json

Inputs (all in the repo): data/raw/ownership/reports.csv + text/<id>.txt (the interim reports'
shareholder tables), the latest data/raw/<session>/allSecurityCode.json, companyInfoSummery/
(shares issued per class) and tradeSummary.json (market capitalisation per class),
config/ownership_aliases.yaml and config/ownership_parents.yaml.

Everything here is derived and rebuilt in full on every run, so a parser fix applies to every
report. The append-only record is the report text itself (one file per report, never replaced).

Three views (docs/METHODS.md §10):
1. Direct: what each owner holds in its own name (after the beneficial-owner rules), at market value.
2. Economic look-through: stakes held by listed companies are passed through to *their*
   shareholders, F = D (I - W)^-1, so value is attributed to ultimate owners without double counting.
3. Control: votes are summed by group (a holder plus the companies it controls), the largest block
   >= 20 % is the company's controlling or influential shareholder, > 50 % makes it a group member.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import storage
from ..config import EQUITY_SUFFIXES, ROOT
from ..fetch import Paths, log_run
from ..models import SecurityList, validate
from . import names, parents, parse
from .collect import own_dir

HOLDING_COLS = ["report_id", "period_date", "code", "symbol", "class", "rank", "name_as_filed", "shares", "pct", "fraction",
                "owner_key", "owner_name", "owner_type", "owner_symbol", "via", "rule", "table_status", "source_url"]
COMPANY_COLS = ["code", "name", "report_id", "kind", "period_date", "report_status", "status_N", "note_N",
                "status_X", "note_X", "rows", "top_holders_pct_voting", "market_cap", "controller_key",
                "controller_name", "controller_type", "control_pct", "control_level", "chain", "source_url"]
OWNER_COLS = ["key", "name", "type", "symbol", "holdings", "direct_value", "lookthrough_value",
              "companies_controlled", "controlled_market_cap", "companies_influenced", "names_as_filed"]
CONTROL, INFLUENCE = 50.0, 20.0
USABLE = {"verified", "verified_total"}


@dataclass
class Market:
    session: str
    names: dict[str, str]                       # code -> company name
    issued: dict[str, float]                    # symbol -> shares issued
    mcap: dict[str, float]                      # symbol -> market capitalisation (LKR)
    listed: dict[str, str] = field(default_factory=dict)   # name key -> code

    def value(self, symbol: str, shares: float) -> float | None:
        if self.issued.get(symbol) and self.mcap.get(symbol) is not None:
            return shares * self.mcap[symbol] / self.issued[symbol]
        return None

    def company_cap(self, code: str) -> float:
        return sum(self.mcap.get(code + s, 0.0) or 0.0 for s in EQUITY_SUFFIXES)


def load_market(root: Path) -> Market:
    raw = root / "data" / "raw"
    sessions = sorted(p for p in raw.iterdir() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))
    secs = validate(SecurityList, json.loads((max(p for p in sessions if (p / "allSecurityCode.json").exists())
                                              / "allSecurityCode.json").read_bytes()), "sec").root
    equities = [s for s in secs if s.symbol.endswith(EQUITY_SUFFIXES)]
    issued, mcap = {}, {}
    for sess in sessions:                       # later sessions overwrite earlier ones; within a session the
                                                # day's tradeSummary overwrites companyInfoSummery
        for f in sorted((sess / "companyInfoSummery").glob("*.json")) if (sess / "companyInfoSummery").exists() else []:
            info = json.loads(f.read_bytes()).get("reqSymbolInfo") or {}
            if info.get("quantityIssued"):
                issued[f.stem] = float(info["quantityIssued"])
            if info.get("marketCap") is not None:
                mcap[f.stem] = float(info["marketCap"])
        ts = sess / "tradeSummary.json"
        if ts.exists():
            for r in json.loads(ts.read_bytes()).get("reqTradeSummery") or []:
                if r.get("marketCap"):
                    mcap[r["symbol"]] = float(r["marketCap"])
    session = max(p.name for p in sessions if (p / "tradeSummary.json").exists())
    m = Market(session, {s.symbol.split(".")[0]: s.name for s in sorted(equities, key=lambda s: s.symbol)},
               issued, mcap)
    m.listed = names.listed_index(equities)
    return m


def latest_reports(root: Path) -> dict[str, dict]:
    """code -> the newest collected report (by period, then upload time)."""
    out: dict[str, dict] = {}
    for r in storage.read_rows(own_dir(root) / "reports.csv"):
        cur = out.get(r["code"])
        if cur is None or (r["period_date"], r["uploaded_utc"]) > (cur["period_date"], cur["uploaded_utc"]):
            out[r["code"]] = r
    return out


def choose_tables(text: str, code: str, market: Market) -> dict[str, parse.Table]:
    """Best table per class. A table whose heading names the wrong class is relabelled when its
    implied share total matches the other class's share count (and not its own)."""
    issued = {"N": market.issued.get(code + ".N0000"), "X": market.issued.get(code + ".X0000")}
    tables = parse.find_tables(text)
    for t in tables:
        other = "X" if t.share_class == "N" else "N"
        if issued.get(other):
            parse.validate_table(t, issued.get(t.share_class))
            if t.status != "verified":
                probe = parse.validate_table(parse.Table(other, t.heading, t.line, t.rows,
                                                         stated_total_pct=t.stated_total_pct), issued[other])
                if probe.status == "verified":
                    t.share_class = other
            t.status, t.note = "unverified", ""
    return parse.best_tables(tables, issued)


def build(root: Path = ROOT) -> dict:
    market = load_market(root)
    aliases = names.load_aliases(root / "config" / "ownership_aliases.yaml")
    reports = latest_reports(root)
    all_reports = storage.read_rows(own_dir(root) / "reports.csv")

    holdings: list[dict] = []
    companies: dict[str, dict] = {}
    for code in sorted(market.names):
        rep = reports.get(code)
        companies[code] = c = {k: "" for k in COMPANY_COLS}
        c.update(code=code, name=market.names[code], market_cap=market.company_cap(code),
                 report_status="no_report" if rep is None else rep["status"])
        if rep:
            c.update(report_id=rep["id"], kind=rep["kind"], period_date=rep["period_date"], source_url=rep["url"])
    # Every collected report is parsed (so earlier quarters stay in holdings.csv for tracking);
    # the analysis below uses each company's latest report only.
    for rep in sorted(all_reports, key=lambda r: (r["code"], r["period_date"], r["id"])):
        if rep["status"] != "ok" or rep["code"] not in market.names:
            continue
        text = (own_dir(root) / "text" / f"{rep['id']}.txt").read_text(errors="replace")
        best = choose_tables(text, rep["code"], market)
        latest = reports[rep["code"]]["id"] == rep["id"]
        if latest:
            c = companies[rep["code"]]
            c["report_status"] = "no_table" if not best else "ok"
            for cls in ("N", "X"):
                if cls in best:
                    c[f"status_{cls}"], c[f"note_{cls}"] = best[cls].status, best[cls].note
            c["rows"] = sum(len(t.rows) for t in best.values())
        for cls, t in best.items():
            sym = f"{rep['code']}.{cls}0000"
            for r in t.rows:
                e = entity_for(r["name"], market, aliases)
                holdings.append({"report_id": rep["id"], "period_date": rep["period_date"], "code": rep["code"],
                                 "symbol": sym, "class": cls, "rank": r["rank"], "name_as_filed": r["name"],
                                 "shares": r["shares"], "pct": r["pct"],
                                 "fraction": r["shares"] / t.period_total if t.period_total else None,
                                 "owner_key": e.key, "owner_name": e.name,
                                 "owner_type": e.type, "owner_symbol": e.symbol, "via": e.via, "rule": e.rule,
                                 "table_status": t.status, "source_url": rep["url"]})
    latest_ids = {r["id"] for r in reports.values()}
    current = [h for h in holdings if h["report_id"] in latest_ids and h["table_status"] in USABLE]
    links = {}
    for hk, link in parents.load(root / "config" / "ownership_parents.yaml").items():
        e = entity_for(link.owner, market, aliases)
        links[hk] = {"owner_key": e.key, "owner_name": e.name, "owner_type": link.owner_type or e.type,
                     "owner_symbol": e.symbol, "pct": link.pct, "source": link.source, "quote": link.quote,
                     "holder": link.holder}
    owners, graph = analyse(current, market, companies, links)
    return {"market": market, "holdings": holdings, "companies": companies, "owners": owners, "graph": graph,
            "current": current, "links": links}


def entity_for(name_as_filed: str, market: Market, aliases: dict) -> names.Entity:
    e = names.resolve(name_as_filed, market.listed, aliases)
    if e.type == "listed" and e.symbol:
        return names.Entity("LISTED:" + e.symbol, market.names.get(e.symbol, e.name), "listed", e.symbol, e.via, e.rule)
    return e


def analyse(rows: list[dict], market: Market, companies: dict[str, dict],
            links: dict[str, dict] | None = None) -> tuple[dict[str, dict], dict]:
    """`links`: confirmed owners of unlisted holders (config/ownership_parents.yaml), keyed by the
    holder's entity key. A link with a percentage passes that share of the holder's look-through value
    to its owner; any link with no percentage or > 50 % also passes the holder's votes (control)."""
    links = links or {}
    covered = sorted({r["code"] for r in rows})
    # Direct holdings, aggregated per (owner, company); value at the latest market price.
    direct: dict[tuple[str, str], float] = defaultdict(float)
    votes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    info: dict[str, dict] = {}
    filed: dict[str, Counter] = defaultdict(Counter)
    treasury = []
    for r in rows:
        k = r["owner_key"]
        if r["owner_symbol"] == r["code"]:
            treasury.append((r["code"], r["name_as_filed"]))      # a company in its own register
            continue
        if r["fraction"] is not None and market.mcap.get(r["symbol"]):
            direct[(k, r["code"])] += r["fraction"] * market.mcap[r["symbol"]]
        if r["class"] == "N":
            votes[r["code"]][k] += float(r["pct"])
        info.setdefault(k, {"key": k, "name": r["owner_name"], "type": r["owner_type"], "symbol": r["owner_symbol"]})
        filed[k][r["name_as_filed"]] += 1

    # Economic look-through. Listed holders whose own register is covered pass value through.
    caps = {c: market.company_cap(c) for c in covered}
    through = [c for c in covered if caps[c] > 0]
    idx = {c: i for i, c in enumerate(through)}
    terminal = sorted({k for (k, _c) in direct if not (k.startswith("LISTED:") and k[7:] in idx)})
    tidx = {k: i for i, k in enumerate(terminal)}
    W = np.zeros((len(through), len(through)))
    D = np.zeros((len(terminal), len(through)))
    for (k, c), v in direct.items():
        if c not in idx:
            continue
        frac = v / caps[c]
        if k.startswith("LISTED:") and k[7:] in idx:
            W[idx[k[7:]], idx[c]] += frac
        else:
            D[tidx[k], idx[c]] += frac
    if len(through) and max(abs(np.linalg.eigvals(W))) >= 1:
        raise SystemExit("ownership look-through: cross-holdings form a closed loop (spectral radius >= 1)")
    F = D @ np.linalg.inv(np.eye(len(through)) - W) if len(through) else D
    V = np.array([caps[c] for c in through])
    look0 = dict(zip(terminal, F @ V if len(through) else np.zeros(len(terminal))))
    # Confirmed owners of unlisted holders: pass the stated share of value up the chain.
    look: dict[str, float] = defaultdict(float)

    def push(k: str, val: float, depth: int = 0) -> None:
        link = links.get(k)
        if link and link["pct"] and depth < 20:
            share = val * link["pct"] / 100
            look[k] += val - share
            push(link["owner_key"], share, depth + 1)
        else:
            look[k] += val
    for k, v in look0.items():
        push(k, float(v))
    for link in links.values():
        info.setdefault(link["owner_key"], {"key": link["owner_key"], "name": link["owner_name"],
                                            "type": link["owner_type"], "symbol": link["owner_symbol"]})
    attributed = F.sum(axis=0) if len(through) else np.zeros(0)

    # Control: votes summed by group, iterated to a fixed point.
    ctrl = control(votes, info, links)
    for c, x in ctrl.items():
        comp = companies[c]
        comp.update(controller_key=x["root"], controller_name=info.get(x["root"], {}).get("name", x["root"]),
                    controller_type=info.get(x["root"], {}).get("type", ""), control_pct=round(x["pct"], 4),
                    control_level=x["level"], chain=" <- ".join(x["chain"]))
    for c in covered:
        companies[c]["top_holders_pct_voting"] = round(sum(votes[c].values()), 4) if c in votes else ""

    owners: dict[str, dict] = {k: {**i, "holdings": 0, "direct_value": 0.0, "lookthrough_value": 0.0,
                                   "companies_controlled": 0, "controlled_market_cap": 0.0, "companies_influenced": 0}
                               for k, i in info.items()}
    held: dict[str, set] = defaultdict(set)
    for r in rows:
        held[r["owner_key"]].add(r["code"])
    for k in info:
        owners[k]["holdings"] = len(held[k])
    for (k, c), v in direct.items():
        owners[k]["direct_value"] += v
    for k, v in look.items():
        owners[k]["lookthrough_value"] = float(v)
    for c, x in ctrl.items():
        o = owners.get(x["root"])
        if o is None:
            continue
        if x["level"] == "controlled":
            o["companies_controlled"] += 1
            o["controlled_market_cap"] += caps.get(c, 0.0)
        else:
            o["companies_influenced"] += 1
    for k, o in owners.items():
        o["names_as_filed"] = " | ".join(n for n, _ in filed[k].most_common(5))
    total_cap = sum(market.company_cap(c) for c in market.names)
    graph = {"covered": covered, "covered_cap": float(sum(caps.values())), "total_cap": float(total_cap),
             "attributed_value": float(attributed @ V) if len(through) else 0.0,
             "listed_passthrough": int((W.sum(axis=1) > 0).sum()), "treasury": treasury, "control": ctrl}
    return owners, graph


def control(votes: dict[str, dict[str, float]], info: dict[str, dict],
            links: dict[str, dict] | None = None) -> dict[str, dict]:
    """company -> {root, pct, level, chain}. A holder's votes count for the group it belongs to:
    a listed holder that is itself > 50 % controlled votes with its controller's group, and an
    unlisted holder with a confirmed controlling owner (config/ownership_parents.yaml) votes with it."""
    ctrl: dict[str, dict] = {}
    links = links or {}

    def root(k: str, seen: frozenset = frozenset()) -> str:
        if not k.startswith("LISTED:"):
            link = links.get(k)
            if link and (link["pct"] is None or link["pct"] > 50) and link["owner_key"] not in seen \
                    and link["owner_key"] != k:
                return root(link["owner_key"], seen | {k})
            return k
        x = ctrl.get(k[7:])
        if x and x["level"] == "controlled" and x["root"] not in seen and x["root"] != k:
            return root(x["root"], seen | {k})
        return k

    for _ in range(50):
        new = {}
        for c, hv in votes.items():
            blocks: dict[str, float] = defaultdict(float)
            members: dict[str, list] = defaultdict(list)
            for h, p in hv.items():
                if info.get(h, {}).get("type") == "nominee":
                    continue
                r = root(h, frozenset({"LISTED:" + c}))
                if r == "LISTED:" + c:
                    continue
                blocks[r] += p
                members[r].append((p, h))
            if not blocks:
                continue
            r, p = max(blocks.items(), key=lambda kv: (kv[1], kv[0]))
            if p < INFLUENCE:
                continue
            new[c] = {"root": r, "pct": p, "level": "controlled" if p > CONTROL else "influence",
                      "members": sorted(members[r], reverse=True)}
        if {c: (x["root"], x["level"]) for c, x in new.items()} == {c: (x["root"], x["level"]) for c, x in ctrl.items()}:
            ctrl = new
            break
        ctrl = new
    for c, x in ctrl.items():                  # readable chain: company <- largest member <- ... <- root
        chain, cur, seen = [c], x, {c}
        while cur:
            top = cur["members"][0][1]
            chain.append(info.get(top, {}).get("name", top) if not top.startswith("LISTED:") else top[7:])
            nxt = top[7:] if top.startswith("LISTED:") else None
            cur = ctrl.get(nxt) if nxt and nxt not in seen and ctrl.get(nxt, {}).get("level") == "controlled" else None
            if nxt:
                seen.add(nxt)
        root_name = info.get(x["root"], {}).get("name", x["root"]) if not x["root"].startswith("LISTED:") else x["root"][7:]
        if chain[-1] != root_name:
            chain.append(root_name)
        x["chain"] = chain
    return ctrl


def write(result: dict, root: Path = ROOT) -> str:
    out = root / "data" / "ownership"
    market, companies, owners, graph = result["market"], result["companies"], result["owners"], result["graph"]
    storage.atomic_write_bytes(out / "holdings.csv", (",".join(HOLDING_COLS) + "\n" + storage._serialise(
        HOLDING_COLS, result["holdings"])).encode())
    storage.atomic_write_bytes(out / "companies.csv", (",".join(COMPANY_COLS) + "\n" + storage._serialise(
        COMPANY_COLS, companies.values())).encode())
    ranked = sorted(owners.values(), key=lambda o: -o["direct_value"])
    storage.atomic_write_bytes(out / "owners.csv", (",".join(OWNER_COLS) + "\n" + storage._serialise(
        OWNER_COLS, ranked)).encode())
    status = Counter(c["report_status"] for c in companies.values())
    verified = sum(1 for c in companies.values() if c["status_N"] in USABLE or c["status_X"] in USABLE)
    groups = defaultdict(list)
    for c, x in graph["control"].items():
        groups[x["root"]].append({"code": c, "name": companies[c]["name"], "pct": round(x["pct"], 2),
                                  "level": x["level"], "cap": companies[c]["market_cap"], "chain": x["chain"]})
    summary = {
        "built_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "price_session": market.session, "companies": len(companies), "report_status": dict(status),
        "verified_companies": verified, "covered_cap": graph["covered_cap"], "total_cap": graph["total_cap"],
        "attributed_value": graph["attributed_value"], "listed_passthrough": graph["listed_passthrough"],
        "periods": dict(Counter(c["period_date"] for c in companies.values() if c["period_date"])),
        "treasury": graph["treasury"],
    }
    latest = {"summary": summary,
              "owners": ranked,
              "groups": {k: sorted(v, key=lambda g: -g["cap"]) for k, v in groups.items()},
              "companies": list(companies.values()),
              "holdings": result["current"],
              "links": [{**v, "holder_key": k, "holder_value": owners.get(k, {}).get("lookthrough_value", 0.0)}
                        for k, v in (result.get("links") or {}).items()]}
    storage.atomic_write_bytes(out / "latest.json", json.dumps(latest, separators=(",", ":"), default=float).encode())
    msg = (f"ownership: {verified}/{len(companies)} companies with a verified shareholder table "
           f"({graph['covered_cap'] / graph['total_cap']:.1%} of market cap); {len(owners)} owners; "
           f"report status {dict(status)}")
    log_run(Paths(root), "ownership-analyse", "ok", market.session, msg)
    return msg


def main() -> None:
    print(write(build(ROOT), ROOT))


if __name__ == "__main__":
    sys.exit(main())
