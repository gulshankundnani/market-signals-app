# Alerts audit, 17 November 2026

Question: do the Telegram alerts favour some names, and why did a block deal in Groww before the open not alert?

## What the channel actually contained
The last 20 messages of t.me/s/marketsignalsapp (16 Nov 20:49 UTC to 17 Nov 04:10 UTC, an overnight stretch):

* 105 headline cards and 3 reading changes: **97% of the content was news**, not readings.
* 43 distinct stocks. The top five (NVDA 11, TSLA 7, BRK-B 5, JPM 5, INTC 4) took **30%** of the cards; NVDA alone 10%.
* **US stocks were 80%** of the cards although they are 34% of the tracked list (41 of 122).
* The site's alert list held 30 entries: 25 news, 5 reading changes, 0 block deals.

## Causes
1. **Selection by news volume.** Each run takes the newest unseen headline of every stock, then the six newest overall. Stocks with
   constant English-language coverage (NVDA, TSLA, JPM) produce a fresh headline almost every ten minutes and win every time. The same
   story from several outlets counted as separate headlines. Overnight (IST) only the US news cycle is awake.
2. **No block-deal alerts existed.** Deals were read only from the NSE's end-of-day `block.csv`, which is empty until after the close
   (at 09:50 IST on 17 Nov it said "NO RECORDS" while the live feed already listed Groww). Even when it filled, deals were shown on a
   stock's page and never raised an alert.
3. **Groww is not tracked.** The list is 122 fixed stocks (81 India, 41 US) plus listings of the last 90 days; Groww (listed Nov 2025)
   is in neither. Any alert filtered by the tracked list would have skipped it.

## Changes
* A stock gets at most **2 headline alerts per 24 hours**; a headline that repeats a story already sent for that stock is skipped; and
  when more stocks have news than fit, the stocks alerted **least** in the last 24 hours go first (newest first among equals).
* **Block deals** are read from the NSE's live block-deal feed (one request per run, honest client, a refusal pauses it for 6 hours) and
  announced once each, for **every NSE stock, tracked or not**, with window, quantity, price, value and the move against the previous
  close. Buyer and seller are published after the close and shown then. Nothing is judged from the deal.
* The status card shows the state of the block-deal feed.

## What is not fixed
* The tracked list is still 122 stocks. Many NSE stocks have no readings or news alerts. Widening it (for example to a larger index)
  multiplies the Yahoo, news and build load and is a separate decision.
* US stocks will still dominate headline alerts while only US news is flowing (Indian night hours): the cap and rotation limit it per
  stock but do not invent Indian news.
* Bulk deals (as opposed to block deals) are published with client names only after the close and are still shown on stock pages, not
  alerted.
