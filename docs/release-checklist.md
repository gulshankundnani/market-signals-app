# Release checklist

Run before every push. Each line says what is checked and how. `tools/qa_sweep.py` runs the data and file checks against a build folder; the browser flows
are listed so the same pass can be repeated.

## 1. Data accuracy (tools/qa_sweep.py)
- [ ] Every row of `data.json` and `index.json` has a finite price and a symbol that is unique across both files.
- [ ] Index and detail records agree: the reading and score in an index row equal the ones in its detail file.
- [ ] Fundamentals are plausible: no ratio is outside a sane range without being a loss-making case (P/E over 2,000, ROE over 500%, a negative price).
- [ ] Dividend figures: 12-month amount equals the sum of the listed payments; recurring yield is not above 12-month yield by more than the specials.
- [ ] Indicators and signals: `python tools/verify_indicators.py --build FOLDER` (indicators recomputed independently, scores recomputed from their parts, labels and confidence from thresholds).
- [ ] Dividend cross-check with Yahoo's indicated amount: share that agree is reported; every "differs" has a note.
- [ ] Market cap, EPS, book value are numbers or null, never zero by default.
- [ ] Stale quotes (older than 7 days) are flagged, not shown as live.
- [ ] Price cross-check against the official source (`tools/verify_market_data.py`) has no mismatches among verified stocks.

## 2. Engine robustness
- [ ] A build with no previous data (first run) completes.
- [ ] A build with an old `state.json` (missing the newer keys) completes.
- [ ] A refused or failing source never stops the run: NSE feeds, Yahoo quotes, Google News, IPO calendar, SME list.
- [ ] Run time stays well under the 20-minute job limit.
- [ ] Output size: `data.json` under 1.6 MB, `index.json` under 1.8 MB, `index_extra.json` under 1.6 MB, each detail file under 450 KB (all enforced by `tools/qa_sweep.py`).
- [ ] `index_extra.json` is not fetched at start-up, only when a technical filter, the technical panel or a column view other than Overview is used.

## 3. Alerts
- [ ] The digest renders for every kind of alert at once (readings, deals, IPO watch, movers, 52-week lows, indexes, briefs, headlines) in Telegram HTML.
- [ ] Every message is under Telegram's limit and every link is well formed; text from outside is escaped.
- [ ] Each alert kind is announced once per stock per day; the per-run caps hold. Sample the live channel (`https://t.me/s/marketsignalsapp`) and check prices, levels and index moves against Yahoo day ranges.
- [ ] Alerts for stocks outside the main list link to their analysis.

## 4. Site flows (browser, desktop and 375 px)
- [ ] First load: no console errors, no stray "null" text, one download of each data file.
- [ ] Tabs: Readings, Screens, IPOs, Market, Events, Record all draw.
- [ ] Table: sort by every header, market filter, Reading filter, horizon, column views, paging, "Show more".
- [ ] Chips: categories on the left and sectors on the right combine; counts equal the rows shown; "Filters on" chips remove their filter.
- [ ] Technical filters set, combine, clear.
- [ ] Search: exact, partial, reordered, case, typo, symbol, no match message.
- [ ] Watchlist star, custom lists, Export CSV.
- [ ] Detail dialog: main-list stock, analysed other stock, quote-only stock, every tab, close, deep link `#stock=SYMBOL&section=...`.
- [ ] Failure states: index file missing (message and Try again), detail file missing (quote card).
- [ ] Layout: wide, mid and phone widths have no sideways page scroll.

## 5. Release hygiene
- [ ] All Python and JS tests pass.
- [ ] README describes what the app does now.
- [ ] Service worker version raised when the page files changed.


## After pushing the learning and news work
* Start **Learn from the past** (Actions, Run workflow) once; it logs the outlook table to the `data` branch (`learning/outlook-table.json`). The 10-minute build uses it from the next run.
* Start **News AI scores** once and read its log: it needs the `models: read` permission and GitHub Models access. If it says nothing was scored, the site keeps using the rule-based news score (nothing breaks). Variables: `NEWS_AI=off` to switch it off, `NEWS_AI_MODEL` to choose a model.
* Check the Market tab: "Data health" lists each source's reliability and any paused stocks; the News tab lists each news provider's state.
* NewsData.io is off unless the secret `NEWSDATA_API_KEY` exists; read its terms for public use first.
