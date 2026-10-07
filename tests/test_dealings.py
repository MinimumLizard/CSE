"""Directors' dealings, parsed from real getAnnouncementById responses (tests/fixtures/dealings/):
39545 = C T Holdings, a director buying in her own name; 39553 = C T Land Development, its listed
parent trading through the 'common directors' related account (two transactions in one notice)."""
import datetime as dt
import json
from pathlib import Path

import pytest

from cse import dealings, flows
from cse.ownership import analyse

FIX = Path(__file__).parent / "fixtures" / "dealings"
REPO = Path(__file__).resolve().parents[1]


def load(aid):
    return json.loads((FIX / f"{aid}.json").read_bytes())


def test_own_account_purchase():
    (r,) = dealings.parse("39545", load("39545"))
    assert (r["symbol"], r["side"], r["account_type"], r["account"]) == ("CTHR", "buy", "own", "Ms. M. M. Page")
    assert r["quantity"] == 30835 and r["price"] == 540 and r["value"] == pytest.approx(30835 * 540)
    assert (r["trade_date"], r["announced"], r["lag_days"]) == ("2026-10-05", "2026-10-06", 1)
    assert r["url"].startswith("https://cdn.cse.lk/") and " " not in r["url"]


def test_related_account_with_two_transactions():
    rows = dealings.parse("39553", load("39553"))
    assert len(rows) == 2 and all(r["account_type"] == "related" for r in rows)
    assert rows[0]["account"] == "CT Holdings PLC - Common Directors"
    assert sum(r["quantity"] for r in rows) == 500 + 72858


@pytest.mark.parametrize("raw, side", [
    ("Purchase of Shares", "buy"), ("PURCHASE", "buy"), ("Acquisition of shares", "buy"), ("purchased", "buy"),
    ("Sale", "sell"), ("Sale of Shares ", "sell"), ("Disposal of Shares (Voting)", "sell"), ("disposal", "sell"),
    ("Gift Transfer", "other"), ("Transfer", "other"), ("", "other"), ("Purchase and sale", "other"),
])
def test_side_from_free_text(raw, side):              # wordings seen in the API's transType field
    assert dealings.side_of(raw) == side


def test_weekday_lag_skips_weekends():
    fri, mon = dt.date(2026, 10, 2), dt.date(2026, 10, 5)
    assert dealings.weekdays_between(fri, mon) == 1 and dealings.weekdays_between(fri, fri) == 0


def test_related_account_resolves_to_the_listed_parent():
    market = analyse.load_market(REPO)
    e = flows.actor_entity("CT Holdings PLC - Common Directors", market, {})
    assert (e.type, e.symbol) == ("listed", "CTHR")
    assert flows.actor_entity("Senthilverl Holdings (Pvt) Ltd- Director", market, {}).name == "Senthilverl Holdings (Pvt) Ltd"
    assert "SPOUSE" in flows.actor_entity("Mrs X Perera (Spouse)", market, {}).key   # a relative is not the director


def test_non_dealing_announcements_are_ignored():
    assert dealings.parse("1", {"reqBaseAnnouncement": {"dType": "AppointmentOfDirectors"}}) == []
    assert dealings.parse("1", {}) == []
