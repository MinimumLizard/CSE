import pytest

from cse.tags import TAGS, tag
from tests.conftest import load

# (category, title) pairs copied from real approvedAnnouncement rows, 27 Sep - 02 Oct 2026.
CASES = [
    ("DEALINGS BY DIRECTORS", "", "dealings"),
    ("CASH DIVIDEND", "", "dividend"),
    ("SCRIP DIVIDEND (DATES)", "", "dividend"),
    ("RIGHTS ISSUE", "", "capital"),
    ("DEBENTURE ISSUE(BASIS OF ALLOTMENT)", "", "capital"),
    ("CORPORATE DISCLOSURE", "Sanasa Life Insurance Company PLC- Rights Issue", "capital"),
    ("OTHER", "Notice on conversion/reclassification of Non-Voting Convertible Shares into Voting Shares for 4Q 2026", "capital"),
    ("SUB-DIVISION OF SHARES", "Sub-Division of Shares", "capital"),
    ("NET ASSET VALUE", "", "results"),
    ("FINANCIAL REPORT", "Interim Financial Statements for the Quarter ended 30th June 2026", "results"),
    ("FINANCIAL REPORT", "Annual Report as at 31st March 2026", "results"),
    ("APPOINTMENT OF DIRECTORS", "", "board"),
    ("RESIGNATION OF CHAIRPERSON", "", "board"),
    ("CORPORATE DISCLOSURE", "APPOINTMENT OF DEPUTY CHIEF EXECUTIVE OFFICER", "board"),
    ("CORPORATE DISCLOSURE", "Changes in the Composition of Board Sub-Committees", "board"),
    ("ANNUAL GENERAL MEETING - APPROVED", "", "other"),
    ("OTHER", "CHANGE OF AUDITORS", "other"),
    ("TRADING HALTED", "", "other"),
    ("CORPORATE DISCLOSURE", "Amalgamation of Lanka Walltiles PLC's wholly owned subsidiary, Vallibel Plantation Management Limited with Lanka Walltiles PLC", "other"),
]


@pytest.mark.parametrize("category,title,expected", CASES)
def test_rules(category, title, expected):
    assert tag(category, title) == expected


def test_dealings_beats_board():
    assert tag("DEALINGS BY DIRECTORS", "Disclosure of Dealings by Directors") == "dealings"


def test_every_real_title_gets_a_known_tag():
    for category, title in load("announcement_titles.json"):
        assert tag(category, title) in TAGS


def test_case_insensitive_and_none_safe():
    assert tag(None, "cash dividend") == "dividend"
    assert tag(None, None) == "other"
