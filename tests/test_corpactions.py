import json
from pathlib import Path

import pytest

from cse import corpactions
from cse.corpactions import Record, effective, parse, parse_rights_text
from tests.conftest import FIX

CA = FIX / "ca"
CLASSES = {"CIC": ["CIC.N0000", "CIC.X0000"], "COMB": ["COMB.N0000", "COMB.X0000"], "HAYL": ["HAYL.N0000"]}


def rec(name, aid, symbol, category, date, source):
    j = json.loads((CA / name).read_text())
    return Record(aid, symbol, category, date, j["reqBaseAnnouncement"], j.get("reqAnnouncementDocs") or [], source)


def by(rows, symbol, typ):
    return [r for r in rows if r["symbol"] == symbol and r["type"] == typ]


def test_cash_dividend_both_share_classes():
    rows = parse([rec("typed_37168_CIC_cash_dividend.json", 37168, "CIC.N0000", "CASH DIVIDEND", "2026-05-29", "typed")],
                 CLASSES)
    n, x = by(rows, "CIC.N0000", "cash_dividend")[0], by(rows, "CIC.X0000", "cash_dividend")[0]
    assert (n["ex_date"], n["amount_per_share"], n["status"]) == ("2026-07-01", 0.5, "confirmed")
    assert (x["ex_date"], x["amount_per_share"]) == ("2026-07-01", 0.5)
    assert n["source_url"].startswith("https://cdn.cse.lk/")


def test_split_ratio_from_share_counts_and_date_from_dates_record():
    rows = parse([rec("typed_32457_CIC_split.json", 32457, "CIC.N0000", "SUB-DIVISION OF SHARES", "2025-08-05", "typed"),
                  rec("general_33380_CIC_split_dates.json", 33380, "CIC.N0000", "SUB-DIVISION OF SHARES (DATES)",
                      "2025-09-19", "general")], CLASSES)
    for sym in ("CIC.N0000", "CIC.X0000"):
        r = by(rows, sym, "subdivision")[0]
        assert r["factor"] == pytest.approx(5.0) and r["ratio_new"] == pytest.approx(4.0) and r["ratio_held"] == 1.0
        assert r["ex_date"] == "2025-10-22" and r["status"] == "confirmed"


def test_split_without_dates_needs_review():
    rows = parse([rec("typed_32457_CIC_split.json", 32457, "CIC.N0000", "SUB-DIVISION OF SHARES", "2025-08-05", "typed")],
                 CLASSES)
    assert by(rows, "CIC.N0000", "subdivision")[0]["status"] == "needs_review"


def test_scrip_proportion():
    rows = parse([rec("typed_36113_COMB_scrip.json", 36113, "COMB.N0000", "SCRIP DIVIDEND (DATES)", "2026-03-09", "typed")],
                 CLASSES)
    n = by(rows, "COMB.N0000", "scrip")[0]
    assert n["ex_date"] == "2026-04-02" and n["ratio_held"] == pytest.approx(108.2352945432)
    assert n["factor"] == pytest.approx(109.2352945432 / 108.2352945432) and n["status"] == "confirmed"
    x = by(rows, "COMB.X0000", "scrip")[0]
    assert x["ratio_held"] == pytest.approx(97.88235324)


def test_rights_are_always_reviewed():
    rows = parse([rec("general_35920_HAYL_rights_dates.json", 35920, "HAYL.N0000", "RIGHTS ISSUE (DATES)", "2026-02-26",
                      "general")], CLASSES)
    r = by(rows, "HAYL.N0000", "rights")[0]
    assert (r["ex_date"], r["ratio_new"], r["ratio_held"]) == ("2026-03-18", 3.0, 50.0)
    assert r["status"] == "needs_review" and "subscription price not found" in r["note"]


