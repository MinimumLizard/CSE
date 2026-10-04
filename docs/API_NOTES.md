# CSE API — Step 0 findings

Probed 2026-10-04 (a Sunday) from a cloud container. All data refer to the last session,
**Friday 2026-10-02**. Every claim below comes from a real response; trimmed copies of
those responses are in [`docs/api_samples/`](api_samples/), and the full raw responses
used by the market screen are in `data/raw/2026-10-02/`.

**Verdict:** the API is reachable, needs no login for everything Phase 1 needs, and is richer
than reported in one area (structured corporate actions). It falls short of the brief in
four places. Each one needs your decision before I build — see
[§7 Decisions needed](#7-decisions-needed).

| # | Gap vs. the brief | Impact |
|---|---|---|
| G1 | Per-stock daily history goes back **1 year only** (240 sessions). | `min_history_weeks: 104` can't be met for about another year. |
| G2 | History is **unadjusted** for sub-divisions (proven) and carries **no turnover** field. | We must adjust ourselves. Backfilled turnover would have to be estimated. |
| G3 | **Foreign holding % is `null`** for every stock tested. | A Panel B column can't be filled. |
| G4 | **Total-return index (ASTRI) level is available for the current session only**. There's no history. | Phase 2 market proxy has to be ASPI until TRI history builds up. |

---

## 1. Conventions

- **Base URL:** `https://www.cse.lk/api/`. No auth, cookies or API key needed for any endpoint below.
- **Methods:** most endpoints are `POST`. A few are **`GET` only** and return `405` to POST
  (`allSecurityCode`, `cntSecurity`, `lastUpdateTime`, `previousUpdateTime`,
  `corporateAnnouncementCategory`, `returnAspiSnp`).
- **Parameters:** sent as `application/x-www-form-urlencoded` form fields, not JSON.
  An empty POST body works for the market-wide endpoints. A missing required parameter gives
  `400 {"apierror":{"status":"BAD_REQUEST","message":"<param> parameter is missing"}}`.
- **Timestamps:** epoch **milliseconds**. Daily bars are stamped at **00:00 Sri Lanka time**
  (UTC+05:30), e.g. `1790879400000` = 2026-10-02 00:00 SLT. Intraday values carry real times.
- **Two id spaces.** Don't mix them up:
  - `id` in `tradeSummary` / `allSecurityCode` / `reqSymbolInfo.id` (LOLC.N0000 = 410). This is
    the `stockId` that `companyChartDataByStock` expects.
  - `securityId` in `reqSymbolBetaInfo`, `companyProfile`, `cntSecurity`, logo paths (LOLC = 378).
    This is a per-*company* id.
  - Probe trap: `stockId=378` returns **CLND**'s history, not LOLC's.
- **Field spelling** as reported: `reqTradeSummery`, `companyInfoSummery`, `marketSummery`,
  `dailyMarketSummery`, `reqFinancialAnnouncemnets`. Use them exactly as spelled.
- **Latency:** about 1–4 s per call. `robots.txt` disallows nothing under `/api/`.
- **Market hours:** 09:30–14:30 SLT, Mon–Fri. Evidence: the intraday ASPI series
  (`chartData` period 1) runs 09:30→14:29, and `lastUpdateTime` reads 14:57 SLT. The proposed
  cron of 11:00 UTC (16:30 SLT) leaves about 90 minutes after the final update. ✔

## 2. Endpoint catalogue (verified)

### Market-wide (Phase 1 daily fetch)

| Endpoint | Method / params | Returns | Fields we use |
|---|---|---|---|
| `tradeSummary` | POST, empty | `{"reqTradeSummery":[…]}`. **Only securities that traded that session** (278 on 2026-10-02, every row with volume > 0). | `id, symbol, name, price, closingPrice, previousClose, open, high, low, sharevolume, tradevolume, turnover, marketCap, change, percentageChange, lastTradedTime` |
| `allSecurityCode` | **GET** | List of all 327 listed instruments: `{id,name,symbol,active}` | symbol resolution, `id`→`stockId` |
| `dailyMarketSummery` | POST, empty | `[[{…}],[{…}]]`: **`[0]` is the latest session, `[1]` the previous session** (2026-10-02 and 2026-10-01 here), one record each | **`tradeDate`** (session date, midnight SLT), `marketTurnover, volumeOfTurnOverNumber, tradesNo`, foreign purchase/sales, `listedCompanyNumber, tradeCompanyNumber`, `marketCap`, **`triasi`** (ASPI TRI level), **`spt`** (S&P SL20 TRI), `asi`, `spp`, market `per`, `pbv`, `dy` |
| `marketSummery` | POST, empty | `{tradeVolume (=turnover, LKR), shareVolume, trades, tradeDate}`. Here `tradeDate` is the *last-update timestamp*, not the session date. | cross-check only |
| `aspiData` / `snpData` | POST, empty | `{value, highValue, lowValue, change, percentage, timestamp}` | index level and change |
| `allSectors` | POST, empty | 22 rows: 20 S&P/CSE GICS industry-group indices + ASI (`sectorId` 1) + S&P SL20 (`sectorId` 40) | `sectorId, symbol, name, indexValue, change, percentage, sectorTurnoverToday, sectorVolumeToday, sectorTradeToday` |
| `marketIndices` | POST, empty | `[[…20 sector rows…]]`: the same as `allSectors` minus ASI and SL20 | not needed (duplicate) |
| `aspi/year` | POST, empty | YTD % only: `{aspiValueForYear, triAspiValue, snpValueForYear, triSnpValue}` | YTD TRI % (cross-check) |
| `lastUpdateTime` | GET | `{lastUpdatedTime}` ms | staleness check |
| `marketStatus` | POST, empty | `{"status":"Market Closed"}` | guard (don't snapshot mid-session) |

Trimmed `tradeSummary` row (real):
```json
{"id":204,"name":"ABANS ELECTRICALS PLC","symbol":"ABAN.N0000","quantity":3,
 "percentageChange":1.5988,"change":16.5,"price":1048.5,"previousClose":1032.0,
 "high":1050.0,"low":1031.0,"lastTradedTime":1790931013823,"issueDate":"01/JAN/1984",
 "turnover":175294.0,"sharevolume":168,"tradevolume":17,"marketCap":5358422160.0,
 "marketCapPercentage":0.0,"open":1031.0,"closingPrice":1048.5,"crossingVolume":168,
 "crossingTradeVol":17,"status":0}
```
`price == closingPrice` for all 278 rows that day. `status` takes values 0–5 with no documented
meaning, so I won't use it.

Trimmed `dailyMarketSummery` (real):
```json
[[{"tradeDate":1790879400000,"marketTurnover":873373950.0,"tradesNo":10586,
   "volumeOfTurnOverNumber":29937080.0,"equityForeignPurchase":43460872.0,
   "equityForeignSales":183668688.0,"listedCompanyNumber":289,"tradeCompanyNumber":261,
   "marketCap":7534534216386.0,"triasi":32783.92,"spt":12495.25,"pbv":1.2,"dy":3.2,
   "per":10.8,"spp":5883.5,"asi":20812.64}], [ … ]]
```

### Per security

| Endpoint | Method / params | Returns | Fields we use |
|---|---|---|---|
| `companyInfoSummery` | POST `symbol=LOLC.N0000` | `reqSymbolInfo` (price stats), `reqSymbolBetaInfo`, `reqLogo` | `lastTradedPrice, previousClose, p12HiPrice, p12LowPrice` (52-wk, **unadjusted**), `marketCap, quantityIssued, isin, wtd/mtd/ytd hi/lo/volume`; beta `triASIBetaValue` (vs TRI-ASI), `betaValueSPSL`, `triASIBetaPeriod`, `quarter` |
| `companyChartDataByStock` | POST `stockId=<id>&period=<n>` | `{"chartData":[{t,p,h,l,q,s,…}]}` | `t` (date), `p` close, `h`, `l`, `q` volume. `o`, `c`, `pc` are null in daily rows. `s` is a monotonically increasing sequence number, **not turnover**. |
| `companyProfile` | POST `symbol=` | directors, business summary, `reqComSumInfo` | **`reqComSumInfo[0].sector`** (GICS industry-group name, matches `allSectors.name`), `boardType` (Main / Diri Savi / Empower) |
| `daysTrade` | POST `symbol=` | intraday trades for the session | not needed |
| `financials` | POST `symbol=` | links to annual/quarterly report PDFs back to 2012 | links only; no numeric fundamentals |

`companyInfoSummery` for LOLC.N0000 (trimmed, real):
```json
{"reqSymbolBetaInfo":{"securityId":378,"triASIBetaValue":0.8594875,"betaValueSPSL":0.9161507,
  "triASIBetaPeriod":"2026","quarter":1},
 "reqSymbolInfo":{"id":410,"symbol":"LOLC.N0000","name":"L O L C HOLDINGS PLC",
  "quantityIssued":475200000,"lastTradedPrice":439.25,"p12HiPrice":640.0,"p12LowPrice":435.0,
  "previousClose":443.0,"foreignHoldings":null,"foreignPercentage":null,
  "marketCap":2.087316E11,"isin":"LK0113N00007"}}
```

`period` values for `companyChartDataByStock` (LOLC, id 410):

| period | rows | span | granularity |
|---|---|---|---|
| 1 | 46 | 2026-10-02 10:01 → 14:29 | each trade (intraday) |
| 0, 2, 6–100 | 5 | 2026-09-28 → 10-02 | daily (1 week; the default for unknown values) |
| 3 | 20 | 2026-09-07 → 10-02 | daily |
| 4 | 40 | 2026-08-06 → 10-02 | daily |
| **5** | **240** | **2025-10-07 → 2026-10-02** | **daily: the maximum** |

Trimmed daily row: `{"h":45.8,"l":44.4,"o":null,"s":97089946215,"q":4103021,"p":45.1,"c":null,"pc":null,"t":1759775400000,"n":null,"id":378}`

### Index history

| Endpoint | Method / params | Returns |
|---|---|---|
| `chartData` | POST `chartId=<sectorId>&period=<n>` | `[{d, v, pc}]`, index level by date. **`chartId` is the index `sectorId`**: 1 = ASPI, 40 = S&P SL20, 223–246 = the sector indices. Periods 1–5 behave as above (5 = 240 sessions, the max). Period 0 and ≥6 return `[]`. Ids 2–39 and 41–222 return `[]`. No TRI series exists under any id. |

The brief reported `chartData` as per-symbol history. It is index-only, and sending `symbol`
gives `400 chartId parameter is missing`.

### Announcements

| Endpoint | Method / params | Returns |
|---|---|---|
| `approvedAnnouncement` | POST, empty | `{"approvedAnnouncements":[…]}`: rolling window of about 6 days (133 items, 27 Sep → 02 Oct). Fields: `id, announcementId, dateOfAnnouncement ("02 Oct 2026"), createdDate (ms), announcementCategory, company, remarks`. **`symbol` is always null** and there is no PDF link. |
| `getAnnouncementByCompany` | POST `symbol=CIC.N0000&fromDate=2000-01-01&toDate=2026-10-03` (ISO dates) | `{"reqCompanyAnnouncement":[…]}`, same row shape. **Goes back to 2012** (CIC 120 rows, COMB 487, JKH 356, HNB 468). `symbol=ALL` returns `{}`. |
| **`getAnnouncementById`** | POST `announcementId=<announcementId>` | **Typed detail record** `reqBaseAnnouncement` with `dType`, `symbol` (bare, e.g. `CIC`), category-specific structured fields, and `reqAnnouncementDocs[]` (`baseUrl` + `fileUrl` = the PDF on `https://cdn.cse.lk/`). "Dates" follow-ups and general items return **`204` with no body** here. |
| **`getGeneralAnnouncementById`** | POST `announcementId=<announcementId>` | **The counterpart for the 204 cases:** `reqBaseAnnouncement` with `title`, `symbol`, and the date fields: `xr`, `recordDate`, `allotment`, `tradingSuspended`, `tradingCommencement`, `votingProportion` (all ms timestamps / text), plus `reqAnnouncementDocs`. Returns `{}` for ids that `getAnnouncementById` serves. So: try typed first, fall back to general. |
| `getFinancialAnnouncement` | POST, empty | latest 5 financial-report uploads with PDF `path` |
| `circularAnnouncement` / `directiveAnnouncement` | POST, empty | latest 5 CSE circulars / SEC directives with PDF path |
| `corporateAnnouncementCategory` | GET | 53 category names (CASH DIVIDEND, SCRIP DIVIDEND (DATES), SUB-DIVISION OF SHARES, RIGHTS ISSUE (DATES), DEALINGS BY DIRECTORS, …) |

PDFs: `https://cdn.cse.lk/` + `fileUrl` returns `200 application/pdf` (tested). One request
hit a transient connection reset; the retry succeeded.

Feed implication: the list endpoints give no symbol and no PDF. So each new announcement needs one
`getAnnouncementById` call, about 20–30 a day. That call also gives the symbol and the PDF link. Dedupe on
`announcementId`. (`id` is a separate listing-row id.)

### Probed and not usable

| Endpoint | Result |
|---|---|
| `historicalTrades` (`symbol, fromDate, toDate, period`) | Found in the cse.lk frontend. The frontend sends it `withCredentials`. Every date format and period I tried returns **`404` with an empty body**, while a missing param returns 400. That points to a **login-gated** feature. Your rules say no logins, so I'm not pursuing it. |
| `charts` (`symbol, fromDate, toDate, period:int`), `charts/52week` | `404`, empty, same pattern. |
| `notifications/*` | GET → 400. POST was not explored (they're user-specific). |
| `allSecurityCode` via POST | `405`. Use GET. |
| `announcementById` | `[]`, or a legacy `infoAnnouncement` stub with a wrong `title`. Not useful. |
| `corporateCompanyCalender`, `agmEgmCalender` | Return empty lists for LOLC 2026. No use found. |
| `secure/*`, `signIn*`, `tradingView`, `orderBook`, … | Not touched: auth/order-related, out of scope. |

## 3. Symbol suffixes (Q4)

From all 327 rows of `allSecurityCode`, checked against `companyInfoSummery` for each type:

| Suffix | Count | Meaning (evidence) | Equity universe? |
|---|---|---|---|
| `.N0000` | 281 | Ordinary **voting** shares (LOLC, JKH, …) | ✅ |
| `.X0000` | 21 | Ordinary **non-voting** shares (COMB.X, HNB.X, CIC.X, NTB.X, …) | ✅ |
| `.R0000` / `.R0001` | 8 | **Rights** entitlements (HAYL.R0000: `quantityIssued 0`, 52-wk range 10.2–40) | ❌ |
| `.W0000` | 1 | **Warrants** (SHL.W0000) | ❌ |
| `.P0000` | 2 | **Preference** shares (AAF.P0000, MBSL.P0000) | ❌ |
| `.U0000` | 5 | **Closed-end fund units** (CAL funds, NAMAL ACUITY VALUE FUND) | ❌ |
| `.D0000` | 6 | Unclear. ABNS, BOC, CBCF, FFL, KOTA, SIC: every price field is null. BOC, CBCF, FFL and ABNS are known debt-only issuers, so probably a debt-issuer placeholder. | ❌ (no prices) |
| *(none)* | 3 | `AFIN`, `MIFL`, `SLFL` (finance companies), every field null | ❌ |

**Debentures don't appear in any of these endpoints.** `allSecurityCode` and `tradeSummary` carry no
debenture lines. So "ordinary equities" = `.N0000` ∪ `.X0000`, 302 securities. A few of those are
suspended or never trade (§4 Q1).

## 4. Answers to the Step 0 questions

### Q1. Price history: depth, frequency, adjustment

**Depth: 1 year.** `companyChartDataByStock` with `period=5` is the maximum. Other values from 0 to 100
were tested; none goes further. Results across 15 symbols, against the 240 ASPI sessions 2025-10-07 → 2026-10-02:

| Symbol | Rows | First → last | Sessions missing vs ASPI | Note |
|---|---|---|---|---|
| LOLC.N0000 | 240 | 2025-10-07 → 2026-10-02 | 0 | |
| JKH.N0000 | 240 | same | 0 | |
| COMB.N0000 | 240 | same | 0 | |
| RIL.N0000 | 240 | same | 0 | |
| CALH.N0000 | 240 | same | 0 | |
| LHCL.N0000 | 240 | same | 0 | |
| NTB.N0000 | 240 | same | 0 | |
| ALLI.N0000 | 239 | same | 1 | |
| CIC.N0000 | 235 | same | 5 | trading suspended 14–21 Oct 2025 for the sub-division |
| LVEN.N0000 | 230 | same | 10 | |
| AHPL.N0000 | 234 | → 2026-10-01 | 6 | illiquid |
| AFSL.N0000 | 224 | → 2026-10-01 | 16 | illiquid |
| ACAP.N0000 | 66 | → 2026-01-14 | 174 | stopped trading in Jan 2026 |
| AMCL.N0000 | 0 | — | 240 | no trades in a year |
| ALHP.N0000 | 0 | — | 240 | no trades in a year |

**Frequency:** one row per session **on which the stock traded**. No-trade days are simply absent;
they aren't forward-filled. That matches the brief's "last traded price on or before each Wednesday".
Each row has close, high, low and volume. **No turnover, no open, no trade count.**

**Adjustment: unadjusted.** Proof: CIC announced a **1:5 sub-division** (`getAnnouncementById`
32457, `dType: ShareSplits`, `votingProportion "1:5"`, 291.6 mn → 1,458 mn shares). Its history shows:

| | 2025-10-13 | 2025-10-22 | ratio |
|---|---|---|---|
| CIC.N0000 close | 170.00 | 34.20 | 4.97 |
| CIC.X0000 close | 128.25 | 26.00 | 4.93 |

An adjusted series would show no break. `companyInfoSummery.p12HiPrice` is also unadjusted
(CIC.N0000 52-wk high 177.0 vs price 27.8). So **the 52-week range position for any name with a
split in the last year has to be computed from our own adjusted series**, not from the API field.
Two more checks, COMB's scrip dividend and HAYL's rights issue, are in [§4a](#4a-additional-adjustment-checks).

### Q2. Dividends and corporate actions: structured or PDFs only?

**Structured, for the modern announcement portal.** `getAnnouncementById` returns typed records:

| `dType` | Key fields (real example) |
|---|---|
| `CashDividendWithDates` | CIC 2026-05-29: `votingDivPerShare 0.5`, `nonVotingDivPerShare 0.5`, **`xd "01 Jul 2026"`**, `payment "20 Jul 2026"`, `recordDate`, `finalDividend true`, `financialYear` |
| `ScripDividendWithDates` | COMB 2026-03-09: `votingPropotion "108.2352945432"` (1 new share per 108.235 held), `votingConsideration 230`, `votingDivPerShare 2.5`, **`xd "02 Apr 2026"`** |
| `ScripDividendToBeNotified` | same without dates |
| `ShareSplits` | CIC 2025-08-05: `votingProportion "1:5"`, existing / resulting share counts, `tradingSuspended`/`tradingCommencement` (null here) |
| `RightsIssue` | WAPO 2026-10-02: `votingShrsPropToBeIssued "One (01) new … for every One (01) existing …"` (**text**), `votingShareConsideration 16`, `xr` (null until dates are announced) |
| `DealingsByDirectors` | MERC: `directorTransactions[{transType, transactionDate, quantity, price}]` |

Caveats that affect how we build `corporate_actions.csv`:

1. **"Dates" follow-ups return `204` from `getAnnouncementById` but are served by
   `getGeneralAnnouncementById`** (found after the first draft of these notes). Real examples:
   - CIC `SUB-DIVISION OF SHARES (DATES)` 33380: `tradingSuspended` 2025-10-14, `lastTradingSuspended`
     2025-10-21, `tradingCommencement` 2025-10-22, matching the 13→22 Oct gap and the 170→34.20 break
     in the price history. So the split's ex-date is the commencement date, 2025-10-22.
   - HAYL `RIGHTS ISSUE (DATES)` 35920: `xr` 2026-03-18, `votingProportion` "Three (3) new … for every
     Fifty (50) existing …"; the subscription price (Rs 200) is in the typed `RightsIssue` record 35376.
   - COMB `RIGHTS ISSUE (DATES)` 25005 (2024): `xr`, `votingProportion "1:5"`.

   So structured data covers the dates as well, and `needs_review` is a fallback for unparseable
   text, not the normal path.
2. **Rights ratios are free text** ("One (01) … for every One (01)"). The parser must read them.
   Anything not parsed cleanly goes to `needs_review`.
3. **Pre-portal history (roughly before 2024) uses free-text categories** ("DIVIDEND ANNOUNCEMENT",
   "CASH & SCRIP DIVIDEND", …). Only the 1-year backfill window matters for adjustment, so this is limited.
4. The list endpoints give no symbol. `getAnnouncementById.symbol` is bare (`CIC`). Which share
   class is affected comes from the `voting…`/`nonVoting…` field pairs.
5. Cash dividends are **not** reflected in prices (the series is unadjusted). The total-return
   formula therefore adds D_t on the ex-date, as specified.

### Q3. Total-return index

**Partly.** `dailyMarketSummery` returns `triasi` (32,783.92 on 2026-10-02, the ASPI Total Return
Index) and `spt` (12,495.25, S&P SL20 TRI), **for the latest session only**. `aspi/year` returns
TRI **year-to-date %** (`triAspiValue -4.66`). No endpoint returns TRI history: every `chartData`
id was scanned, and only ASPI, S&P SL20 and the sector indices exist. ASPI *price* history goes
back 1 year via `chartData` (chartId 1, period 5).

So a TRI series can only be built forward from the first daily run. The CSE does publish its own
stock betas against the TRI-ASI (`triASIBetaValue`) per stock. That's useful as a cross-check on our
Dimson betas.

### Q4. Symbol suffixes

See §3.

### Q5. Sector per security

**Yes**, via `companyProfile` → `reqComSumInfo[0].sector`, e.g. LOLC → "Diversified Financials".
It takes one call per company, so I'll cache it and refresh weekly. `tradeSummary` itself carries
no sector.

**The strings aren't clean.** The same group appears as `"FOOD BEVERAGE & TOBACCO"`,
`"Food Beverage & Tobacco"` and `"Food, Beverage & Tobacco"`; there's `"Insurance "` with a
trailing space; `allSectors` itself uses `"Real Estate Management&Development"`; and some
labels aren't industry groups at all (`"Diversified Financial"`, and
`"Investment Banking & Brokerage"`, a GICS *sub-industry*). The screen therefore normalises
case, punctuation and spacing to the 20 `allSectors` index names, plus two explicit aliases,
both → Diversified Financials. Phase 2's `max_sector_weight` will use the same tested mapping and
fail loudly on any unmapped label.

### 4a. Additional adjustment checks

| Event | Cum close | Ex close | What adjustment would change | Verdict |
|---|---|---|---|---|
| CIC 1:5 sub-division, trading resumed 2025-10-22 | 170.00 (10-13) | 34.20 | 5× | **Unadjusted (conclusive)** |
| HAYL rights 3:50 at Rs 200, XR 2026-03-18 | 205.00 | 203.00 | TERP 204.72, i.e. a 0.14% factor | Too small to tell; consistent with unadjusted |
| COMB scrip 1 per 108.235 at Rs 230 (+ Rs 2.50 dividend), XD 2026-04-02 | 204.25 | 201.75 (−1.2% vs ASPI +0.2%) | under 1% | Consistent with unadjusted; not conclusive |

Conclusion: treat all history as unadjusted. That is the only reading consistent with the
conclusive split case. The Phase 2 "no double adjustment" test will pin this down using CIC.

## 5. Other differences from the brief

- `tradeSummary` lists only securities that traded. Symbol resolution must use `allSecurityCode`
  (GET). A watchlist name that didn't trade simply has no row that day.
- Session date: use `dailyMarketSummery[0][0].tradeDate`. `marketSummery.tradeDate` and
  `aspiData.timestamp` are update timestamps. On a Sunday, every market endpoint still returns
  Friday's session, so "no new session" detection matters.
- Market turnover, trades and volume (for the 20-day averages) exist **only for the current
  session**. Panel A's 20-day averages will show "— (n sessions needed)" for the first 20 runs,
  unless we accept an estimate (Decision D5).
- Market-level foreign buying and selling, and market P/E, P/BV and dividend yield, are available daily
  from `dailyMarketSummery`. Per-stock P/E, P/BV and yield are not (`financials` only links PDFs).
- Your watchlist: all 8 symbols in the draft `universe.yaml` resolve. So do all 31 tickers in your
  screenshots (PKME = Digital Mobility Solutions Lanka, CHOT = Ceylon Hotels Corporation,
  DOCK = Colombo Dockyard, FCT = First Capital Treasuries, CALT = Capital Alliance).

## 6. Fields per output file (proposed)

| File | Source fields |
|---|---|
| `prices.csv` (daily) | `tradeSummary`: `symbol, closingPrice→close, previousClose, high, low, sharevolume→volume, turnover` |
| `prices.csv` (backfill) | `companyChartDataByStock` p5: `t→date, p→close, h, l, q→volume`; `turnover` empty (see D2); `source=backfill` |
| `indices.csv` | `allSectors` (ASI, S&P SL20 and 20 sectors: `indexValue, change`) plus `dailyMarketSummery.triasi / spt` as indices `ASTRI` / `SPSL20TRI`; backfill from `chartData` p5 for ASPI, SL20 and sectors |
| `announcements.csv` | `approvedAnnouncement` + `getAnnouncementById`: `announcementId→id, dateOfAnnouncement→date, symbol, company, remarks/category→title, announcementCategory→category, baseUrl+fileUrl→url` |
| `corporate_actions.csv` | `getAnnouncementById` for CA categories; `needs_review` where a field is text or the record is 204 |

## 7. Decisions

**Decided 2026-10-04: option (a) for D1–D6, as recommended below.** Summary of what this means for the build:

| # | Decision |
|---|---|
| D1 | Backfill 1 year now and accumulate daily. Phase 2 runs as series **`v0-52w`** (`min_history_weeks: 52`). A **`v1-104w`** series starts once 104 weeks exist. CSE-published beta is shown beside ours. |
| D2 | Backfill rows: `turnover` empty, separate `turnover_est = close × volume`. Liquidity filters use the estimate only until 52 weeks of real turnover exist, and the page says so. |
| D3 | Foreign holding % column dropped from Panel B. Market net foreign flow added to Panel A. |
| D4 | ASTRI/S&P SL20 TRI recorded daily from day 1. Dimson betas use ASPI (stated on page) until TRI history covers the window, then a new versioned series. |
| D5 | Panel A 20-day averages show "— (n sessions needed)" until 20 real sessions exist. |
| D6 | Corporate actions with a missing (204) or text-only record: parsed from title/remarks, amounts never inferred, marked `needs_review` and listed on the site. |

Options as originally presented:

**D1 — 1 year of history vs. `min_history_weeks: 104`** (G1)
- (a) *Recommended:* backfill the 1 year now and accumulate daily. Run Phase 2 with
  `min_history_weeks: 52` as an explicitly versioned "v0-52w" series, then start a "v1-104w" series once 104
  weeks exist (about Oct 2027). With about 51 weekly returns and roughly 60–100 names, the sample
  covariance is singular, but Ledoit-Wolf handles that. Betas will be noisy, so I'd show the CSE-published
  beta next to ours.
- (b) Keep 104 weeks and leave Phase 2 dark until about Oct 2027.
- (c) A paid or other data source for longer history. That changes the source, which your brief rules out without your approval.

**D2 — No turnover in backfilled history** (G2)
- (a) *Recommended:* leave `turnover` empty on backfill rows. Add a separate `turnover_est = close × volume`
  column, used only for the liquidity filters until 52 weeks of real turnover exist, and say so on the page.
- (b) Liquidity filters use only real turnover, so they're inactive for about a year.

**D3 — Foreign holding % unavailable** (G3)
- (a) *Recommended:* drop the column from Panel B and add market-level net foreign flow
  (`equityForeignPurchase − equityForeignSales`) to Panel A.
- (b) Keep the column and show "n/a (not published by API)".

**D4 — Market proxy for beta (no TRI history)** (G4)
- (a) *Recommended:* record ASTRI daily from day 1, and use ASPI (price) for Dimson betas, stated on the page.
  Switch to ASTRI as a new versioned series once its history covers the estimation window.
- (b) Use ASPI permanently.

**D5 — 20-day market averages in Panel A**
- (a) *Recommended:* show "— (n sessions needed)" until 20 real sessions exist.
- (b) Estimate past market turnover as Σ(close × volume) across stocks from the backfill.

**D6 — Corporate actions where the structured record is missing (204) or text-only**
- Proposed: parse the title/remarks, never infer amounts, mark `needs_review`, and list them on the site for
  you to confirm. *Update:* with `getGeneralAnnouncementById` the dates are structured too, so this
  fallback should be rare (mainly free-text rights ratios that don't parse).
