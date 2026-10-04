# CSE Terminal + Portfolio Lab

Personal market terminal for the Colombo Stock Exchange, built as a static site and rebuilt
daily by a GitHub Action from the public JSON API behind cse.lk.

## Status

**Step 0 (data-source verification) — done, awaiting review.**

- [`docs/API_NOTES.md`](docs/API_NOTES.md) — verified endpoints, real responses, answers to the
  Step 0 questions, and the decisions needed before Phase 1.
- [`docs/WATCHLIST_SCREEN.md`](docs/WATCHLIST_SCREEN.md) — whole-market liquidity / size / sector
  screen used to propose watchlist additions. Reproduce with:

  ```bash
  pip install -r requirements.txt
  python scripts/screen_watchlist.py fetch    # ~1 h, polite sequential API pull
  python scripts/screen_watchlist.py report   # recomputes the report from data/raw/
  ```

Phase 1 (terminal) and Phase 2 (portfolio lab) follow after review. Full setup, config and
GitHub Pages instructions will land here with Phase 1.
