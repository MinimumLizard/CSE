"""Fundamentals read from real interim-report text (tests/fixtures/fundamentals/, extracted from
cdn.cse.lk PDFs by cse.ownership.collect) and CSE share counts captured on 2026-10-02."""
import csv
import json
from pathlib import Path

import pytest

from cse import fundamentals as f

FIX = Path(__file__).parent / "fixtures" / "fundamentals"
REPORTS = {r["id"]: r for r in csv.DictReader((FIX / "reports.csv").open())}


def shares(code):
    total = 0.0
    for cls in "NX":
        p = FIX / "companyInfoSummery" / f"{code}.{cls}0000.json"
        if p.exists():
            total += float(json.loads(p.read_bytes())["reqSymbolInfo"]["quantityIssued"])
    return total


def text(rid):
    return (FIX / "text" / f"{rid}.txt").read_text(errors="replace")


def test_bank_eps_and_nav_reconcile_with_profit_and_equity():
    x = f.extract(text("52504"), shares("COMB"))                 # COMB, quarter to 30 Jun 2026
    assert x["eps"] == 21.14 and x["unit"] == 1000.0
    assert x["eps"] * shares("COMB") == pytest.approx(x["profit"], rel=0.03)
    assert x["nav"] == 216.14 and x["nav"] * shares("COMB") == pytest.approx(x["equity"], rel=0.03)


def test_eps_on_a_separate_basic_line():
    x = f.extract(text("52111"), shares("JKH"))                  # 'Earnings per share' / 'Basic  0.004'
    assert x["eps"] == 0.004 and x["eps_class"] == "quarter"


def period_shares(rid, code):
    """The report's own period-end share count, from its verified top-20 table (as the pipeline does)."""
    from cse.ownership import parse
    issued = {"N": shares(code), "X": None}
    t = parse.best_tables(parse.find_tables(text(rid)), issued)["N"]
    return t.period_total


def test_note_reference_is_not_an_eps():
    x = f.extract(text("51564"), period_shares("51564", "HAYL"))   # 'Earnings per share   9' (note 9)
    assert x["eps"] == 18.71 and x["eps_class"] == "ytd"         # the year to 31 March 2026


def test_wrong_share_count_does_not_reconcile():
    assert f.extract(text("52504"), shares("COMB") * 1.37)["eps"] is None


def test_ttm_is_built_from_profit_so_a_split_does_not_distort_it():
    rows = []
    for rid, r in REPORTS.items():
        if r["code"] != "NAMU":
            continue
        x = f.extract(text(rid), shares("NAMU"))
        if x["eps"] is None:                                       # pre-split reports: their own share count
            x = f.extract(text(rid), shares("NAMU") / 10)
        rows.append({"period_date": r["period_date"], "eps_status": "verified" if x["eps"] is not None else "",
                     "eps": x["eps"], "eps_class": x["eps_class"], "profit": x["profit"]})
    profit, to, how = f.ttm(rows, "1")
    assert to == "2026-06-30" and how.startswith("sum of 4")
    eps = profit / shares("NAMU")
    assert 4 < eps < 15                                            # summing per-share figures across the split gives ~45
    assert sum(r["eps"] for r in rows if r["period_date"] > "2025-06-30") > 40


@pytest.mark.parametrize("period, fye, expected", [
    ("2026-06-30", "1", (2027, 1)), ("2026-03-31", "1", (2026, 4)), ("2025-12-31", "1", (2026, 3)),
    ("2026-06-30", "2", (2026, 2)), ("2025-12-31", "2", (2025, 4)), ("2026-07-28", "1", None),
])
def test_fiscal_quarter(period, fye, expected):
    assert f.fiscal_quarter(period, fye) == expected


@pytest.mark.parametrize("title, period, expected", [
    ("Interim Financial Statements for the Quarter ended 30th June 2026", "2026-07-28", "2026-06-30"),
    ("Quarterly Financial Report as at 30/06/2025", "2025-06-30", "2025-06-30"),
    ("Interim Financial Statements", "2026-05-26", "2026-03-31"),
])
def test_quarter_end(title, period, expected):
    assert f.quarter_end(title, period) == expected
