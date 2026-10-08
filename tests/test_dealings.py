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
    ("Aquisition", "buy"), ("Puchase", "buy"), ("Share Allotment - Rights Issue", "buy"),
    ("Share Allotment", "other"), ("Transmission", "other"), ("Ordinary Voting Shares", "other"),
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
    assert flows.actor_entity("Disposal of shares by Distilleries Company of Sri Lanka PLC", market, {}).symbol == "DIST"
    assert flows.actor_entity("Odeon Holdings (Ceylon) (Private) Limited, privately held company owned by Mr. L R Page",
                              market, {}).name == "Odeon Holdings (Ceylon) (Private) Limited"
    assert flows.GENERIC_ACCOUNT.match("Please refer attachment")


def test_non_dealing_announcements_are_ignored():
    assert dealings.parse("1", {"reqBaseAnnouncement": {"dType": "AppointmentOfDirectors"}}) == []
    assert dealings.parse("1", {}) == []


# --- quarter-to-quarter list changes ------------------------------------------------------------
# tests/fixtures/flows/holdings.csv: the real parsed voting lists of ACL, CDB and HNB for Jun 2025 to
# Jun 2026 (data/ownership/holdings.csv); ACL had a 3-for-1 split and a holder filed as 'H A S
# MADANAYAKE' one quarter and 'Mr. Suren Madanayake' the others; CDB's largest holder split its
# holding between two accounts; HNB's Mr. Y.S.H.I. Silva was filed under his full name in Sep 2025.

@pytest.fixture(scope="module")
def changes(tmp_path_factory):
    import shutil
    root = tmp_path_factory.mktemp("flows")
    (root / "data" / "ownership").mkdir(parents=True)
    (root / "data" / "raw" / "ownership").mkdir(parents=True)
    shutil.copy(Path(__file__).parent / "fixtures" / "flows" / "holdings.csv", root / "data" / "ownership" / "holdings.csv")
    shutil.copy(Path(__file__).parent / "fixtures" / "flows" / "reports.csv", root / "data" / "raw" / "ownership" / "reports.csv")
    return flows.quarterly_changes(root, analyse.load_market(REPO))


def test_renamed_holder_across_a_split_is_not_a_trade(changes):
    acl = [c for c in changes if c["code"] == "ACL" and "MADANAYAKE" in c["owner"].upper()]
    assert not [c for c in acl if c["traded"] and c["kind"] in ("entered list", "left list")]
    assert any(c["kind"] == "corporate action" for c in acl)          # the 3-for-1 split


def test_moving_shares_between_one_owners_accounts_is_not_a_sale(changes):
    cdb = [c for c in changes if c["code"] == "CDB" and "CEYLINCO" in c["owner"].upper() and c["traded"]]
    assert not cdb


def test_full_name_and_initials_of_one_holder_are_paired(changes):
    silva = [c for c in changes if c["code"] == "HNB" and c["renamed_from"] and "SILVA" in c["renamed_from"].upper()]
    assert silva and silva[0]["kind"] == "bought" and silva[0]["frac_to"] > silva[0]["frac_from"]


def test_trade_values_use_fraction_change_at_latest_price(changes):
    m = analyse.load_market(REPO)
    for c in changes:
        if c["traded"]:
            assert c["value"] == pytest.approx(c["dfrac"] * m.mcap[c["symbol"]])
        else:
            assert c["value"] == 0.0


@pytest.mark.parametrize("a, b, same", [
    ("H.H. ABDULHUSEIN", "HUZAIFA HAMZAALLY ABDULHUSEIN", True),
    ("MR. Y.S.H.I. SILVA", "MR. YONMERENNE SIMON HEWAGE INDRAKUMARA", True),
    ("Mr. A. B. Perera", "Mr. C. D. Perera", False),                 # same surname, other initials
    ("JANASHAKTHI PLC", "Janasakthi Ltd", True),
    ("Ceylon Steel Corporation Limited", "Ceylon Biscuits Limited", False),
])
def test_similar_names(a, b, same):
    t = "individual" if a.upper().startswith(("MR", "H.H")) else "company"
    assert flows.similar_names({"name": a, "type": t}, {"name": b, "type": t}) is same
