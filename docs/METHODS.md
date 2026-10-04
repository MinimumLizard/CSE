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

## 8. Phase 2 — portfolio lab: corporate actions, returns, estimates

Config: `config/portfolio.yaml`. Code: `cse/corpactions.py`, `cse/returns.py`, `cse/estimates.py`,
`cse/optimize.py`, `cse/record.py`, orchestrated by `cse/lab.py`. Series label **`v0-52w`** (decision D1).

### 8.1 Corporate actions (`data/history/corporate_actions.csv`)

Built from the CSE's structured announcement records (API_NOTES Q2): typed detail from
`getAnnouncementById`, and "(DATES)" follow-ups from `getGeneralAnnouncementById`.

| Type | Amount / ratio taken from | Ex-date taken from | Status |
|---|---|---|---|
| `cash_dividend` | `votingDivPerShare` / `nonVotingDivPerShare` (one row per share class) | `xd` | confirmed |
| `scrip` | `votingPropotion` = shares held per 1 new share, so a = proportion, b = 1 | `xd` | confirmed |
| `subdivision` | resulting ÷ existing share counts = F; a = 1, b = F − 1 | `tradingCommencement` of the (DATES) record (first session after the split) | confirmed once the dates exist |
| `rights` | ratio read from text ("Three (3) new … for every Fifty (50) …"), S = `votingShareConsideration` | `xr` of the (DATES) record | **always `needs_review`** (ratio is text) |
| bonus / consolidation | none | none | `needs_review`, numbers left blank |

- Amounts are never inferred. A ratio like "1:5" on a rights issue is ambiguous about which side is
  "new", so it isn't parsed.
- `needs_review` rows are used only once confirmed (optionally corrected) in
  `config/corporate_actions_review.yaml`. Rows listed under `reject` are ignored.
- The CSV is append-only. When a split's dates arrive after its ratio, a new complete row is appended
  (its id includes the dates record), and the earlier incomplete row is *superseded*: it no longer
  appears for review or in use.

### 8.2 Total returns

For consecutive traded closes P_{t0} → P_{t1}, with every effective action whose ex-date lies in
(t0, t1]. An action applies to the **first traded close on or after its ex-date**, which matters for
names that don't trade on the ex-date:

- F = ∏ share-count factors; sub-division or scrip of b new per a held: F = (a + b) / a
- D = Σ cash dividends per share
- base = TERP = (n × P_{t0} + m × S) / (n + m) if a rights issue (m new per n at S) goes ex; otherwise P_{t0}
- **R = (P_{t1} × F + D × (1 − withholding)) / base − 1**

With a rights issue alone, this reduces to P_ex / TERP − 1, as in the brief. On all other days
F = 1 and D = 0. TRI_t = ∏ (1 + R).

**No double adjustment.** The API's history is unadjusted (API_NOTES Q1, proven at CIC's 1:5 split).
`history_adjusted: false` therefore applies F and TERP. At every confirmed share-count event with
F ≥ 1.5, `check_adjustment` verifies that the raw close actually dropped by about F. If it didn't (an
adjusted source) the run stops rather than adjust twice; if `history_adjusted: true` and the close
*did* drop, it also stops. Tests cover both directions using CIC's real closes.

### 8.3 Universe

Applied in order. Every security in `allSecurityCode` that fails a step is listed on the page with
its reason.

1. Ordinary shares only: `.N0000` (voting) and `.X0000` (non-voting).
2. Not in `excluded`.
3. History: number of weekly total returns ≥ `min_history_weeks` (52).
4. Traded on ≥ `min_traded_share` (80%) of ASPI sessions in the last 52 weeks.
5. Median daily turnover over those sessions (0 on no-trade days) ≥ `min_median_turnover_lkr`.
   Turnover is real where the daily job recorded it, otherwise close × volume (decision D2). The page
   says which per stock.
6. Sector classifiable (§6). Unclassified stocks are dropped with a pointer to `config/sector_overrides.yaml`.

### 8.4 Estimates

- **Weekly returns**: TRI sampled at the last traded close on or before each Wednesday;
  r_w = TRI_w / TRI_{w−1} − 1. The window is the last `min_history_weeks` weeks ending at the last
  Wednesday on or before the session.
- **Covariance**: Ledoit-Wolf (scikit-learn `LedoitWolf`, default shrinkage target) on the T × N weekly
  return matrix, × 52. The shrinkage intensity is shown on the page.
- **Beta (Dimson)**: r_i − r_f = α + β₀ (r_m − r_f)_t + β₁ (r_m − r_f)_{t−1} + ε by OLS; β = β₀ + β₁.
  r_f weekly = (1 + risk_free_annual)^{1/52} − 1. Market = ASPI weekly price returns (decision D4).
