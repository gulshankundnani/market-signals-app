# Strategy report (generated 2026-11-16T11:12:59+00:00)

Fixed rules, never tuned, tested against buy-and-hold on up to 5 years of Yahoo daily prices, dividends included for both, assumed trading costs (see `engine/costs.py`). Today's stock list: survivorship bias is NOT removed (see README). Past results do not predict future results.

## NSE (India): weeks view rule (81 stocks)

* Beat buy-and-hold on total return (with dividends): **12 of 81** (price only: 14). Median total return: rule 14.5%, buy-and-hold 90.2%.
* Median time invested 48%, median 45 trades, median holding 12 days.

| Measure | Rule (median) | Buy-and-hold (median) | Rule better in |
|---|---|---|---|
| Maximum drawdown | -35.5% | -37.6% | 43 of 81 |
| Volatility (annual) | 18.5% | 27.5% | 81 of 81 |
| Downside deviation (annual) | 12.9% | 18.6% | 81 of 81 |
| Sharpe | 0.24 | 0.64 | 10 of 81 |
| Sortino | 0.38 | 0.93 | 11 of 81 |
| Calmar | 0.09 | 0.41 | 17 of 81 |

| Regime (index) | Stock-rule pairs | Median days in regime | Rule: average daily return x 252 | Buy-and-hold: same | Rule better in | Rule invested |
|---|---|---|---|---|---|---|
| bull | 81 | 691 | 5.5% | 22.3% | 10 of 81 | 53% |
| bear | 81 | 164 | 8.5% | 25.2% | 12 of 81 | 37% |
| sideways | 81 | 159 | -8.9% | 1.4% | 25 of 81 | 39% |

## NSE (India): months-to-years view rule (81 stocks)

* Beat buy-and-hold on total return (with dividends): **14 of 81** (price only: 17). Median total return: rule 38.8%, buy-and-hold 92.7%.
* Median time invested 61%, median 6 trades, median holding 111 days.

| Measure | Rule (median) | Buy-and-hold (median) | Rule better in |
|---|---|---|---|
| Maximum drawdown | -29.8% | -35.8% | 57 of 81 |
| Volatility (annual) | 20.5% | 26.1% | 81 of 81 |
| Downside deviation (annual) | 14.3% | 18.1% | 81 of 81 |
| Sharpe | 0.46 | 0.73 | 12 of 81 |
| Sortino | 0.67 | 1.07 | 13 of 81 |
| Calmar | 0.26 | 0.54 | 21 of 81 |

| Regime (index) | Stock-rule pairs | Median days in regime | Rule: average daily return x 252 | Buy-and-hold: same | Rule better in | Rule invested |
|---|---|---|---|---|---|---|
| bull | 81 | 691 | 17.6% | 22.3% | 28 of 81 | 78% |
| bear | 81 | 164 | 0.0% | 25.2% | 8 of 81 | 33% |
| sideways | 81 | 159 | -2.2% | 1.4% | 30 of 81 | 32% |

## United States: weeks view rule (41 stocks)

* Beat buy-and-hold on total return (with dividends): **7 of 41** (price only: 8). Median total return: rule 21.9%, buy-and-hold 91.7%.
* Median time invested 49%, median 44 trades, median holding 13 days.

| Measure | Rule (median) | Buy-and-hold (median) | Rule better in |
|---|---|---|---|
| Maximum drawdown | -29.8% | -37.1% | 32 of 41 |
| Volatility (annual) | 18.3% | 26.8% | 41 of 41 |
| Downside deviation (annual) | 12.5% | 18.9% | 41 of 41 |
| Sharpe | 0.32 | 0.59 | 8 of 41 |
| Sortino | 0.48 | 0.88 | 7 of 41 |
| Calmar | 0.14 | 0.39 | 11 of 41 |

| Regime (index) | Stock-rule pairs | Median days in regime | Rule: average daily return x 252 | Buy-and-hold: same | Rule better in | Rule invested |
|---|---|---|---|---|---|---|
| bull | 41 | 841 | 8.7% | 19.3% | 3 of 41 | 53% |
| bear | 41 | 92 | 37.7% | 91.2% | 2 of 41 | 53% |
| sideways | 41 | 101 | -10.0% | -14.9% | 17 of 41 | 41% |

## United States: months-to-years view rule (41 stocks)

* Beat buy-and-hold on total return (with dividends): **6 of 41** (price only: 6). Median total return: rule 39.4%, buy-and-hold 103.5%.
* Median time invested 63%, median 6 trades, median holding 97 days.

| Measure | Rule (median) | Buy-and-hold (median) | Rule better in |
|---|---|---|---|
| Maximum drawdown | -28.3% | -32.6% | 28 of 41 |
| Volatility (annual) | 19.3% | 26.0% | 41 of 41 |
| Downside deviation (annual) | 14.3% | 18.0% | 41 of 41 |
| Sharpe | 0.50 | 0.77 | 8 of 41 |
| Sortino | 0.71 | 1.17 | 9 of 41 |
| Calmar | 0.27 | 0.56 | 11 of 41 |

| Regime (index) | Stock-rule pairs | Median days in regime | Rule: average daily return x 252 | Buy-and-hold: same | Rule better in | Rule invested |
|---|---|---|---|---|---|---|
| bull | 41 | 841 | 10.7% | 19.3% | 6 of 41 | 68% |
| bear | 41 | 92 | 15.9% | 91.2% | 2 of 41 | 32% |
| sideways | 41 | 101 | -4.1% | -14.9% | 17 of 41 | 53% |
