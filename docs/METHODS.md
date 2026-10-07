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

Constraints for 1–3: 0 ≤ w_i ≤ cap_i; Σ_{sector} w ≤ `max_sector_weight`; Σ_{ownership group} w ≤
`max_group_weight` (from series v1-52w-grp20; §10.5); Σ w = 1, where
**cap_i = min(max_weight, participation × median daily turnover_i × days_to_build / portfolio_size_lkr)**.

- Feasibility is checked first. Σ cap_i ≥ 1 and Σ_sectors min(sector cap, Σ caps in sector) ≥ 1 are
  necessary, and for box + sector constraints alone also sufficient. With group caps, the same reach test
  is applied per group, and then an exact LP decides. If any check fails, nothing is solved or traded, and
  the page names the binding constraint (max_weight with too few stocks, liquidity caps, sector caps,
  group caps, or sector and group caps together).
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

## 10. Ownership tracker

Page: `site/ownership.html`. Code: `cse/ownership/` (`collect`, `parse`, `names`, `analyse`, `site`). Tests:
`tests/test_ownership.py`, on real report text.

**Source.** CSE Listing Rule 7.4 requires every interim report to list the company's twenty largest
shareholders. The CSE API's `shareholderList` endpoint only holds directors' holdings at listing, so the
reports themselves are the source.

### 10.1 Collection (`python -m cse.ownership.collect`, weekly)

- For each company (one call per company code, voting class preferred), `financials` lists its filed
  reports. The newest quarterly report is taken by period date, then upload time. The newest annual report
  is used only when a company has no quarterly filing.
- The PDF is downloaded from cdn.cse.lk with the same polite rules as the daily job and converted with
  `pdftotext -layout` (poppler-utils). Only the text is kept: `data/raw/ownership/text/<report id>.txt`.
  It is indexed in `data/raw/ownership/reports.csv` (append-only, keyed on report id), and the
  `financials` response is saved under `data/raw/ownership/financials/`.
- Report text is never replaced. A new quarter is a new report id, so earlier quarters stay and every
  run re-parses all of them (`holdings.csv` covers every collected period).
- Reports under 200 words are `no_text`: scanned images with no text layer. They are not OCR'd.

### 10.2 Table parsing (`cse/ownership/parse.py`)

- **Headings.** A heading is "twenty / thirty / top / first / major / largest / list of / main …
  shareholders (or shareholdings)", or "shareholders … as at". Headings mentioning debentures, directors,
  key management, related parties, bonds, preference shares, analysis, distribution, categories, public
  holding or the number of shareholders are skipped.
- **Share class.** "Non-voting" in the heading, or in a sub-heading within 40 lines after a table,
  marks a non-voting (`X`) table.
- **Rows.** A row is an optional rank, a name, the current-period share count and its percentage. Any
  prior-period columns after them are ignored. The parser also handles:
  - names wrapped above or below their numbers;
  - sub-numbered rows (`1.1`, `1.2`);
  - one holder's accounts followed by their combined line;
  - footnote markers (`** 7.91`);
  - digits split by the PDF (`1 7,52 1 ,1 1 0`);
  - whole-number percentages (`67%`).

  A row whose current-period cell is `-`, `N/A` or `Nil` is a holder that held nothing this period, and
  is dropped.
- **End of table.** A table ends at its total or subtotal line (its share count and percentage are
  recorded), at the next heading or section (directors, public holding…), or when ranks restart at 1.
- **Checks.** A table is used only if it passes all of these:
  - at least 5 rows, and the percentages sum to ≤ 100.5;
  - **internal consistency**: each of the ten largest rows implies a class total
    T = shares × 100 / %. Because the printed % is rounded or truncated to *d* places, each row gives an
    interval [shares × 100 / (% + 10⁻ᵈ), shares × 100 / (% − 10⁻ᵈ)]. The intervals must overlap (0.5 %
    slack). The implied total is the geometric midpoint of the overlap;
  - **share count**:
    - `verified` means the implied total is within 10 % of the CSE's `quantityIssued` for that class;
    - otherwise `verified_total` means the overlap contains the report's own printed class total (a
      "Total … 100 %" line). The share count has changed since the report date (rights issue, split);
    - anything else is `unverified` and is not used;
  - if the table prints a subtotal, the rows must add up to it: shares within 0.5 %, or percentages
    within 0.25 + n × 10⁻ᵈ / 2.

  When several tables pass for one class, the one with more rows is kept.
