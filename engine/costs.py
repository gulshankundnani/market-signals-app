"""Trading cost assumptions for the backtest, as fractions of the trade value.

The rates below are ASSUMPTIONS written into the code, not live quotes. Exchange charges, taxes and broker fees change,
and every broker differs: check your own contract note. Override any rate in config/costs.json, for example
{"IN": {"brokerage": 0.0003}, "US": {"slippage": 0.001}}. Each side of a trade (entry, exit) is costed separately because
some charges apply to one side only.

India (NSE cash market, delivery trades, which is what a buy-and-hold-for-weeks rule does):
  brokerage      broker fee as a share of the value (0 at discount brokers for delivery; set yours)
  stt_buy/sell   Securities Transaction Tax, 0.1% on both sides for delivery
  exchange_txn   NSE transaction charge, about 0.003% each side
  sebi           SEBI turnover fee, 0.0001% each side
  stamp_buy      stamp duty, 0.015% on the buy side only
  dp_sell        depository charge on a sale: a flat amount per share line, here spread over an assumed Rs 1,00,000 trade
  gst            18% on brokerage, exchange charge and SEBI fee
  slippage       assumed price concession against you on each side (0.05%)
United States (cash equities):
  brokerage      0 at most retail brokers
  regulatory_sell  placeholder for the small sell-side regulatory fees (SEC and FINRA): set from the current fee schedule
  slippage       assumed concession against you on each side (0.05%; large caps trade with tight spreads)
"""

DEFAULTS = {
    "IN": {"brokerage": 0.0, "stt_buy": 0.001, "stt_sell": 0.001, "exchange_txn": 0.0000297, "sebi": 0.000001,
           "stamp_buy": 0.00015, "dp_sell": 0.00015, "gst": 0.18, "slippage": 0.0005},
    "US": {"brokerage": 0.0, "regulatory_sell": 0.00003, "slippage": 0.0005},
}


def settings(market, overrides=None):
    """The rates for a market with any overrides applied. Unknown markets use the United States model."""
    base = dict(DEFAULTS["IN" if market == "IN" else "US"])
    for key, value in ((overrides or {}).get(market) or {}).items():
        if key in base and isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            base[key] = float(value)
    return base


def sides(market, overrides=None):
    """(entry_cost, exit_cost) as fractions of the trade value."""
    s = settings(market, overrides)
    if market == "IN":
        taxable = s["brokerage"] + s["exchange_txn"] + s["sebi"]
        gst = s["gst"] * taxable
        entry = s["brokerage"] + s["stt_buy"] + s["exchange_txn"] + s["sebi"] + s["stamp_buy"] + gst + s["slippage"]
        exit_ = s["brokerage"] + s["stt_sell"] + s["exchange_txn"] + s["sebi"] + s["dp_sell"] + gst + s["slippage"]
        return entry, exit_
    return s["brokerage"] + s["slippage"], s["brokerage"] + s["regulatory_sell"] + s["slippage"]


def describe(overrides=None):
    """The assumptions in the form published with the data."""
    out = {}
    for market in ("IN", "US"):
        entry, exit_ = sides(market, overrides)
        out[market] = {"entry_pct": round(entry * 100, 4), "exit_pct": round(exit_ * 100, 4), "round_trip_pct": round((entry + exit_) * 100, 4),
                       "rates": settings(market, overrides)}
    return out
