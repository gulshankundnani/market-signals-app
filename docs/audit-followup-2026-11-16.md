# Follow-up audit, 16 November 2026

Everything below was measured on 16 Nov 2026 between about 10:30 and 11:15 UTC. Commands to repeat each check are given. Nothing here
is a claim of accuracy beyond what the numbers show.

## 1. Price verification (repeatable)

`python tools/verify_market_data.py --volume --json report.json`

* Same trading session only. If the NSE file or Cboe row for the app's date is not published, the row is **UNVERIFIED**, never
  passed or failed. A provisional (still moving) price is UNVERIFIED; the latest completed session is verified separately.
* Result at 11:01 UTC: 40 price rows, 20 PASSED (previous completed session, 10 NSE + 10 US, largest difference 0.0002%), 0 FAILED,
  20 UNVERIFIED (the newest session: the NSE file for 16 Nov and Cboe's 13 Nov row were not published yet).
* References: NSE end-of-day file (India), Cboe delayed-quote history (US). Both are independent of Yahoo Finance.

## 2. Reliance volume

* Completed sessions: Yahoo's daily volume equals the NSE final volume **exactly**, 90 of 90 stock-sessions (10 stocks x 9 sessions,
  2 to 13 Nov). So there is no adjustment, timestamp or exchange difference for completed sessions.
* 16 Nov: Reliance 7,653,026 against 13.8 to 19.7 million on the previous four sessions. Yahoo's daily bar and its
  `regularMarketVolume` agree; the 5-minute bars to 15:25 IST sum to 7,498,682 with one empty bar, so the difference to the daily
  figure is the last bar. This was measured about 30 minutes after the close.
* **Resolved on 17 Nov (04:09 UTC), when the NSE file for 16 Nov was published.** The NSE final volume for Reliance was 7,653,528.
  Yahoo had shown 7,653,026 half an hour after the close, 502 shares (0.0066%) short, and now equals the NSE figure exactly. So the
  low reading was **genuine low volume**, not an incomplete count, an adjustment or a different data source. For all 10 NSE stocks
  the volume Yahoo showed 30 minutes after the close was within 0.054% of the NSE final (largest: LT, 615 shares); every later Yahoo
  figure equals the NSE final exactly. The small late additions are consistent with trades reported after the close.
  **The volume calculation was not changed.** Missing volume stays missing and a real zero stays zero.
* Provisional readings were observed live on 17 Nov at 04:09 UTC with the Indian market open: 119 Indian stocks flagged provisional,
  `confirmed_as_of` 2026-11-16, a separate provisional reading with volume left out and "reduced" confidence, and the "Market open"
  chip and note on the stock page. No US stock was provisional (that market was closed).

## 3. Survivorship bias: what free data can and cannot do

* Delisted companies: Yahoo returns 404 for SIVB, FRC, TWTR, ATVI, CTXS, DHFL.NS, VIDEOIND.NS, SINTEX.NS, JPASSOCIAT.NS. Some old
  tickers have been reused by new companies (BBBY now shows a history from 2026-07; SBNY from 2024), so including them would test the
  wrong company. A few suspended names (RCOM.NS, JETAIRWAYS.NS) still have history.
* Index membership: Wikipedia's S&P 500 table gives each current member's addition date (`tools/build_membership.py`, 41 of 41 US
  stocks). All 41 joined before the 5-year test window, so limiting a backtest to the membership period changes nothing today. The
  NSE publishes only the current Nifty lists, with no addition dates.
* So the Indian and US results still contain **survivorship bias** (companies that failed or left the index are missing). This is
  documented in the app, and no result is described as survivorship-bias-free.

## 4. Dividends

Both modes are in `engine/backtest.py`: price-only headline figures and a `total` block with cash dividends on the ex-date for the rule
and for buy-and-hold, reinvested at the ex-date close at no cost (or kept as cash with `reinvest=False`). A buyer on the ex-date gets no
dividend; a seller at the ex-date open does. Yahoo's prices and dividend amounts are adjusted for splits and bonus issues. Tax on
dividends, spin-offs and mergers are not modelled.

## 5. Strategy performance

`python tools/strategy_report.py` writes `docs/strategy-report.md` and `.json` (81 NSE and 41 US stocks with at least 260 bars, both
rules). Headlines, medians, dividends included for both sides, assumed costs:

| | NSE weeks | NSE months-years | US weeks | US months-years |
|---|---|---|---|---|
| Rule beat buy-and-hold on total return | 12 of 81 | 14 of 81 | 7 of 41 | 6 of 41 |
| Volatility lower than buy-and-hold | 81 of 81 | 81 of 81 | 41 of 41 | 41 of 41 |
| Max drawdown smaller | 43 of 81 | 57 of 81 | 32 of 41 | 28 of 41 |
| Sharpe higher | 10 of 81 | 12 of 81 | 8 of 41 | 8 of 41 |
| Calmar higher | 17 of 81 | 21 of 81 | 11 of 41 | 11 of 41 |
| Median time invested | 48% | 61% | 49% | 63% |

The rules lower volatility and downside deviation in every stock, but mainly by being out of the market about half the time. They do
not improve risk-adjusted return: Sharpe, Sortino and Calmar are worse than buy-and-hold in most stocks. Regime labels (index above a
rising 200-day average = bull) lag turning points, so "bear" days include early rebounds. No threshold was changed.

## 6. US IPO calendar

* Root cause: `api.nasdaq.com` answers the engine's request with **HTTP 403 Access Denied** from its CDN (Akamai). It refuses
  automated clients. This was reproduced with the engine's own honest request. It was not worked around.
* Fix: the failure is now logged with its reason, a refusal pauses further requests for 6 hours instead of repeating every run,
  carried-over records are marked Stale (and Date passed when their date has gone), and the page shows the last successful
  refresh, the original data date, its age and the error, with a link to the live Nasdaq calendar. A list older than a week is dropped.

## 7. Market capitalisation

* The 11 stocks without one on the live site were ARHAM, BONDADA, BRIGHTOUTD, IIRM, INDTONER, IOC, KHAZANCHI, PARASHLTH, RECLTD,
  RELIANCE and TCS. Querying Yahoo directly returned a market cap for RELIANCE, TCS, IOC and RECLTD at 10:5x UTC, so the app had cached
  a partial Yahoo answer for up to 20 hours. ARHAM returned empty fields for market cap and shares: a real gap for small or new names.
* Fix: an answer without a market cap is asked for again after 30 minutes (up to 4 times, then daily), a later partial answer never
  overwrites an earlier complete one, nothing is replaced by zero, and unavailable figures show as "n/a".
* Scores do not use fundamentals (tested). The Screens ranking used to score missing fundamentals as zero; it now leaves them out
  and says so.

## 8. Event dates

* The NSE's public corporate-action and board-meeting feeds answered the engine's honest request (HTTP 200) from this machine.
  `engine/official.py` reads them (two requests per refresh, not per stock), confirms or contradicts the app's dates, and adds
  official events Yahoo missed. Example: PFC.NS ex-dividend 17 Nov, Interim Dividend Rs 4.10, now **CONFIRMED by NSE**.
* If the NSE refuses (403) the feed is left alone for 6 hours, every event stays **UNVERIFIED** with the reason, a link to the
  official page and the last check time. It has not yet been seen whether the NSE answers the GitHub Actions servers.
* US dates have no official machine-readable feed: always UNVERIFIED, with a link to the company's SEC filings.
