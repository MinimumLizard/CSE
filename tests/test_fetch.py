import pytest

from cse import fetch, storage
from cse.config import SymbolResolutionError
from tests.conftest import FakeClient, load, snapshot_files


def test_first_run_writes_history(root, client):
    msg = fetch.run(client, root)
    assert msg.startswith("session 2026-10-02")
    p = fetch.Paths(root)
    prices = storage.read_rows(p.prices)
    lolc = next(r for r in prices if r["symbol"] == "LOLC.N0000")
    assert lolc == {"date": "2026-10-02", "symbol": "LOLC.N0000", "close": "439.25", "previous_close": "443",
                    "high": "445", "low": "435", "volume": "5539", "turnover": "2412993", "turnover_est": "",
                    "source": "daily"}
    assert len(prices) == len(load("tradeSummary.json")["reqTradeSummery"])
    idx = {(r["date"], r["index"]): r for r in storage.read_rows(p.indices)}
    assert idx[("2026-10-02", "ASI")]["value"] == "20812.64"
    assert idx[("2026-10-02", "ASTRI")]["value"] == "32783.92"
    assert idx[("2026-10-01", "ASTRI")]["change"] == ""        # no earlier session to diff against
    assert float(idx[("2026-10-02", "ASTRI")]["change"]) == pytest.approx(32783.92 - 32891.44)
    market = storage.read_rows(p.market)
    assert [m["date"] for m in market] == ["2026-10-02", "2026-10-01"]
    assert (root / "data/raw/2026-10-02/tradeSummary.json").read_bytes() == \
        (FakeClient().fixtures / "tradeSummary.json").read_bytes()  # untouched


def test_announcements_symbol_url_and_dedupe(root, client):
    fetch.run(client, root)
    rows = {r["id"]: r for r in storage.read_rows(fetch.Paths(root).announcements)}
    rights = rows["39511"]
    assert rights["symbol"] == "WAPO" and rights["category"] == "RIGHTS ISSUE"
    assert rights["url"].startswith("https://cdn.cse.lk/cmt/announcement_portal_prod/Disclosure_Rights%20Issue")
    no_detail = rows["39516"]                     # detail returned 204: no PDF, symbol from company name
    assert no_detail["url"] == "" and no_detail["symbol"] == "FCT"
    assert any(i.startswith("F") for i in rows)   # financial reports included
    assert len(rows) == len(set(rows))


def test_rerun_same_session_appends_nothing(root, client):
    fetch.run(client, root)
    before = snapshot_files(root)
    calls_before = client.calls
    msg = fetch.run(client, root)
    assert msg.startswith("no new session")
    assert snapshot_files(root) == before
    assert client.calls - calls_before == 3        # status, security list, market summary; then stops
    assert storage.read_rows(fetch.Paths(root).runs)[-1]["status"] == "no_new_session"


def test_validation_failure_writes_nothing(root):
    bad = load("tradeSummary.json")
    del bad["reqTradeSummery"][3]["turnover"]
    with pytest.raises(ValueError, match="validation failed for tradeSummary"):
        fetch.run(FakeClient(overrides={"tradeSummary": bad}), root)
    assert snapshot_files(root) == {}
    assert storage.read_rows(fetch.Paths(root).runs)[-1]["status"] == "failed"


def test_validation_failure_keeps_previous_good_data(root, client):
    fetch.run(client, root)
    good = snapshot_files(root)
    bad = load("allSecurityCode.json")[:50]         # truncated security list fails validation
    with pytest.raises(ValueError, match="allSecurityCode"):
        fetch.run(FakeClient(overrides={"allSecurityCode": bad}), root)
    assert snapshot_files(root) == good


def test_missing_headline_index_fails(root):
    bad = load("allSectors.json")[:5]               # ASI / S&P SL20 rows missing
    with pytest.raises(ValueError, match="allSectors"):
        fetch.run(FakeClient(overrides={"allSectors": bad}), root)
    assert snapshot_files(root) == {}


def test_unknown_symbol_stops_and_names_it(root, client):
    (root / "config/universe.yaml").write_text("watchlist:\n  - LOLC.N0000\n  - LOLC.N000\nexcluded: []\n")
    with pytest.raises(SymbolResolutionError, match=r"LOLC\.N000\b"):
        fetch.run(client, root)
    assert snapshot_files(root) == {}


def test_inconsistent_session_dates_fail(root):
    dms = load("dailyMarketSummery.json")[1:]       # API claims the previous session is latest
    with pytest.raises(Exception, match="tradeSummary is for 2026-10-02"):
        fetch.run(FakeClient(overrides={"dailyMarketSummery": dms}), root)


def test_announcement_detail_falls_back_to_general_endpoint(client):
    detail, endpoint, raw = fetch.announcement_detail(client, 33380)   # CIC sub-division (dates)
    assert endpoint == "getGeneralAnnouncementById"
    assert detail.reqBaseAnnouncement.symbol == "CIC"
    assert fetch.doc_url(detail).startswith("https://cdn.cse.lk/cmt/announcement_portal_prod/")
    assert [e for e, _ in client.log] == ["getAnnouncementById", "getGeneralAnnouncementById"]


def test_typed_detail_is_used_when_available(client):
    detail, endpoint, _ = fetch.announcement_detail(client, 39511)
    assert endpoint == "getAnnouncementById" and detail.reqBaseAnnouncement.dType == "RightsIssue"