- **Holding fraction.** f = shares / T₀, where T₀ is, in this order:
  1. the report's printed class total, when the overlap contains it;
  2. otherwise the CSE share count, when the overlap contains it;
  3. otherwise the implied total.

  Using the report-date total keeps f right across later splits and new issues.

### 10.3 Beneficial owner and entity (`cse/ownership/names.py`; user decision: credit the beneficial owner, show names as filed)

1. Normalise for matching only. The steps:
   - upper case, without apostrophes or punctuation;
   - drop titles (Mr/Mrs/Dr/Prof/Rev/M/S…), a leading "The", and account designations ("A/C No.2",
     "No 3 Share Investment Account", "(Collateral)");
   - treat "(Pvt) Ltd / Private Limited / Limited / PLC" as the same suffix.

   A cell that the PDF printed twice is collapsed to one copy.
2. **Custodians and trustees** are stripped, and the owner named after them is credited, with the
   custodian kept as `via`. This covers:
   - "<bank> S/A <owner>", "BNYM SA/NV-…", "SSBT-…", "BBH-…", "JPMCB NA-…", "CACEIS…", "Citibank N.A.…";
   - "<…> as trustee for/to <fund>";
   - "<bank> A/C <fund or trust>";
   - "<bank> - <fund / trust / scheme>".
3. **Margin and financed accounts**: "<lender>/<owner>" credits the owner after the last slash, but only
   when the part before it names a bank, finance, leasing, capital, securities, wealth or investment
   firm, or a company. Otherwise slashes mean joint holders.
4. Insurers' **life and policyholder funds** are institutions in their own right. They are never chained
   to the listed insurer, because that money belongs to policyholders.
5. **Types**:
   - `listed`: the normalised name equals a CSE-listed company's name. A holder filed as "X Limited" matches
     "X PLC"; companies re-register as PLC on listing.
   - `estate`, `trust`, `institution`: funds, provident and pension schemes, insurers' funds,
     development-finance institutions, the Treasury and state bodies.
   - `company`: unlisted, including foreign companies.
   - `joint`, `individual`, `nominee`: a nominee is unidentified and never counts as a controller.
6. **Merging.** Holders merge only when their normalised names are identical. Initials are never matched
   to full names, so "K.D.D. Perera" and "K.A.D.D. Perera" stay separate. Merging those would be a guess
   about a person's identity. `config/ownership_aliases.yaml` merges spellings that you know are the same
   holder, and it always wins.

### 10.4 Graph (`cse/ownership/analyse.py`)

The analysis uses each company's latest report, with only `verified` and `verified_total` tables. Market
value is f × the class's market capitalisation, from the latest `tradeSummary` (or `companyInfoSummery` for
a class that didn't trade). Holdings are as at the report date and prices are as at the latest session.

- **Direct value** of holder h: V_h = Σ_c Σ_class f(h, c, class) × cap(c, class). A listed holding
  company appears here, and summing these values double-counts.
- **Economic look-through.**
  - Let W[c′, c] be the fraction of company c's market value held by listed company c′, counting only
    listed companies whose own register is covered.
  - Let D[o, c] be the same fraction for every other holder o.
  - Then F = D (I − W)⁻¹ gives o's ultimate fraction of c through any chain of listed holding companies,
    and look-through value = F · cap. The run stops if the spectral radius of W is ≥ 1, which would mean a
    closed loop of cross-holdings.
  - Each column of F sums to at most 1, so Σ look-through ≤ covered market cap, and nothing is counted
    twice.
  - Stakes held by a listed company whose own register isn't verified stay with that company
    ("listed holder, register not verified").
  - Value outside the published top-20 lists is shown as unattributed.
