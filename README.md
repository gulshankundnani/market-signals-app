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
| Dividends | Actual payments from Yahoo | Frequency, amount, 12-month and recurring yield, specials, overdue payments |
| Fundamentals and peers | Yahoo quoteSummary | Refreshed once a day. Shows "unavailable" when missing |
| New listings | NSE equity list and Nasdaq/NYSE symbol list | Added to the "New listings" list on their own. Signals start after about 60 trading days |
| India IPOs | Investorgain's public IPO table (robots.txt allows it), read at most once an hour | Open, close, allotment and listing dates, price band, lot size, subscription, grey-market premium (GMP), estimated listing price and profit or loss per lot. Past IPOs show the actual listing price and gain |
| GMP cross-check | Google News headlines that name the IPO and quote a premium | Flags "news agrees" or "news differs". GMP is an unofficial rumour and can be wrong |
| US IPO calendar | Nasdaq | Date, price range, size |
| Market conditions, breadth, sectors | Computed from the index (Nifty 50, S&P 500), its volatility index and the stocks in this app's list | Seven simple checks worth +1, 0 or -1 each, advancers and decliners, share of stocks above their 20/50/200-day averages, sector moves over 1, 5 and 20 days. Breadth and sectors cover this app's list, not the whole exchange |
| Lists | Computed daily | Government-backed, high dividend, low debt, low volatility, momentum, oversold, near 52-week high, delivery buying, sectors |

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
* Each score runs from -100 to +100: +20 or more is Buy, -20 or less is Sell, and +/-50 is Strong.
* **Backtest:** the same fixed rules are run on up to 5 years of each stock, trading at the next open with a 0.1% cost per trade, and compared with buy-and-hold. The most recent 40% is reported separately. In strong bull markets the rules often lag buy-and-hold, and the app says so.
* **Volatility risk level:** 2 x the 14-day average true range below the price. Prices can gap past it.

Never invest money you cannot afford to lose.