@pytest.mark.parametrize("text,expected", [
    ("Three (3) new Ordinary Voting Shares for every Fifty (50) existing Ordinary Voting Shares ", (3.0, 50.0)),
    ("One (01) new Ordinary Voting Share for every One (01) existing issued Ordinary voting shares held", (1.0, 1.0)),
    ("1:5", None),                      # ambiguous order: never guessed
    (None, None),
])
def test_rights_text(text, expected):
    assert parse_rights_text(text) == expected


ROWS = [
    {"id": "1:A.N0000", "symbol": "A.N0000", "type": "cash_dividend", "ex_date": "2026-01-05", "amount_per_share": "2",
     "ratio_new": "", "ratio_held": "", "factor": "", "subscription_price": "", "status": "confirmed", "note": "", "source_url": ""},
    {"id": "2:A.N0000", "symbol": "A.N0000", "type": "rights", "ex_date": "2026-02-05", "amount_per_share": "",
     "ratio_new": "1", "ratio_held": "5", "factor": "", "subscription_price": "", "status": "needs_review", "note": "", "source_url": ""},
    {"id": "3:A.N0000", "symbol": "A.N0000", "type": "scrip", "ex_date": "2026-03-05", "amount_per_share": "",
     "ratio_new": "", "ratio_held": "", "factor": "", "subscription_price": "", "status": "needs_review", "note": "", "source_url": ""},
]


def test_unconfirmed_rows_are_never_used():
    eff = effective(ROWS, ({}, set()))
    assert [r["id"] for r in eff] == ["1:A.N0000"]


def test_confirmation_with_correction_and_rejection():
    eff = effective(ROWS, ({"2:A.N0000": {"subscription_price": 10.0}}, {"1:A.N0000"}))
    assert [r["id"] for r in eff] == ["2:A.N0000"]
    assert eff[0]["subscription_price"] == 10.0 and eff[0]["status"] == "confirmed (reviewed)"


def test_review_file_rejects_unknown_fields(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text('confirm:\n  "2:A.N0000": {price: 10}\n')
    with pytest.raises(ValueError, match="unknown fields"):
        corpactions.load_reviews(p)


def test_repo_review_file_is_valid():
    corpactions.load_reviews(Path(__file__).resolve().parents[1] / "config" / "corporate_actions_review.yaml")


def test_split_completed_later_supersedes_the_incomplete_row(tmp_path):
    """Day 1: only the ratio is known (needs_review). Later: the dates arrive and a complete row
    is appended under a new id; the old one drops out of both review and use."""
    from cse import storage
    typed = rec("typed_32457_CIC_split.json", 32457, "CIC.N0000", "SUB-DIVISION OF SHARES", "2025-08-05", "typed")
    dates = rec("general_33380_CIC_split_dates.json", 33380, "CIC.N0000", "SUB-DIVISION OF SHARES (DATES)",
                "2025-09-19", "general")
    p = tmp_path / "ca.csv"
    storage.append(p, corpactions.CA_COLS, parse([typed], CLASSES), key=("id",))
    storage.append(p, corpactions.CA_COLS, parse([typed, dates], CLASSES), key=("id",))
    rows = storage.read_rows(p)
    assert len(rows) == 4                                   # 2 old (needs_review) + 2 new, nothing rewritten
    assert corpactions.pending_review(rows, ({}, set())) == []
    eff = effective(rows, ({}, set()))
    assert sorted(r["id"] for r in eff) == ["32457+33380:CIC.N0000", "32457+33380:CIC.X0000"]
    assert all(r["factor"] == pytest.approx(5.0) and r["ex_date"] == "2025-10-22" for r in eff)


def test_repeated_announcement_for_same_event_is_counted_once():
    dup = dict(ROWS[0], id="9:A.N0000", amount_per_share="2.5")          # later announcement, same ex-date
    eff = effective([ROWS[0], dup], ({}, set()))
    assert len(eff) == 1 and eff[0]["id"] == "9:A.N0000" and eff[0]["amount_per_share"] == 2.5