- **Control** (voting shares only).
  - For each company, votes are summed by **block**: a holder's accounts, plus the votes of every listed
    company that the holder controls.
  - The largest block (nominees excluded, the company's own shares excluded) makes the company
    `controlled` if it is > 50 %, or `influence` if it is 20–50 %.
  - This is iterated to a fixed point, so control can pass up a chain. The page shows the chain as
    company ← largest member of the block ← … ← block owner.
  - Statutory caps on voting rights are not applied. For example, HNB footnotes that some holders' combined
    votes are capped at 10 % under the Banking Act. Those holders show at their registered percentage.
- **Outputs** (rebuilt in full every run):
  - `data/ownership/holdings.csv`: every parsed row of every collected report, with the credited owner,
    type, `via`, rule, f and table status;
  - `companies.csv`: per company, the report, table status and note, controller, block %, level and chain;
  - `owners.csv`: per owner, direct and look-through value, holdings, companies controlled and influenced;
  - `latest.json`: page input, not committed; rebuilt by the daily job at that session's prices.

  The weekly workflow commits the CSVs. The daily workflow re-values them for the page and doesn't commit
  them.

### 10.5 Ownership groups and the group exposure cap (`cse/ownership/groups.py`)

The portfolio lab caps the weight in any one **ownership group** at `max_group_weight` (20 %). The
decision was taken 2026-10-07 and the record series became `v1-52w-grp20`.

- **Building a group.** Start from a stock's company and follow its largest voting block (§10.4) upward.
  Follow it only when the block is ≥ 20 % and its owner is not an institution, fund or unidentified
  nominee. When that owner is a listed company, repeat from it. Stop at an owner that isn't listed, at a
  listed company with no such block, or at a loop.
- **Membership.** Stocks that end at the same owner form one group, and both share classes of a company
  are in the same group.
- **Wider than the page's control groups.** The page's control groups require > 50 %; this rule
  deliberately goes wider. A 43 % holder (Milford Exports in Melstacorp) runs the company in practice,
  and for a risk cap grouping too widely is the safe error. Funds are not group heads, because a fund
  holding 20 % of several companies doesn't make them move together.
- **When the constraint applies.** It is added for every group with two or more stocks in the universe.
  It also applies to single-stock groups whenever `max_group_weight` is below `max_weight`.
- **No data.** Without `data/ownership/companies.csv` there is no group constraint.
- **Inputs.** Groups come from the committed `data/ownership/companies.csv` and include your merges in
  `config/ownership_aliases.yaml`. They change when the weekly ownership run sees a new quarter.
- **Effect when adopted (session 2026-10-07).** The cap did not bind any model portfolio. The largest
  group weight was 15.6 % (Mr. K.D.D. Perera's companies in maximum Sharpe). In dry runs, a 10 % cap
  changed maximum Sharpe's ratio from 0.544 to 0.543.

### 10.6 Owners of unlisted holders (`cse/ownership/annual.py`, `parents.py`; decision 2026-10-07: with review file)

- **Sources.** Listed companies' reports often name the owner of their unlisted parent. They do so in:
  - the parent and ultimate-parent note (LKAS 1 para 138(c));
  - the "ultimate beneficial ownership" or ultimate controlling party note (LKAS 24);
  - directors' indirect holdings ("through Odeon Holdings (Ceylon) (Pvt) Ltd");
  - related-party descriptions ("a company wholly owned by the Chairman").
- **Collection.** `cse.ownership.annual` downloads each company's latest annual report (the `financials`
  response saved by the weekly collector, so it makes no extra API call). From the text it keeps only
  the passages around those phrases: 5 lines either side, merged, at most 60 per report, with strong
  mentions first. They go to `data/raw/ownership/annual/<id>.json`, indexed in `annual_reports.csv`.
  The full text is not kept, because reports run to 100 000+ words; the PDF stays linked. The interim
  reports' text is scanned the same way.
- **Evidence sheet.** `cse.ownership.parents` writes `docs/OWNERSHIP_PARENTS_EVIDENCE.md`. It covers
  every unlisted company (or trust) that is a listed company's largest voting block, ordered by the
  value it ultimately holds. Under each one it quotes those companies' passages and lists the
  statements a pattern recognised. Recognised statements are suggestions only; two-column layouts can
  garble them.
- **Confirmation.** A link is used only when you copy it into `config/ownership_parents.yaml`, with its
  owner, the percentage if stated, the source PDF and the quote. Then:
  - **Control:** the holder's votes count with its owner's. This applies when no percentage is given (the
    report states control) or the percentage is > 50 %. Control chains, control groups and the lab's
    ownership groups continue upward.
  - **Value:** the stated percentage of the holder's look-through value passes to the owner, and the
    rest stays with the holder. Without a percentage, no value moves. Links can chain (A → B → C), so
    the total is conserved.

### 10.7 Limits

- Only the top 20–30 holders per company are visible. Holders below that are not, nor are owners of
  unlisted companies (who owns Milford Exports, for example, is not in any CSE filing).
- Data is as at each report's period end, which is usually the last quarter end.
- The `no_text`, `no_table` and `unverified` companies are listed in the lookup, each with its reason. Nothing
  is estimated for them.

## 11. Money flows

Page: `site/flows.html`. Code: `cse/dealings.py` and `cse/flows.py`. Tests: `tests/test_dealings.py`, on
real API responses. The page shows who moved money where, as early as the disclosures allow. It doesn't
predict prices.

### 11.1 Directors' dealings (`cse.dealings`)

- **Source.** Listed companies announce every share dealing by a director, in the director's own name or
  through a "relevant interest" account: a company or relative the director is connected with. The API's
  `getAnnouncementById` returns these as structured records (`dType = DealingsByDirectors`). Each record
  has the director, the nature of the directorship, own or related account and the related account's
  name, plus each transaction's type, date, quantity and price.
- **Collection.**
  - Daily: the fetch already saves the detail of every new announcement in
    `data/raw/<session>/getAnnouncementById/`, so no extra calls are needed.
  - One-off: the past year's notices (ids from the per-company announcement lists in
    `data/raw/announcements/byCompany/`) were fetched into `data/raw/dealings/<id>.json` by
    `python -m cse.dealings`.
