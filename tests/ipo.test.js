const test = require('node:test');
const assert = require('node:assert/strict');
const { IPO } = require('../site/ipo.js');

const strong = { profitMargin: 20, revenueGrowth: 35, debtToEquity: 0.2, roe: 22, pe: 20, peerPe: 28, ofsShare: 10, qibSubscription: 45, gmp: 20 };
const weak = { profitMargin: -8, revenueGrowth: -3, debtToEquity: 3.5, roe: -5, pe: 90, peerPe: 25, ofsShare: 90, qibSubscription: 0.6, gmp: -5 };

test('strong company is Subscribe with a high score', () => {
  const v = IPO.ipoVerdict(strong);
  assert.equal(v.label, 'Subscribe');
  assert.ok(v.score >= 60, `score ${v.score}`);
  assert.equal(v.longTerm.label, 'Buy');
  assert.deepEqual(v.missing, []);
});

test('weak company is Avoid with a low score', () => {
  const v = IPO.ipoVerdict(weak);
  assert.equal(v.label, 'Avoid');
  assert.ok(v.score <= -60, `score ${v.score}`);
  assert.equal(v.longTerm.label, 'Sell');
});

test('score is always within -100..100', () => {
  for (const input of [strong, weak, {}, { profitMargin: 1e9, revenueGrowth: 1e9 }]) {
    const { score } = IPO.ipoVerdict(input);
    assert.ok(score === null || (score >= -100 && score <= 100));
  }
});

test('too little data gives no verdict instead of a guess', () => {
  const v = IPO.ipoVerdict({ gmp: 30 });
  assert.equal(v.score, null);
  assert.equal(v.label, 'Not enough data');
  assert.equal(IPO.ipoVerdict({}).label, 'Not enough data');
  assert.equal(IPO.ipoVerdict(null).label, 'Not enough data');
});

test('bad inputs are treated as missing', () => {
  const v = IPO.ipoVerdict({ ...strong, roe: 'abc', pe: NaN, debtToEquity: Infinity, gmp: undefined });
  assert.ok(v.missing.includes('returns'));
  assert.ok(v.missing.includes('leverage'));
  assert.ok(v.missing.includes('grey'));
  assert.equal(typeof v.score, 'number');
});

test('valuation: premium to peers lowers the score', () => {
  const cheap = IPO.ipoVerdict({ ...strong, pe: 15 }).score;
  const dear = IPO.ipoVerdict({ ...strong, pe: 60 }).score;
  assert.ok(cheap > dear);
});

test('loss-making company: negative earnings flagged on valuation', () => {
  const v = IPO.ipoVerdict({ profitMargin: -5, revenueGrowth: 40, debtToEquity: 0.3, roe: -2, pe: -10, peerPe: 25 });
  assert.ok(v.reasons.some((r) => /No positive earnings/.test(r.text)));
});

test('long-term score ignores demand, promoter exit and grey market', () => {
  const base = { profitMargin: 20, revenueGrowth: 35, debtToEquity: 0.2, roe: 22, pe: 20, peerPe: 28 };
  const a = IPO.ipoVerdict({ ...base, qibSubscription: 50, gmp: 30, ofsShare: 5 });
  const b = IPO.ipoVerdict({ ...base, qibSubscription: 0.2, gmp: -20, ofsShare: 95 });
  assert.equal(a.longTerm.score, b.longTerm.score);
  assert.ok(a.score > b.score);
});

test('reasons carry tones', () => {
  const v = IPO.ipoVerdict(strong);
  assert.ok(v.reasons.length >= 6);
  assert.ok(v.reasons.every((r) => ['pos', 'neg', 'neutral'].includes(r.tone) && r.text));
});

test('quality from Yahoo fundamentals converts fractions to percents', () => {
  const v = IPO.qualityFromFundamentals({ profit_margin: 0.2, revenue_growth: 0.3, roe: 0.25, debt_to_equity: 0.1, pe: 20 }, 25);
  assert.equal(v.longTerm.label, 'Buy');
  assert.ok(v.reasons.some((r) => /Profit margin 20\.0%/.test(r.text)));
  assert.equal(IPO.qualityFromFundamentals(null, null).label, 'Not enough data');
  assert.equal(IPO.qualityFromFundamentals({}, 25).label, 'Not enough data');
});

test('working days skip weekends', () => {
  assert.equal(IPO.addWorkingDays('2026-11-13', 1), '2026-11-16'); // Friday -> Monday
  assert.equal(IPO.addWorkingDays('2026-11-12', 3), '2026-11-17'); // Thursday + 3 -> Tuesday
  assert.equal(IPO.addWorkingDays('2026-11-14', 1), '2026-11-16'); // Saturday -> Monday
  assert.equal(IPO.addWorkingDays('2026-11-16', 0), '2026-11-16');
  assert.equal(IPO.addWorkingDays('not a date', 2), null);
  assert.equal(IPO.addWorkingDays('2026-02-30', 2), null);
});

test('timeline estimates allotment (T+2) and listing (T+3) from the close date', () => {
  const t = IPO.timeline({ open: '2026-11-10', close: '2026-11-12' }, '2026-11-01');
  assert.equal(t.valid, true);
  assert.equal(t.allotment, '2026-11-16');
  assert.equal(t.listing, '2026-11-17');
  assert.equal(t.allotmentEstimated, true);
  assert.equal(t.listingEstimated, true);
});

