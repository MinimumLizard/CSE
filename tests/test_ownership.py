"""Ownership tracker. Fixtures are real: tests/fixtures/ownership/text/ holds the text of each
company's interim report exactly as `cse.ownership.collect` extracted it from cdn.cse.lk, and
companyInfoSummery/ the CSE share counts captured on 2026-10-02. Each case below is a layout
that a simpler parser got wrong."""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from cse.ownership import analyse, names, parse

OWN = Path(__file__).parent / "fixtures" / "ownership"
FIX = Path(__file__).parent / "fixtures" / "2026-10-02"
REPORTS = {r["code"]: r for r in csv.DictReader((OWN / "reports.csv").open())}


def text(code: str) -> str:
    return (OWN / "text" / f"{REPORTS[code]['id']}.txt").read_text(errors="replace")


def issued(code: str) -> dict[str, float | None]:
    out = {}
    for cls in ("N", "X"):
        f = OWN / "companyInfoSummery" / f"{code}.{cls}0000.json"
        out[cls] = float(json.loads(f.read_bytes())["reqSymbolInfo"]["quantityIssued"]) if f.exists() else None
    return out


def best(code: str) -> dict[str, parse.Table]:
    return parse.best_tables(parse.find_tables(text(code)), issued(code))


def names_of(t: parse.Table) -> list[str]:
    return [r["name"] for r in t.rows]


# --- parsing --------------------------------------------------------------------------------

def test_directors_table_after_top20_is_not_merged():
    t = best("ACL")["N"]
    assert t.status == "verified" and len(t.rows) == 20
    assert not any("Chairman" in n for n in names_of(t))
    assert t.stated_total_pct == 79.86                  # unlabelled total line ends the table


def test_voting_and_non_voting_tables_are_separate():
    b = best("HNB")
    assert b["N"].status == "verified" and b["X"].status == "verified"
    assert len(b["N"].rows) == 20 and len(b["X"].rows) == 21      # two holders tie at rank 14
    milford = [r for r in b["N"].rows if r["name"].startswith("MILFORD EXPORTS")]
    assert milford and milford[0]["pct"] == 7.91        # '** 7.91' footnote marker
    assert "METROCORP" in b["X"].rows[0]["name"]


def test_holders_with_no_shares_this_period_are_dropped():
    t = best("CTLD")["N"]
    assert t.status == "verified"                        # 1-decimal percentages: tolerance-aware check
    assert not any("0.0%" in n for n in names_of(t))
    assert len(t.rows) == 20


def test_wrapped_name_with_prior_period_numbers_is_one_row():
    t = best("JAT")["N"]
    assert t.status == "verified" and len(t.rows) == 20
    assert not any(n.startswith("PENSION FUND") for n in names_of(t))
    assert any("CAPITAL ALLIANCE QUANTITATIVE EQUITY" in n.upper() for n in names_of(t))


def test_sub_numbered_rows_are_kept():
    t = best("CFVF")["N"]
    assert t.status == "verified"
    assert sum("Janashakthi" in n for n in names_of(t)) >= 9


def test_distribution_table_is_not_a_shareholder_table():
    t = best("EMER")["N"]
    assert t.status == "verified"
    assert not any(n.startswith("Over ") for n in names_of(t))
    assert t.rows[0]["name"] == "J. B. L. De Silva"


def test_whole_number_percentages():
    t = best("CWL")["N"]
    assert t.status == "verified" and t.rows[0]["pct"] == 67


def test_name_above_its_numbers():
    t = best("BREW")["N"]
    assert t.status == "verified"
    assert any(n.startswith("DEUTSCHE BANK AG SINGAPORE") and r["shares"] == 128211
               for n, r in zip(names_of(t), t.rows))


def test_per_holder_subtotal_inside_table():
    t = best("PHAR")["N"]
    assert t.status == "verified" and len(t.rows) == 21
    assert t.rows[0]["name"].startswith("SEYLAN BANK PLC/ AMBEON HOLDINGS PLC")


