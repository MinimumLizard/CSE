# The CSE Terminal, explained simply

This page explains the whole project in plain words, as if to a five-year-old. The exact rules
are in [METHODS.md](METHODS.md). Each code file also starts with a short **"In plain words"** note.

## The big idea

Imagine a little robot that lives on GitHub.

- Every weekday evening, after the Colombo Stock Exchange has closed, an alarm clock wakes it up.
- It goes to the exchange's website and politely asks what happened today: the prices, the news,
  and who bought or sold.
- It writes everything down in notebooks, does its sums, and paints four web pages for you to read.
- It never buys or sells anything. It only looks.

The robot has three rules it never breaks:

1. **No made-up numbers.** Every number comes from the exchange or from a company's own report.
   If it can't find a number it's sure about, it shows a dash (—) instead of guessing.
2. **Never rub anything out.** Notebooks only ever get new lines at the end, so you can always see
   exactly what the robot knew, and when.
3. **Check before writing.** If an answer from the website looks wrong, the robot writes nothing that
   night, and yesterday's good pages stay up.

## A day in the robot's life

| Step | What happens | Which file does it |
|---|---|---|
| 1 | The alarm clock rings on weekday evenings | `.github/workflows/daily.yml` |
| 2 | The robot checks its own homework (the tests) | `tests/` |
| 3 | It fetches today's prices, totals and announcements, politely | `cse/fetch.py`, using `cse/http.py` |
| 4 | It checks every answer has the right shape | `cse/models.py` |
| 5 | It writes the answers into its notebooks | `cse/storage.py` → `data/` |
| 6 | It updates the pretend-money portfolios | `cse/lab.py` and friends |
| 7 | It updates who owns what, and who bought or sold | `cse/ownership/`, `cse/dealings.py`, `cse/flows.py` |
| 8 | It works out which prices look cheap or expensive | `cse/fundamentals.py` |
| 9 | It paints the web pages | `cse/build.py` + `cse/templates/` |
| 10 | It saves everything and publishes the website | `daily.yml` again |

On Saturdays a second alarm clock (`.github/workflows/ownership.yml`) collects new company reports
and rebuilds the ownership family tree.

## The four web pages

| Page | The question it answers |
|---|---|
| **Terminal** (`index.html`) | What did the market and my stocks do today? Panel A is the whole market, B is your stocks, C is the news, D is the pretend-money portfolios. |
| **Ownership** | Who really owns each company, and who owns the owners? |
| **Money flows** | Who has been buying and selling, and where are the bosses putting their own money? |
| **Fundamentals** | Does each company's price look cheap or expensive compared with what it earns and owns? |

## Every file, in one line each

### The robot's tools (`cse/`)

| File | What it does |
|---|---|
| `http.py` | The robot's polite voice: one question at a time, a pause between, never asks to buy or sell. |
| `models.py` | Checklists that every answer from the website must pass. |
| `config.py` | Reads your list of stocks and checks every name exists. |
| `storage.py` | Notebook rules: add at the end only, no duplicates, no half-written pages. |
| `fetch.py` | The nightly trip to collect today's data. |
| `backfill.py` | The one-time trip to collect a year of past prices. |
| `metrics.py` | Sums for Panels A and B (moves against the market, busy days, yearly high and low). |
| `tags.py` | Sorts announcements into labelled boxes. |
| `sectors.py` | Puts each company into its industry family. |
| `corpactions.py` | Keeps track of dividends, splits, free shares and rights issues. |
| `returns.py` | Works out what an owner really made, including dividends. |
| `estimates.py` | Picks stocks fit for the lab and measures how bumpy and how easy to trade each one is. |
| `optimize.py` | Mixes stocks into model baskets with maths, inside safety rules. |
| `record.py` | The pretend-money diary that never changes old pages. |
| `lab.py` | Runs the lab's steps in order. |
| `build.py` | Paints the Terminal page and asks the other painters to paint theirs. |
| `dealings.py` | Collects "a director bought or sold shares" notices. |
| `flows.py` | Adds up who is buying and selling, and spots several insiders buying together. |
| `fundamentals.py` | Reads profit and what each company owns from its reports, then works out P/E, P/B and dividend yield. |

