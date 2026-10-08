# CSE Terminal + Portfolio Lab

A personal market terminal for the Colombo Stock Exchange (CSE), published as a static site
on GitHub Pages and rebuilt once per trading day by a GitHub Action.

A Python job fetches the public JSON API behind cse.lk, validates every response, commits the
raw responses and append-only history CSVs to this repo, and renders `site/index.html`. There is
no backend, the browser makes no API calls, and every number on the page can be reproduced from
files in `data/`.

**Status:** Step 0, Phase 1 (terminal) and Phase 2 (portfolio lab) are built. The live record starts
automatically on the first daily run with 52 weekly returns of history (about 7 Oct 2026; see below).

| Doc | What's in it |
|---|---|
| [docs/API_NOTES.md](docs/API_NOTES.md) | What the API actually serves: endpoints, real responses, gaps, decisions D1–D6 |
| [docs/METHODS.md](docs/METHODS.md) | Every formula on the site, exactly as implemented |
| [docs/WATCHLIST_SCREEN.md](docs/WATCHLIST_SCREEN.md) | Whole-market liquidity / size / sector screen behind the watchlist additions |
| [docs/METHODS.md §10](docs/METHODS.md#10-ownership-tracker) | Ownership tracker: report parsing, beneficial owners, look-through and control |
| [docs/METHODS.md §11](docs/METHODS.md#11-money-flows) | Money flows: directors' dealings and quarterly stake changes |

## Layout

```
cse/                 python package
  http.py            polite read-only client (sequential, 1 s pause, 3 retries, 30 s timeout)
  models.py          pydantic models for every response used
  fetch.py           daily job          -> data/raw/<session>/, data/history/*.csv
  backfill.py        one-off history    -> prices.csv / indices.csv (source=backfill)
  build.py           static site        -> site/index.html
  metrics.py         Panel A/B computations
  tags.py            disclosure tagging rules (one tested file)
  sectors.py         sector-label normalisation
  corpactions.py     corporate actions from structured CSE announcements
  returns.py         total returns (dividends, splits, scrip, rights / TERP)
  estimates.py       universe filter, Ledoit-Wolf, Dimson beta, CAPM, Amihud
  optimize.py        cvxpy: min variance, risk parity, max Sharpe, frontier
  record.py          forward-only live record and rebalancing
  lab.py             portfolio lab job -> data/lab/, record/
  dealings.py        directors' dealings      -> data/dealings/dealings.csv
  flows.py           money flows              -> site/flows.html
  ownership/         who owns the CSE: collect.py (interim reports -> text), parse.py (top-20 tables),
                     names.py (beneficial owners), analyse.py (graph), site.py (site/ownership.html)
config/
  universe.yaml      your watchlist and excluded names
  portfolio.yaml     portfolio lab settings
  sector_overrides.yaml
  corporate_actions_review.yaml   your confirmations of needs_review corporate actions
  ownership_aliases.yaml          spellings of one shareholder to merge (individuals are never merged automatically)
  ownership_parents.yaml          owners of unlisted holders that you confirmed from the evidence sheet
data/
  raw/<session>/     untouched API responses
  history/           prices.csv, indices.csv, market.csv, announcements.csv (append-only)
  history/corporate_actions.csv
  raw/announcements/ cached announcement lists and details (corporate actions)
  lab/<session>.json everything Panel D shows, per session
  raw/ownership/     interim-report text per report id + reports.csv index (append-only)
  ownership/         holdings.csv, companies.csv, owners.csv (rebuilt from raw/ownership each week)
  runs.csv           one line per run (ok / no_new_session / failed / waiting / infeasible)
record/              live record: nav.csv, holdings.csv, trades.csv (append-only)
tests/               pytest; fixtures are real API responses
```

## Setup

Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q          # all tests run offline against recorded real responses

python -m cse.fetch          # today's session (about 5-15 min; the API is slow and we are polite)
python -m cse.backfill       # one-off: ~1 year of history for every equity (about 20 min cold;
                             #   it reuses any raw responses already saved under data/raw/)
python -m cse.corpactions    # one-off: ~1 year of corporate actions (about 1 h cold; cached)
python -m cse.lab            # portfolio lab (seconds); --dry-run computes without writing
python -m cse.build          # writes site/index.html (and site/ownership.html); open it in a browser

# ownership tracker (needs pdftotext: apt install poppler-utils / brew install poppler)
python -m cse.ownership.collect   # latest interim report of every company (about 25 min cold; resumable)
python -m cse.ownership.annual    # parent / ultimate-owner passages from annual reports (about 70 min cold)
python -m cse.ownership.analyse   # parse + graph -> data/ownership/ (seconds, no network)
python -m cse.ownership.parents   # evidence sheet -> docs/OWNERSHIP_PARENTS_EVIDENCE.md
```

The daily workflow runs `fetch`, `lab` and `build`. New corporate actions in the daily announcement
feed are picked up by `lab` automatically; `cse.corpactions` is only needed once.

You can run `fetch` and `backfill` in either order, and re-run either one. Neither can create
duplicate rows. If the API's session date equals the last stored one, `fetch` prints
`no new session` and writes nothing.

## Editing the config

### `config/universe.yaml`

```yaml
watchlist:              # a flat list, or groups -> lists (groups become headings in Panel B)
  BANKS:
    - COMB.N0000        # .N0000 = voting share
    - HNB.X0000         # .X0000 = non-voting share
excluded:               # conflict-of-interest names: flagged everywhere, never held by models
  - NTB.N0000
display:
  gainers_losers_min_turnover_lkr: 1000000
```

- Symbols must match the CSE security list exactly, suffix included. On every run they are
  checked against `allSecurityCode`. An unknown symbol stops the run with a message naming it,
  and nothing is guessed.
- The `SCREEN ADDS` group came from the whole-market screen. Delete any line you don't want.
- Exclusion is per symbol. If a conflict covers a whole company, list each of its share classes.

### `config/sector_overrides.yaml`

These are per-symbol sector assignments for the few companies whose CSE label isn't a GICS
industry group. The current entries are proposals, so confirm or edit them. Values must be one
of the 20 S&P/CSE industry-group names; anything else fails validation.

### `config/portfolio.yaml`

Values are the brief's, plus `series`, `history_adjusted` and `my_book`. Lines marked EDIT are yours:

- `portfolio_size_lkr`: your real size, used for liquidity caps and the trade list.
- `risk_free_annual`: the 12-month T-bill yield. Update it by hand when it moves; it feeds CAPM and Sharpe.
- `dividend_withholding`: set it to the current rate for net-of-tax returns.
- `cost_per_side`: verify against the current CSE cost schedule.
- `my_book`: optional `{SYMBOL: shares}` to compare your holdings with the models.
- `max_group_weight`: cap on the total weight of companies that trace to the same owner (e.g. all of
  Mr. K.D.D. Perera's Hayleys and Vallibel companies). Groups come from the ownership tracker
  (METHODS §10.5). Changing it is a method change: change `series` too.
- `series`: the record's version label. **If you change a method setting in a way that would make past
  record rows inconsistent, also change the label** (e.g. `v1-...`). A new series starts at the next run
  and the old one stays visible. Never edit `record/` by hand.
- `history_adjusted`: keep `false`. The run stops if the price data ever contradicts it (§8.2 of METHODS).
- `min_history_weeks: 52` (decision D1). The API gives about 1 year of history, so the first run with 52
  weekly returns is around 7 Oct 2026, provided the daily job has been running since then. Until that
  run, Panel D shows "waiting". To preview the lab before then without touching the record:
  `python -m cse.lab --dry-run --min-history-weeks 51 --out /tmp/lab`.

## Enabling GitHub Pages and the daily run

1. Scheduled workflows only run on the repository's **default branch**. Right now the only branch,
   `claude/modest-ptolemy-vjpc84`, *is* the default, so the schedule will run from it as is. If you
   later create `main` and make it the default, merge this work into it first.
2. **Settings → Pages → Build and deployment → Source: "GitHub Actions".**
3. **Settings → Actions → General → Workflow permissions: "Read and write permissions"**, so the
   workflow can commit `data/`.
4. Run it once by hand: **Actions → daily → Run workflow**. The site URL appears on the
   `deploy` job and under Settings → Pages.

Schedule: weekdays 11:00 UTC (16:30 Sri Lanka time). The CSE trades 09:30–14:30 SLT, and the API
finishes updating at about 14:57. The workflow installs, tests, fetches, builds, commits `data/`
and deploys `site/`. **If the fetch fails, the job fails, nothing is written, and the previous
site stays up.** The failure is recorded in `data/runs.csv`, and the live page shows a stale-data
banner once its data falls a session behind. No secrets are needed.

## Ownership tracker

`site/ownership.html` answers "who owns the CSE". It reads the twenty-largest-shareholders table from every
listed company's latest interim report and checks each table against the CSE's share count. It credits
custodian and margin accounts to the beneficial owner, and then traces holdings through listed holding
companies:

- **Biggest ultimate owners** (look-through). Value held directly, plus a share of everything held by listed
  companies the owner has stakes in. Each rupee is counted once.
- **Biggest registered holders**. Holdings in each holder's own name; listed holding companies included.
- **Control groups**. Who controls whom (> 50 % voting block, or 20–50 % influence), shown as chains.
- **Company lookup**. Any company's list as filed, what each line is credited to, and why a table was
  rejected.

Chains stop at unlisted holders (Milford Exports, Odeon Holdings…) unless you confirm who owns them.
`docs/OWNERSHIP_PARENTS_EVIDENCE.md` quotes what the listed companies' own annual and interim reports
say. For example, CT Holdings' reports name its ultimate beneficial owner. Copy the links you've
checked into `config/ownership_parents.yaml`, and from the next run the chains continue upward.

The `ownership` workflow collects new reports every Saturday. The daily workflow re-values them at that
day's prices. Names are shown exactly as filed. Individuals are never merged across different spellings.
If you know two spellings are the same person, add them to `config/ownership_aliases.yaml`, which has an
example. See METHODS §10 for every rule and its limits.

## Money flows

`site/flows.html` follows the big owners' money:
- **Who is buying and selling:** a diverging bar chart of net buying and selling by owner over 30 days,
  90 days or 1 year.
- **Where insiders are buying:** net value per company, with clusters of several buyers and your
  watchlist stocks flagged.
- **Quarter to quarter:** stake changes between top-20 lists, which also covers holders who aren't
  directors.
- **Follow one owner:** an owner's cumulative net buying, every trade, and every quarterly change.
- **Latest disclosures**, with links to the notices.

The data is directors' dealings, which arrive about 1–2 market days after the trade, plus the quarterly
lists. See METHODS §11.

```bash
python -m cse.dealings            # one-off: a year of dealing notices (about 15 min); daily runs use --offline
python -m cse.ownership.collect --since 2025-06-30   # one-off: four quarters of shareholder lists
python -m cse.flows               # page input -> data/flows/latest.json
```

## Confirming `needs_review` corporate actions

`data/history/corporate_actions.csv` is built from the CSE's structured announcement records (API_NOTES
Q2). Cash dividends, scrip dividends and dated sub-divisions come from numeric fields and are used
directly. Anything read from free text, notably every rights issue (its ratio is a sentence), is marked
`needs_review`. These rows are listed at the bottom of Panel D with a link to the source PDF, and they're
**not used** for returns or the record until you confirm them.

To confirm, open the PDF, check the ex-date, ratio and subscription price, and add the row's `id` to
`config/corporate_actions_review.yaml`:

```yaml
confirm:
  "35920:HAYL.N0000": {}                                   # correct as parsed
  "40001:ABC.N0000": {ratio_new: 1, ratio_held: 10}        # correct it while confirming
reject:
  - "40002:XYZ.N0000"                                      # not a real event / duplicate
```

Commit the file. The next run uses the action, and Panel D stops listing it.

**Shortcut:** [`docs/CA_REVIEW_EVIDENCE.md`](docs/CA_REVIEW_EVIDENCE.md) quotes what each source PDF
says (read by Claude, including scanned PDFs) and ends with a suggested review file. It's a starting
point: check each line against its PDF before you adopt it. Regenerate it with
`python scripts/review_evidence.py` after new items appear. The CSV itself is never
edited; it's append-only. If you confirm an action after its ex-date has passed, the total-return
history picks it up on the next run (estimates are recomputed every run). The live record does **not**
book it retroactively, because history is never restated. Confirm promptly when a name you hold goes ex.

## When the API changes

The API is undocumented, so expect this eventually. What you'll see is a failed workflow, an
email from GitHub, and a stale-data banner on the site. Good data is never overwritten.

1. Open the failed run's log. The error names the endpoint and the field, e.g.
   `validation failed for tradeSummary: reqTradeSummery.0.closingPrice Field required`.
2. Reproduce locally with `python -m cse.fetch`.
3. Fetch the endpoint by hand and compare it with `docs/api_samples/` and the latest good
   `data/raw/<session>/`.
4. Update `cse/models.py` (and the code that reads the field). Add the new real response to
   `tests/fixtures/` and update `docs/API_NOTES.md`.
5. `python -m pytest -q`, then push. The next scheduled run catches up. Missed sessions can't be
   recovered from this API beyond what `cse.backfill` provides (about 1 year of closes and volumes).

If an endpoint disappears entirely, don't substitute another data source without deciding to.
That's a change of source, and per the original brief it needs an explicit decision.

## Rules this project follows

- Read-only, no logins, and no broker or order endpoints.
- No fabricated, interpolated or hard-coded market data, including in tests.
- Dependencies are pinned in `requirements.txt`.
- History files and the live record are append-only and are never restated.
- Panel D is model output from stated assumptions, not investment advice.
