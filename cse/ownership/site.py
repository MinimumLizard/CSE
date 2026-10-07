"""site/ownership.html from data/ownership/latest.json (written by `python -m cse.ownership.analyse`)."""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..build import METHODS_URL
from ..config import ROOT

TYPE_LABEL = {"individual": "Individual", "joint": "Joint holders", "estate": "Estate", "trust": "Trust",
              "institution": "Institution / fund", "company": "Unlisted company", "listed": "Listed company",
              "nominee": "Unidentified"}
SLT = dt.timezone(dt.timedelta(hours=5, minutes=30))
TOP = 75


def f_bn(v) -> str:
    return "—" if v is None else f"{v / 1e9:,.1f}"


def f_pct(v, dp: int = 2) -> str:
    return "—" if v is None or v == "" else f"{float(v):.{dp}f}%"


def context(root: Path = ROOT) -> dict | None:
    path = root / "data" / "ownership" / "latest.json"
    if not path.exists():
        return None
    d = json.loads(path.read_bytes())
    s, owners, companies = d["summary"], d["owners"], {c["code"]: c for c in d["companies"]}
    cap = {c: float(x["market_cap"] or 0) for c, x in companies.items()}

    by_owner: dict[str, list] = defaultdict(list)
    rows_by_co: dict[str, list] = defaultdict(list)
    for h in d["holdings"]:
        frac = h["fraction"] or 0.0
        by_owner[h["owner_key"]].append((h["code"], h["class"], float(h["pct"]), frac))
        rows_by_co[h["code"]].append(h)

    def holdings_of(key: str, n: int = 5) -> list[dict]:
        agg: dict[tuple, float] = defaultdict(float)
        for code, cls, pct, _f in by_owner[key]:
            agg[(code, cls)] += pct
        top = sorted(agg.items(), key=lambda kv: -kv[1] * cap.get(kv[0][0], 0))[:n]
        return [{"code": c + ("" if cls == "N" else " (X)"), "pct": p} for (c, cls), p in top]

    for o in owners:
        o["label"] = TYPE_LABEL.get(o["type"], o["type"])
        o["top"] = holdings_of(o["key"])
        o["more"] = max(0, len({c for c, *_ in by_owner[o["key"]]}) - 5)
    ultimate = sorted((o for o in owners if o["type"] != "listed" and o["lookthrough_value"] > 0),
                      key=lambda o: -o["lookthrough_value"])
    direct = sorted(owners, key=lambda o: -o["direct_value"])

    # Who ends up owning the covered market, by owner type (look-through), and what stays unattributed.
    covered = s["covered_cap"]
    by_type: dict[str, float] = defaultdict(float)
    for o in ultimate:
        by_type[o["type"]] += o["lookthrough_value"]
    stuck = sum(o["lookthrough_value"] for o in owners if o["type"] == "listed")  # listed holders without a verified register
    mix = [{"t": t, "type": TYPE_LABEL.get(t, t), "value": v, "share": v / covered} for t, v in
           sorted(by_type.items(), key=lambda kv: -kv[1])]
    if stuck:
        mix.append({"t": "listed", "type": "Listed holder, register not verified", "value": stuck,
                    "share": stuck / covered})
    rest = covered - sum(m["value"] for m in mix)
    mix.append({"t": "rest", "type": "Outside the top-20 lists (public, smaller holders)", "value": rest,
                "share": rest / covered})

    owner_by_key = {o["key"]: o for o in owners}
    groups = []
    for key, members in d["groups"].items():
        o = owner_by_key.get(key, {"name": key, "type": "", "label": ""})
        ctl = [m for m in members if m["level"] == "controlled"]
        inf = [m for m in members if m["level"] != "controlled"]
        parent = None
        if key.startswith("LISTED:") and companies.get(key[7:], {}).get("controller_key"):
            pc = companies[key[7:]]
            parent = {"name": pc["controller_name"], "pct": pc["control_pct"], "level": pc["control_level"]}
        groups.append({"key": key, "name": o["name"], "label": o.get("label", ""), "controlled": ctl,
                       "influenced": inf, "cap": sum(m["cap"] for m in ctl), "parent": parent})
    groups.sort(key=lambda g: (-g["cap"], g["name"]))
    influence = sorted(({**m, "holder": g["name"], "label": g["label"]} for g in groups if not g["controlled"]
                        for m in g["influenced"]), key=lambda m: -m["cap"])
    groups = [g for g in groups if g["controlled"]]

    lookup = {}
    for code, c in sorted(companies.items()):
        lookup[code] = {
            "n": c["name"], "cap": cap[code], "st": c["report_status"], "sn": c["status_N"], "nn": c["note_N"],
            "sx": c["status_X"], "nx": c["note_X"], "p": c["period_date"], "u": c["source_url"],
            "ctl": c["controller_name"], "lvl": c["control_level"], "cp": c["control_pct"], "ch": c["chain"],
            "r": [[h["class"], h["rank"], h["name_as_filed"], h["owner_name"], h["owner_type"], h["owner_symbol"],
                   h["via"], h["pct"]] for h in rows_by_co.get(code, [])],
        }
    built = dt.datetime.strptime(s["built_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    status = s["report_status"]
    return {"s": s, "ultimate": ultimate[:TOP], "direct": direct[:TOP], "groups": groups, "influence": influence,
            "mix": mix,
            "covered_share": covered / s["total_cap"] if s["total_cap"] else 0,
            "built_slt": built.astimezone(SLT).strftime("%Y-%m-%d %H:%M"),
            "periods": sorted(s["periods"].items(), key=lambda kv: -kv[1]),
            "no_text": status.get("no_text", 0), "no_table": status.get("no_table", 0),
            "n_unverified": sum(1 for c in companies.values() if c["report_status"] == "ok"
                                and c["status_N"] not in ("verified", "verified_total")
                                and c["status_X"] not in ("verified", "verified_total")),
            "lookup_json": json.dumps(lookup, separators=(",", ":")), "type_label": TYPE_LABEL,
            "methods_url": METHODS_URL + "#10-ownership-tracker"}


def render(ctx: dict, out: Path) -> None:
    env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "templates"),
                      autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)
    env.filters.update(bn=f_bn, pct=f_pct)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(env.get_template("ownership.html.j2").render(**ctx))


def main() -> None:
    ctx = context(ROOT)
    if ctx is None:
        raise SystemExit("no data/ownership/latest.json; run `python -m cse.ownership.analyse` first")
    out = ROOT / "site" / "ownership.html"
    render(ctx, out)
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
