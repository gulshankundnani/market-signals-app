# Stock Signals

A free, installable web app that shows Buy / Hold / Sell signals for Indian (NSE) and US stocks,
with news, buying and selling pressure, dividends, risk, IPOs and a backtest for every stock.
It runs on GitHub only: GitHub Actions refreshes the data every 15 minutes, GitHub Pages hosts the site.

It is not investment advice. Signals come from fixed rules on free, delayed data and can be wrong.

## What updates by itself

| Feature | Source | Notes |
|---|---|---|
| Prices, signals, patterns, risk, backtest | Yahoo Finance (free), cross-checked against the NSE end-of-day file (India) and Cboe delayed-quote history (US) | Prices can be up to 15 minutes late. The cross-check covers completed sessions; see Price accuracy below |
| News | Google News | Shown as published, with source and age. No good-or-bad tone is worked out from the words. The stock comes first: a headline is attached to a stock only if it is clearly about the company: the full name, a ticker that is not an ordinary word, or a single name word together with business wording (shares, results, Q2, dividend). Stories about people or sports that share a name, rating headlines that name a bank only as the one giving the rating, lists and roundups, and headlines about more than one listed company are ignored. A headline is "fresh", and can be sent as an alert, for 6 hours |
| Buying / selling pressure | Volume (Chaikin Money Flow, up-volume share) | A proxy. The exchange order book is not published for free |
| NSE delivery % and bulk/block deals | NSE end-of-day files | India only, updated after each trading day |
| Upcoming events | Yahoo Finance calendar, plus estimates from each stock's own dividend history | Results dates, ex-dividend dates and dividend payment dates. Each is labelled "unconfirmed" (Yahoo's figure) or "estimated". Recent splits come from price history |
| Dividends | Actual payments from Yahoo | Frequency, amount, 12-month and recurring yield, specials, overdue payments |
| Fundamentals and peers | Yahoo quoteSummary | Refreshed once a day. Shows "unavailable" when missing |
| New listings | NSE equity list and Nasdaq/NYSE symbol list | Added to the "New listings" list on their own. Signals start after about 60 trading days |
| India IPOs | Investorgain's public IPO table (robots.txt allows it), read at most once an hour | Open, close, allotment and listing dates, price band, lot size, subscription, grey-market premium (GMP), estimated listing price and profit or loss per lot. Past IPOs show the actual listing price and gain |
| GMP cross-check | Google News headlines that name the IPO and quote a premium | Flags "news agrees" or "news differs". GMP is an unofficial rumour and can be wrong |
| US IPO calendar | Nasdaq | Date, price range, size |
| Market conditions, breadth, sectors | Computed from the index (Nifty 50, S&P 500), its volatility index and the stocks in this app's list | Seven simple checks worth +1, 0 or -1 each, advancers and decliners, share of stocks above their 20/50/200-day averages, sector moves over 1, 5 and 20 days. Breadth and sectors cover this app's list, not the whole exchange |
| Lists | Computed daily | Government-backed, high dividend, low debt, low volatility, momentum, oversold, near 52-week high, near 52-week low, delivery buying, sectors |

## What is not automatic

* An unlisted company's financials are not published as data anywhere free, so scoring an IPO on profit,
  debt and valuation uses the optional "Check an IPO yourself" tool on the IPOs tab, where you type the
  figures. Everything else about Indian IPOs fills in on its own.
* The NSE blocks scripted access to its IPO data, and IPO Watch loads its table from a path its
  robots.txt disallows. This project does not try to get around either, so Investorgain is the only
  source for Indian IPO dates and GMP. If its page layout changes, the parser returns nothing instead of
  guessing and the app keeps showing the last good data with a warning.
* Order book depth, real-time ticks, and insider or bulk-deal data for the US.
* Alerts when the app is closed need the optional Telegram setup (see Alerts below).

## Set it up (about 10 minutes)

1. Create a free GitHub account if you do not have one, then create a new **public** repository
   (public repositories get unlimited free Actions minutes; the site and its data will be public).
2. Upload everything in this folder to the repository's `main` branch.
3. In the repository: **Settings > Pages > Build and deployment > Source: GitHub Actions**.
4. Open the **Actions** tab, choose **Update signals**, and press **Run workflow**. The first run takes a few minutes.
5. Your site is at `https://<owner>.github.io/<repository-name>/`. This project's site is
   `https://marketsignals-app.github.io/market-signals-app/`.
   On your phone open it in Chrome or Safari and choose **Add to Home screen** to install it like an app.

