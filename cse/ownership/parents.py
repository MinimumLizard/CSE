"""Owners of unlisted shareholders: evidence, suggestions, and the confirmed links the graph uses.

    python -m cse.ownership.parents       -> docs/OWNERSHIP_PARENTS_EVIDENCE.md

The tracker's chains stop at unlisted holders (Milford Exports, Odeon Holdings...). Annual reports
often say who stands behind them: "the ultimate parent is X", "the ultimate beneficial owner of the
Company is Mr. Y", "Z (a company wholly owned by the Chairman, Mr. W)". `cse.ownership.annual` keeps
those passages. This module

1. lists, for every unlisted holder that is a listed company's largest voting block, the passages
   from that company's annual report, with the statements a pattern recognised (suggestions only);
2. loads config/ownership_parents.yaml: the links YOU confirmed, each with its source and quote.
   Only confirmed links change the graph (cse.ownership.analyse).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .. import storage
from ..config import ROOT
from . import names
from .annual import passages

NAME = r"(?P<x>(?:Mr|Mrs|Ms|Miss|Dr)\.?\s+[A-Z][A-Za-z.\s]{1,60}?|[A-Z][\w&'’().,\- ]{2,90}?)"
END = r"(?=\s*(?:[,;]|\.\s|\.$|\(|\swhich\b|\sa\s|\sincorporated\b|\sholds\b|\sand\s+the\b|$))"
PATTERNS = [
    ("ultimate parent", re.compile(r"ultimate\s+parent(?:\s+(?:company|entity|undertaking|enterprise))?"
                                   r"(?:\s+(?:of|for)\s+(?:the\s+)?[^,.]{0,60}?)?\s+is\s+(?:the\s+)?" + NAME + END, re.I)),
    ("immediate and ultimate parent", re.compile(r"immediate\s+and\s+ultimate\s+parent(?:\s+\w+)?(?:\s+of\s+[^,.]{0,60}?)?"
                                                 r"\s+is\s+" + NAME + END, re.I)),
    ("ultimate controlling party", re.compile(r"ultimate\s+(?:controlling\s+(?:party|entity|shareholder)|beneficial\s+owner)"
                                              r"(?:\s+of\s+(?:the\s+)?[^,.]{0,60}?)?\s+(?:is|are)\s+" + NAME + END, re.I)),
    ("parent", re.compile(r"(?<!ultimate\s)(?<!immediate\s)parent\s+(?:company|entity|undertaking|enterprise)"
                          r"(?:\s+of\s+(?:the\s+)?[^,.]{0,60}?)?\s+is\s+(?:the\s+)?" + NAME + END, re.I)),
    ("wholly owned by", re.compile(r"(?P<e>[A-Z][\w&'’().,\- ]{2,80}?)\s*\(?\s*(?:a|which\s+is\s+a)\s+company\s+wholly[\s-]+owned"
                                   r"\s+by\s+(?:the\s+)?(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s*,\s*)?"
                                   r"(?P<x>(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z][A-Za-z.\s]{1,40}?)" + END, re.S)),
    ("indirect holding through", re.compile(r"Indirect\s+Holding\s*\(?\s*through\s+"
                                            r"(?P<e>[^\n]{3,80}?(?:Ltd|Limited|PLC|Company|LLC|Inc))\.?\)", re.I)),
]


@dataclass(frozen=True)
class Link:
    holder: str          # the unlisted holder, as the tracker names it
    owner: str           # who owns or controls it
    owner_type: str
    pct: float | None    # % of the holder owned; None = control stated without a percentage
    source: str
    quote: str


def load(path: Path) -> dict[str, Link]:
    """holder key -> confirmed link, from config/ownership_parents.yaml."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    out = {}
    for holder, spec in (data.get("parents") or {}).items():
        spec = spec or {}
        for req in ("owner", "source", "quote"):
            if not spec.get(req):
                raise ValueError(f"config/ownership_parents.yaml: '{holder}' needs '{req}'")
        pct = spec.get("pct")
        if pct is not None and not 0 < float(pct) <= 100:
            raise ValueError(f"config/ownership_parents.yaml: '{holder}' pct must be in (0, 100]")
        out[names.key_of(holder)] = Link(holder, spec["owner"], spec.get("owner_type") or "",
                                         None if pct is None else float(pct), spec["source"], spec["quote"])
    return out


def statements(text: str) -> list[tuple[str, str, str]]:
    """(kind, subject or '', named owner) recognised in a passage. Lines are joined with spaces, so
    a two-column layout can garble a statement; the evidence sheet always shows the passage too."""
    flat = re.sub(r"\s+", " ", text)
    found = []
    for kind, pat in PATTERNS:
        for m in pat.finditer(flat):
            x = re.sub(r"\s+", " ", m.groupdict().get("x") or "").strip(" .,")
            e = re.sub(r"\s+", " ", m.groupdict().get("e") or "").strip(" .,(")
            if kind == "indirect holding through":
                x, e = "", e
            if len(x) > 2 or e:
                found.append((kind, e, x))
    return list(dict.fromkeys(found))


