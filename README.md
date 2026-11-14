# Stock Signals

A free, installable web app that shows Buy / Hold / Sell signals for Indian (NSE) and US stocks,
with news, buying and selling pressure, dividends, risk, IPOs and a backtest for every stock.
It runs on GitHub only: GitHub Actions refreshes the data every 15 minutes, GitHub Pages hosts the site.

It is not investment advice. Signals come from fixed rules on free, delayed data and can be wrong.

## What updates by itself

| Feature | Source | Notes |
|---|---|---|
| Prices, signals, patterns, risk, backtest | Yahoo Finance (free) | Prices can be up to 15 minutes late |
| News and tone | Google News | Only headlines that name the company count toward a stock's news tone or alerts |
| Buying / selling pressure | Volume (Chaikin Money Flow, up-volume share) | A proxy. The exchange order book is not published for free |
| NSE delivery % and bulk/block deals | NSE end-of-day files | India only, updated after each trading day |
| Dividends | Actual payments from Yahoo | Frequency, amount, 12-month and recurring yield, specials, overdue payments |
| Fundamentals and peers | Yahoo quoteSummary | Refreshed once a day. Shows "unavailable" when missing |
| New listings | NSE equity list and Nasdaq/NYSE symbol list | Added to the "New listings" list on their own. Signals start after about 60 trading days |
| India IPOs | Investorgain's public IPO table (robots.txt allows it), read at most once an hour | Open, close, allotment and listing dates, price band, lot size, subscription, grey-market premium (GMP), estimated listing price and profit or loss per lot. Past IPOs show the actual listing price and gain |
| GMP cross-check | Google News headlines that name the IPO and quote a premium | Flags "news agrees" or "news differs". GMP is an unofficial rumour and can be wrong |
| US IPO calendar | Nasdaq | Date, price range, size |
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
* Alerts when the app is closed need a one-time Telegram setup (below).

## Set it up (about 10 minutes)

1. Create a free GitHub account if you do not have one, then create a new **public** repository
   (public repositories get unlimited free Actions minutes; the site and its data will be public).
2. Upload everything in this folder to the repository's `main` branch.
3. In the repository: **Settings > Pages > Build and deployment > Source: GitHub Actions**.
4. Open the **Actions** tab, choose **Update signals**, and press **Run workflow**. The first run takes a few minutes.
5. Your site is at `https://<your-username>.github.io/<repository-name>/`.
   On your phone open it in Chrome or Safari and choose **Add to Home screen** to install it like an app.

The workflow then runs by itself every 15 minutes on weekdays. If a test fails or too many data
sources fail, the site is not replaced and the previous version stays online.

## Telegram alerts (optional, works when the app is closed)

1. In Telegram, message `@BotFather`, send `/newbot` and follow the steps. Keep the token private.
2. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a
   browser (with your token) and note the number after `"chat":{"id":`.
3. In the repository: **Settings > Secrets and variables > Actions > New repository secret**. Add
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
4. Edit `config/alerts.json` and list the stocks to watch, for example
   `{"symbols": ["TCS.NS", "SBIN.NS", "AAPL"]}`.

You are then messaged when a watched stock's short- or long-term signal moves between buy, hold and sell,
and when breaking news that names the company appears. Delivery depends on the 15-minute schedule
(GitHub may delay scheduled runs). The first run only records the current state and sends nothing.

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

* **Short-term (weeks):** trend (20/50-day averages), MACD, RSI, volume, volume pressure and news tone.
* **Long-term (months to years):** price against the 200-day average, golden/death cross, 6-month return, 52-week position and volatility.
* Each score runs from -100 to +100: +20 or more is Buy, -20 or less is Sell, and +/-50 is Strong.
* **Backtest:** the same fixed rules are run on up to 5 years of each stock, trading at the next open with a 0.1% cost per trade, and compared with buy-and-hold. The most recent 40% is reported separately. In strong bull markets the rules often lag buy-and-hold, and the app says so.
* **Stop-loss:** 2 x the 14-day average true range below the price. Prices can gap past it.

Never invest money you cannot afford to lose.