The data is refreshed on a fixed beat by the **Refresh loop** workflow: every 10 minutes while the markets are open
(weekdays 03:00-20:59 UTC) and every hour otherwise. GitHub's own cron is best-effort and may never start a run, so the
loop starts "Update signals" itself and starts a fresh copy of itself before GitHub's 6-hour job limit. Start it once
with **Actions > Refresh loop > Run workflow** (pushing the file also starts it). To stop it, set the repository variable
`REFRESH_LOOP` to `off` (Settings > Secrets and variables > Actions > Variables) and cancel the running loop. A cron
line in each workflow is a backup that restarts the loop or asks for a refresh once an hour. The loop keeps one runner busy
most of the time, which public repositories get free; if you would rather not use it, set `REFRESH_LOOP` to `off` and
refresh by hand with **Actions > Update signals > Run workflow**. When the data is more than 45 minutes old on a market
day, the site shows a banner saying so.

## Alerts

`config/alerts.json` controls what is watched:

```json
{"symbols": ["*"], "news": true}
```

* `symbols`: `["*"]` watches every stock. Or list the ones you want, for example `["TCS.NS", "SBIN.NS", "AAPL"]`.
* `news`: `true` includes fresh headlines about the stock in the alerts, `false` leaves them out. Headlines are shown as
  published, with their source and a link to the article. The app never judges one as good or bad news.

An alert is raised when a watched stock's short- or long-term signal moves between buy, hold and sell, and when a
headline from the last 6 hours that is clearly about the company appears (at most one per stock and 6 per run). To stop a score that hovers at a threshold from sending a message
every run, a stock only moves into Buy or Sell at a score of +/-25 and only drops back once it falls through
+/-10. The move is measured from the side last announced, so a slow drift is still reported once it is clear.
The first run only records the current state and raises nothing. A busy run is sent as up to 3 messages, and anything
beyond them is counted, with all alerts listed on the Market tab.

### What an alert looks like

Each Telegram message is a short list of bulleted cards, readings first and headlines second. A reading card says what
changed, gives a resistance or support reference and a volatility risk level, lists the indicator readings behind it,
and links to the full analysis. A headline card shows the headline as published, its source and age, and links to the
article and to the stock's analysis:

```
📊 Market Signals · Mon 16 Nov, 09:38 IST
1 reading change · 1 headline

━━ READINGS ━━

🔴 Coal India (COALINDIA.NS)
Aligned bearish · Weeks view
• Change: Mixed → Aligned bearish (alignment -27)
• Price: ₹398.80
• Support reference: ₹364.95 (-8.5%, volatility-based, 2x the risk distance)
• Volatility risk level: ₹415.73 (+4.2%)
• Indicator readings:
   – Trend: price below 20-day average, 20-day below 50-day
   – Volume pressure: selling pressure over 20 days
• Open analysis: https://marketsignals-app.github.io/market-signals-app/#stock=COALINDIA.NS&section=signals

━━ NEWS ━━

📰 Berkshire Hathaway (BRK-B)
• Headline: Berkshire's Rails and Renewables Offset a Costlier GEICO as Buybacks Outrun Forecasts
• Source: ad-hoc-news.de · 64 min ago
• Read article: https://news.google.com/...
• Open analysis: https://marketsignals-app.github.io/market-signals-app/#stock=BRK-B&section=news

ℹ️ Levels are indicative, from fixed chart rules. Not advice.
Educational information only, not investment advice. We are not a SEBI-registered adviser or research analyst.
```

Alert links are built from the repository's owner and name on every run, so they follow the repository if it is
moved or renamed. Nothing in the code holds the address.

* **Volatility risk level:** 2 x the 14-day average true range from the price (below it for a bullish reading, above
  it for a bearish one). This distance is called R.
* **Short-term target:** the nearest resistance (buy) or support (sell) if it is 1 to 4 R away, otherwise 2 R.
* **Long-term target:** the 52-week high (buy) or low (sell) if it is at least 1 R away, otherwise 3 R.
* **Indicator readings:** the reasons on the stock's page that support the new signal. For a signal that has faded to hold, the
  reasons that weakened the old one, and no reference level is given.