- **Expected return (CAPM)**: E[R_i] = r_f + β_i × ERP. Historical means are never used.
- **Volatility**: √diag(Σ).
- **Amihud**: mean over weeks with turnover > 0 of |r_w| / (weekly turnover in Rs mn), where weekly
  turnover = Σ daily turnover over (previous Wednesday, Wednesday].

### 8.5 Model portfolios (cvxpy, Clarabel solver)

Constraints for 1–3: 0 ≤ w_i ≤ cap_i; Σ_{sector} w ≤ `max_sector_weight`; Σ w = 1, where
**cap_i = min(max_weight, participation × median daily turnover_i × days_to_build / portfolio_size_lkr)**.

- Feasibility is checked first. Σ cap_i ≥ 1 and Σ_sectors min(sector cap, Σ caps in sector) ≥ 1 are
  necessary and sufficient here. If either fails, nothing is solved or traded, and the page names the
  binding constraint (max_weight with too few stocks, liquidity caps, or sector caps).
- *Note:* with the default settings, the liquidity cap is ≥ 0.20 × Rs 1 mn × 10 / Rs 5 mn = 40%, so it
  can't bind below `max_weight` (10%). It starts to matter above roughly Rs 20 mn of portfolio size.

1. **Minimum variance**: min w′Σw.
2. **Risk parity**: the convex log-barrier form (Spinu 2013), min ½ y′Σy − (1/n) Σ log y_i, with
   the caps written homogeneously (y_i ≤ cap_i Σy, sector sums ≤ cap Σy); w = y / Σy. With no binding
   cap this gives exactly equal risk contributions. When a cap binds they can't all be equal, and the
   actual contributions are shown. *(The brief didn't specify how risk parity meets the caps; this is
   the standard convex formulation.)*
3. **Maximum Sharpe** on CAPM expected returns: the homogenised problem min y′Σy subject to
   (μ − r_f)′y = 1, y = κw, constraints scaled by κ ≥ 0; w = y / κ.
4. **Equal weight**: 1/N across the filtered universe, with no caps (benchmark).
5. **Market**: ASPI, held as index units (benchmark, not investable).
6. **My book**: shares listed under `my_book` in the config, valued daily (comparison only; not traded).

Per portfolio the page shows weights, sector split, risk contribution RC_i = w_i (Σw)_i / w′Σw (sums to 1),
E[R], σ = √(w′Σw) and Sharpe = (E[R] − r_f) / σ. The **efficient frontier** is minimum variance at 25
target returns, from the minimum-variance portfolio's E[R] up to the highest E[R] the constraints allow.

## 9. Live record and rebalancing

- **Inception** = the first lab run whose universe passes the filters. Each traded portfolio starts with
  `record_notional_lkr` and buys whole shares at that session's close:
  shares_i = ⌊w_i × N / (P_i × (1 + cost))⌋. It pays `cost_per_side` on each trade value and holds the rest as cash.
- **Daily value** = Σ shares × last traded close + cash. If a share-count action went ex but the stock
  hasn't traded since, the stale close is divided by F (or replaced by TERP for rights, or reduced by
  the dividend for cash dividends), so value doesn't jump.
- **Corporate actions on holdings**, on the ex-date (confirmed actions only):
  - cash dividend: credited = shares × D × (1 − withholding);
  - split/scrip: shares = ⌊shares × F⌋ (fractions dropped);
  - rights: the portfolio subscribes, ⌊shares × m / n⌋ new shares at S, paid from cash. If cash is short
    it takes up what it can afford, and the trade row records the shortfall. *(Brief: "share counts adjust
    for rights"; subscribing is how they adjust.)*
- **Scheduled rebalance**: on the first session of each month in `rebalance_months`, re-estimate,
  re-optimise, and trade every name to its new target at that close, with costs. Sells come first,
  then buys limited by cash. Trades under `min_trade_lkr` are skipped.
- **Drift**: after each session, name i is flagged if |w_i − target_i| > max(`band_relative` × target_i,
  `band_absolute`). Flagged names trade back to target at the **next** session's close (targets unchanged).
- **Trade list** (page): per portfolio, the trades executed today and those flagged for the next close,
  scaled to `portfolio_size_lkr`: amount = (target − current weight) × size, shares = amount / price,
  est. cost = amount × cost_per_side, and the trade as a multiple of the stock's median daily turnover.
  Under `min_trade_lkr` is omitted.
- **Files**: `record/nav.csv`, `record/holdings.csv` (with target weight and flag), `record/trades.csv`
  (buys, sells, dividends, share-count changes, rights). All are append-only and carry the series label.
  A second run on the same session writes nothing. A method change gets a new series label; the old
  series stays in the files and on the page.
- **Statistics** (page), only once 26 weeks have passed since inception: return since inception,
  annualised volatility of weekly (Wednesday) NAV returns, and maximum drawdown = min(NAV / running
  max − 1). No performance is shown for any date before inception.