- **Table.** `data/dealings/dealings.csv` has one row per transaction and is rebuilt from the raw files
  every run.
  - value = quantity × price;
  - lag = weekdays from the trade date to the announcement (public holidays not removed).
- **Side** comes from the free-text transaction type: buy (purchase, acquisition, subscription) or sell
  (sale, disposal). Anything else, including gifts, transfers, inheritance and mixed or unrecognised
  wording, is `other` and is never counted as buying or selling.

### 11.2 Who traded (`cse.flows`)

- **The actor** is the account that traded: the director, or the related account's holder.
- **Cleaning account names.** Suffixes such as "- Directors", "- Common Directors" and "- Directors /
  Shareholders" are removed, so "CT Holdings PLC - Common Directors" becomes C T Holdings PLC (listed).
  Generic account names ("Shareholders", "N/A", "as per attachment") fall back to the director(s).
  Relatives keep their own name: "Mrs X (Spouse)" is not Mrs X.
- **Matching to owners.** Actors go through the ownership tracker's name resolution and your aliases. A
  listed actor carries its ownership group (§10.5).
- **Per window (30, 90 and 365 days to the latest trade date):**
  - per actor: bought, sold, net, number of trades, net by stock;
  - per company: net insider value and the distinct buying and selling owners;
  - a **cluster** is two or more distinct owners buying with net buying positive.
- **Flags.**
  - *large*: ≥ Rs 10 mn;
  - *watchlist*: your stocks;
  - *own group*: the actor and the stock are in the same ownership group, e.g. a parent buying its
    subsidiary;
  - *related account*.

### 11.3 Quarter-to-quarter list changes

- **What is compared.** Consecutive verified top-20 lists of the same company and share class, 40–130 days
  apart. The weekly collector keeps every quarterly report from 30 June 2025 on (`--since`). Per holder,
  both share count and fraction of the class are compared:

| Shares | Fraction | Classified as | Counted as a trade |
|---|---|---|---|
| changed | changed | bought / sold | yes |
| same | changed | diluted / concentrated by others' trades or new issues | no |
| changed | same | corporate action (split, bonus, scrip) | no |
| — | new to the list | entered list: bought at least (fraction − previous list's smallest fraction) | yes |
| — | dropped off | left list: sold at least (fraction − new list's smallest fraction) | yes |

- **Values** are Δfraction × the class's latest market capitalisation. All quarters are valued at one
  price, and splits don't distort them.
- **Limits.** Only holders in the top 20–30 are visible. The lists come out 1–2 months after the quarter
  ends.
