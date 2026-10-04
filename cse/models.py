"""Pydantic models for every API response the job uses.

Built from the real Step 0 responses in docs/api_samples/. Fields we rely on are required
and typed; unknown extra fields are ignored so additive API changes do not break the run,
but a renamed, missing or mistyped field fails validation and therefore fails the run.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, RootModel, model_validator

SLT = dt.timezone(dt.timedelta(hours=5, minutes=30))


def ms_to_date(ms: int) -> dt.date:
    """API timestamps are epoch milliseconds; daily bars are stamped 00:00 Sri Lanka time."""
    return dt.datetime.fromtimestamp(ms / 1000, SLT).date()


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


# --- market-wide ---------------------------------------------------------------------------

class TradeSummaryRow(Model):
    id: int
    symbol: str = Field(min_length=1)
    name: str
    price: float = Field(gt=0)
    closingPrice: float = Field(gt=0)
    previousClose: float = Field(ge=0)  # 0 on a new listing's first day
    open: float | None = None
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    change: float
    percentageChange: float
    sharevolume: int = Field(ge=0)
    tradevolume: int = Field(ge=0)
    turnover: NonNegativeFloat
    marketCap: float | None = None
    lastTradedTime: int

    @model_validator(mode="after")
    def _range(self):
        if self.high < self.low:
            raise ValueError(f"{self.symbol}: high {self.high} < low {self.low}")
        return self


class TradeSummary(Model):
    reqTradeSummery: list[TradeSummaryRow] = Field(min_length=1)


class Security(Model):
    id: int
    name: str
    symbol: str = Field(min_length=1)
    active: int


class SecurityList(RootModel[list[Security]]):
    @model_validator(mode="after")
    def _non_empty(self):
        if len(self.root) < 100:
            raise ValueError(f"security list has only {len(self.root)} rows")
        return self


class DailyMarketRow(Model):
    tradeDate: int
    marketTurnover: NonNegativeFloat
    volumeOfTurnOverNumber: NonNegativeFloat
    tradesNo: int = Field(ge=0)
    equityForeignPurchase: NonNegativeFloat
    equityForeignSales: NonNegativeFloat
    listedCompanyNumber: int
    tradeCompanyNumber: int
    marketCap: NonNegativeFloat
    asi: float = Field(gt=0)
    spp: float | None = None
    triasi: float | None = None
    spt: float | None = None
    per: float | None = None
    pbv: float | None = None
    dy: float | None = None

    @property
    def session(self) -> dt.date:
        return ms_to_date(self.tradeDate)


class DailyMarketSummary(RootModel[list[list[DailyMarketRow]]]):
    """`[[latest session], [previous session]]`."""

    @model_validator(mode="after")
    def _shape(self):
        if not self.root or any(len(x) != 1 for x in self.root):
            raise ValueError("expected [[row], [row]]")
        sessions = [x[0].session for x in self.root]
        if sessions != sorted(sessions, reverse=True):
            raise ValueError(f"sessions not newest-first: {sessions}")
        return self

    @property
    def latest(self) -> DailyMarketRow:
        return self.root[0][0]

    @property
    def rows(self) -> list[DailyMarketRow]:
        return [x[0] for x in self.root]


class SectorRow(Model):
    sectorId: int
    symbol: str
    name: str
    indexValue: float = Field(gt=0)
    change: float
    percentage: float
    sectorTurnoverToday: float | None = None
    sectorVolumeToday: float | None = None
    sectorTradeToday: float | None = None
    sectorPreviousClose: float | None = None
    transactionTime: int


class SectorList(RootModel[list[SectorRow]]):
    @model_validator(mode="after")
    def _has_headline_indices(self):
        ids = {r.sectorId for r in self.root}
        if not {1, 40} <= ids:
            raise ValueError("allSectors is missing ASI (1) or S&P SL20 (40)")
        return self


class IndexTick(Model):
    value: float = Field(gt=0)
    change: float
    percentage: float
    highValue: float | None = None
    lowValue: float | None = None
    timestamp: int


class MarketSummery(Model):
    tradeVolume: NonNegativeFloat    # LKR turnover, despite the name
    shareVolume: NonNegativeFloat
    trades: int = Field(ge=0)
    tradeDate: int                   # last-update timestamp, not the session date


class MarketStatus(Model):
    status: str


# --- per security ---------------------------------------------------------------------------

class SymbolInfo(Model):
    id: int
    symbol: str
    name: str
    lastTradedPrice: float | None = None
    previousClose: float | None = None
    p12HiPrice: float | None = None
    p12LowPrice: float | None = None
    marketCap: float | None = None
    quantityIssued: int | None = None
    foreignPercentage: float | None = None


class BetaInfo(Model):
    triASIBetaValue: float | None = None
    betaValueSPSL: float | None = None
    triASIBetaPeriod: str | None = None
    quarter: int | None = None


class CompanyInfo(Model):
    reqSymbolInfo: SymbolInfo
    reqSymbolBetaInfo: BetaInfo | None = None


class ChartBar(Model):
    t: int
    p: float = Field(gt=0)
    h: float | None = None
    l: float | None = None
    q: NonNegativeFloat


class StockChart(Model):
    chartData: list[ChartBar]


class IndexPoint(Model):
    d: int
    v: float = Field(gt=0)
    pc: float | None = None


class IndexChart(RootModel[list[IndexPoint]]):
    pass


class ComSumInfo(Model):
    symbol: str | None = None
    sector: str | None = None
    boardType: str | None = None


class CompanyProfile(Model):
    reqComSumInfo: list[ComSumInfo] = Field(default_factory=list)


# --- announcements --------------------------------------------------------------------------

class AnnouncementRow(Model):
    id: int
    announcementId: int
    dateOfAnnouncement: str
    createdDate: int | None = None
    announcementCategory: str
    company: str
    remarks: str | None = None

    @property
    def date(self) -> dt.date:
        return dt.datetime.strptime(self.dateOfAnnouncement.strip(), "%d %b %Y").date()


class ApprovedAnnouncements(Model):
    approvedAnnouncements: list[AnnouncementRow]


class AnnouncementDoc(Model):
    fileUrl: str
    baseUrl: str | None = None


class BaseAnnouncement(Model):
    model_config = ConfigDict(extra="allow", frozen=True)  # keep typed CA fields for Phase 2
    id: int
    dType: str | None = None
    symbol: str | None = None
    companyName: str | None = None
    remarks: str | None = None


class AnnouncementDetail(Model):
    reqBaseAnnouncement: BaseAnnouncement
    reqAnnouncementDocs: list[AnnouncementDoc] | None = None


class FinancialAnnouncementRow(Model):
    id: int
    path: str
    fileText: str
    name: str
    symbol: str | None = None
    uploadedDate: str

    @property
    def date(self) -> dt.date:
        return dt.datetime.strptime(self.uploadedDate.strip(), "%d %b %Y %I:%M:%S %p").date()


class FinancialAnnouncements(Model):
    reqFinancialAnnouncemnets: list[FinancialAnnouncementRow]


def validate(model: type[BaseModel], data: Any, what: str):
    """Validate or raise a ValueError naming the endpoint, so the run fails loudly."""
    try:
        return model.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError and our own ValueErrors
        raise ValueError(f"validation failed for {what}: {exc}") from exc
