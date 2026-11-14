/* IPO and company-quality scoring. Pure functions, no DOM, so they can be tested in Node.
 * Inputs are plain numbers (percent values as percents, e.g. 18 for 18%). Missing inputs are null.
 * Each component scores -1 (poor) to +1 (strong). The score is the weighted average of the
 * components that have data, scaled to -100..100. */
(function (root) {
  'use strict';

  const WEIGHTS = {
    profitability: 25, growth: 20, leverage: 15, returns: 10, valuation: 20,
    promoterExit: 5, demand: 5, grey: 5,
  };
  const LONG_TERM_KEYS = ['profitability', 'growth', 'leverage', 'returns', 'valuation'];
  const MIN_SHARE = 0.5;

  const num = (x) => (typeof x === 'number' && Number.isFinite(x) ? x : null);
  const pct = (x) => `${x.toFixed(1)}%`;

  function components(input) {
    const margin = num(input.profitMargin);
    const growth = num(input.revenueGrowth);
    const debt = num(input.debtToEquity);
    const roe = num(input.roe);
    const pe = num(input.pe);
    const peerPe = num(input.peerPe);
    const ofs = num(input.ofsShare);
    const qib = num(input.qibSubscription);
    const grey = num(input.gmp);
    const out = {};

    if (margin !== null) {
      const v = margin < 0 ? -1 : margin < 5 ? 0 : margin < 15 ? 0.5 : 1;
      out.profitability = [v, margin < 0 ? `Loss-making (margin ${pct(margin)})` : `Profit margin ${pct(margin)}`];
    }
    if (growth !== null) {
      const v = growth < 0 ? -1 : growth < 5 ? -0.5 : growth < 15 ? 0 : growth < 30 ? 0.5 : 1;
      out.growth = [v, `Revenue growth ${pct(growth)}`];
    }
    if (debt !== null) {
      const v = debt <= 0.5 ? 1 : debt <= 1 ? 0.3 : debt <= 2 ? -0.5 : -1;
      out.leverage = [v, `Debt-to-equity ${debt.toFixed(2)}`];
    }
    if (roe !== null) {
      const v = roe < 0 ? -1 : roe < 6 ? -0.5 : roe < 12 ? 0 : roe < 18 ? 0.5 : 1;
      out.returns = [v, `Return on equity ${pct(roe)}`];
    }
    if (pe !== null && pe > 0 && peerPe !== null && peerPe > 0) {
      const premium = pe / peerPe - 1;
      const v = premium <= -0.15 ? 1 : premium <= 0.1 ? 0.3 : premium <= 0.3 ? -0.3 : -1;
      out.valuation = [v, `P/E ${pe.toFixed(1)} is ${premium >= 0 ? `${pct(premium * 100)} above` : `${pct(-premium * 100)} below`} peers (${peerPe.toFixed(1)})`];
    } else if (pe !== null && pe <= 0) {
      out.valuation = [-0.5, 'No positive earnings, so P/E cannot justify the price'];
    }
    if (ofs !== null) {
      const v = ofs <= 25 ? 0.5 : ofs <= 60 ? 0 : -1;
      out.promoterExit = [v, `${pct(ofs)} of the issue is an offer for sale (existing holders selling)`];
    }
    if (qib !== null) {
      const v = qib >= 20 ? 1 : qib >= 5 ? 0.4 : qib >= 1 ? -0.2 : -1;
      out.demand = [v, `Institutional (QIB) demand ${qib.toFixed(1)}x subscribed`];
    }
    if (grey !== null) {
      const v = grey >= 15 ? 1 : grey >= 5 ? 0.5 : grey > -2 ? 0 : -1;
      out.grey = [v, `Grey-market premium ${pct(grey)} (unofficial and unreliable)`];
    }
    return out;
  }

  function combine(parts, keys) {
    const used = keys.filter((k) => parts[k]);
    const total = used.reduce((s, k) => s + WEIGHTS[k], 0);
    const possible = keys.reduce((s, k) => s + WEIGHTS[k], 0);
    if (total === 0 || total < MIN_SHARE * possible) return null;
    return Math.round((used.reduce((s, k) => s + WEIGHTS[k] * parts[k][0], 0) / total) * 100);
  }

  const verdict = (score) => (score === null ? 'Not enough data' : score >= 30 ? 'Subscribe' : score <= -20 ? 'Avoid' : 'Neutral');
  const holdLabel = (score) => (score === null ? 'N/A' : score >= 30 ? 'Buy' : score <= -20 ? 'Sell' : 'Hold');

  function ipoVerdict(input) {
    const parts = components(input || {});
    const keys = Object.keys(WEIGHTS);
    const score = combine(parts, keys);
    const longScore = combine(parts, LONG_TERM_KEYS);
    const reasons = keys.filter((k) => parts[k]).map((k) => ({
      text: parts[k][1], tone: parts[k][0] > 0.15 ? 'pos' : parts[k][0] < -0.15 ? 'neg' : 'neutral',
    }));
    return {
      score, label: verdict(score), longTerm: { score: longScore, label: holdLabel(longScore) },
      reasons, missing: keys.filter((k) => !parts[k]),
    };
  }

  /* Quality of an already-listed company from Yahoo fundamentals (fractions, not percents). */
  function qualityFromFundamentals(f, peerPe) {
    f = f || {};
    const scale = (x) => (num(x) === null ? null : x * 100);
    return ipoVerdict({
      profitMargin: scale(f.profit_margin), revenueGrowth: scale(f.revenue_growth), roe: scale(f.roe),
      debtToEquity: f.debt_to_equity, pe: f.pe, peerPe,
    });
  }

  function median(values) {
    const xs = values.filter((v) => num(v) !== null && v > 0).sort((a, b) => a - b);
    if (!xs.length) return null;
    const mid = Math.floor(xs.length / 2);
    return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
  }

  /* ---- dates and estimated profit ---- */
  const DAY_MS = 86400000;
  const ALLOTMENT_DAYS = 2;   // usual: allotment about 2 working days after close
  const LISTING_DAYS = 3;     // SEBI timeline: listing 3 working days after close

  function parseIso(text) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text || '');
    if (!m) return null;
    const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    return d.getUTCFullYear() === +m[1] && d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3] ? d : null;
  }
  const toIso = (d) => d.toISOString().slice(0, 10);

  /* Adds working days (Monday to Friday). Exchange holidays are not known, so this is approximate. */
  function addWorkingDays(iso, n) {
    let d = parseIso(iso);
    if (!d) return null;
    let left = n;
    while (left > 0) {
      d = new Date(d.getTime() + DAY_MS);
      const weekday = d.getUTCDay();
      if (weekday !== 0 && weekday !== 6) left -= 1;
    }
    return toIso(d);
  }

  const dayDiff = (a, b) => Math.round((parseIso(b) - parseIso(a)) / DAY_MS);
  const days = (n) => `${n} day${n === 1 ? '' : 's'}`;

  /* entry: {open, close, allotment?, listing?} as YYYY-MM-DD. today: YYYY-MM-DD. */
  function timeline(entry, today) {
    const e = entry || {};
    if (!parseIso(e.open) || !parseIso(e.close)) return { valid: false, error: 'enter the open and close dates.' };
    if (e.close < e.open) return { valid: false, error: 'the close date is before the open date.' };
    const allotmentGiven = parseIso(e.allotment) !== null;
    const listingGiven = parseIso(e.listing) !== null;
    const allotment = allotmentGiven ? e.allotment : addWorkingDays(e.close, ALLOTMENT_DAYS);
    const listing = listingGiven ? e.listing : addWorkingDays(e.close, LISTING_DAYS);
    if (allotment < e.close) return { valid: false, error: 'the allotment date is before the close date.' };
    if (listing < allotment) return { valid: false, error: 'the listing date is before the allotment date.' };

    let phase, status;
    if (today < e.open) { phase = 'upcoming'; status = `Opens in ${days(dayDiff(today, e.open))}`; }
    else if (today <= e.close) { phase = 'open'; status = today === e.close ? 'Open, closes today' : `Open, closes in ${days(dayDiff(today, e.close))}`; }
    else if (today < allotment) { phase = 'closed'; status = `Closed, allotment on ${allotment}`; }
    else if (today === allotment) { phase = 'closed'; status = 'Allotment today'; }
    else if (today < listing) { phase = 'closed'; status = `Allotted, lists on ${listing}`; }
    else if (today === listing) { phase = 'listed'; status = 'Lists today'; }
    else { phase = 'listed'; status = 'Listed'; }
    return {
      valid: true, open: e.open, close: e.close, allotment, listing, phase, status,
      allotmentEstimated: !allotmentGiven, listingEstimated: !listingGiven,
    };
  }

  /* Estimated listing price and gain from the grey-market premium (GMP, rupees or dollars per share). */
  function estimate(entry) {
    const e = entry || {};
    const high = num(e.priceHigh), lot = num(e.lot), gmp = num(e.gmp);
    const out = { applyAmount: null, listingPrice: null, gainPerShare: null, gainPct: null, gainPerLot: null };
    if (high !== null && high > 0 && lot !== null && lot > 0) out.applyAmount = high * lot;
    if (high !== null && high > 0 && gmp !== null) {
      out.listingPrice = high + gmp;
      out.gainPerShare = gmp;
      out.gainPct = (gmp / high) * 100;
      if (lot !== null && lot > 0) out.gainPerLot = gmp * lot;
    }
    return out;
  }

  /* Market demand for an IPO from the grey-market premium and total subscription.
   This measures hype, not the quality of the business. */
  function demand(input) {
    const e = input || {};
    const gmpPct = num(e.gmpPct), subscription = num(e.subscription);
    const parts = [];
    if (gmpPct !== null) {
      const v = gmpPct >= 15 ? 1 : gmpPct >= 5 ? 0.5 : gmpPct > -2 ? 0 : -1;
      parts.push([v, `Grey-market premium ${pct(gmpPct)} (unofficial)`]);
    }
    if (subscription !== null && subscription >= 0) {
      const v = subscription >= 50 ? 1 : subscription >= 10 ? 0.6 : subscription >= 2 ? 0.2 : subscription >= 1 ? -0.2 : -1;
      parts.push([v, `Subscribed ${subscription.toFixed(1)}x overall`]);
    }
    if (!parts.length) return { score: null, label: 'No demand data', reasons: [] };
    const score = Math.round((parts.reduce((s, p) => s + p[0], 0) / parts.length) * 100);
    const label = score >= 40 ? 'Strong demand' : score >= 10 ? 'Moderate demand' : score > -20 ? 'Weak demand' : 'Poor demand';
    return { score, label, reasons: parts.map((p) => ({ text: p[1], tone: p[0] > 0.15 ? 'pos' : p[0] < -0.15 ? 'neg' : 'neutral' })) };
  }

  root.IPO = { ipoVerdict, qualityFromFundamentals, median, WEIGHTS, addWorkingDays, timeline, estimate, demand };
})(typeof window !== 'undefined' ? window : module.exports);