def test_wrapped_prefix_above_first_row():
    t = best("GLAS")["N"]
    assert t.rows[0]["name"].startswith("DEUTSCHE BANK AG COLOMBO BRANCH")
    assert names.resolve(t.rows[0]["name"], {}, {}).name == "PGP GLASS PRIVATE LIMITED"


def test_ocr_garbage_is_never_verified():
    assert best("AFSL")["N"].status == "unverified"


def test_share_count_change_uses_the_reports_own_total_and_skips_preference_shares():
    t = best("AAF")["N"]
    assert t.status == "verified_total"
    assert "PREFERENCE" not in t.heading.upper()
    assert t.period_total == t.class_total_shares


# --- names ----------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def listed():
    from cse.models import SecurityList, validate
    secs = validate(SecurityList, json.loads((FIX / "allSecurityCode.json").read_bytes()), "sec").root
    return names.listed_index(secs)


@pytest.mark.parametrize("filed, owner, typ, via", [
    ("DEUTSCHE BANK AG AS TRUSTEE TO ASSETLINE INCOME PLUS GROWTH FUND", "ASSETLINE INCOME PLUS GROWTH FUND",
     "institution", "DEUTSCHE BANK AG AS TRUSTEE TO"),
    ("SAMPATH BANK PLC/ ANDARADENIYA ESTATE PRIVATE LIMITED", "ANDARADENIYA ESTATE PRIVATE LIMITED", "company",
     "SAMPATH BANK PLC"),
    ("UNION ASSURANCE PLC-UNIVERSAL LIFE FUND", "UNION ASSURANCE PLC-UNIVERSAL LIFE FUND", "institution", ""),
    ("BANK OF CEYLON A/C CEYBANK UNIT TRUST", "CEYBANK UNIT TRUST", "institution", "BANK OF CEYLON"),
    ("Hatton National Bank PLC - Senfin Growth Fund", "Senfin Growth Fund", "institution", "Hatton National Bank PLC"),
    ("BBH-Tundra Sustainable Frontier Fund", "Tundra Sustainable Frontier Fund", "institution", "BBH"),
    ("Mr. Husseinally Mohsinally Abdulhussein joint with Mrs. Saema Enayat Lokhandwalla",
     "Mr. Husseinally Mohsinally Abdulhussein joint with Mrs. Saema Enayat Lokhandwalla", "joint", ""),
    ("ESTATE OF LATE S. MAHADEVA", "ESTATE OF LATE S. MAHADEVA", "estate", ""),
    ("CARLSBERG A/S", "CARLSBERG A/S", "company", ""),
])
def test_beneficial_owner_rules(filed, owner, typ, via, listed):
    e = names.resolve(filed, listed, {})
    assert (e.name, e.type, e.via) == (owner, typ, via)


def test_listed_holders_are_recognised_through_accounts(listed):
    for filed, code in (("HAYLEYS PLC", "HAYL"), ("Hayleys PLC No 3 Share Investment Account", "HAYL"),
                        ("SEYLAN BANK PLC/AMBEON CAPITAL PLC (COLLATERAL)", "TAP"),
                        ("CARSON CUMBERBATCH PLC A/C NO.2", "CARS")):
        e = names.resolve(filed, listed, {})
        assert (e.type, e.symbol) == ("listed", code), filed


def test_life_fund_is_not_the_listed_insurer(listed):
    e = names.resolve("SRI LANKA INSURANCE CORPORATION LTD-LIFE FUND", listed, {})
    assert e.type == "institution" and e.symbol == ""


def test_repeated_cell_is_deduplicated():
    assert names.dedupe_repeat("J.B Cocoshell (Pvt) LtdJ.B. COCOSHELL (PVT) LTD") == "J.B Cocoshell (Pvt) Ltd"
    assert names.dedupe_repeat("Mr. A. B. Perera") == "Mr. A. B. Perera"


def test_individuals_merge_only_on_exact_normalised_names():
    assert names.key_of("Mr. Y. S. H. I. Silva") == names.key_of("MR. Y.S.H.I. SILVA")
    assert names.key_of("Mr. Y.S.H.I. Silva") != names.key_of("Mr. Y.S.H.R.S. Silva")


