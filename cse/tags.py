"""Keyword rules that tag a disclosure as board / dealings / dividend / capital / results / other.

Single source of truth for tagging; tested in tests/test_tags.py against real announcement
titles. Rules run on the upper-cased "category + title" text. The FIRST matching tag wins,
so order matters: "DEALINGS BY DIRECTORS" must hit `dealings` before `board` sees DIRECTOR,
and "SCRIP DIVIDEND" is a `dividend` before it is a `capital` event. The site always shows
the raw title next to the tag.
"""
from __future__ import annotations

TAGS = ("board", "dealings", "dividend", "capital", "results", "other")

RULES: list[tuple[str, tuple[str, ...]]] = [
    ("dealings", ("DEALINGS BY DIRECTOR", "DEALING BY DIRECTOR", "DEALINGS BY KEY", "RELEVANT INTEREST")),
    ("dividend", ("DIVIDEND",)),
    ("capital", ("RIGHTS ISSUE", "RIGHTS/WARRANT", "SCRIP", "SUB-DIVISION", "SUBDIVISION", "SUB DIVISION",
                 "CAPITALIZATION", "CAPITALISATION", "PRIVATE PLACEMENT", "SHARE SWAP", "REPURCHASE",
                 "STATED CAPITAL", "DEBENTURE", "ESOS", "EMPLOYEE SHARE", "LISTING OF SHARES",
                 "INITIAL PUBLIC OFFER", "PUBLIC SUBSCRIPTION", "WARRANT", "CONSOLIDATION OF SHARES",
                 "NON-VOTING", "NON VOTING")),
    ("results", ("INTERIM FINANCIAL", "FINANCIAL STATEMENT", "FINANCIAL REPORT", "ANNUAL REPORT",
                 "QUARTER ENDED", "NET ASSET VALUE", "FIVE YEAR SUMMARY", "ERRATA")),
    ("board", ("DIRECTOR", "CHAIRPERSON", "CHAIRMAN", "CHIEF EXECUTIVE", "BOARD", "COMPANY SECRETARY",
               "SUB-COMMITTEE", "SUB COMMITTEE", "SUBCOMMITTEE", "DEPUTY CHIEF")),
]


def tag(category: str | None, title: str | None) -> str:
    text = f"{category or ''} {title or ''}".upper()
    for name, keywords in RULES:
        if any(k in text for k in keywords):
            return name
    return "other"
