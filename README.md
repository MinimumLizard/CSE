# CSE Terminal + Portfolio Lab

A personal market terminal for the Colombo Stock Exchange (CSE), published as a static site
on GitHub Pages and rebuilt once per trading day by a GitHub Action.

A Python job fetches the public JSON API behind cse.lk, validates every response, commits the
raw responses and append-only history CSVs to this repo, and renders `site/index.html`. There is
no backend, the browser makes no API calls, and every number on the page can be reproduced from
files in `data/`.

**Status:** Step 0 and Phase 1 (terminal) are done. Phase 2 (portfolio lab) comes after review.

| Doc | What's in it |
|---|---|
| [docs/API_NOTES.md](docs/API_NOTES.md) | What the API actually serves: endpoints, real responses, gaps, decisions D1–D6 |
| [docs/METHODS.md](docs/METHODS.md) | Every formula on the site, exactly as implemented |
| [docs/WATCHLIST_SCREEN.md](docs/WATCHLIST_SCREEN.md) | Whole-market liquidity / size / sector screen behind the watchlist additions |

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
config/
  universe.yaml      your watchlist and excluded names
  sector_overrides.yaml
data/
  raw/<session>/     untouched API responses
  history/           prices.csv, indices.csv, market.csv, announcements.csv (append-only)
  runs.csv           one line per run (ok / no_new_session / failed)
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
python -m cse.build          # writes site/index.html; open it in a browser
```

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

### `config/portfolio.yaml` (Phase 2)

Arrives with Phase 2: portfolio size, risk-free rate, costs, constraints, rebalance schedule.

## Enabling GitHub Pages and the daily run

1. Merge this branch into the repository's default branch. Scheduled workflows only run there.
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

## Confirming `needs_review` corporate actions (Phase 2)

Phase 2 builds `data/history/corporate_actions.csv` from the CSE's structured announcement
records (see API_NOTES Q2). Any row that couldn't be filled from structured fields, typically a
rights ratio given only as free text, is marked `needs_review` and listed on the site with a link
to its PDF. To confirm one, open the PDF, check the type, ex-date and amount or ratio, and set
`status` to `confirmed` in that row (or correct it first). Amounts are never inferred. A row
stays out of the total-return calculation until it is confirmed.

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
- History files are append-only and are never restated.