def test_alias_file_wins(tmp_path, listed):
    (tmp_path / "a.yaml").write_text("aliases:\n  Employees' Provident Fund:\n    variants: [EPF]\n    type: institution\n")
    e = names.resolve("EPF", listed, names.load_aliases(tmp_path / "a.yaml"))
    assert (e.name, e.type, e.rule) == ("Employees' Provident Fund", "institution", "alias")


# --- graph ----------------------------------------------------------------------------------

def make_root(root: Path) -> Path:
    sess = root / "data" / "raw" / "2026-10-02"
    (sess / "companyInfoSummery").mkdir(parents=True)
    for f in ("allSecurityCode.json", "tradeSummary.json"):
        shutil.copy(FIX / f, sess / f)
    for f in (OWN / "companyInfoSummery").iterdir():
        shutil.copy(f, sess / "companyInfoSummery" / f.name)
    shutil.copytree(OWN / "text", root / "data" / "raw" / "ownership" / "text")
    shutil.copy(OWN / "reports.csv", root / "data" / "raw" / "ownership" / "reports.csv")
    return root


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    root = make_root(tmp_path_factory.mktemp("own"))
    res = analyse.build(root)
    analyse.write(res, root)
    return root, res


def test_control_chain_through_a_listed_holding_company(result):
    _root, res = result
    c = res["companies"]
    assert c["ALUM"]["control_level"] == "controlled"
    assert c["ALUM"]["chain"].split(" <- ")[:2] == ["ALUM", "HAYL"]
    # Hayleys' controller and Alumex's ultimate controller are the same person.
    assert c["ALUM"]["controller_key"] == c["HAYL"]["controller_key"]
    assert c["DIST"]["chain"].split(" <- ")[:2] == ["DIST", "MELS"]


def test_lookthrough_matches_hand_calculation(result):
    _root, res = result
    rows, m = res["current"], res["market"]
    hayl = analyse.entity_for("HAYLEYS PLC", m, {}).key
    top = res["companies"]["HAYL"]["controller_key"]
    def frac(owner, code):
        return sum(r["fraction"] for r in rows if r["owner_key"] == owner and r["code"] == code)
    expected = (frac(top, "HAYL") * m.company_cap("HAYL")
                + (frac(top, "ALUM") + frac(top, "HAYL") * frac(hayl, "ALUM")) * m.company_cap("ALUM"))
    assert res["owners"][top]["lookthrough_value"] == pytest.approx(expected, rel=1e-9)
    assert hayl not in {k for k, o in res["owners"].items() if o["lookthrough_value"] > 0}


def test_lookthrough_never_attributes_more_than_the_market(result):
    _root, res = result
    g = res["graph"]
    assert 0 < g["attributed_value"] <= g["covered_cap"]
    total_look = sum(o["lookthrough_value"] for o in res["owners"].values())
    assert total_look == pytest.approx(g["attributed_value"], rel=1e-9)


def test_outputs_are_written_and_reproducible(result):
    root, res = result
    out = root / "data" / "ownership"
    first = {f: (out / f).read_bytes() for f in ("holdings.csv", "companies.csv", "owners.csv")}
    analyse.write(analyse.build(root), root)
    assert {f: (out / f).read_bytes() for f in first} == first
    latest = json.loads((out / "latest.json").read_bytes())
    assert latest["summary"]["verified_companies"] == sum(
        1 for c in res["companies"].values() if c["status_N"] in analyse.USABLE or c["status_X"] in analyse.USABLE)
    assert np.isfinite([o["direct_value"] for o in latest["owners"]]).all()


def test_groups_for_the_exposure_cap(result):
    from cse.ownership import groups
    root, _res = result
    g, labels = groups.group_of(root)
    assert g["ALUM"] == g["HAYL"]                       # Alumex <- Hayleys <- the same individual
    assert g["DIST"] == g["MELS"]                       # Distilleries <- Melstacorp <- Milford (43 %, influence)
    assert not g["DIST"].startswith("LISTED:") and "MILFORD" in labels[g["DIST"]].upper()
    sym, _ = groups.for_symbols(root, ["DIST.N0000", "MELS.N0000", "ACL.N0000"])
    assert sym["DIST.N0000"] == sym["MELS.N0000"] != sym["ACL.N0000"]