* **News is separate from readings.** It never changes a reading and is never graded as good or bad. A headline is
  attached to a stock only when it is clearly about that stock (see the News row in the table above), and a headline about
  more than one company in the list is attached to none of them. The stock comes first and its news follows, never the
  other way round.

The reference levels come from fixed rules, are not forecasts, and prices can gap past the volatility risk level. Tapping the
link opens that stock's page on the site, with the alert at the top followed by the full analysis (signals,
chart, levels, risk, fundamentals, dividends, news and backtest). The Market tab lists recent alerts with a
"View analysis" button for each.

* **In the app:** the Alerts button turns on notifications for the stocks you starred, while the app is open.
  Recent alerts are listed on the Market tab.
* **Telegram channel (alerts on your phone with the app closed):** the site links to the channel
  (`https://t.me/marketsignalsapp`) from its header and footer. To make the refresh job post there:
  1. In Telegram, message `@BotFather`, send `/newbot` and follow the steps. Keep the token private.
  2. Add the bot to the channel as an **administrator** who can post messages.
  3. Find the channel's chat ID: post once in the channel, then open
     `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser (with your token) and note the number after
     `"chat":{"id":` (it starts with `-100`). For a public channel you can also use `@marketsignalsapp`.
  4. In the repository: **Settings > Secrets and variables > Actions > New repository secret**. Add
     `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
  5. Check it: **Actions > Update signals > Run workflow**, tick **Send a test message to Telegram first**, then run.

Delivery depends on the 15-minute schedule, and GitHub may delay scheduled runs. Anyone can join the public
channel and get the same alerts. Without Telegram nothing is sent outside the app: a web page cannot push
notifications to a closed browser without a push service or a server that stores each person's browser
address, and this project uses neither.

### Target-hit replies

Each bullish or bearish alert is recorded (price, reference level, volatility risk level). When a later price reaches
either level, the bot replies under the original Telegram message ("REFERENCE LEVEL REACHED" or "VOLATILITY RISK LEVEL REACHED") with a link to
the analysis. A call that simply runs out of time is recorded but not announced. If the original message was
deleted, the reply is still sent on its own. Each outcome is announced once.

## Track record

Every call and its outcome is stored in plain files on a separate **`data`** branch of this repository (created on
the first run; your code on `main` is never touched). `calls-YYYY-MM.jsonl` holds calls and `outcomes-YYYY-MM.jsonl`
holds how they ended; lines are only ever added, and git history shows when each was written. The **Track record**
tab shows hit rates with confidence ranges, average return against the market index, results by score band, which
signal components were stronger on winners, and suggested improvements.

* It only **measures**. Suggestions are for you to read; nothing changes the signal rules automatically.
* Figures under 30 resolved calls are labelled unreliable. Past results do not predict future ones.
* Fills are assumed at the next bar's open; a bar that touches both stop and target counts as a stop.
* The repository must allow the workflow to write: **Settings > Actions > General > Workflow permissions > Read and write**.
  If the ledger cannot be saved, calls wait in the site's state and are saved on the next run.
* The data branch is public if the repository is public.

## Layout: glance first

Modelled on what Groww, Angel One, Upstox and INDmoney do on a phone:

* **Bottom navigation** on phones (Readings, Screens, IPOs, Market, Events, Record); the top tab row is used on larger screens.
* **Stock page**: a sticky bar with the price and day change, then a glance block: both readings with their bars, an
  overall line ("Both views aligned bullish", "The two views differ" and so on) with a bar showing how many of the fixed
  technical checks point up, down or neither, the 52-week range with a marker, key figures, and chips for the next event
  and fresh headlines. Below it are tabs: Overview, Technicals, Fundamentals, News & events, Backtest. Alert links open the
  right tab.
* **Price map**: resistance, support and the volatility risk level sit in one small table with the price and each level's
  distance from it, and the 52-week range bar is under it. There is no price chart; a static line added nothing the table
  does not say. The whole top of the page (readings, levels, key figures) fits in about one phone screen.
* **Track record**: one headline first. Until 30 readings have settled it says "Too early for a success rate" with a
  progress bar (the rate would be unreliable). After that it shows the success rate (the price reached the reference
  level before the risk level or the time limit), failure and expired shares in one bar, the average move, and a card
  per reading type. Open readings, alignment and market splits, and the glossary sit below or under "More detail".
* **Readings**: each market has a stacked bar (bullish, mixed, bearish) and the index's trend and day move.
* The labels stay neutral ("aligned bullish", "mixed", "aligned bearish") rather than buy, hold or sell, because the
  readings describe conditions and are not advice.

## Method checks (what was verified, and how)

These were checked on 2026-11-16 against independent calculations and sources. The numbers are in the tests, so they can be
repeated.

* **Missing volume is not zero.** Yahoo sometimes gives no volume for a day. That stays missing; a real zero stays zero. Any
  volume indicator (VWAP, Chaikin money flow, up-volume share, average volume, the volume spike rule) that would need a
  missing day gives no value, so a gap can never create a pressure reading. Each reading now carries a coverage figure (the
  share of its rule's weight that had data), a confidence of full, reduced or limited, and the parts that were missing.
* **Unfinished daily candles.** While a market is open Yahoo's newest daily bar is the day so far: the close is the live
  price, the range is the range so far and the volume is only what has traded. (Seen on 16 Nov 2026 at 10:34 UTC with Xetra and
  London open: SAP.DE's bar had volume 155,629 against 1.41 million the day before.) So the price shown stays live, but every
  indicator, level, backtest and track-record check uses completed candles, and the unfinished candle gets a separate
  "provisional" reading (volume left out) that is recalculated after the close. Yahoo's trading-period times in the chart
  metadata decide when a bar is still running, with 30 minutes of grace after the close.
* **Indicators.** SMA 20, 50, 200, EMA 12 and 26, RSI 14 (Wilder), MACD 12/26/9, ATR 14 (Wilder), 30-day annualised volatility,
  VWAP 20, Chaikin money flow 20, up-volume share, 126-day momentum and 252-session highs and lows were compared with pandas and
  numpy on the same 700-bar series: the largest relative difference was 9e-13 (EMA 26), most were below 1e-13. Nothing was
  rewritten. Fibonacci levels and support/resistance have worked examples in the tests.
* **Golden and death crosses are events.** "50-day crossed above the 200-day 9 sessions ago (golden cross)" appears only when
  the crossover happened in the last 20 sessions. A 50-day average that simply sits above the 200-day is described as
  "bullish alignment, no recent crossover". The score still follows the alignment.
* **Thresholds are unchanged** (Buy at +20, Strong Buy at +50, and the same on the sell side): there are no settled results yet
  to justify changing them, and they are never tuned to flatter the history.
* **Backtests** have a second set of figures: CAGR, Sortino, expectancy, gross against net return (cost drag), and buy-and-hold
  CAGR. Costs are now separate for entry and exit and include assumed taxes, fees and slippage (`engine/costs.py`,
  overridable in `config/costs.json`); an open position at the end is valued as if sold. The simulation was re-implemented
  independently and matched the engine to the last digit on 16 of 16 stock and rule pairs. The rule beat buy-and-hold over the
  whole period in only 3 of those 16, and the "recent 40%" is not an independent out-of-sample test (same history, same rules).
  Limits: prices exclude dividends, the stock list is today's (survivorship bias), no tax on gains.
* **Track record** counts only calls made under the current rule version (now v3). Older calls stay in the ledger unchanged and
  keep being resolved, but are not mixed into the success rate.

## Every-stock coverage, briefs and index alerts

* **Full analysis (readings, backtests):** the 200 Nifty 200 stocks (plus Groww) and 41 US stocks in `config/universe.json`. Refresh the list with
  `python tools/update_universe.py`; existing entries are never changed.
  **The list also updates itself:** once a day from 10:00 IST (`engine/universe_sync.py`) the app reads the NSE's Nifty 200 list and adds any
  stock it does not yet analyse (no cap), kept in the saved state, so `config/universe.json` is not rewritten. Nothing is ever removed.
  A failed or refused download is retried after an hour, never worked around.
* **Every other listed stock:** all ~2,170 other NSE equity-series stocks and the rest of the S&P 500 (`engine/wide.py`) get live quotes in
  batches of 100, factual **mover** alerts (a move of 8% India / 6% US or more, a volume surge of 3x average on a 2%+ move, a new 52-week high
  or low; liquid stocks). **Penny and small stocks are included** with higher bars (12% India / 10% US, or 5x volume on a 5%+ move, no 52-week alerts), an
  average-turnover floor so a handful of trades cannot raise an alert, a "small stock" label, and at most 3 of a run's mover alerts; their own headlines looked up **stock by stock** in a rotating slice of 50 per run (stocks that just moved
  first), so the list is covered every few hours. Deals and official event dates cover every NSE stock.
* **Every listed stock is in the table** (`engine/catalog.py`). The dashboard loads only `data.json` (the curated list) and a small `index.json`
  (one row per other stock: price, move, size, 52-week range and, once analysed, the two readings), and shows 150 rows at a time (sorting and
  filters cover every stock). A stock's full analysis is a separate file in `detail/NN.json` (64 shards), fetched only when the stock is opened.
  Each run analyses up to 60 stocks (never analysed first, most traded first, then the stalest), so the whole list is covered within hours of
  the first run and each stock is refreshed about once a day, which is how often completed daily candles change. Until a stock is analysed its
  row says "quote only" and opening it shows the quote. The previous shards are read back from the published site on every run.
  Nothing is left half-done for these stocks: the table says while the full list is loading, when it could not load (with a "Try again" button) and how
  many stocks are analysed so far; the Reading filter says how many rows it hides because they have no reading yet; an empty result offers "Clear
  filters"; a quote-only stock can be starred and opened on the NSE or Yahoo pages; "Near 52-week high/low" include every stock from its quote alone, and
  analysed stocks also join the other screens (Oversold, Low debt and so on); alert cards in Telegram link to the analysis of any listed stock. The NSE
  publishes no sector, so these stocks have no sector chip or peer group.
* **52-week lows** are their own alert category ("52-WEEK LOWS" in Telegram, at most 5 per run), separate from movers.
* **IPO watch:** every stock listed within the last 90 days has its listing-day high and low taken from its first daily candle. While its market is
  open, a price above that high sends an "IPO breakout" alert and a price below that low an "IPO breakdown" alert (once per stock per day, at most 5
  per run). They are statements of where the price is, not forecasts. The pre-open brief ends with a "Stock watch" line listing these stocks, their
  last price and listing-day range. IPO and grey-market pages (IPO GMP, price band, subscription status) never count as news for a listed stock, and a
  group company ("NTPC Green") is not matched to its parent ("NTPC").
* **Index alerts:** moves past 1, 2 and 3 percent, 52-week highs and lows, and volatility indexes past a level, for the main NSE indexes (while
  the market is open) and the global ones (once per session).
* **Briefs:** India pre-open (09:08 to 09:40 IST), post-close (16:00 to 19:30) and institutional flows (18:30 to 22:00), and the US close
  (02:45 to 06:00 IST), each sent once per day from the exchange's own figures. Not sent when the data is not there.
* **Short alert wording:** indicator lines are abbreviated (P>20D, 50D<200D, RSI 61 normal) with a key; the site keeps the full wording.
* **Survivorship bias:** still not resolved and never claimed to be. Free data has no delisted prices or point-in-time index membership. If
  you have such files, `tools/survivorship_probe.py` tests them with the same rules, apart from the published figures.

## Alerts: fairness and block deals

See `docs/alerts-audit-2026-11-17.md`. A stock gets at most 2 headline alerts a day, repeated stories are skipped, and the stocks alerted
least go first, so heavily covered names cannot fill the channel. Block deals are announced from the NSE's live feed for any NSE stock,
tracked or not, as soon as they appear. Only the 122 tracked stocks (plus new listings) get readings and headlines.

## Verification tools and follow-up audit

Details and evidence: `docs/audit-followup-2026-11-16.md`.

* `python tools/verify_market_data.py --volume` checks the deployed prices (and NSE volumes) against the NSE end-of-day file and the
  Cboe history, same session only. Not-yet-published references give UNVERIFIED, never a pass or a fail.
* `python tools/check_production.py --cache signals-v33` reads what the deployed site published (data freshness, the NSE feed and US IPO
  status as the Actions run saw them, stale handling, the service-worker cache name) and prints PASS, FAIL or UNVERIFIED for each. It cannot read
  the Actions logs, which need a login, so "error logged" stays UNVERIFIED unless you read the run log yourself.
* `python tools/strategy_report.py` measures the two rules against buy-and-hold (drawdown, downside deviation, Sharpe, Sortino,
  Calmar, exposure, holding period, bull/bear/sideways) separately for NSE and US stocks, with dividends included for both.
* `python tools/build_membership.py` refreshes the S&P 500 addition dates used to keep a backtest from using years before a stock
  joined the index. Survivorship bias is not removed and is never claimed to be.
* US IPO calendar: Nasdaq refuses automated requests (HTTP 403). The failure is logged, retried only every 6 hours, and an old list
  is shown as stale with its original date, never as current.
* Event dates: Indian dates are checked against the NSE's own announcement feeds (confirmed, different official date, or
  unverified with a link and the last check time); US dates are never confirmed.

## Events tab

The **Events** tab lists what is coming up for the stocks in this app: when results are expected, the ex-dividend date (buy
before it to receive the dividend), the dividend payment date, and splits or bonus issues when a prominent outlet has
reported them. The layout follows what established platforms do (Yahoo Finance's category tabs, Groww's
event cards):

* **One card per category**: Results, Dividend dates, Splits, bonus and other actions, and Announced in the news (no date
  stated). Each card shows its count and what is next, groups events by day, and shows the first 8 with "Show all".
* **Category chips with counts** at the top (a swipeable row on phones), plus search, market and "My watchlist only".
* Each event is a row with a date badge, what it is, and the key figure on the right (dividend per share, split ratio).
  Tap a row to open it: its sources (YAHOO, NEWS, ESTIMATED, AGREED), the news headline as evidence, and a button to the
  stock's page.
* It is built for phones first: one column, rows that are one large tap target, chips that scroll sideways, and two
  columns of cards only on wide screens.

Each stock's page also has an "Upcoming events" section and lists any splits of the last three years.

* **Where the dates come from.** Results, ex-dividend and payment dates come from Yahoo Finance (taken from the same request
  that fetches fundamentals, so no extra calls). They can be estimates, mostly for Indian companies, so they are labelled
  **unconfirmed**: the company confirms its dates by notice to the exchange. When Yahoo has no future ex-dividend date, one
  is **estimated**: the same week a year after an earlier payment, or the usual gap between payments for a newer payer.
* **How good the estimates are.** Wherever Yahoo does give a real date, the estimate is worked out as if it were unknown and
  compared with it. The tab shows the result (at the first live run: median error 1 day, 70% within a week, on 10 stocks).
  Some estimates are weeks off, so they are a rough guide.
* **When no official source can be read: the company's announcements as reported by prominent outlets.** The NSE and
  BSE do not allow free scripted access to their corporate-action lists, and this project does not try to get around
  that. Instead, news headlines about each stock are searched once a day (a batch per run, gently) for the company's own
  announcements: an ex-dividend or record date, a payment date, a results or board-meeting date, a stock split or bonus
  issue with its ratio, or an announced dividend amount. This is deliberately strict and shows its evidence:
  * only headlines from a list of prominent outlets are used (CNBC TV18, The Economic Times, Business Standard, Mint,
    Moneycontrol, Financial Express, NDTV Profit, Reuters, Bloomberg, Yahoo Finance and similar);
  * the headline must be clearly about that one company, must use a precise phrase ("stock split", "ex-dividend",
    "record date", "bonus issue", "results on"; the words "split" and "bonus" alone are not enough), and must state a
    date, ratio or amount itself. Nothing is guessed from tone;
  * a "this week" range from a roundup of record dates ("Nov 16-20: Cochin Shipyard, PFC...") counts too, when the
    outlet names the company;
  * each event shows its headline, source and link. Where Yahoo and a headline give the same date (or Yahoo's date falls
    inside a roundup's range) the event is marked **agreed**. A headline with no date is listed separately as
    "announced, date not stated".
* **Checked against CNBC TV18.** On 16 Nov 2026 the app's dates were compared with CNBC TV18's "Record Dates This Week"
  article. Of its 10 named stocks only 2 are in this app's stock list (EAAA Alternatives and PFC), and both dates matched.
  The article also showed that Yahoo's dividend amount is not the announced one (PFC: Rs 4.10 announced), so the app says
  "latest regular payment" and shows the announced amount only when a headline states it. The app lists only the stocks
  in `config/universe.json`.
* **Limits.** Headlines state few dates, so most stocks have no news-based event, and the page says how often news and
  Yahoo agree. Past splits come from price history.
* Only dates from today onwards are shown, within 120 days (estimates within 75).

## Price accuracy

Yahoo is the main price source, so the build checks it against an independent one on every run: the **NSE's official
end-of-day file** for Indian stocks (the exchange's own closing price) and **Cboe's public delayed-quote history** for US
stocks. The closing prices of the last completed sessions are compared date by date. Within 0.5% is a match, 0.5% to 2% a
small difference, and more than 2% a disagreement. A stock whose prices disagree gets a warning on its page and **no new
alerts** until the sources agree again. The Market tab shows the result for each source: stocks checked, how many match,
median and largest difference, and the latest session compared.

* This verifies completed sessions. The live price during the session comes from Yahoo alone: the NSE blocks scripted
  access to live prices, and no other free source offers them.
* Cboe's robots.txt cannot be read by this project, so its feed is used gently: one request at a time with a pause, a
  dozen stocks per run, once per day per stock, stopping at the first "slow down" answer. Stocks it does not cover are
  shown as not covered, never as matching.

## Market tab and data status

The Market tab shows market conditions for India and the United States (seven plain checks on the main index: price against
its 20 and 50-day averages, the 50 against the 200-day average, the 20-day move, trend strength (ADX), breadth and the
volatility index), index cards with a 30-day line, breadth, sector moves, alerts and world headlines. These describe how
the market is behaving now. They are not forecasts and not calls on any stock. Each block carries a small source tag:
**DELAYED** (free data, up to about 15 minutes late), **DERIVED** (worked out by this app from its own stock list) or
**EXTERNAL** (headlines from outside sources, not checked).

At the bottom is a **Data status** panel. Every refresh checks that prices, news, index data, market conditions, NSE
delivery data, India IPOs and the track record loaded, and says which did not. The index and volatility symbols used for
market conditions are set under `regime` in `config/universe.json`.

## Change the stock list

Edit `config/universe.json`. Each stock is `[symbol, name, market, sector, tags]`. Indian symbols end in
`.NS`. Use the tag `psu` for government-backed companies. Keep the name close to how news writes it, because
headlines must name the company to count.

## Run and test locally

```bash
python -m unittest discover -s tests -t .
node --test tests/ipo.test.js
python -m engine.build --out site --limit 14
python -m http.server 8765 --directory site
```

Then open http://127.0.0.1:8765/. Python 3.10+ and Node 18+ are the only requirements; there are no packages to install.

## How the signals work

* **Short-term (weeks):** trend (20/50-day averages), MACD, RSI, volume and volume pressure. News is not part of it.
* **Smooth scoring:** every part of a score changes gradually with the price (for example 5% above or below the 200-day
  average counts as the full +1 or -1). A stock sitting on an average therefore scores near zero and its reading cannot flip
  between two extremes when the price ticks across the line.
* **Long-term (months to years):** price against the 200-day average, golden/death cross, 6-month return, 52-week position and volatility.
* **Sorting:** click any table header on the Signals page to sort by that column (numbers largest first, text A to Z); click again to reverse. Rows with no value always go last.
* **Chart patterns** (shown on the stock page, not part of the score): fixed rules on the latest completed candles. Candle shapes: doji, hammer,
  shooting star, bullish/bearish engulfing, morning/evening star, three white soldiers/black crows, bullish/bearish harami, inside day.
  Price structure: golden/death cross (last 10 days), MACD cross (last 3 days), 20-day breakout/breakdown, within 3% of the 52-week high or low,
  gap up/down (opened 1% or more past the previous day's range), tight range (20 days inside a range under 6% wide), four days of higher or lower
  highs and lows. Volume: a surge of 2x the 20-day average on a 2%+ move (skipped when volume is not reported). Each is a description of what the
  candles did, tagged bullish, bearish or neutral by its rule, not a forecast. Many stocks have no pattern on a given day, and the page says so.
* Each score runs from -100 to +100: +20 or more is Buy, -20 or less is Sell, and +/-50 is Strong.
* **Backtest:** the same fixed rules are run on up to 5 years of each stock, trading at the next open with a 0.1% cost per trade, and compared with buy-and-hold. The most recent 40% is reported separately. In strong bull markets the rules often lag buy-and-hold, and the app says so.
* **Volatility risk level:** 2 x the 14-day average true range below the price. Prices can gap past it.

Never invest money you cannot afford to lose.
