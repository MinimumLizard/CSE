"""Who is a shareholder really? Name normalisation, beneficial-owner rules and classification.

Rules (each tested in tests/test_ownership.py, documented in docs/METHODS.md §10):
1. Normalise case, punctuation, apostrophes and spacing; drop titles (Mr/Mrs/Dr/M/S...) and
   account designations ("A/C No. 2"); canonicalise legal suffixes ((Pvt) Ltd, Private Limited...).
2. Custodian / special accounts: "<custodian> S/A <owner>", "BNYM SA/NV-<owner>", "SSBT-<owner>",
   "JPMCB NA - <owner>", "<custodian> as trustee for <owner>" -> owner.
3. Margin / financed accounts: "<lender>/<owner>" -> owner, lender kept as `via`.
4. Insurance life / policyholder funds stay institutions; they are never mapped to the listed
   insurer (the money belongs to policyholders, so ownership must not chain through the insurer).
5. Classify: listed (a CSE-listed company), individual, joint, estate, trust, institution (funds,
   pension/provident funds, insurers' funds, DFIs, state bodies), company (not CSE-listed, incl.
   foreign), or nominee (unidentified).
   Individuals are merged only when their normalised names match exactly; initials are never
   matched to full names automatically (that would be guessing at a person's identity).
6. config/ownership_aliases.yaml can merge spellings or link entities; it always wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

TITLES = r"(?:M/S|MESSRS|MR|MRS|MS|MISS|DR|PROF|REV|VEN|HON|SIR|DEP|ENG|GEN|CAPT|LT|COL)"
CUSTODIAN_SEP = re.compile(
    r"^(?P<cust>.*?\b(?:S/A|SA/NV|A/C\s+OF|AS\s+TRUSTEE\s+(?:FOR|OF|TO)|TRUSTEE\s+(?:FOR|TO|OF)|"
    r"NOMINEES?\s+(?:FOR|A/C))\b)[\s:-]*(?P<owner>.+)$", re.I)
CUSTODIAN_DASH = re.compile(
    r"^(?P<cust>(?:SSBT|SSB|BBH|JPMCB\s*NA|JPMLU|BNYM(?:SANV)?(?:\s*RE)?|BNYMSANV\s*RE|BNYM\s+SA/NV(?:\s*RE)?|HSBC\s+INTL\s+NOM(?:\s+LTD)?|"
    r"CITIBANK\s+N\.?A\.?|CACEIS\s+BANK[^-]*|RBC\s+INVESTOR\s+SERVICES[^-]*|NORTHERN\s+TRUST[^-]*|"
    r"DEUTSCHE\s+BANK\s+AG[^-]*|STANDARD\s+CHARTERED[^-]*|PERSHING\s+LLC)\s*[-:]\s*"
    r"|(?:BBH|SSBT|BNYM\s+RE|BNYMSANV\s+RE)\s+)(?P<owner>.+)$", re.I)
BANK_AC = re.compile(r"^(?P<cust>.*?\bBANK\b[^/]*?)\s+A/C\s+(?P<owner>(?!NO\b).*\b(?:FUND|FUNDS|TRUST|SCHEME)\b.*)$", re.I)
BANK_DASH = re.compile(r"^(?P<cust>.*?\bBANK\b[^-/]*?(?:PLC|LTD|LIMITED)?(?:\s+A/C\s*NO\.?\s*\d+)?)\s*-\s*(?P<owner>.*\b(?:FUND|FUNDS|TRUST|SCHEME)\b.*)$", re.I)
LIFE_FUND = re.compile(r"\b(LIFE|POLICY\s?HOLDERS?|UNIVERSAL\s+LIFE|PARTICIPATING)\b.*\bFUNDS?\b|\bLIFE\s+FUND\b", re.I)
FUND_WORDS = re.compile(r"\b(FUND|FUNDS|TRUST\s+FUND|PROVIDENT|PENSION|SUPERANNUATION|UNIT\s+TRUST|ICAV|SICAV|"
                        r"UCITS|PORTFOLIO|ENDOWMENT|INSURANCE\s+CORPORATION|INTERNATIONAL\s+FINANCE\s+CORPORATION|"
                        r"TREASURY|GOVERNMENT|MONETARY\s+BOARD|CENTRAL\s+BANK|ASSET\s+MANAGEMENT|DEVELOPMENT\s+BANK|"
                        r"FINANCE\s+CORPORATION|SOVEREIGN|NORGES|ENDOWMENT|FOUNDATION|AUTHORITY|MINISTRY|COMMISSION|"
                        r"SECRETARY\s+TO\s+THE\s+TREASURY|DEPARTMENT)\b", re.I)
COMPANY_WORDS = re.compile(r"\b(LTD|LIMITED|PLC|PVT|PRIVATE|INC|LLC|LP|L\.P|CORP|CORPORATION|COMPANY|CO|HOLDINGS|"
                           r"B\.?V|SARL|S\.?A|AG|GMBH|PTE|BHD|BERHAD|SDN|INVESTMENTS?|ENTERPRISES?|TRADING|GROUP|BANK|N\.?V|"
                           r"S\.?A\.?R\.?L|A/S|AB|ASA|OYJ|K\.?K|PJSC|SPA|S\.?P\.?A|LLP|PARTNERS)\b", re.I)
LEGAL = [(r"\(?\bPRIVATE\)?\s+LIMITED\b", "PVT LTD"), (r"\(\s*PVT\s*\)\s*LTD\b", "PVT LTD"),
         (r"\bPVT\.?\s+LTD\b", "PVT LTD"), (r"\bLIMITED\b", "LTD"), (r"\bCOMPANY\b", "CO")]
ACCOUNT = re.compile(r"\b(?:A/?C|ACCOUNT)\.?\s*(?:NO\.?|NUMBER)?\s*[:.]?\s*\d+\b|\bNO\.?\s*\d+\s*A/?C\b|"
                     r"\bNO\.?\s*\d+\s+(?:SHARE\s+)?INVESTMENTS?\s+(?:A/?C|ACCOUNT)\b|"
                     r"\b(?:A/?C|ACCOUNT)\s*$|\((?:COLLATERAL|JOINT|DECEASED|MARGIN)\)", re.I)


@dataclass(frozen=True)
class Entity:
    key: str            # matching key (normalised)
    name: str           # display name
    type: str           # listed | individual | joint | estate | trust | institution | company | nominee
    symbol: str = ""    # CSE code when type == listed
    via: str = ""       # custodian / lender the shares are held through
    rule: str = ""      # which rule produced this attribution


def clean(s: str) -> str:
    s = s.upper().replace("’", "'").replace("`", "'")
    s = re.sub(r"[\"'’]", "", s)
    s = re.sub(rf"^\s*\d{{1,2}}\s*[.)]\s*", "", s)                       # leftover rank
    s = re.sub(rf"(^|[\s,&/(]){TITLES}\b\.?", r"\1", s)
    s = s.replace("&", " AND ")
    for pat, rep in LEGAL:
        s = re.sub(pat, rep, s)
    s = re.sub(r"[.,;:()]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -/")


def key_of(s: str) -> str:
    s = ACCOUNT.sub("", clean(s))
    s = re.sub(r"^THE\s+", "", s)
    s = re.sub(r"\b(PVT LTD|PRIVATE LTD|PVT|PRIVATE|LTD|PLC|INC|LLC)\b(\s+\d{1,2}$)?", "", s)
    return re.sub(r"[^A-Z0-9]", "", s)


def dedupe_repeat(name: str) -> str:
    """PDF text sometimes repeats a cell: 'J.B Cocoshell (Pvt) LtdJ.B. COCOSHELL (PVT) LTD'."""
    k = re.sub(r"[^A-Z0-9]", "", name.upper())      # raw letters, so suffixes count on both halves
    if len(k) >= 8 and len(k) % 2 == 0 and k[: len(k) // 2] == k[len(k) // 2:]:
        letters = 0
        for i, ch in enumerate(name):
            letters += ch.isalnum()
            if letters == len(k) // 2:
                return name[: i + 1] + re.match(r"[^A-Za-z0-9]*", name[i + 1:]).group(0).rstrip()
    return name


def split_beneficial(name: str) -> tuple[str, str, str]:
    """Return (owner_part, via, rule)."""
    raw = dedupe_repeat(re.sub(r"\s+", " ", name).strip())
    work = re.sub(r"^\s*M\s*/\s*S\.?\s*", "", raw, flags=re.I)           # 'M/S.' is a title, not a margin slash
    m = CUSTODIAN_SEP.match(work)
    if m and m.group("owner").strip():
        return m.group("owner").strip(" -:"), m.group("cust").strip(), "custodian (S/A or trustee)"
    m = BANK_AC.match(work)
    if m and m.group("owner").strip():
        return m.group("owner").strip(" -:"), m.group("cust").strip(" -:"), "bank as custodian / trustee of a fund"
    m = BANK_DASH.match(work)
    if m and m.group("owner").strip():
        return m.group("owner").strip(" -:"), m.group("cust").strip(" -:"), "bank as custodian / trustee of a fund"
    m = CUSTODIAN_DASH.match(work)
    if m and m.group("owner").strip():
        return m.group("owner").strip(" -:"), m.group("cust").strip(" -:"), "custodian prefix"
    no_acct = re.sub(r"\bA/C\b", "A_C", ACCOUNT.sub("", work), flags=re.I)     # 'A/C' is not a separator
    if "/" in no_acct:
        left, right = no_acct.rsplit("/", 1)       # the owner follows the last slash
        if right.strip() and re.search(r"\b(BANK|FINANCE|LEASING|CAPITAL|SECURITIES|WEALTH|INVESTMENT|PLC|LTD)\b",
                                       left, re.I) and not re.match(r"^\s*(?:S|C|O)\s*$", left):
            return right.strip(" -/").replace("A_C", "A/C"), left.strip(" -/").replace("A_C", "A/C"), \
                "margin / financed account"
    return work, "", ""


def classify(owner: str, listed: dict[str, str], via: str = "") -> tuple[str, str]:
    """(type, symbol). Types: listed, individual, joint, estate, trust, institution, company, nominee."""
    c = clean(owner)
    k = key_of(owner)
    if not k:
        return "nominee", ""
    if via and re.fullmatch(r"[A-Z0-9 ]{2,6}", c) and " " not in c:     # 'CACEIS ...-NEF': an account code
        return "institution", ""
    if LIFE_FUND.search(c):
        return "institution", ""
    if k in listed:
        return "listed", listed[k]
    if re.search(r"\b(ESTATE OF|EST OF|EST LATE|LATE)\b", c):
        return "estate", ""
    if (re.search(r"\bTRUSTEES?\b|\bTRUST\b(?!\s+FUND)", c) and not FUND_WORDS.search(c)
            and not re.search(r"\b(PVT LTD|LTD|PLC|INC|LLC|CO|CORPORATION)\b", c)):
        return "trust", ""
    if FUND_WORDS.search(c):
        return "institution", ""
    if COMPANY_WORDS.search(c):
        return "company", ""
    if " AND " in c or "/" in c or re.search(r"\bJOINT\b|\bJT\b", c):
        return "joint", ""
    if re.search(rf"^(?:{TITLES}\b|[A-Z]\s)|^([A-Z]{{1,3}}\s)+[A-Z]{{3,}}", c) or re.match(r"^[A-Z ]+$", c):
        return "individual", ""
    return "company", ""


def load_aliases(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    out = {}
    for name, spec in (data.get("aliases") or {}).items():
        spec = spec or {}
        for variant in [name] + list(spec.get("variants") or []):
            out[key_of(variant)] = {"name": name, "type": spec.get("type"), "symbol": spec.get("symbol", "")}
    return out


def resolve(name_as_filed: str, listed: dict[str, str], aliases: dict[str, dict]) -> Entity:
    owner, via, rule = split_beneficial(name_as_filed)
    k = key_of(owner)
    if k in aliases:
        a = aliases[k]
        return Entity(key_of(a["name"]), a["name"], a.get("type") or classify(a["name"], listed)[0],
                      a.get("symbol") or listed.get(key_of(a["name"]), ""), via, (rule + "; " if rule else "") + "alias")
    typ, sym = classify(owner, listed, via)
    display = re.sub(r"\s+", " ", ACCOUNT.sub("", owner)).strip(" -/,")
    return Entity(k or key_of(name_as_filed), display or name_as_filed.strip(), typ, sym, via, rule)


def listed_index(securities) -> dict[str, str]:
    """key -> company code, from the CSE security list (both share classes map to the code)."""
    out = {}
    for s in securities:
        if s.symbol.endswith((".N0000", ".X0000")):
            out.setdefault(key_of(s.name), s.symbol.split(".")[0])
    return out