### The family-tree detective (`cse/ownership/`)

| File | What it does |
|---|---|
| `collect.py` | Downloads each company's quarterly report and keeps the words. |
| `parse.py` | Finds and reads the "top 20 shareholders" table, and checks it adds up. |
| `names.py` | Works out who a shareholder really is, and what kind of holder it is. |
| `analyse.py` | Builds the family tree: who owns whom, and who is in charge. |
| `groups.py` | Puts companies with the same boss into one group. |
| `annual.py` | Saves sentences like "our ultimate parent is..." from annual reports. |
| `parents.py` | Turns those sentences into a homework sheet for you to check. |
| `site.py` | Paints the Ownership page. |

### Everything else

| Where | What it is |
|---|---|
| `cse/templates/` | The drawings for each web page. The robot fills in the blanks. |
| `config/` | **Your** settings. This is the only folder you normally edit (each file says what it's for at the top). |
| `data/raw/` | Exactly what the website answered, untouched, one folder per day. |
| `data/history/` | The main notebooks: prices, index levels, market totals, announcements, share events. |
| `data/ownership/`, `data/dealings/`, `data/fundamentals/`, `data/lab/` | Answers the robot worked out from the notebooks. Rebuilt every run. |
| `data/runs.csv` | The robot's diary of every run: worked, nothing new, or failed. |
| `record/` | The pretend-money portfolios' diary. Old lines are never changed. |
| `site/` | The finished web pages (rebuilt every night, not saved in the repo). |
| `tests/` | The robot checking its own homework against real answers the exchange once gave. |
| `scripts/` | Helpers you run by hand. |
| `docs/` | Notes: how the website's data works (`API_NOTES.md`), the exact rules (`METHODS.md`), homework sheets for you (`*_EVIDENCE.md`), and this page. |

## Words you'll meet

| Word | In plain words |
|---|---|
| **Share** (or stock) | A tiny piece of a company. Own a share and you own a tiny bit of the company. |
| **Stock exchange** | The market where people buy and sell shares. Here, the Colombo Stock Exchange (CSE). |
| **Price / close** | What one share cost at the end of the day. |
| **Index (ASPI, S&P SL20)** | One number that shows whether the whole market went up or down, like a class's average mark. |
| **Turnover** | How much money changed hands buying and selling today. |
| **Market cap** | What the whole company is worth at today's price: price × number of shares. |
| **Dividend** | Cash a company pays its owners out of its profit, so much per share. |
| **Split / scrip / bonus** | The company cuts each share into smaller pieces or hands out free extra shares. You own the same slice of the company; the price per share just drops. |
| **Rights issue** | The company offers its current owners new shares, usually at a discount. |
| **Ex-date** | The first day a buyer no longer gets that dividend or right. The price usually drops by about that much. |
| **Profit / EPS** | What the company earned. EPS is that profit divided by the number of shares. |
| **P/E** | Price ÷ yearly earnings per share: how many years of today's profit you're paying for. Lower can mean cheaper. |
| **P/B** | Price ÷ what each share is worth on paper (everything the company owns minus everything it owes, per share). Below 1 means the price is less than that. |
| **Dividend yield** | Last year's dividends ÷ price: the cash you got back per rupee invested. |
| **ROE** | Profit ÷ what the owners have put in: how good the company is at turning money into profit. |
| **Director dealing / insider** | A company's boss buying or selling that company's shares. They must tell everyone within a few days. |
| **Cluster** | Two or more different insiders buying the same company around the same time. |
| **Top-20 list** | The 20 biggest owners of a company, printed in its quarterly report. |
| **Control** | Owning more than half the votes, which means you're in charge. |
| **Volatility** | How bumpy a stock's ride is. Bumpier means more risk. |
| **Beta** | How much a stock tends to move when the whole market moves. 1 = the same; 2 = twice as much. |
| **Portfolio / basket** | A mix of several stocks, so one bad stock doesn't sink you. |
| **Liquidity** | How easy a stock is to buy or sell without moving its price. Many CSE stocks trade very little. |
| **Trading cost** | The fee for each buy or sell (about 1.1% each way on the CSE), which the pretend portfolios pay too. |
