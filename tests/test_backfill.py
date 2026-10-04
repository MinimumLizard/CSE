import pytest

from cse import backfill, fetch, storage
from cse.models import StockChart, validate
from tests.conftest import load, snapshot_files


def test_price_rows_from_real_history():
    chart = validate(StockChart, load("companyChartDataByStock/CIC.N0000.json"), "cic")
    rows = backfill.price_rows("CIC.N0000", chart)
    assert len(rows) == len(chart.chartData)
    assert rows[0]["previous_close"] is None
    for a, b in zip(rows, rows[1:]):
        assert b["previous_close"] == a["close"] and a["date"] < b["date"]
    for r, bar in zip(rows, sorted(chart.chartData, key=lambda x: x.t)):
        assert r["turnover"] is None and r["turnover_est"] == pytest.approx(bar.p * bar.q)
        assert r["source"] == "backfill"


def test_backfilled_prices_are_stored_unadjusted():
    """CIC's 1:5 sub-division (Oct 2025) must stay visible in stored backfill prices."""
    chart = validate(StockChart, load("companyChartDataByStock/CIC.N0000.json"), "cic")
    rows = {r["date"]: r["close"] for r in backfill.price_rows("CIC.N0000", chart)}
    assert rows["2025-10-13"] == 170.0 and rows["2025-10-22"] == 34.2


def test_backfill_after_fetch_creates_no_duplicates(root, client):
    fetch.run(client, root)
    backfill.run(client, root)
    rows = storage.read_rows(fetch.Paths(root).prices)
    keys = [(r["date"], r["symbol"]) for r in rows]
    assert len(keys) == len(set(keys))
    lolc_last = [r for r in rows if r["symbol"] == "LOLC.N0000" and r["date"] == "2026-10-02"]
    assert len(lolc_last) == 1 and lolc_last[0]["source"] == "daily"
    assert any(r["source"] == "backfill" and r["symbol"] == "LOLC.N0000" for r in rows)


def test_backfill_is_idempotent(root, client):
    backfill.run(client, root)
    before = snapshot_files(root)
    backfill.run(client, root)
    assert snapshot_files(root) == before