def evidence(root: Path = ROOT) -> str:
    base = root / "data" / "raw" / "ownership"
    annual = {}
    for r in storage.read_rows(base / "annual_reports.csv"):
        cur = annual.get(r["code"])
        if r["status"] == "ok" and (cur is None or r["period_date"] > cur["period_date"]):
            annual[r["code"]] = r
    interim = {}
    for r in storage.read_rows(base / "reports.csv"):
        cur = interim.get(r["code"])
        if cur is None or (r["period_date"], r["uploaded_utc"]) > (cur["period_date"], cur["uploaded_utc"]):
            interim[r["code"]] = r
    comps = storage.read_rows(root / "data" / "ownership" / "companies.csv")
    owners = {o["key"]: o for o in storage.read_rows(root / "data" / "ownership" / "owners.csv")}
    confirmed = load(root / "config" / "ownership_parents.yaml")
    # unlisted holders that head a listed company's largest voting block
    heads: dict[str, list[dict]] = {}
    for c in comps:
        k = c["controller_key"]
        if k and not k.startswith("LISTED:") and c["controller_type"] in ("company", "trust", ""):
            heads.setdefault(k, []).append(c)
    order = sorted(heads, key=lambda k: -float(owners.get(k, {}).get("lookthrough_value") or 0))
    out = ["# Owners of unlisted shareholders: evidence sheet\n",
           "Generated by `python -m cse.ownership.parents` from the companies' own words: passages "
           "from their latest annual reports (`data/raw/ownership/annual/`) and interim reports (`text/`). **Nothing here is confirmed.** A link changes the ownership "
           "graph only after you add it to `config/ownership_parents.yaml` (format at the end).\n",
           "Each section is an unlisted company that is the largest voting block in one or more listed "
           "companies, in order of the value it ultimately holds. Under it: what those companies' reports "
           "say about their parent and ultimate owner. *Recognised* statements are pattern matches "
           "on text that may be garbled by two-column layouts; read the quoted passage.\n"]
    n_found = 0
    for k in order:
        o = owners.get(k, {})
        status = f" · **confirmed**: {confirmed[k].owner}" if k in confirmed else ""
        out.append(f"## {o.get('name', k)}\n")
        out.append(f"Look-through value Rs {float(o.get('lookthrough_value') or 0) / 1e9:,.1f} bn{status}. "
                   f"Largest voting block in: " + ", ".join(
                       f"{c['code']} ({float(c['control_pct']):.2f}%, {c['control_level']})" for c in heads[k]) + "\n")
        any_text = False
        for c in heads[k]:
            docs = []
            rep = annual.get(c["code"])
            if rep:
                docs.append((rep, json.loads((base / "annual" / f"{rep['id']}.json").read_bytes())["passages"]))
            irep = interim.get(c["code"])
            if irep and irep["status"] == "ok":
                docs.append((irep, passages((base / "text" / f"{irep['id']}.txt").read_text(errors="replace"))))
            if not docs:
                out.append(f"- {c['code']}: no report text collected.\n")
                continue
            for rep, ps in docs:
                strong = [p for p in ps if p["strong"]]
                recog = [s for p in ps for s in statements(p["text"])]
                if not strong and not recog:
                    continue
                out.append(f"**{c['code']}** — [{rep['title']}]({rep['url']}) "
                           f"({len(strong)} passages naming an ultimate or indirect owner)\n")
                if recog:
                    n_found += 1
                    out.append("Recognised: " + "; ".join(
                        f"*{kind}*: {e + ' → ' if e else ''}{x}" for kind, e, x in recog[:6]) + "\n")
                for p in strong[:4]:
                    any_text = True
                    q = p["text"].replace("\n", " ⏎ ")
                    out.append(f"> line {p['line']}: {q[:700]}\n")
        if not any_text:
            out.append("_No passage naming an ultimate or indirect owner. The registry (Companies "
                       "(Amendment) Act No. 12 of 2025, beneficial-ownership register) is the remaining source._\n")
    out.append("## Format of `config/ownership_parents.yaml`\n")
    out.append("```yaml\nparents:\n  Odeon Holdings (Ceylon) (Private) Ltd:      # the holder, as the tracker names it\n"
               "    owner: Mr. L.R. Page                     # who owns / controls it\n"
               "    owner_type: individual                   # individual | company | institution | trust\n"
               "    pct: 100                                 # % of the holder; omit if only control is stated\n"
               "    source: https://cdn.cse.lk/...pdf        # the PDF\n"
               "    quote: \"... a company wholly owned by the Chairman, Mr. L R Page ...\"\n```\n")
    doc = "\n".join(out)
    (root / "docs" / "OWNERSHIP_PARENTS_EVIDENCE.md").write_text(doc)
    return f"wrote docs/OWNERSHIP_PARENTS_EVIDENCE.md: {len(order)} unlisted holders, {n_found} reports with a recognised statement"


if __name__ == "__main__":
    print(evidence())