# --- confirmed owners of unlisted holders (config/ownership_parents.yaml) ---------------------------
# Quotes are the companies' own words: CT Holdings' interim report (fixture text, line 533) and its
# 2025/26 annual report (cdn.cse.lk/cmt/upload_report_file/503_1785423763477.pdf, note on contributions).

ODEON_CONTROL = """parents:
  Odeon Holdings (Ceylon) (Private) Ltd:
    owner: Mr. Louis Page
    owner_type: individual
    source: https://cdn.cse.lk/cmt/upload_report_file/503_1785423763477.pdf
    quote: "The ultimate beneficial owner of the CT Holdings PLC is Mr. Louis Page."
"""
ODEON_OWNED = ODEON_CONTROL.replace("    owner_type: individual\n", "    owner_type: individual\n    pct: 100\n").replace(
    'quote: "The ultimate beneficial owner of the CT Holdings PLC is Mr. Louis Page."',
    'quote: "Odeon Holdings (Ceylon) (Private) Limited (a company wholly owned by the Chairman, Mr. L R Page)"')


def built_with(tmp_path, cfg: str | None):
    root = make_root(tmp_path)
    if cfg:
        (root / "config").mkdir()
        (root / "config" / "ownership_parents.yaml").write_text(cfg)
    return analyse.build(root)


def test_without_confirmed_parent_chain_stops_at_the_unlisted_holder(tmp_path):
    res = built_with(tmp_path, None)
    assert res["companies"]["CTHR"]["controller_name"].startswith("Odeon Holdings")
    assert res["companies"]["CARG"]["chain"].split(" <- ")[:2] == ["CARG", "CTHR"]


def test_confirmed_control_link_moves_control_but_not_value(tmp_path):
    base = built_with(tmp_path / "a", None)
    res = built_with(tmp_path / "b", ODEON_CONTROL)
    page = analyse.entity_for("Mr. Louis Page", res["market"], {}).key
    c = res["companies"]
    assert c["CTHR"]["controller_key"] == page and c["CTHR"]["control_level"] == "controlled"
    assert c["CARG"]["controller_key"] == page               # CARG <- CTHR <- Odeon <- Mr. Louis Page
    assert c["CARG"]["chain"].endswith("Mr. Louis Page")
    odeon = names.key_of("Odeon Holdings (Ceylon) (Private) Ltd")
    # no percentage stated: value stays with Odeon
    assert res["owners"][odeon]["lookthrough_value"] == pytest.approx(base["owners"][odeon]["lookthrough_value"])


def test_confirmed_ownership_link_passes_value_up(tmp_path):
    base = built_with(tmp_path / "a", None)
    res = built_with(tmp_path / "b", ODEON_OWNED)
    odeon = names.key_of("Odeon Holdings (Ceylon) (Private) Ltd")
    page = analyse.entity_for("Mr. Louis Page", res["market"], {}).key
    before_page = base["owners"].get(page, {}).get("lookthrough_value", 0.0)
    assert res["owners"][odeon]["lookthrough_value"] == pytest.approx(0.0, abs=1.0)   # rupees
    assert res["owners"][page]["lookthrough_value"] == pytest.approx(
        before_page + base["owners"][odeon]["lookthrough_value"], rel=1e-9)
    total = lambda r: sum(o["lookthrough_value"] for o in r["owners"].values())
    assert total(res) == pytest.approx(total(base), rel=1e-9)  # value moves, never appears


def test_parent_file_requires_evidence(tmp_path):
    from cse.ownership import parents
    bad = tmp_path / "p.yaml"
    bad.write_text("parents:\n  X Ltd:\n    owner: Y\n    source: https://cdn.cse.lk/a.pdf\n")
    with pytest.raises(ValueError, match="needs 'quote'"):
        parents.load(bad)


def test_statements_recognised_in_report_text():
    from cse.ownership import parents
    found = parents.statements(text("CTHR"))
    assert ("ultimate controlling party", "", "Mr. Louis Page") in found
