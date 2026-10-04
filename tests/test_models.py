import copy

import pytest

from cse.models import (AnnouncementDetail, ApprovedAnnouncements, CompanyInfo, DailyMarketSummary,
                        FinancialAnnouncements, IndexChart, SectorList, SecurityList, StockChart,
                        TradeSummary, ms_to_date, validate)
from tests.conftest import load


@pytest.mark.parametrize("model,name", [
    (TradeSummary, "tradeSummary.json"),
    (SecurityList, "allSecurityCode.json"),
    (DailyMarketSummary, "dailyMarketSummery.json"),
    (SectorList, "allSectors.json"),
    (CompanyInfo, "companyInfoSummery/LOLC.N0000.json"),
    (StockChart, "companyChartDataByStock/CIC.N0000.json"),
    (IndexChart, "chartData_1_p5.json"),
    (ApprovedAnnouncements, "approvedAnnouncement.json"),
    (AnnouncementDetail, "getAnnouncementById/39511.json"),
    (FinancialAnnouncements, "getFinancialAnnouncement.json"),
])
def test_real_responses_validate(model, name):
    validate(model, load(name), name)


def test_session_date_comes_from_daily_market_summary():
    dms = validate(DailyMarketSummary, load("dailyMarketSummery.json"), "dms")
    assert dms.latest.session.isoformat() == "2026-10-02"
    assert [r.session.isoformat() for r in dms.rows] == ["2026-10-02", "2026-10-01"]


def test_missing_field_fails():
    data = load("tradeSummary.json")
    del data["reqTradeSummery"][0]["closingPrice"]
    with pytest.raises(ValueError, match="tradeSummary"):
        validate(TradeSummary, data, "tradeSummary")


def test_renamed_container_fails():
    data = load("tradeSummary.json")
    data = {"reqTradeSummary": data["reqTradeSummery"]}  # the 'correct' spelling is NOT what the API uses
    with pytest.raises(ValueError):
        validate(TradeSummary, data, "tradeSummary")


def test_wrong_type_fails():
    data = load("tradeSummary.json")
    data["reqTradeSummery"][0]["price"] = "n/a"
    with pytest.raises(ValueError):
        validate(TradeSummary, data, "tradeSummary")


def test_empty_trade_summary_fails():
    with pytest.raises(ValueError):
        validate(TradeSummary, {"reqTradeSummery": []}, "tradeSummary")


def test_high_below_low_fails():
    data = load("tradeSummary.json")
    row = data["reqTradeSummery"][0]
    row["high"], row["low"] = row["low"] - 1, row["high"]
    with pytest.raises(ValueError, match="high"):
        validate(TradeSummary, data, "tradeSummary")


def test_daily_market_summary_order_checked():
    data = load("dailyMarketSummery.json")
    with pytest.raises(ValueError, match="newest-first"):
        validate(DailyMarketSummary, list(reversed(copy.deepcopy(data))), "dms")


def test_bars_are_stamped_midnight_sri_lanka_time():
    bar = load("companyChartDataByStock/CIC.N0000.json")["chartData"][-1]
    assert ms_to_date(bar["t"]).isoformat() == "2026-10-02"