test('timeline uses dates the user gave instead of estimates', () => {
  const t = IPO.timeline({ open: '2026-11-10', close: '2026-11-12', allotment: '2026-11-13', listing: '2026-11-18' }, '2026-11-01');
  assert.equal(t.allotment, '2026-11-13');
  assert.equal(t.listing, '2026-11-18');
  assert.equal(t.allotmentEstimated, false);
  assert.equal(t.listingEstimated, false);
});

test('timeline status follows the calendar', () => {
  const e = { open: '2026-11-10', close: '2026-11-12' }; // allotment 11-16, listing 11-17
  const at = (today) => IPO.timeline(e, today);
  assert.equal(at('2026-11-07').status, 'Opens in 3 days');
  assert.equal(at('2026-11-09').status, 'Opens in 1 day');
  assert.equal(at('2026-11-10').status, 'Open, closes in 2 days');
  assert.equal(at('2026-11-12').status, 'Open, closes today');
  assert.equal(at('2026-11-13').status, 'Closed, allotment on 2026-11-16');
  assert.equal(at('2026-11-16').status, 'Allotment today');
  assert.equal(at('2026-11-17').status, 'Lists today');
  assert.equal(at('2026-11-18').status, 'Listed');
  assert.equal(at('2026-11-10').phase, 'open');
  assert.equal(at('2026-11-18').phase, 'listed');
});

test('timeline rejects missing or impossible dates', () => {
  assert.equal(IPO.timeline({}, '2026-11-01').valid, false);
  assert.equal(IPO.timeline({ open: '2026-11-10' }, '2026-11-01').valid, false);
  assert.equal(IPO.timeline(null, '2026-11-01').valid, false);
  assert.equal(IPO.timeline({ open: '2026-11-12', close: '2026-11-10' }, '2026-11-01').valid, false);
  assert.equal(IPO.timeline({ open: '2026-11-10', close: '2026-11-12', allotment: '2026-11-11' }, '2026-11-01').valid, false);
  assert.equal(IPO.timeline({ open: '2026-11-10', close: '2026-11-12', allotment: '2026-11-16', listing: '2026-11-13' }, '2026-11-01').valid, false);
});

test('estimate: profit from the grey-market premium', () => {
  const e = IPO.estimate({ priceHigh: 100, lot: 150, gmp: 25 });
  assert.equal(e.applyAmount, 15000);
  assert.equal(e.listingPrice, 125);
  assert.equal(e.gainPerShare, 25);
  assert.equal(e.gainPct, 25);
  assert.equal(e.gainPerLot, 3750);
});

test('estimate: a negative premium is a loss', () => {
  const e = IPO.estimate({ priceHigh: 200, lot: 75, gmp: -10 });
  assert.equal(e.listingPrice, 190);
  assert.equal(e.gainPct, -5);
  assert.equal(e.gainPerLot, -750);
});

test('estimate: missing inputs give nulls, not zeros', () => {
  const none = IPO.estimate({});
  assert.deepEqual(Object.values(none), [null, null, null, null, null]);
  const noLot = IPO.estimate({ priceHigh: 100, gmp: 10 });
  assert.equal(noLot.gainPerLot, null);
  assert.equal(noLot.applyAmount, null);
  assert.equal(noLot.listingPrice, 110);
  assert.equal(IPO.estimate({ priceHigh: 0, lot: 10, gmp: 5 }).listingPrice, null);
  assert.equal(IPO.estimate({ priceHigh: 'abc', lot: 10, gmp: 5 }).listingPrice, null);
  assert.equal(IPO.estimate({ priceHigh: 100, lot: 10, gmp: 0 }).gainPerLot, 0);
});

test('demand: strong premium and heavy subscription', () => {
  const d = IPO.demand({ gmpPct: 25, subscription: 80 });
  assert.equal(d.label, 'Strong demand');
  assert.equal(d.score, 100);
  assert.equal(d.reasons.length, 2);
});

test('demand: discount and weak subscription is poor', () => {
  const d = IPO.demand({ gmpPct: -6, subscription: 0.5 });
  assert.equal(d.label, 'Poor demand');
  assert.equal(d.score, -100);
});

test('demand: mixed signals land in the middle', () => {
  const d = IPO.demand({ gmpPct: 0, subscription: 1.5 });
  assert.equal(d.label, 'Weak demand');
  assert.equal(IPO.demand({ gmpPct: 0, subscription: 3 }).label, 'Moderate demand');
  assert.equal(IPO.demand({ gmpPct: 8, subscription: 12 }).label, 'Strong demand');
});

test('demand: one input still works, none gives no verdict', () => {
  assert.equal(IPO.demand({ gmpPct: 6 }).reasons.length, 1);
  assert.equal(IPO.demand({ subscription: 5 }).label, 'Moderate demand');
  assert.equal(IPO.demand({ subscription: 30 }).label, 'Strong demand');
  const none = IPO.demand({ gmpPct: null, subscription: undefined });
  assert.equal(none.score, null);
  assert.equal(none.label, 'No demand data');
  assert.equal(IPO.demand(null).label, 'No demand data');
  assert.equal(IPO.demand({ gmpPct: 'x', subscription: NaN }).label, 'No demand data');
  assert.equal(IPO.demand({ subscription: -3 }).label, 'No demand data');
});

test('median ignores junk and handles empties', () => {
  assert.equal(IPO.median([10, 30, 20]), 20);
  assert.equal(IPO.median([10, 20, 30, 40]), 25);
  assert.equal(IPO.median([null, undefined, -5, 0, NaN, 12]), 12);
  assert.equal(IPO.median([]), null);
});
