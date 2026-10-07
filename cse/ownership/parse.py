"""Find and parse the largest-shareholders tables in an interim report's text.

Reports differ: "Twenty largest shareholders", "Top 25 Shareholders", "FIRST TWENTY
SHAREHOLDERS", "List of Shareholders", "Major shareholders"; ranks may be present or not;
a row may carry a second (prior-period) shares/% pair; voting and non-voting classes get
separate tables. Debenture-holder and directors' tables are skipped.

Every table is checked numerically: shares / % implies the class's total shares, and that
total must be consistent across rows and close to the share count the CSE reports
(`quantityIssued`). Only tables that pass are marked `verified`.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

HEADING = re.compile(
    r"(?:(?:twenty|thirty|top|first|major|largest|list\s+of|main)\b[^\n]{0,60}?share\s?hold(?:ers|ings?)"
    r"|share\s?holders?\b[^\n]{0,25}\b(?:as\s+at|as\s+of)\b"
    r"|(?:twenty|20|25|30)\s+(?:largest|major|top)\b)", re.I)
SKIP_HEADING = re.compile(r"debenture|director|key management|related part|bond|preference|analysis|distribution|"
                          r"public|number of share\s?holders|"
                          r"categor|composition|spread of|range of", re.I)
SECTION_END = re.compile(r"\b(directors?'?s?|debentures?|preference|public\s+(holding|share)|float\s+adjusted)\b", re.I)
NUM = r"(?:\d{1,3}(?:,\d{3})+|\d{4,})(?:\.0{1,2}(?!\d))?"
PCT = r"\*{0,2}\s*(?P<pct>\d{1,2}\.\d{1,6}|100(?:\.0+)?|0\.\d{1,6}|\d{1,2}(?=\s*%))"
ROW = re.compile(
    rf"^\s*(?:(?P<rank>\d{{1,2}})\s*[.)]?\s+)?(?:(?P<sub>\d{{1,2}}\.\d{{1,2}})\s+)?(?P<name>[^\d\s][^\n]*?)\s{{2,}}"
    rf"(?P<shares>{NUM})\s+{PCT}\s*%?(?:\s+.*)?$")
RANK_ONLY = re.compile(rf"^\s*(?P<rank>\d{{1,2}})\s*[.)]?\s{{2,}}(?P<shares>{NUM})\s+{PCT}\s*%?(?:\s+.*)?$")
GROUP_LINE = re.compile(rf"^\s*(?:\d{{1,2}}\s*[.)]?\s+)?(?P<name>[^\d\s][^\n]*?)\s{{2,}}(?:{NUM})\s*$")
NUMBERS_ONLY = re.compile(rf"^\s*(?P<shares>{NUM})\s+{PCT}\s*%?(?:\s+.*)?$")
TOTAL_PAIR = re.compile(rf"(?P<shares>{NUM})\s+{PCT}\s*%?")
TOTAL_SHARES = re.compile(rf"total\b[^\n]*?(?P<shares>{NUM})\s+100(?:\.0+)?\s*%?", re.I)
ZERO_NOW = re.compile(r"\s(?:-|N/?A|NIL)\s+(?:-|\d{1,3}(?:\.\d+)?\s*%?)$", re.I)      # '- 0.0%': no shares this period, prior-period numbers follow
DIGIT_TYPO = re.compile(r"(\d),(\d{1,2}) (\d{1,2})(?=\s{2,}|\s*$)")   # '128,421,60 4' -> '128,421,604'
STOP = re.compile(r"^\s*(sub\s?-?total|total|others?\b|other share|balance|public (holding|share)|"
                  r"percentage of public|shares held by directors|float adjusted)", re.I)
NONVOTING = re.compile(r"non[\s-]?voting", re.I)
# "2  Non-Voting Shares" / "14.2 Ordinary Shares (Non Voting)": a sub-heading, recognised only just after a table
SUBHEAD_X = re.compile(r"^\s*(?:\d{1,2}(?:\.\d{1,2})?\.?\s+)?[^\d\n]{0,40}non[\s-]?voting[^\d\n]{0,40}$", re.I)
WRAP_TAIL = re.compile(r"[/&,\-–(]$")
CONT_START = re.compile(r"^(?:LIMITED|LTD|PLC|\(?PRIVATE\)?|\(?PVT\)?|COMPANY|FUND|TRUST|INC|LLC|CORPORATION|"
                        r"BRANCH|ACCOUNT|A/C)\b", re.I)
HEADER_WORDS = re.compile(r"\b(?:names?|shares|holdings?|number|percentage|as\s+at|no\.?)\b|%", re.I)


@dataclass
class Table:
    share_class: str                    # "N" voting / "X" non-voting
    heading: str
    line: int
    rows: list[dict] = field(default_factory=list)
    implied_total: float | None = None
    stated_total_pct: float | None = None      # the table's own Total/Subtotal %, when printed
    stated_total_shares: float | None = None   # ... and its share count
    class_total_shares: float | None = None    # a printed total for the whole class (100 %)
    period_total: float | None = None          # shares in issue at the report date, used for each row's fraction
    status: str = "unverified"
    note: str = ""


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _dp(pct: str) -> int:
    return len(pct.split(".")[1]) if "." in pct else 0


TOKEN_OK = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?|-")
FRAGMENT = re.compile(r"\d{1,2}%?")


def _despace(line: str) -> str:
    """Join a number that the PDF split with single spaces ('1 7,52 1 ,1 1 0', '1. 7 7%', '0.41 %'),
    in the numeric part of a row only. Two well-formed numbers one space apart ('250,000,000 25.00%')
    stay separate columns; a 1-2 digit fragment after a number is taken as part of it."""
    letters = [k for k, ch in enumerate(line) if ch.isalpha()]
    cut = letters[-1] + 1 if letters else 0
    out = []
    for seg in re.split(r"(\s{2,})", line[cut:]):
        if not seg.strip() or seg.isspace():
            out.append(seg)
            continue
        lead = seg[: len(seg) - len(seg.lstrip(" "))]
        toks = seg.strip(" ").split(" ")
        merged = [toks[0]]
        for tok in toks[1:]:
            numeric = re.fullmatch(r"[\d,.%]+", merged[-1]) and re.fullmatch(r"[\d,.%]+", tok)
            if numeric and (not TOKEN_OK.fullmatch(merged[-1]) or not TOKEN_OK.fullmatch(tok) or FRAGMENT.fullmatch(tok)):
                merged[-1] += tok
            else:
                merged.append(tok)
        out.append(lead + " ".join(merged))
    return line[:cut] + "".join(out)


def _trailing_text(line: str) -> str:
    """A short name fragment on its own line, ignoring a leading rank and trailing numbers."""
    m = re.match(r"^\s*(?:\d{1,2}\s*[.)]?\s+)?([A-Za-z(][^\d]{0,60}?)(?:\s{2,}.*)?$", line)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m and not re.search(NUM, line) else ""


def find_tables(text: str) -> list[Table]:
    lines = text.splitlines()
    tables: list[Table] = []
    i, last_end = 0, -10**6
    while i < len(lines):
        pieces = re.split(r"\s{8,}", lines[i].strip())     # a heading, not a column printed beside it
        ln = next((p for p in pieces if HEADING.search(p)), pieces[0])
        sub_x = i - last_end <= 40 and bool(SUBHEAD_X.match(lines[i])) and not SKIP_HEADING.search(ln)
        if not sub_x and (not HEADING.search(ln) or SKIP_HEADING.search(ln)):
            i += 1
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        context = ln + " " + (re.split(r"\s{8,}", nxt.strip())[0] if len(nxt) - len(nxt.lstrip()) < 40 else "")
        # (the heading and the line after it, never the section above or a column beside it)
        t = Table("X" if NONVOTING.search(ln) or NONVOTING.search(lines[i + 1] if i + 1 < len(lines) else "") else "N",
                  re.sub(r"\s+", " ", ln).strip(), i + 1)
        if SKIP_HEADING.search(context) and not re.search(r"ordinary|voting", context, re.I):
            i += 1
            continue
        pending, group, misses, j = "", "", 0, i + 1
        while j < len(lines) and j < i + 120:
            raw = DIGIT_TYPO.sub(lambda m: m.group(1) + "," + m.group(2) + m.group(3)
                                 if len(m.group(2) + m.group(3)) == 3 else m.group(0), _despace(lines[j]))
            j += 1
            stripped = raw.strip()
            if not t.rows and HEADING.search(raw):
                break                           # another heading before any row: start again from there
            if not t.rows and not stripped:
                pending = ""                    # text above a blank line is not part of the first name
            if t.rows and (HEADING.search(raw) or (SECTION_END.search(raw) and not re.search(NUM, raw))):
                j -= 1                          # the next table or section starts here
                break
            no = NUMBERS_ONLY.match(raw)
            if no and t.rows and float(no.group("pct")) < sum(r["pct"] for r in t.rows) - 0.5 and group:
                # One holder's accounts listed with shares only, then the holder's combined line.
                t.rows.append({"rank": len(t.rows) + 1, "name": group, "shares": _num(no.group("shares")),
                               "pct": float(no.group("pct")), "dp": _dp(no.group("pct")), "ranked": True})
                pending, group, misses = "", "", 0
                continue
            gm = GROUP_LINE.match(raw)
            if gm and t.rows and not ROW.match(raw) and not STOP.search(raw) and not HEADING.search(raw):
                group = group or re.sub(r"\s+", " ", gm.group("name")).strip()
                continue
            if no and pending and t.rows:
                # A name printed above its numbers with no rank: text above (+ a fragment below).
                name, nxt = pending, _trailing_text(lines[j]) if j < len(lines) else ""
                if nxt and not HEADING.search(nxt) and not SECTION_END.search(nxt) and not STOP.search(nxt):
                    name, j = f"{name} {nxt}", j + 1
                t.rows.append({"rank": len(t.rows) + 1, "name": name, "shares": _num(no.group("shares")),
                               "pct": float(no.group("pct")), "dp": _dp(no.group("pct")), "ranked": True})
                pending, misses = "", 0
                continue
            if t.rows and re.match(r"^\s*total\b", raw, re.I):
                peek = next((x for x in lines[j: j + 3] if x.strip()), "")
                pm = ROW.match(peek)
                if pm and pm.group("rank") and int(pm.group("rank")) > max(r["rank"] for r in t.rows if r.get("ranked")) \
                        if any(r.get("ranked") for r in t.rows) else False:
                    continue                    # one holder's accounts added up mid-table; the table goes on
            if t.rows and (STOP.search(raw) or no):
                tot = TOTAL_PAIR.search(raw)   # first shares/% pair = this period (a prior period may follow)
                if tot and (NUMBERS_ONLY.match(raw) or re.match(r"^\s*(sub\s?-?total|total)", raw, re.I)):
                    t.stated_total_pct, t.stated_total_shares = float(tot.group("pct")), _num(tot.group("shares"))
                break
            m = ROW.match(raw)
            if m and not STOP.search(m.group("name")):
                if ZERO_NOW.search(m.group("name")):
                    pending = ""
                    continue
                if m.group("rank") and int(m.group("rank")) == 1 and len(t.rows) >= 3 and not m.group("sub"):
                    j -= 1                      # ranks restart: a second table without a heading
                    break
                if (not m.group("rank") and not m.group("sub") and len(t.rows) >= 3
                        and sum(bool(r.get("ranked")) for r in t.rows) >= 0.8 * len(t.rows)):
                    # Every row so far is numbered, this one isn't: the tail of a wrapped name, whose
                    # numbers belong to another column (e.g. the prior period). Keep the text only.
                    t.rows[-1]["name"] += " " + re.sub(r"\s+", " ", m.group("name")).strip()
                    continue
                name = re.sub(r"\s+", " ", m.group("name")).strip()
                if pending and (WRAP_TAIL.search(pending) or name[:1].islower() or name[:1] in "&("
                                or CONT_START.match(name)):
                    name = f"{pending} {name}".strip()
                pending, misses = "", 0
                t.rows.append({"rank": int(m.group("rank")) if m.group("rank") else len(t.rows) + 1,
                               "name": name, "shares": _num(m.group("shares")), "pct": float(m.group("pct")),
                               "dp": _dp(m.group("pct")), "ranked": bool(m.group("rank"))})
                continue
            ro = RANK_ONLY.match(raw)
            if ro and pending:
                # Name wrapped around the numbers: text above + (optionally) one text line below.
                name = pending
                nxt = lines[j].strip() if j < len(lines) else ""
                if nxt and not re.search(r"\d{3}", nxt) and len(nxt) < 60 and not ROW.match(lines[j]) \
                        and not HEADING.search(nxt) and not SECTION_END.search(nxt):
                    name = f"{name} {re.sub(chr(92) + 's+', ' ', nxt)}"
                    j += 1
                t.rows.append({"rank": int(ro.group("rank")), "name": name, "shares": _num(ro.group("shares")),
                               "pct": float(ro.group("pct")), "dp": _dp(ro.group("pct")), "ranked": True})
                pending, misses = "", 0
                continue
            if stripped and not re.search(r"\d{3}", stripped) and len(stripped) < 130 and not HEADING.search(stripped) \
                    and (t.rows or not HEADER_WORDS.search(stripped)):
                pending = re.sub(r"^\d{1,2}\s*[.)]?\s+(?=[A-Za-z(])", "", re.sub(r"\s+", " ", stripped))
            elif stripped:
                misses += 1
            if t.rows and misses > 12:
                break
            if not t.rows and j > i + 40:
                break
        if len(t.rows) >= 3:
            for k in range(j, min(j + 40, len(lines))):          # a printed total for the whole class
                m = TOTAL_SHARES.search(lines[k])
                if m:
                    t.class_total_shares = _num(m.group("shares"))
                    break
                if k > j and HEADING.search(lines[k]):
                    break
            tables.append(t)
            i = last_end = j
        else:
            i += 1
    return tables


def missing_ranks(t: Table) -> list[int]:
    ranks = sorted({r["rank"] for r in t.rows})
    return [k for k in range(1, (ranks[-1] if ranks else 0) + 1) if k not in ranks] if ranks else []


def _interval(r: dict) -> tuple[float, float]:
    """Total shares consistent with this row: the printed % may be rounded or truncated to `dp` places."""
    h = 10 ** -r.get("dp", 2)
    lo_p, hi_p = max(r["pct"] - h, 1e-9), r["pct"] + h
    return r["shares"] * 100 / hi_p, r["shares"] * 100 / lo_p


def validate_table(t: Table, shares_issued: float | None) -> Table:
    t.status = "unverified"
    gaps = missing_ranks(t)
    if len(t.rows) < 5:
        t.note = f"only {len(t.rows)} rows"
        return t
    pct_sum = sum(r["pct"] for r in t.rows)
    if pct_sum > 100.5:
        t.note = f"percentages sum to {pct_sum:.2f}"
        return t
    top = sorted((r for r in t.rows if r["pct"] > 0), key=lambda r: -r["pct"])[:10]
    if len(top) < 3:
        t.note = "fewer than 3 rows with a percentage"
        return t
    ivs = [_interval(r) for r in top]
    lo, hi = max(a for a, _ in ivs), min(b for _, b in ivs)
    med = (lo * hi) ** 0.5 if lo <= hi else statistics.median(r["shares"] / r["pct"] * 100 for r in top)
    t.implied_total = med
    if lo > hi * 1.005:
        worst = max(top, key=lambda r: abs(r["shares"] / r["pct"] * 100 / med - 1))
        t.note = (f"rows imply different totals ({lo:,.0f} > {hi:,.0f}); "
                  f"'{worst['name'][:40]}' implies {worst['shares'] / worst['pct'] * 100:,.0f}")
        return t
    own_total = t.class_total_shares or (t.stated_total_shares if t.stated_total_pct and
                                          abs(t.stated_total_pct - 100) < 0.01 else None)
    if own_total and lo * 0.995 <= own_total <= hi * 1.005:
        t.period_total = own_total
    elif shares_issued and lo * 0.995 <= shares_issued <= hi * 1.005:
        t.period_total = shares_issued
    else:
        t.period_total = med
    if shares_issued and abs(med / shares_issued - 1) <= 0.10:
        t.status = "verified"
        t.note = f"implied total within {abs(med / shares_issued - 1):.1%} of the CSE share count"
    elif own_total and lo * 0.995 <= own_total <= hi * 1.005:
        t.status = "verified_total"
        t.note = (f"rows agree with the report's own total of {own_total:,.0f} shares; the CSE now reports "
                  f"{shares_issued:,.0f} (shares issued or cancelled since the period end?)" if shares_issued else
                  f"rows agree with the report's own total of {own_total:,.0f} shares; no CSE share count")
    elif shares_issued:
        t.note = f"implied total {med:,.0f} vs CSE share count {shares_issued:,.0f} ({med / shares_issued - 1:+.1%})"
        return t
    else:
        t.status = "consistent"
        t.note = "no CSE share count to compare"
    if t.stated_total_shares is not None and t.stated_total_pct is not None and t.stated_total_pct < 99.99:
        sh_sum = sum(r["shares"] for r in t.rows)
        if abs(sh_sum / t.stated_total_shares - 1) > 0.005:
            tol = 0.25 + len(t.rows) * 10 ** -min(r.get("dp", 2) for r in t.rows) / 2
            if abs(pct_sum - t.stated_total_pct) > tol:
                t.status = "unverified"
                t.note = (f"rows sum to {sh_sum:,.0f} shares / {pct_sum:.2f}% but the table's total says "
                          f"{t.stated_total_shares:,.0f} / {t.stated_total_pct:.2f}%")
                return t
        t.note += f"; rows add up to the table's stated total ({t.stated_total_pct:.2f}%)"
    if gaps:
        t.note += f"; ranks not parsed: {gaps}"
    return t


def best_tables(tables: list[Table], issued: dict[str, float | None]) -> dict[str, Table]:
    """One table per share class: verified beats consistent beats unverified, then most rows."""
    rank = {"verified": 3, "verified_total": 2, "consistent": 1, "unverified": 0}
    out: dict[str, Table] = {}
    for t in tables:
        validate_table(t, issued.get(t.share_class))
        cur = out.get(t.share_class)
        if cur is None or (rank[t.status], len(t.rows)) > (rank[cur.status], len(cur.rows)):
            out[t.share_class] = t
    # A "non-voting" heading may be mislabelled; if an X table matches the N share count, relabel.
    return out
