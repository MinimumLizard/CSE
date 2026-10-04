# Methods

Every number on the site is computed by code in `cse/` from files in `data/`. This page
states each definition exactly as implemented. Data-source facts (what the API serves, and
what it doesn't) are in [API_NOTES.md](API_NOTES.md).

Notation: P = closing price (LKR), V = share volume, sessions = days on which ASPI has a value.

---

## 1. Data conventions

| Item | Rule | Code |
|---|---|---|
| Session date | `dailyMarketSummery[0][0].tradeDate`, converted from epoch ms to the date in Sri Lanka time (UTC+05:30). Never the run date. | `cse.fetch.collect` |
| New session? | If the API session equals the latest `source=daily` date in `prices.csv`, nothing is appended and the run reports "no new session". If it is *older*, the run fails. | `cse.fetch.collect` |
| Consistency check | The latest `lastTradedTime` in `tradeSummary` must fall on the session date, or the run fails. | `cse.fetch.collect` |
| Close | `tradeSummary.closingPrice`. It equalled `price` for every row on 2026-10-02. | `cse.fetch` |
| Backfill rows | From `companyChartDataByStock` (period 5, about 240 sessions). `previous_close` = the previous bar's close in the same series. `turnover` is empty; `turnover_est` = P × V. | `cse.backfill.price_rows` |
| Same date in both sources | Readers keep the `daily` row (it carries real turnover). Files are append-only, so both rows can exist. | `cse.metrics._prefer_daily` |
| Adjustment | **None.** All stored prices are as served, i.e. unadjusted for splits, scrip and rights (API_NOTES Q1). Adjustment is applied downstream in Phase 2. | — |
| TRI change | ASTRI_t − ASTRI_{t−1}, from the two sessions in `dailyMarketSummery`. Blank when the previous value is unknown. | `cse.fetch` |

## 2. Panel A — market tape

- **Index level and change**: `allSectors` rows for ASI (sectorId 1) and S&P SL20 (40):
  `indexValue`, `change`, `percentage`. The charts plot `indices.csv` for those indices
  (backfill from `chartData` plus daily values).
- **ASPI TRI / S&P SL20 TRI**: level from `dailyMarketSummery.triasi` / `spt`; % change =
  change / (level − change).
- **Activity vs 20-session average**: for X ∈ {turnover, volume, trades} from `market.csv`
  (daily rows only):
  ratio = X_today / mean(X over the previous 20 sessions) − 1.
  Shows "— (n more sessions)" until 21 daily sessions exist (decision D5).
- **Breadth**: count of `.N0000`/`.X0000` rows in today's `tradeSummary` with change > 0, < 0, = 0.
  Traded / listed companies come from `dailyMarketSummery.tradeCompanyNumber` / `listedCompanyNumber`.
- **Foreign flow**: net = `equityForeignPurchase − equityForeignSales` (LKR), from
  `dailyMarketSummery` (decision D3).
- **Market valuation**: `per`, `pbv`, `dy`, `marketCap` as published in `dailyMarketSummery`.
- **Top 10 by turnover**: today's `tradeSummary` sorted by `turnover`.
- **Top 10 gainers / losers**: today's `tradeSummary` rows with `turnover` ≥ floor
  (`display.gainers_losers_min_turnover_lkr`, default Rs 1 mn), sorted by `percentageChange`.
- **Sectors**: the 20 industry-group rows of `allSectors`, sorted by `sectorTurnoverToday`.

## 3. Panel B — my book

For symbol *s* on session *T*, with the session calendar c₀ … c_T:

- **Price**: today's close if *s* traded today, otherwise `companyInfoSummery.lastTradedPrice`.
- **Day change**: from `tradeSummary`. "No trade" if *s* has no row today.
- **52-week position** = (P − L) / (H − L), with H, L = `p12HiPrice`, `p12LowPrice` from
  `companyInfoSummery`. These are **unadjusted**, so if the stock's own price history in the
  last ~240 sessions has a one-bar move with |P_t / P_{t−1} − 1| > 40%, the cell is flagged
  "⚠ unadjusted". Such a move almost always means a split, scrip or rights issue.
- **Volume vs average** = V_T / mean(V over c_{T−20} … c_{T−1}), with V = 0 on sessions
  where *s* did not trade. Shows "— (n sessions needed)" until 21 sessions exist.
- **Return vs ASPI over k sessions**, k ∈ {5 (1w), 21 (1m), 63 (3m)}:
  - P_start = last traded close on or before c_{T−k}; P_end = last traded close on or before c_T.
  - A_start, A_end = ASPI on the same dates.
  - shown value = (P_end / P_start − 1) − (A_end / A_start − 1), in percentage points.
  - Price-only on both sides (no dividends), and flagged ⚠ if a > 40% one-bar move lies in the window.
  - "— (n sessions needed)" when the calendar has fewer than k + 1 sessions; "—" when *s* has
    no trade on or before c_{T−k}.
- **Market cap**: `tradeSummary.marketCap` if traded today, else `companyInfoSummery.marketCap`.
- **β CSE**: the CSE-published `triASIBetaValue` (beta vs the total-return ASI). The CSE doesn't
  document its estimation window. It's shown as a reference; it isn't computed here.
- Foreign holding % is not shown: the API returns null for every stock (decision D3).

## 4. Panel C — disclosures

- Source: `approvedAnnouncement` (about 6 days, rolling) plus `getFinancialAnnouncement`. Each
  new `announcementId` gets one detail call: `getAnnouncementById`, or
  `getGeneralAnnouncementById` when the first returns 204. The detail supplies the symbol and
  the PDF URL (`baseUrl` + URL-encoded `fileUrl`). When neither endpoint has a detail, the symbol
  comes from an exact (normalised) company-name match against `allSecurityCode`, and the PDF
  link is left empty.
- De-duplicated on `id` (= `announcementId`; financial reports use `F` + their id).
- **Title** = `remarks` if present, otherwise the category. The raw title is always shown.
- **Tag**: `cse/tags.py`. Keyword rules on upper-cased "category + title", first match wins,
  in the order dealings → dividend → capital → results → board → other.
- **My universe** = announcements whose bare company code matches a watchlist symbol's code.

## 5. Header and banners

- Build time is shown in Sri Lanka time.
- **Last run failed**: the latest `fetch` row in `data/runs.csv` has status `failed`.
- **Stale data** (computed in the browser): expected session = today in Sri Lanka time if it
  is past 17:30, otherwise the previous day, stepped back past weekends. If the data's session is
  older, the page shows how many weekday sessions are missing. Exchange holidays aren't known to
  the page, so a holiday can trigger the warning.

## 6. Sector classification

`cse/sectors.py` maps `companyProfile.sector` to the 20 S&P/CSE industry groups, in this order:
(1) per-symbol overrides in `config/sector_overrides.yaml`; (2) the label after normalising case,
punctuation, spacing and leading GICS codes; (3) a fixed table mapping GICS sub-industry to industry
group. Anything else is "Unclassified".

## 7. Whole-market screen (`scripts/screen_watchlist.py`)

- Traded share = sessions with a trade ÷ ASPI sessions in the 1-year window.
- Median turnover (est.) = median over all sessions of P × V, with 0 on no-trade days.
- Tier A: traded ≥ 95% and median ≥ Rs 10 mn. Tier B: ≥ 80% and ≥ Rs 1 mn. Tier C: the rest.

---

## 8. Phase 2 (portfolio lab): formulas from the brief, not yet implemented

These will be implemented and tested in Phase 2. They're listed here so the method is fixed in
advance. Series label **`v0-52w`** (decision D1).

- Total return on ex-date t: R_t = (P_t × F_t + D_t × (1 − w)) / P_{t−1} − 1. D_t = cash dividend
  per share; F_t = share-count factor; w = `dividend_withholding`.
- Sub-division or scrip of b new per a held: F = (a + b) / a.
- Rights, m new per n held at S, cum-rights close P_cum: TERP = (n × P_cum + m × S) / (n + m);
  return over the ex-date = P_ex / TERP − 1.
- Otherwise F = 1, D = 0.
- Weekly returns: Wednesday close to Wednesday close, last traded price on or before each Wednesday.
- Covariance: Ledoit-Wolf shrinkage on weekly returns, × 52.
- Beta (Dimson): r_i − r_f = α + β₀(r_m − r_f)_t + β₁(r_m − r_f)_{t−1} + ε; β = β₀ + β₁.
  Market = ASPI (price) until TRI history covers the window (decision D4).
- Expected return (CAPM): E[R_i] = r_f + β_i × ERP.
- Amihud illiquidity: mean over weeks of |r_week| / turnover_week (Rs mn), using `turnover_est`
  where real turnover is absent (decision D2).
- Liquidity cap per stock: (participation × median daily turnover × days_to_build) / portfolio_size.
