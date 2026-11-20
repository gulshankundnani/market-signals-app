'use strict';
(function () {
  const IPO = window.IPO;
  const REFRESH_MS = 5 * 60 * 1000;
  const CURRENCY = { INR: '₹', USD: '$' };
  const FREQ_CODE = { Monthly: 'M', Quarterly: 'Q', 'Half-yearly': 'H', Yearly: 'Y' };
  const FIXED_LISTS = ['Government-backed', 'High dividend', 'Low debt', 'Low volatility', 'Momentum leaders',
    'Strong long-term trend', 'Strong delivery buying', 'Oversold', 'Near 52-week high', 'Near 52-week low', 'New listings', 'US mega cap'];

  /* ---------- helpers ---------- */
  const $ = (sel, el = document) => el.querySelector(sel);
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const isNum = (x) => typeof x === 'number' && Number.isFinite(x);

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key.startsWith('on')) el.addEventListener(key.slice(2).toLowerCase(), value);
      else el.setAttribute(key, value === true ? '' : value);
    }
    append(el, kids);
    return el;
  }
  function append(el, kids) {
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
  }
  function svg(tag, attrs, ...kids) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs || {})) el.setAttribute(key, value);
    append(el, kids);
    return el;
  }

  const store = {
    get(key, fallback) {
      try { const raw = localStorage.getItem('ss.' + key); return raw === null ? fallback : JSON.parse(raw); } catch (e) { return fallback; }
    },
    set(key, value) { try { localStorage.setItem('ss.' + key, JSON.stringify(value)); } catch (e) { /* storage unavailable */ } },
  };

  const money = (v, cur) => (isNum(v) ? (CURRENCY[cur] || '') + v.toLocaleString(undefined, { maximumFractionDigits: v < 10 ? 3 : 2 }) : '–');
  const signed = (v, digits = 2, suffix = '%') => (isNum(v) ? `${v > 0 ? '+' : ''}${v.toFixed(digits)}${suffix}` : '–');
  const percent = (v, digits = 1) => (isNum(v) ? `${(v * 100).toFixed(digits)}%` : '–');
  const number = (v, digits = 2) => (isNum(v) ? v.toFixed(digits) : '–');
  const big = (v, cur) => {
    if (!isNum(v)) return '–';
    const units = [[1e12, 'T'], [1e9, 'B'], [1e6, 'M']];
    for (const [size, name] of units) if (Math.abs(v) >= size) return (CURRENCY[cur] || '') + (v / size).toFixed(1) + name;
    return (CURRENCY[cur] || '') + v.toFixed(0);
  };
  function ago(minutes) {
    if (!isNum(minutes)) return 'date unknown';
    if (minutes < 60) return `${minutes} min ago`;
    if (minutes < 1440) return `${Math.floor(minutes / 60)} h ago`;
    return `${Math.floor(minutes / 1440)} d ago`;
  }
  const safeUrl = (url) => (typeof url === 'string' && url.startsWith('https://') ? url : null);
  const side = (label) => (label === 'Buy' || label === 'Strong Buy' ? 'buy' : label === 'Sell' || label === 'Strong Sell' ? 'sell' : 'hold');

  /* What each label is shown as. The stored labels stay Buy/Hold/Sell so filters and records keep working. */
  const LABEL_TEXT = { 'Strong Buy': 'Strongly aligned bullish', Buy: 'Aligned bullish', Hold: 'Mixed', Sell: 'Aligned bearish', 'Strong Sell': 'Strongly aligned bearish',
    Subscribe: 'Strong readings', Avoid: 'Weak readings', Neutral: 'Mixed' };
  const labelText = (label) => LABEL_TEXT[label] || label;

  function badge(sig) {
    const label = (sig && sig.label) || 'N/A';
    const cls = label.includes('Buy') ? 'buy' : label.includes('Sell') ? 'sell' : label === 'Hold' ? 'hold' : 'na';
    const shortText = { 'Strong Buy': 'Strong bullish', Buy: 'Bullish', Sell: 'Bearish', 'Strong Sell': 'Strong bearish' }[label] || labelText(label);
    return h('span', { class: `badge ${cls}`, title: labelText(label) }, h('span', { class: 'bfull' }, labelText(label)), h('span', { class: 'bshort' }, shortText),
      isNum(sig && sig.score) ? h('small', null, ` ${sig.score > 0 ? '+' : ''}${sig.score}`) : null);
  }

  /* ---------- state ---------- */
  const S = {
    data: null,
    error: null,
    tab: 'signals',
    others: [], shards: new Map(), shown: {}, catalog: 'loading', catalogInfo: null, sig: '',
    filters: Object.assign({ market: 'ALL', horizon: 'short', signal: 'ALL', list: 'ALL', sector: 'ALL', tech: {}, sort: 'score', view: 'overview' }, store.get('filters', {})),
    q: '',
    watch: new Set(store.get('watch', [])),
    lists: store.get('lists', {}),
    profile: Object.assign({ goal: 'balanced', horizon: 'long', risk: 'medium', market: 'ALL' }, store.get('profile', {})),
    calc: Object.assign({ capital: 100000, risk: 1 }, store.get('calc', {})),
    notify: store.get('notify', false),
    ipoName: '',
  };
  const saveFilters = () => store.set('filters', S.filters);
  const saveWatch = () => store.set('watch', [...S.watch]);

  /* ---------- toasts and notifications ---------- */
  function toast(text) {
    const el = h('div', { class: 'toast' }, text);
    $('#toasts').append(el);
    setTimeout(() => el.remove(), 9000);
  }
  function announce(text) {
    toast(text);
    try {
      if (S.notify && 'Notification' in window && Notification.permission === 'granted') new Notification('Stock Signals', { body: text });
    } catch (e) { /* notifications unavailable */ }
  }
  function checkChanges() {
    const previous = store.get('lastSeen', null);
    const current = {};
    const messages = [];
    for (const s of S.data.stocks) {
      if (!S.watch.has(s.symbol)) continue;
      current[s.symbol] = { short: s.short.label, long: s.long.label, news: s.news.fresh.map((a) => a.link || a.title) };
      const before = previous && previous[s.symbol];
      if (!before) continue;
      for (const [key, name] of [['short', 'short-term'], ['long', 'long-term']]) {
        const was = before[key];
        const now = current[s.symbol][key];
        if (was && was !== 'N/A' && now !== 'N/A' && side(was) !== side(now)) messages.push(`${s.name}: ${name} reading ${labelText(was)} → ${labelText(now)}`);
      }
      for (const a of s.news.fresh) {
        if (!before.news.includes(a.link || a.title)) messages.push(`News, ${s.name}: ${a.title}`);
      }
    }
    store.set('lastSeen', current);
    messages.slice(0, 6).forEach(announce);
  }

  /* ---------- data loading ---------- */
  async function load(manual) {
    // The start-up load, a "page became visible" event and the timer can fire together. Only one download runs at a time, and an automatic
    // reload is skipped if the data is under a minute old, so the same 2 MB is never fetched twice in a row.
    if (S.loading || (!manual && S.data && Date.now() - (S.loadedAt || 0) < 60000)) return;
    S.loading = true;
    const button = $('#refresh');
    button.disabled = true;
    const listPromise = fetch('index.json', { cache: 'no-store' }).catch(() => null);      // the other stocks, downloaded at the same time
    try {
      const response = await fetch('data.json', { cache: 'no-store' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      if (!data || !Array.isArray(data.stocks)) throw new Error('unexpected data');
      S.data = data;
      S.loadedAt = Date.now();
      S.shards.clear();                 // full records are per run, so none from an earlier run is reused
      S.error = null;
      checkChanges();
      if (manual) toast('Data refreshed');
    } catch (err) {
      S.error = S.data ? 'Could not refresh. Showing the data already loaded.' : 'Could not load data. Check your connection and try again.';
    } finally {
      button.disabled = false;
      S.loading = false;
    }
    updateHeader();
    render();
    openFromHash();
    loadCatalog(listPromise);
  }

  /* Every other listed stock: one small row each (index.json), loaded after the dashboard so it never delays it. A stock's full analysis
     is a separate file, fetched only when the stock is opened. */
  const slimStock = (r) => ({ ...r, lists: r.lists || [], tags: [], news: { fresh: [] }, slim: true,
    short: r.short || { label: null, score: null }, long: r.long || { label: null, score: null },
    dividend_yield: r.dividend_yield,
    fundamentals: { market_cap: r.cap, pe: r.pe, forward_pe: r.forward_pe, pb: r.pb, eps: r.eps, book_value: r.book_value, ...(r.fund || {}) }, indicators: { ...(r.ind || {}), hi252: r.high52, lo252: r.low52 },
    pressure: r.pressure ? { label: r.pressure } : null, patterns: (r.patterns || []).map(([name, bias]) => ({ name, bias })) });
  async function loadCatalog(prefetched) {
    S.catalog = 'loading';
    if ($('#chips')) updateSignals();
    try {
      const response = await (prefetched || fetch('index.json', { cache: 'no-store' }));
      if (!response || !response.ok) throw new Error('index.json');
      const payload = await response.json();
      const known = new Set(S.data.stocks.map((s) => s.symbol));
      S.others = (payload.stocks || []).filter((r) => !known.has(r.symbol)).map(slimStock);
      S.catalogInfo = { count: S.others.length, analysed: S.others.filter((r) => r.analysed).length };
      S.catalog = 'ready';
      S.catalogLoaded = true;
      openFromHash();
    } catch (err) {
      S.catalog = 'failed';        // the dashboard keeps working with the curated list
    }
    if ($('#chips')) updateSignals();
  }
  async function loadRecord(row) {
    let shard = S.shards.get(row.shard);
    if (!shard) {
      try {
        const response = await fetch(`detail/${row.shard}.json`, { cache: 'no-cache' });
        shard = response.ok ? await response.json() : null;
      } catch (err) { shard = null; }
      if (shard) S.shards.set(row.shard, shard);
    }
    return (shard && shard[row.symbol]) || null;
  }

  function updateHeader() {
    const banner = $('#banner');
    const label = $('#updated');
    const minutes = S.data ? Math.max(0, Math.round((Date.now() - Date.parse(S.data.generated_at)) / 60000)) : null;
    const late = minutes !== null && minutes > STALE_MINUTES && marketsOpenNow();
    const message = S.error || (late ? `The latest data is from ${ago(minutes)} and the markets are open. It should refresh about every 10 minutes, but GitHub sometimes delays runs, so prices and signals may be out of date.` : '');
    banner.hidden = !message;
    banner.textContent = message;
    if (!S.data) { label.textContent = 'No data'; return; }
    label.textContent = `Updated ${ago(minutes)}` + (minutes > 180 && !late ? ' (updates pause when markets are closed)' : '');
  }

  /* India opens at 03:45 UTC and the US closes at 21:00 UTC, Monday to Friday. */
  const STALE_MINUTES = 45;
  function marketsOpenNow(now = new Date()) {
    const day = now.getUTCDay();
    const minute = now.getUTCHours() * 60 + now.getUTCMinutes();
    return day >= 1 && day <= 5 && minute >= 3 * 60 + 45 && minute < 21 * 60;
  }

  /* ---------- shared pieces ---------- */
  function newsList(items, withTopic) {
    if (!items || !items.length) return h('p', { class: 'muted small' }, 'No recent headlines found.');
    return h('ul', { class: 'clean' }, items.map((item) => {
      const url = safeUrl(item.link);
      const title = url ? h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, item.title) : item.title;
      return h('li', null,
        title,
        item.relevant === false ? h('span', { class: 'tag', title: 'The headline is not clearly about this company, so it is not treated as news for it' }, 'Related news') : null,
        h('div', { class: 'muted small' }, [item.source, ago(item.age_min), withTopic ? item.topic || item.market : null].filter(Boolean).join(' · ')));
    }));
  }

  function reasonsList(reasons) {
    return h('ul', { class: 'clean reasons' }, (reasons || []).map((r) => h('li', { class: r.tone }, r.text)));
  }

  const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  function section(title, ...kids) {
    return h('section', { class: 'card', id: 'd-' + slug(title) }, h('h3', null, title), ...kids);
  }


  /* ---------- help text: a "?" next to each section heading opens a plain-language note ---------- */
  const HELP = [
    [/^(India \(NSE\)|United States)$/, 'Every stock in this market for the time frame you chose. The label says how many of the fixed indicator rules point the same way: aligned bullish (most point up), mixed (they disagree) or aligned bearish (most point down). The number is the indicator alignment, from −100 to +100. It describes current conditions. It is not a forecast or a recommendation.'],
    [/^Stock screens$/, 'A short ranked list built from your answers (goal, time frame, risk, market). It filters by readings, fundamentals and risk. It is a screen to study, not personal advice.'],
    [/^Market conditions$/, 'How each market is behaving now, from seven simple checks on its main index. Each check is worth +1, 0 or −1 and the reason is shown. This describes the market. It is not a forecast and not a call on any stock.'],
    [/market conditions$/, 'The seven check scores added up, from −7 to +7. +5 or more is strongly bullish conditions, +2 to +4 bullish, −1 to +1 sideways, −2 to −4 bearish and −5 or less strongly bearish. The bar shows where today sits.'],
    [/^Indexes and global cues/, 'The latest close, today\'s move and the one-month move for the main indexes, volatility indexes, currency, oil, gold and the US 10-year yield. The line is the last 30 sessions. Uptrend means above the 50 and 200-day averages. A high volatility index (VIX above 25) means large swings.'],
    [/^Breadth and sectors$/, 'Breadth shows how many stocks are taking part in a move, not just the index. Sectors show which industries are leading or lagging. Both are counted over the stocks in this app\'s list, not the whole exchange.'],
    [/ breadth$/, 'Advancers rose today and decliners fell. The percentages show how many stocks are above their 20, 50 and 200-day averages. When most stocks are above their averages the move is broad. New 20-day highs and lows count stocks closing above or below the last 20 days\' range.'],
    [/ sectors$/, 'The average move of the stocks in each sector over 1, 5 and 20 trading days, every stock counted the same. The last column is how many stocks are in the sector in this app\'s list.'],
    [/^Upcoming events$/, 'Dates that matter to holders: when results are expected, the ex-dividend date (buy before it to receive the dividend), the dividend payment date, and splits or bonus issues when a prominent news outlet has reported them. Each date shows its sources: YAHOO (a date from Yahoo Finance), NEWS (a headline from a prominent outlet, shown below it) or ESTIMATED (worked out from past payments), and AGREED when two sources give the same date. Companies confirm their dates by notice to the exchange, so check it before acting.'],
    [/^Price accuracy$/, 'Yahoo is the main price source, so its closing prices on the last few completed sessions are compared with an independent source: the official NSE end-of-day file for India, and the Cboe delayed-quote history for the US. "Match" means within 0.5%. A stock that differs by more than 2% is flagged and gets no new alerts until the sources agree. This checks completed sessions only. The live price during the session has no free independent source.'],
    [/^Recent alerts$/, 'The latest alerts the app has raised: a change in a stock\'s reading, a fresh headline about the stock, or a price reaching its reference or risk level. Use "View analysis" to open the stock.'],
    [/^World and market news/, 'Headlines about markets, interest rates, oil, geopolitics and the economy, from outside news sources. They are not checked by this app.'],
    [/^India IPOs, dates and grey-market premium/, 'Open and upcoming India IPOs with their dates, price band, lot size and subscription. Grey-market premium (GMP) is an unofficial rumour of where the shares may list, and it can be wrong. The listing price and profit or loss are illustrations only.'],
    [/^Recently listed stocks/, 'Stocks that have listed recently, found automatically on the NSE and Nasdaq/NYSE lists. They need about 60 trading days of history before readings appear.'],
    [/^US IPO calendar/, 'Upcoming US IPOs from the Nasdaq calendar: the date, price range and size. Financials for companies that have not listed yet are not available here.'],
    [/^IPO headlines/, 'News headlines that mention IPOs, from outside sources. They are not checked by this app.'],
    [/^Check an IPO yourself/, 'Optional. Free sources do not publish an unlisted company\'s financials, so you type the figures here and the same fixed rules check them. It is the one place where you enter data.'],
    [/^IPO checker$/, 'Type in the figures of an IPO from its official documents and the fixed rules turn them into a reading for listing day and for holding long term. The reading is not a recommendation to apply.'],
    [/^Dates, price and grey-market premium$/, 'Optional. Add the dates, price band, lot size and your own grey-market premium figure to see the estimated listing price and profit or loss per lot. The premium is an unofficial rumour and is never used in the reading.'],
    [/^My IPO tracker$/, 'IPOs you saved from the checker. They are kept in this browser only, with their dates and the estimated profit or loss.'],
    [/^Track record$/, 'How the app\'s own bullish and bearish readings played out afterwards, as a plain record. It is not a forecast. Figures from fewer than 30 readings are unreliable.'],
    [/^By reading type$/, 'Results grouped by time frame (weeks view, months-to-years view) and direction (bullish, bearish). "Reference reached" means the price got to the reference level before the volatility risk level or the time limit.'],
    [/^By indicator alignment$/, 'The same results grouped by how strong the alignment was when the alert went out. If stronger alignment does not do better, the rules need review.'],
    [/^By market$/, 'The same results for India and the United States separately.'],
    [/^Ideas to review$/, 'Observations drawn from the record, for you to judge. The app never changes its own rules.'],
    [/^Which indicators lined up/, 'For each indicator, how readings it supported moved on average compared with readings it opposed. It shows which inputs have been helping.'],
    [/^Latest results$/, 'The most recent readings that ended, with how they ended and the price move.'],
    [/^Open readings$/, 'Readings still running: price at the alert, the reference level and the volatility risk level they are being checked against.'],
    [/^Recent alerts for this stock$/, 'The latest alerts for this stock, with their reference level, volatility risk level and the indicator readings behind them.'],
    [/^Latest headlines$/, 'Headlines from the last few hours that are clearly about this company, shown as published with their source. No judgement of good or bad news is made from the words, and news is never part of a reading. Headlines come from outside sources and are not checked.'],
    [/^(Short-term view|Long-term view)/, 'The label counts how many fixed indicator rules point the same way for this time frame. Weeks view uses trend, momentum, RSI, volume and volume pressure. Months-to-years view uses the 200-day average, whether the 50-day average sits above or below it (alignment, with any crossover in the last 20 sessions named separately), 6-month return, 52-week position and volatility. Readings use completed daily candles. While the market is open, the unfinished candle of the day gets its own provisional reading. The bar runs from −100 to +100.'],
    [/^Buying and selling pressure$/, 'An estimate from trading volume (money flow and the share of volume on up days) of whether buyers or sellers dominated recently. For India it adds the share of shares actually delivered. It is not exchange order-book data.'],
    [/^Position size$/, 'A calculator, not advice. Enter your capital and the share you are willing to lose, and it works out how many shares keep a move to the volatility risk level within that amount. Prices can gap past the level.'],
    [/^Levels and risk$/, 'Support and resistance are price areas from history where the price has turned before. The volatility risk level is 2 times the average daily range from the price. Volatility is the yearly size of swings. The 1-day loss (95%) is a loss a normal day has stayed within on 95% of days. The worst fall is the biggest drop over the last year.'],
    [/^Patterns$/, 'Chart patterns spotted in the latest candles, with whether they lean bullish or bearish. Patterns are weak evidence on their own.'],
    [/^Fundamentals$/, 'Company size, profit, debt and valuation figures, refreshed about once a day. They show "unavailable" when the free source has no data.'],
    [/^Dividends$/, 'Actual dividend payments: how often they are paid, the yield over the last 12 months, special payments and any overdue one. Dividends are not guaranteed.'],
    [/^Peers in the same sector$/, 'Other stocks in this app\'s list from the same sector, to compare readings and valuation.'],
    [/^News$/, 'Recent headlines that are clearly about this company, shown as published with their source and age. They come from outside sources and are not checked. The app does not judge them as good or bad, and they do not change any reading.'],
    [/^How have these rules worked on this stock\?/, 'The same fixed rules run over up to 5 years of this stock, trading at the next open with assumed taxes, fees and slippage, compared with simply holding. The most recent 40% is reported on its own, but it is the same history with the same rules, not an independent test. It is past data only and does not predict the future.'],
    [/^Lists$/, 'Tick the lists this stock belongs to, such as your watchlist or your own lists.'],
  ];
  let helpCount = 0;

  function decorateHelp(root) {
    for (const heading of root.querySelectorAll('h2, h3')) {
      if (heading.querySelector('.helpbtn')) continue;
      const own = [...heading.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim();
      const entry = HELP.find(([pattern]) => pattern.test(own));
      if (!entry) continue;
      const note = h('p', { class: 'helptext', id: `help-${++helpCount}`, hidden: true }, entry[1]);
      const button = h('button', { type: 'button', class: 'helpbtn', 'aria-expanded': 'false', 'aria-controls': note.id, 'aria-label': `What does "${own}" show?`, title: 'What is this?' }, '?');
      button.addEventListener('click', (e) => {
        e.stopPropagation();
        note.hidden = !note.hidden;
        button.setAttribute('aria-expanded', String(!note.hidden));
      });
      heading.append(' ', button);
      const anchor = heading.parentElement && heading.parentElement.classList.contains('row') ? heading.parentElement : heading;
      anchor.after(note);
    }
  }

  /* Marks a detail-view card to span the full width of the two-column layout. */
  function wide(el) {
    el.classList.add('wide');
    return el;
  }

  function kv(pairs) {
    return h('div', { class: 'kv' }, pairs.map(([k, v]) => h('div', null, h('span', null, k), h('b', null, naOr(v)))));
  }

  /* ---------- signals tab ---------- */
  // Table headers sort the rows: a click sorts by that column (numbers largest first, text A to Z), a second click reverses it.
  function sortValue(s) {
    const f = S.filters;
    if (f.sort === 'name') return s.name;
    if (f.sort === 'price') return s.price;
    if (f.sort === 'change') return s.change_pct;
    if (f.sort === 'dividend') return s.dividend_yield;
    if (typeof f.sort === 'string' && f.sort.startsWith('col:')) {
      const column = extraColumns(f.view).find((c) => 'col:' + c.label === f.sort);
      if (column && column.val) return column.val(s);
    }
    return s[f.horizon].score;
  }
  const defaultDir = (key) => (key === 'name' || key === 'col:Pays' || key === 'col:Last payment' || key === 'col:Pattern' || key === 'col:Pressure' ? 'asc' : 'desc');
  const currentDir = () => S.filters.dir || defaultDir(S.filters.sort);
  function sortBy(key) {
    const f = S.filters;
    f.dir = f.sort === key ? (currentDir() === 'asc' ? 'desc' : 'asc') : defaultDir(key);
    f.sort = key;
    saveFilters(); updateSignals();
  }
  function sortHead(key, label, cls) {
    const f = S.filters, on = f.sort === key || (key === 'score' && !['name', 'price', 'change', 'dividend'].includes(f.sort) && !String(f.sort).startsWith('col:'));
    const dir = on ? currentDir() : null;
    return h('th', { class: cls || '', 'aria-sort': on ? (dir === 'asc' ? 'ascending' : 'descending') : 'none' },
      h('button', { type: 'button', class: 'sorth' + (on ? ' on' : ''), title: `Sort by ${label}`, onclick: () => sortBy(key) }, label, on ? h('span', { class: 'arrow', 'aria-hidden': 'true' }, dir === 'asc' ? ' ▲' : ' ▼') : null));
  }

  // A list (screen, watchlist, own list) and a sector can be combined: a stock must be in both.
  function inList(s, key) {
    if (!key || key === 'ALL') return true;
    if (key === 'WATCH') return S.watch.has(s.symbol);
    if (key.startsWith('C:')) return (S.lists[key.slice(2)] || []).includes(s.symbol);
    return s.lists.includes(key);
  }
  const inSector = (s, sector) => !sector || sector === 'ALL' || s.lists.includes(sector);

  /* Search is a "like" match, not an exact one: case, punctuation and the order of words do not matter ("bajaj auto", "auto bajaj" and "bajaj-auto" all
     find Bajaj Auto), a word can be typed in part ("hdfc ba"), "&" and "and" are the same, and the legal suffix and exchange ending are ignored.
     If nothing matches that way, close matches are shown instead (one or two letters wrong, such as "relaince"), and the page says so. */
  const normText = (text) => String(text || '').toLowerCase().replace(/&/g, ' and ').replace(/\.(ns|bo)$/, '').replace(/[^a-z0-9]+/g, ' ').trim();
  function withinEdits(a, b, max) {
    if (Math.abs(a.length - b.length) > max) return false;
    let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
    for (let i = 1; i <= a.length; i += 1) {
      const row = [i];
      for (let j = 1; j <= b.length; j += 1) row[j] = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      if (Math.min(...row) > max) return false;
      prev = row;
    }
    return prev[b.length] <= max;
  }
  // A search word matches when a word of the name or the symbol starts with it, or (3 letters or more) it appears inside the name.
  // A symbol is only matched from its start, so "tcs" does not find a stock whose symbol happens to contain those letters.
  function searchKey(s) {
    if (!s._k) {
      const name = normText(s.name), symbol = normText(s.symbol);
      s._k = { name, symbol, compact: symbol.replace(/ /g, ''), words: name.split(' ').concat(symbol.split(' ')) };
    }
    return s._k;
  }
  function matchesQuery(s, tokens, fuzzy) {
    const key = searchKey(s);
    return tokens.every((tok) => key.words.some((w) => w.startsWith(tok)) || (tok.length >= 3 && key.name.includes(tok))
      || (fuzzy && tok.length >= 4 && key.words.some((w) => withinEdits(w.slice(0, tok.length), tok, tok.length >= 7 ? 2 : 1))));
  }
  // How good a match is: 0 the symbol or the whole name, 1 the start of the name or symbol, 2 anything else that matched.
  function matchRank(s, q) {
    const key = searchKey(s), compact = q.replace(/ /g, '');
    if (key.compact === compact || key.name === q) return 0;
    return key.name.startsWith(q) || key.compact.startsWith(compact) ? 1 : 2;
  }

  /* Technical filters. Each one is decided from the stock's own indicator values; a stock without them (not analysed yet) never passes an active one. */
  const PATTERN_NAMES = ['Doji', 'Hammer', 'Shooting star', 'Bullish engulfing', 'Bearish engulfing', 'Morning star', 'Evening star', 'Three white soldiers', 'Three black crows',
    'Bullish harami', 'Bearish harami', 'Inside day', 'Golden cross', 'Death cross', 'MACD bullish cross', 'MACD bearish cross', '20-day breakout', '20-day breakdown',
    'Near 52-week high', 'Near 52-week low', 'Gap up', 'Gap down', 'Tight range', 'Higher highs and lows', 'Lower highs and lows', 'Volume surge'];
  const indOf = (s) => s.indicators || {};
  const aboveAvg = (s, key, want) => (isNum(indOf(s)[key]) && isNum(s.price) ? (want === 'above' ? s.price > indOf(s)[key] : s.price < indOf(s)[key]) : false);
  const rangePos = (s) => (isNum(indOf(s).hi252) && isNum(indOf(s).lo252) && indOf(s).hi252 > indOf(s).lo252 ? (s.price - indOf(s).lo252) / (indOf(s).hi252 - indOf(s).lo252) : null);
  const TECH_FILTERS = [
    ['rsi', 'RSI', [['low', 'Oversold (under 30)'], ['mid', 'Neutral (30 to 70)'], ['high', 'Overbought (over 70)']],
      (s, v) => { const r = indOf(s).rsi; return isNum(r) && (v === 'low' ? r < 30 : v === 'high' ? r > 70 : r >= 30 && r <= 70); }],
    ['sma200', 'Price vs 200-day average', [['above', 'Above'], ['below', 'Below']], (s, v) => aboveAvg(s, 'sma200', v)],
    ['sma50', 'Price vs 50-day average', [['above', 'Above'], ['below', 'Below']], (s, v) => aboveAvg(s, 'sma50', v)],
    ['sma20', 'Price vs 20-day average', [['above', 'Above'], ['below', 'Below']], (s, v) => aboveAvg(s, 'sma20', v)],
    ['macd', 'MACD', [['up', 'Above its signal line'], ['down', 'Below its signal line']], (s, v) => isNum(indOf(s).macd_hist) && (v === 'up' ? indOf(s).macd_hist > 0 : indOf(s).macd_hist < 0)],
    ['mom', '6-month return', [['up', 'Positive'], ['down', 'Negative']], (s, v) => isNum(indOf(s).momentum126) && (v === 'up' ? indOf(s).momentum126 > 0 : indOf(s).momentum126 < 0)],
    ['pos', 'Position in 52-week range', [['high', 'Top 10%'], ['mid', 'Middle'], ['low', 'Bottom 10%']],
      (s, v) => { const x = rangePos(s); return x !== null && (v === 'high' ? x >= 0.9 : v === 'low' ? x <= 0.1 : x > 0.1 && x < 0.9); }],
    ['pressure', 'Buying or selling pressure', [['buy', 'Buying pressure'], ['sell', 'Selling pressure']],
      (s, v) => !!s.pressure && s.pressure.label.startsWith(v === 'buy' ? 'Buying' : 'Selling')],
    ['swing', 'Daily swing (ATR as % of price)', [['low', 'Calm (under 1.5%)'], ['mid', 'Moderate'], ['high', 'Wide (over 3.5%)']],
      (s, v) => { const a = indOf(s).atr; if (!isNum(a) || !isNum(s.price) || !s.price) return false; const x = (a / s.price) * 100; return v === 'low' ? x < 1.5 : v === 'high' ? x > 3.5 : x >= 1.5 && x <= 3.5; }],
    ['pattern', 'Pattern on the latest candles', [['bullish', 'Any bullish pattern'], ['bearish', 'Any bearish pattern']].concat(PATTERN_NAMES.map((n) => [n, n])),
      (s, v) => (s.patterns || []).some((p) => (v === 'bullish' || v === 'bearish' ? p.bias === v : p.name === v))],
  ];
  const activeTech = () => TECH_FILTERS.filter(([key]) => S.filters.tech && S.filters.tech[key]);
  const hasTech = (s) => (s.slim ? !!s.analysed : !!s.indicators);
  const techPass = (s) => activeTech().every(([key, , , test]) => test(s, S.filters.tech[key]));

  function visibleStocks(skipSignal, skipTech) {
    const f = S.filters;
    const query = normText(S.q);
    let tokens = query.split(' ').filter(Boolean);
    if (tokens.length > 1) tokens = tokens.filter((w) => w !== 'and');          // "m&m" and "m and m" are the same; "and" alone says nothing
    const pass = (fuzzy) => S.data.stocks.concat(S.others).filter((s) => {
      if (f.market !== 'ALL' && s.market !== f.market) return false;
      if (!inList(s, f.list) || !inSector(s, f.sector)) return false;
      const label = s[f.horizon].label;
      if (skipSignal) { /* counted separately */ }
      else if (f.signal === 'BUY' && side(label) !== 'buy') return false;
      else if (f.signal === 'HOLD' && label !== 'Hold') return false;
      else if (f.signal === 'SELL' && side(label) !== 'sell') return false;
      if (tokens.length && !matchesQuery(s, tokens, fuzzy)) return false;
      if (!skipTech && !techPass(s)) return false;
      return true;
    });
    let rows = pass(false);
    S.fuzzy = false;
    if (tokens.length && !rows.length) { rows = pass(true); S.fuzzy = rows.length > 0; }
    const asc = currentDir() === 'asc';
    const byMatch = tokens.length && f.sort === 'score' && !skipSignal;       // while searching with the default order, the best matches come first
    const present = (v) => isNum(v) || (typeof v === 'string' && v !== '');
    rows.sort((a, b) => {
      if (byMatch) { const d = matchRank(a, query) - matchRank(b, query); if (d) return d; }
      const av = sortValue(a), bv = sortValue(b);
      if (!present(av) && !present(bv)) return 0;
      if (!present(av)) return 1;                       // rows without a value always go last
      if (!present(bv)) return -1;
      const order = typeof av === 'string' || typeof bv === 'string' ? String(av).localeCompare(String(bv)) : av - bv;
      return asc ? order : -order;
    });
    return rows;
  }

  /* Text cells are guarded so a spreadsheet never runs one as a formula. Numbers pass through untouched. */
  function csvCell(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '';
    let text = String(value);
    if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
    return /[",\n\r]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
  }

  function exportCsv() {
    const rows = visibleStocks();
    const head = ['Symbol', 'Name', 'Market', 'Sector', 'Currency', 'Price', 'Day change %', 'Weeks-view reading', 'Weeks-view indicator alignment', 'Months-view reading',
      'Months-view indicator alignment', 'Pressure', 'Dividend yield (12m)', 'P/E', 'ROE', 'Debt/Equity', 'Yearly volatility', 'Last price date'];
    const lines = [head.map(csvCell).join(',')];
    for (const s of rows) {
      const fund = s.fundamentals || {};
      lines.push([s.symbol, s.name, s.market, s.sector, s.currency, s.price, s.change_pct, labelText(s.short.label), s.short.score, labelText(s.long.label), s.long.score,
        s.pressure ? s.pressure.label : '', s.dividend_yield, fund.pe, fund.roe, fund.debt_to_equity, (s.risk || {}).annual_vol, s.as_of].map(csvCell).join(','));
    }
    const url = URL.createObjectURL(new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' }));
    const link = h('a', { href: url, download: `stock-signals-${todayIso()}.csv` });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`Exported ${rows.length} stock${rows.length === 1 ? '' : 's'}`);
  }

  function listChips() {
    const f = S.filters;
    const pool = S.data.stocks.concat(S.others).filter((s) => f.market === 'ALL' || s.market === f.market);
    const inSectorPool = pool.filter((s) => inSector(s, f.sector));      // list counts follow the chosen sector
    const inListPool = pool.filter((s) => inList(s, f.list));            // sector counts follow the chosen list
    const names = new Set();
    for (const s of pool) for (const name of s.lists) names.add(name);
    const countList = (key) => inSectorPool.filter((s) => inList(s, key)).length;
    const chips = [['ALL', 'All', countList('ALL')], ['WATCH', 'My watchlist', countList('WATCH')]];
    for (const name of Object.keys(S.lists)) chips.push(['C:' + name, name, countList('C:' + name)]);
    for (const name of FIXED_LISTS) if (names.has(name)) chips.push([name, name, countList(name)]);
    const sectors = [...names].filter((n) => !FIXED_LISTS.includes(n) && n !== 'Other').sort();
    const sectorChips = [['ALL', 'All sectors', inListPool.length], ...sectors.map((name) => [name, name, inListPool.filter((s) => inSector(s, name)).length])];
    const make = (field) => ([key, label, count]) => h('button', {
      type: 'button', class: 'chip' + (f[field] === key ? ' on' : ''), 'aria-pressed': f[field] === key ? 'true' : 'false',
      onclick: () => { f[field] = key; saveFilters(); updateSignals(); },
    }, `${label} (${count})`);
    const buttons = chips.map(make('list'));
    buttons.push(h('button', { type: 'button', class: 'chip', onclick: () => {
      const name = (prompt('Name for the new list (for example "Retirement")') || '').trim().slice(0, 30);
      if (!name) return;
      if (S.lists[name]) { toast('A list with that name already exists'); return; }
      S.lists[name] = []; store.set('lists', S.lists); f.list = 'C:' + name; saveFilters(); updateSignals();
      toast('List created. Open a stock to add it to this list.');
    } }, '+ New list'));
    return { lists: buttons, sectors: sectors.length ? sectorChips.map(make('sector')) : [] };
  }

  const VIEWS = [['overview', 'Overview'], ['valuation', 'Valuation'], ['dividend', 'Dividends'], ['technical', 'Technical'], ['risk', 'Risk']];
  const DASH = '–';

  const pressureText = (s) => (s.pressure ? s.pressure.label.replace(' pressure', '') : DASH);
  const pressureClass = (s) => (s.pressure && s.pressure.label.startsWith('Buying') ? 'pos' : s.pressure && s.pressure.label.startsWith('Selling') ? 'neg' : '');
  const dividendCell = (s) => (isNum(s.dividend_yield) && s.dividend_yield > 0
    ? [percent(s.dividend_yield, 2), s.dividend ? h('span', { class: 'muted small', title: `${s.dividend.frequency} dividend${s.dividend.overdue ? ', payment overdue' : ''}` }, ` ${FREQ_CODE[s.dividend.frequency] || ''}${s.dividend.overdue ? '!' : ''}`) : null]
    : DASH);
  const newsCell = (s) => (s.news.fresh.length ? h('span', { class: 'tag', title: 'A headline about this company from the last few hours' }, 'New') : null);

  /* Extra columns for each table view, after the fixed Stock, Price, Day and Signal columns. */
  function extraColumns(view) {
    const fund = (s) => s.fundamentals || {};
    const ind = (s) => s.indicators || {};
    const risk = (s) => s.risk || {};
    const sets = {
      overview: [
        { label: 'Pressure', render: pressureText, cls: pressureClass },
        { label: 'Dividend', num: true, render: dividendCell },
        { label: 'News', render: newsCell, hideMd: true },
      ],
      valuation: [
        { label: 'Mkt cap', num: true, render: (s) => big(fund(s).market_cap, s.currency) },
        { label: 'P/E', num: true, render: (s) => number(fund(s).pe, 1) },
        { label: 'Forward P/E', num: true, render: (s) => number(fund(s).forward_pe, 1), hideMd: true },
        { label: 'P/B', num: true, render: (s) => number(fund(s).pb, 1) },
        { label: 'EV/EBITDA', num: true, render: (s) => number(fund(s).ev_ebitda, 1) },
        { label: 'P/S', num: true, render: (s) => number(fund(s).price_to_sales, 1), hideMd: true },
        { label: 'EPS', num: true, render: (s) => number(fund(s).eps, 2), hideMd: true },
        { label: 'EBITDA', num: true, render: (s) => big(fund(s).ebitda, s.currency), hideMd: true },
        { label: 'Op. margin', num: true, render: (s) => percent(fund(s).operating_margin, 0), hideMd: true },
        { label: 'ROE', num: true, render: (s) => percent(fund(s).roe, 0) },
        { label: 'Revenue growth', num: true, render: (s) => percent(fund(s).revenue_growth, 0), hideMd: true },
        { label: 'Debt/Equity', num: true, render: (s) => number(fund(s).debt_to_equity) },
        { label: 'Free cash flow', num: true, render: (s) => big(fund(s).free_cash_flow, s.currency), hideMd: true },
        { label: 'Current ratio', num: true, render: (s) => number(fund(s).current_ratio, 2), hideMd: true },
      ],
      dividend: [
        { label: 'Yield (12m)', num: true, render: (s) => (s.dividend_yield > 0 ? percent(s.dividend_yield, 2) : DASH) },
        { label: 'Recurring yield', num: true, render: (s) => (s.dividend && isNum(s.dividend.forward_yield) ? percent(s.dividend.forward_yield, 2) : DASH) },
        { label: 'Pays', render: (s) => (s.dividend ? s.dividend.frequency + (s.dividend.overdue ? ' (overdue)' : '') : DASH) },
        { label: 'Last payment', render: (s) => (s.dividend ? `${money(s.dividend.last_amount, s.currency)} on ${s.dividend.last_date}` : DASH), hideMd: true },
        { label: 'Change', num: true, render: (s) => (s.dividend && isNum(s.dividend.growth) ? signed(s.dividend.growth * 100, 1) : DASH) },
        { label: 'Payout', num: true, render: (s) => percent(fund(s).payout_ratio, 0) },
      ],
      technical: [
        { label: 'RSI', num: true, render: (s) => number(ind(s).rsi, 0) },
        { label: 'vs 200-day', num: true, render: (s) => (isNum(ind(s).sma200) ? signed((s.price / ind(s).sma200 - 1) * 100, 1) : DASH) },
        { label: '6-month', num: true, render: (s) => (isNum(ind(s).momentum126) ? signed(ind(s).momentum126 * 100, 1) : DASH) },
        { label: '52-week range', num: true, render: (s) => (isNum(ind(s).hi252) && ind(s).hi252 > ind(s).lo252 ? percent((s.price - ind(s).lo252) / (ind(s).hi252 - ind(s).lo252), 0) : DASH) },
        { label: 'Pressure', render: pressureText, cls: pressureClass },
        { label: 'Pattern', render: (s) => ((s.patterns || [])[0] ? s.patterns[0].name : DASH), hideMd: true },
      ],
      risk: [
        { label: 'Volatility', num: true, render: (s) => percent(risk(s).annual_vol, 0) },
        { label: '1-day VaR', num: true, render: (s) => percent(risk(s).var95, 1) },
        { label: 'Worst fall', num: true, render: (s) => percent(risk(s).max_drawdown, 0) },
        { label: 'Beta', num: true, render: (s) => number(risk(s).beta) },
        { label: 'Volatility risk level', num: true, render: (s) => (s.stop ? signed(s.stop.pct, 1) : DASH) },
        { label: 'Rule vs hold', render: (s) => { const b = (s.backtest || {}).short; return b ? (b.beats_buy_hold_oos ? 'Beat' : 'Lagged') : DASH; }, hideMd: true },
      ],
    };
    const values = {
      Pressure: (s) => (s.pressure ? (s.pressure.label.startsWith('Buying') ? 1 : s.pressure.label.startsWith('Selling') ? -1 : 0) : null),
      Dividend: (s) => s.dividend_yield, News: (s) => s.news.fresh.length,
      'Mkt cap': (s) => fund(s).market_cap, 'P/E': (s) => fund(s).pe, 'P/B': (s) => fund(s).pb, ROE: (s) => fund(s).roe,
      'Revenue growth': (s) => fund(s).revenue_growth, 'Debt/Equity': (s) => fund(s).debt_to_equity,
      'Forward P/E': (s) => fund(s).forward_pe, 'EV/EBITDA': (s) => fund(s).ev_ebitda, 'P/S': (s) => fund(s).price_to_sales, EPS: (s) => fund(s).eps,
      EBITDA: (s) => fund(s).ebitda, 'Op. margin': (s) => fund(s).operating_margin, 'Free cash flow': (s) => fund(s).free_cash_flow, 'Current ratio': (s) => fund(s).current_ratio,
      'Yield (12m)': (s) => s.dividend_yield, 'Recurring yield': (s) => (s.dividend ? s.dividend.forward_yield : null),
      Pays: (s) => (s.dividend ? s.dividend.frequency : null), 'Last payment': (s) => (s.dividend ? s.dividend.last_date : null),
      Change: (s) => (s.dividend ? s.dividend.growth : null), Payout: (s) => fund(s).payout_ratio,
      RSI: (s) => ind(s).rsi, 'vs 200-day': (s) => (isNum(ind(s).sma200) ? s.price / ind(s).sma200 - 1 : null), '6-month': (s) => ind(s).momentum126,
      '52-week range': (s) => (isNum(ind(s).hi252) && ind(s).hi252 > ind(s).lo252 ? (s.price - ind(s).lo252) / (ind(s).hi252 - ind(s).lo252) : null),
      Pattern: (s) => ((s.patterns || [])[0] ? s.patterns[0].name : null),
      Volatility: (s) => risk(s).annual_vol, '1-day VaR': (s) => risk(s).var95, 'Worst fall': (s) => risk(s).max_drawdown, Beta: (s) => risk(s).beta,
      'Volatility risk level': (s) => (s.stop ? s.stop.pct : null), 'Rule vs hold': (s) => { const b = (s.backtest || {}).short; return b ? (b.beats_buy_hold_oos ? 1 : 0) : null; },
    };
    return (sets[view] || sets.overview).map((c) => ({ ...c, val: values[c.label] }));
  }
  const columnClass = (column) => `${column.num ? 'num ' : ''}hide-sm${column.hideMd ? ' hide-md' : ''}`;

  function stockRow(s) {
    const f = S.filters;
    const star = h('button', {
      type: 'button', class: 'star' + (S.watch.has(s.symbol) ? ' on' : ''), 'aria-label': `${S.watch.has(s.symbol) ? 'Remove' : 'Add'} ${s.name} ${S.watch.has(s.symbol) ? 'from' : 'to'} watchlist`,
      'aria-pressed': S.watch.has(s.symbol) ? 'true' : 'false',
      onclick: (e) => { e.stopPropagation(); S.watch.has(s.symbol) ? S.watch.delete(s.symbol) : S.watch.add(s.symbol); saveWatch(); updateSignals(); },
    }, S.watch.has(s.symbol) ? '★' : '☆');
    const row = h('tr', { class: 'stock', tabindex: '0', 'aria-label': `${s.name} details` },
      h('td', null, star),
      h('td', { class: 'name' }, h('b', null, s.name), h('span', null, s.symbol, s.new ? ' · new listing' : '', s.stale ? ' · stale price' : '', s.slim && !s.analysed ? ' · quote only' : '')),
      h('td', { class: 'num' }, money(s.price, s.currency)),
      h('td', { class: `num ${isNum(s.change_pct) ? (s.change_pct >= 0 ? 'up' : 'down') : ''}` }, signed(s.change_pct)),
      h('td', null, badge(s[f.horizon])),
      extraColumns(f.view).map((column) => h('td', { class: `${columnClass(column)}${column.cls ? ' ' + column.cls(s) : ''}` }, column.render(s))));
    row.addEventListener('click', () => openDetail(s.symbol, row));
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter') openDetail(s.symbol, row); });
    return row;
  }

  const PAGE = 150;                       // rows drawn at a time; the sort and filters always cover every stock
  const MARKET_NAMES = { IN: 'India (NSE)', US: 'United States' };
  const MARKET_INDEX = { IN: '^NSEI', US: '^GSPC' };

  /* One panel per market: a header with buy/hold/sell counts and the index trend, then its table. */
  function marketPanel(code, rows) {
    const horizon = S.filters.horizon;
    const count = { buy: 0, hold: 0, sell: 0 };
    for (const s of rows) if (s[horizon].label) count[side(s[horizon].label)] += 1;
    const index = ((S.data.market || {}).watch || []).find((w) => w.symbol === MARKET_INDEX[code]);
    const head = h('div', { class: 'panelhead' },
      h('h2', null, MARKET_NAMES[code]),
      h('span', { class: 'muted small' }, `${rows.length} stock${rows.length === 1 ? '' : 's'}`),
      index && index.regime ? h('span', { class: `tag ${index.regime === 'Downtrend' ? 'warn' : ''}`, title: `${index.name} ${signed(index.change_pct)} today` }, `${index.name}: ${index.regime}`, isNum(index.change_pct) ? ` ${signed(index.change_pct)}` : '') : null);
    const bar = rows.length ? h('div', null,
      h('div', { class: 'leanbar', role: 'img', 'aria-label': `${count.buy} aligned bullish, ${count.hold} mixed, ${count.sell} aligned bearish` },
        [['u', count.buy], ['n', count.hold], ['d', count.sell]].map(([k, n]) => (n ? h('i', { class: k, style: `flex:${n}` }) : null))),
      h('div', { class: 'leanlegend' }, h('span', { class: 'up' }, `${count.buy} bullish`), h('span', { class: 'muted' }, `${count.hold} mixed`), h('span', { class: 'down' }, `${count.sell} bearish`))) : null;
    const body = rows.length
      ? h('div', { class: 'tablewrap' }, h('table', null,
        h('thead', null, h('tr', null, h('th', null, ''), sortHead('name', 'Stock'), sortHead('price', 'Price', 'num'), sortHead('change', 'Day', 'num'),
          sortHead('score', 'Signal'), extraColumns(S.filters.view).map((column) => (column.val ? sortHead('col:' + column.label, column.label, columnClass(column)) : h('th', { class: columnClass(column) }, column.label))))),
        h('tbody', null, rows.slice(0, S.shown[code] || PAGE).map(stockRow))),
        rows.length > (S.shown[code] || PAGE) ? h('button', { type: 'button', class: 'btn ghost more', onclick: () => { S.shown[code] = (S.shown[code] || PAGE) + PAGE; updateSignals(); } },
          `Show ${Math.min(PAGE, rows.length - (S.shown[code] || PAGE))} more (${rows.length - (S.shown[code] || PAGE)} not shown)`) : null)
      : h('div', { class: 'empty' }, 'No stocks match these filters.');
    return h('section', { class: 'card panel', 'aria-label': MARKET_NAMES[code] }, head, bar, body);
  }

  function updateSignals() {
    const chips = $('#chips'), results = $('#results');
    if (!chips || !results) return;
    const strips = listChips();
    chips.replaceChildren(...[h('div', { class: 'chips', role: 'group', 'aria-label': 'Lists and technical screens' }, strips.lists),
      strips.sectors.length ? h('div', { class: 'chips', role: 'group', 'aria-label': 'Sectors' }, strips.sectors) : null].filter(Boolean));
    const rows = visibleStocks();
    const fuzzy = S.fuzzy;                 // a later call below recomputes S.fuzzy
    const f = S.filters;
    const signature = [f.market, f.list, f.sector, f.signal, S.q, JSON.stringify(f.tech || {})].join('|');
    if (signature !== S.sig) { S.sig = signature; S.shown = {}; }        // a new search or filter starts again at the first page
    const hidden = f.signal === 'ALL' ? 0 : visibleStocks(true).filter((s) => !s[f.horizon].label).length;
    const hiddenTech = activeTech().length ? visibleStocks(false, true).filter((s) => !hasTech(s)).length : 0;
    const info = S.catalogInfo;
    const status = S.catalog === 'loading' ? h('p', { class: 'muted small notice' }, 'Loading every other listed stock… the table shows the main list for now.')
      : S.catalog === 'failed' ? h('p', { class: 'small notice warnline' }, 'The full list of listed stocks could not be loaded, so only the main list is shown. ',
        h('button', { type: 'button', class: 'chip', onclick: () => loadCatalog() }, 'Try again'))
        : info && info.analysed < info.count * 0.98 ? h('p', { class: 'muted small notice' }, `${info.analysed.toLocaleString()} of ${info.count.toLocaleString()} other listed stocks are analysed so far. The rest show their quote only and fill in over the next few hours.`) : null;
    const head = h('div', { class: 'row small muted', style: 'justify-content:space-between;margin-bottom:6px' },
      h('span', null, `${rows.length} stock${rows.length === 1 ? '' : 's'} · ${f.horizon === 'short' ? 'weeks view' : 'months-to-years view'} reading`,
        hidden ? ` · ${hidden.toLocaleString()} without a reading yet hidden by the Reading filter` : '',
        hiddenTech ? ` · ${hiddenTech.toLocaleString()} without technical data hidden by the technical filters` : '', fuzzy ? ` · no exact match for "${S.q.trim()}", showing close matches` : ''),
      f.list.startsWith('C:') ? h('button', { type: 'button', class: 'chip', onclick: () => {
        if (!confirm(`Delete the list "${f.list.slice(2)}"? The stocks are not deleted.`)) return;
        delete S.lists[f.list.slice(2)]; store.set('lists', S.lists); f.list = 'ALL'; saveFilters(); updateSignals();
      } }, 'Delete this list') : null);
    if (!rows.length) {
      const clear = () => { f.market = 'ALL'; f.list = 'ALL'; f.sector = 'ALL'; f.signal = 'ALL'; f.tech = {}; S.q = ''; saveFilters(); render(); };
      results.replaceChildren(...[status, head, h('div', { class: 'empty' },
        f.list === 'WATCH' && !S.q ? 'Your watchlist is empty. Tap the star next to a stock to add it.'
          : S.catalog === 'loading' && S.q ? 'Nothing found in the main list yet. Every other listed stock is still loading.' : 'No stocks match these filters. ',
        f.list === 'WATCH' && !S.q ? null : h('button', { type: 'button', class: 'chip', onclick: clear }, 'Clear filters'))].filter(Boolean));
      return;
    }
    const codes = f.market === 'ALL' ? ['IN', 'US'] : [f.market];
    results.replaceChildren(...[status, head, h('div', { class: 'cols' + (codes.length > 1 ? ' two' : '') + (f.view !== 'overview' ? ' wide-view' : '') }, codes.map((code) => marketPanel(code, rows.filter((s) => s.market === code))))].filter(Boolean));
    decorateHelp(results);
  }

  function select(label, value, options, onChange) {
    const el = h('select', { onchange: (e) => onChange(e.target.value) }, options.map(([v, text]) => h('option', { value: v, selected: v === value }, text)));
    return h('label', { class: 'field' }, label, el);
  }

  function techPanel(open) {
    const f = S.filters;
    f.tech = f.tech || {};
    const n = activeTech().length;
    const choose = (key, label, options) => h('label', { class: 'field' }, label,
      h('select', { onchange: (e) => { if (e.target.value) f.tech[key] = e.target.value; else delete f.tech[key]; saveFilters(); renderTech(); updateSignals(); } },
        [['', 'Any']].concat(options).map(([v, text]) => h('option', { value: v, selected: v === (f.tech[key] || '') }, text))));
    return h('details', { class: 'card techfilters', open: open ? 'open' : null },
      h('summary', null, 'Technical filters', n ? h('span', { class: 'tag', style: 'margin-left:8px' }, `${n} on`) : null),
      h('div', { class: 'row', style: 'margin-top:8px' }, TECH_FILTERS.map(([key, label, options]) => choose(key, label, options))),
      h('p', { class: 'muted small', style: 'margin:6px 0 0' }, 'Every filter you set must be true for a stock to show. Each one reads the stock\'s indicator values; a stock that is not analysed yet has none, so it is hidden while a technical filter is on. ',
        n ? h('button', { type: 'button', class: 'chip', onclick: () => { f.tech = {}; saveFilters(); renderTech(); updateSignals(); } }, 'Clear technical filters') : null));
  }
  function renderTech() {
    const box = $('#techbox');
    if (!box) return;
    const open = !!(box.firstChild && box.firstChild.open);
    box.replaceChildren(techPanel(open));
  }

  function viewSignals() {
    const f = S.filters;
    const search = h('input', { type: 'search', placeholder: 'Name or symbol', value: S.q, 'aria-label': 'Search stocks', oninput: (e) => { S.q = e.target.value; updateSignals(); } });
    const bar = h('div', { class: 'card filterbar' }, h('div', { class: 'row' },
      h('label', { class: 'field' }, 'Search', search),
      select('Market', f.market, [['ALL', 'All markets'], ['IN', 'India'], ['US', 'United States']], (v) => { f.market = v; f.list = 'ALL'; f.sector = 'ALL'; saveFilters(); updateSignals(); }),
      select('Reading', f.signal, [['ALL', 'Any'], ['BUY', 'Aligned bullish'], ['HOLD', 'Mixed'], ['SELL', 'Aligned bearish']], (v) => { f.signal = v; saveFilters(); updateSignals(); }),
      select('Sort by', f.sort, [['score', 'Indicator alignment'], ['change', 'Day change'], ['dividend', 'Dividend yield'], ['name', 'Name']].concat(['score', 'change', 'dividend', 'name'].includes(f.sort) ? [] : [[f.sort, 'Table column']]), (v) => { f.sort = v; f.dir = defaultDir(v); saveFilters(); updateSignals(); }),
      h('div', { class: 'field' }, 'Horizon', h('div', { class: 'row', style: 'gap:6px' },
        ['short', 'long'].map((hz) => h('button', { type: 'button', class: 'chip' + (f.horizon === hz ? ' on' : ''), 'aria-pressed': f.horizon === hz ? 'true' : 'false',
          onclick: () => { f.horizon = hz; saveFilters(); render(); } }, hz === 'short' ? 'Short-term' : 'Long-term')))),
      select('Columns', f.view, VIEWS, (v) => { f.view = v; saveFilters(); updateSignals(); }),
      h('button', { type: 'button', class: 'chip', title: 'Download the stocks shown as a spreadsheet file', onclick: exportCsv }, 'Export CSV')));
    const root = h('div', null, bar, h('div', { id: 'chips' }), h('div', { id: 'techbox' }), h('div', { id: 'results' }));
    queueMicrotask(() => { renderTech(); updateSignals(); });
    return root;
  }

  /* ---------- detail dialog ---------- */
  let lastFocus = null;
  function quoteView(row, note) {
    const range = isNum(row.high52) && isNum(row.low52) && row.high52 > row.low52 ? percent((row.price - row.low52) / (row.high52 - row.low52), 0) : DASH;
    return h('div', { class: 'detailview' },
      h('div', { class: 'row', style: 'justify-content:space-between;gap:8px;padding:12px' },
        h('div', null, h('h2', { style: 'margin:0' }, row.name), h('span', { class: 'muted small' }, row.symbol)),
        h('div', { class: 'row', style: 'gap:6px' },
          h('button', { type: 'button', class: 'chip' + (S.watch.has(row.symbol) ? ' on' : ''), 'aria-pressed': S.watch.has(row.symbol) ? 'true' : 'false',
            onclick: (e) => { S.watch.has(row.symbol) ? S.watch.delete(row.symbol) : S.watch.add(row.symbol); saveWatch(); e.currentTarget.classList.toggle('on', S.watch.has(row.symbol));
              e.currentTarget.textContent = S.watch.has(row.symbol) ? '★ In watchlist' : '☆ Add to watchlist'; e.currentTarget.setAttribute('aria-pressed', String(S.watch.has(row.symbol))); updateSignals(); } },
          S.watch.has(row.symbol) ? '★ In watchlist' : '☆ Add to watchlist'),
          h('button', { type: 'button', class: 'dclose', 'aria-label': 'Close', onclick: closeDetail }, '✕'))),
      h('div', { class: 'kv', style: 'padding:0 12px' }, [['Price', money(row.price, row.currency)], ['Day', signed(row.change_pct)], ['Market cap', big(row.cap, row.currency)],
        ['P/E', number(row.pe, 1)], ['P/B', number(row.pb, 1)], ['EPS', number(row.eps, 2)], ['Book value per share', number(row.book_value, 2)],
        ['Position in 52-week range', range], ['Volume vs average', isNum(row.volume) && row.avg_volume ? `${(row.volume / row.avg_volume).toFixed(1)}x` : DASH]]
        .map(([k, v]) => h('div', null, h('span', null, k), h('b', null, naOr(v))))),
      h('p', { class: 'muted small', style: 'padding:8px 12px' }, note),
      h('p', { class: 'small', style: 'padding:0 12px 12px' }, 'Official pages: ',
        row.symbol.endsWith('.NS') ? [h('a', { href: `https://www.nseindia.com/get-quotes/equity?symbol=${encodeURIComponent(row.symbol.slice(0, -3))}`, target: '_blank', rel: 'noopener' }, 'NSE quote'), ' · '] : null,
        h('a', { href: `https://finance.yahoo.com/quote/${encodeURIComponent(row.symbol)}`, target: '_blank', rel: 'noopener' }, 'Yahoo Finance')));
  }

  async function openDetail(symbol, opener, tab) {
    const s = S.data.stocks.find((x) => x.symbol === symbol);
    if (!s) {
      const row = S.others.find((x) => x.symbol === symbol);
      if (!row) return;
      lastFocus = opener || document.activeElement;
      const dlg = $('#detail');
      dlg.replaceChildren(quoteView(row, 'Loading the analysis…'));
      if (!dlg.open) dlg.showModal();
      const record = row.analysed ? await loadRecord(row) : null;
      if (!dlg.open) return;
      if (record) showDetail(record, tab);
      else dlg.replaceChildren(quoteView(row, row.analysed ? 'The analysis could not be loaded. Try again in a moment.' : 'This stock has not been analysed yet. Every listed stock is analysed in turn and refreshed about once a day. Until then only its quote is shown.'));
      return;
    }
    lastFocus = opener || document.activeElement;
    if (s.lite) {
      const dlg = $('#detail');
      dlg.replaceChildren(h('div', { class: 'detailview' }, h('div', { class: 'row', style: 'justify-content:space-between;gap:8px;padding:12px' },
        h('div', null, h('h2', { style: 'margin:0' }, s.name), h('span', { class: 'muted small' }, s.symbol)),
        h('button', { type: 'button', class: 'dclose', 'aria-label': 'Close', onclick: closeDetail }, '✕')), h('p', { class: 'muted small', style: 'padding:0 12px 16px' }, 'Loading the analysis…')));
      if (!dlg.open) dlg.showModal();
      const full = await loadRecord(s);
      if (!dlg.open) return;
      if (!full) {                      // the summary record cannot draw the whole page, so show the quote and say so
        const f = s.fundamentals || {}, i = s.indicators || {};
        dlg.replaceChildren(quoteView({ ...s, cap: f.market_cap, high52: i.hi252, low52: i.lo252 }, 'The full analysis could not be loaded. Close this and try again in a moment.'));
        return;
      }
      showDetail(full, tab);
      return;
    }
    showDetail(s, tab);
  }
  function showDetail(s, tab) {
    const dlg = $('#detail');
    dlg.replaceChildren(detailView(s));
    decorateHelp(dlg);
    activateDetailTab(tab || 'overview');
    if (!dlg.open) dlg.showModal();
    dlg.scrollTop = 0;
  }
  function closeDetail() { const dlg = $('#detail'); if (dlg.open) dlg.close(); }

  /* Links from alerts look like #stock=AAPL&section=signals and open that stock's analysis. */
  let handledHash = '';
  function clearHash() {
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
    handledHash = '';
  }
  function openFromHash() {
    const match = /^#stock=([^&]+)(?:&section=([a-z-]+))?$/.exec(location.hash);
    if (!match || !S.data || handledHash === location.hash) return;
    handledHash = location.hash;
    let symbol;
    try { symbol = decodeURIComponent(match[1]); } catch (e) { clearHash(); return; }
    if (!S.data.stocks.some((s) => s.symbol === symbol) && !S.others.some((s) => s.symbol === symbol)) {
      if (!S.catalogLoaded) { handledHash = ''; return; }
      toast(`${symbol} is not on the list right now.`);
      clearHash();
      return;
    }
    openDetail(symbol, null, SECTION_TAB[match[2]]);
    if (match[2]) {
      setTimeout(() => { const target = document.getElementById('d-' + match[2]); if (target) target.scrollIntoView({ block: 'start' }); }, 150);
    }
  }

  /* The latest alerts for one stock, with their targets and reasons, shown at the top of its page. */
  function alertsForStock(s) {
    const recent = (S.data.alerts || []).filter((a) => a && a.symbol === s.symbol).slice(-3).reverse();
    if (!recent.length) return null;
    return wide(section('Recent alerts for this stock', h('ul', { class: 'clean' }, recent.map((a) => h('li', null,
      h('span', { style: 'white-space:pre-line' }, a.text),
      h('div', { class: 'muted small' }, ago(Math.max(0, Math.round((Date.now() / 1000 - a.ts) / 60)))))))));
  }

  const PART_NAMES = { trend: 'trend', macd: 'MACD momentum', rsi: 'RSI', volume: 'volume spike check', pressure: 'volume pressure',
    trend200: '200-day average', cross: '50/200-day average', momentum: '6-month return', position: '52-week range', volatility: 'volatility' };

  /* Why a reading is what it is: what supports it, what pulls against it, how complete the data was, and what would weaken it. */
  function signalCard(title, sig) {
    const pos = isNum(sig.score) ? clamp((sig.score + 100) / 2, 0, 100) : 50;
    const reasons = sig.reasons || [];
    const up = reasons.filter((r) => r.tone === 'pos').length, down = reasons.filter((r) => r.tone === 'neg').length;
    const missing = (sig.missing || []).map((k) => PART_NAMES[k] || k);
    const sure = sig.confidence && sig.confidence !== 'full'
      ? h('p', { class: 'small warnline' }, `${sig.confidence === 'limited' ? 'Limited' : 'Reduced'} confidence: no data for ${missing.join(' and ')}, so this reading rests on the other parts only.`) : null;
    return h('div', null, h('h3', null, title), badge(sig),
      isNum(sig.score) ? h('div', { class: 'meter', role: 'img', 'aria-label': `Score ${sig.score} on a scale from minus 100 to plus 100` }, h('i', { style: `left:${pos}%` })) : null,
      reasons.length ? h('p', { class: 'small muted', style: 'margin:4px 0' }, `${up} part${up === 1 ? '' : 's'} supporting, ${down} against, ${reasons.length - up - down} neutral.`) : null,
      reasonsList(reasons), sure,
      (sig.invalidation || []).length ? h('div', { class: 'small invalid' }, h('b', null, 'What would weaken this: '), sig.invalidation.join('; '), '.') : null);
  }

  /* Where the numbers come from and how current they are. */
  function dataNote(s) {
    const lines = [`Prices: free Yahoo Finance data, delayed by up to about 15 minutes. Price date ${s.as_of}.`,
      `Readings use completed daily candles up to ${s.confirmed_as_of || s.as_of}.`];
    const live = s.live;
    return h('div', { class: 'card wide small datanote' },
      lines.map((l) => h('div', null, l)),
      s.provisional ? h('div', { class: 'provnote' }, h('b', null, 'Market open: '), `today's candle is unfinished, so the readings above are from the last completed day. Provisional reading with today's price so far (volume left out): ${live ? `weeks view ${labelText(live.short.label)}${isNum(live.short.score) ? ` ${live.short.score > 0 ? '+' : ''}${live.short.score}` : ''}, months-to-years view ${labelText(live.long.label)}${isNum(live.long.score) ? ` ${live.long.score > 0 ? '+' : ''}${live.long.score}` : ''}` : 'not available'}. It is recalculated after the close.`) : null);
  }

  /* Every level in one list, highest price first, so the gaps above and below the price read at a glance. */
  function levelRows(s) {
    const zones = ((s.levels || {}).zones) || { support: [], resistance: [] };
    const rows = [];
    for (const z of zones.resistance.slice(0, 1)) rows.push({ kind: 'res', label: 'Resistance', note: 'a price area where rises have stalled before', v: z.level });
    for (const z of zones.support.slice(0, 1)) rows.push({ kind: 'sup', label: 'Support', note: 'a price area where falls have paused before', v: z.level });
    if (s.stop && isNum(s.stop.price)) rows.push({ kind: 'risk', label: 'Risk level', note: 'two average daily ranges below the price; a close below it means the move has gone against the reading', v: s.stop.price });
    return rows.filter((r) => isNum(r.v)).sort((a, b) => b.v - a.v);
  }

  /* Where the price sits: resistance, support and risk level as one small table, with the 52-week range under it. */
  const shortDay = (iso) => (typeof iso === 'string' && iso.length === 10 ? new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }) : 'unknown date');
  function priceMap(s) {
    const rows = levelRows(s);
    const i = s.indicators || {};
    const hasRange = isNum(i.lo252) && isNum(i.hi252) && i.hi252 > i.lo252 && isNum(s.price);
    if ((!rows.length && !hasRange) || !isNum(s.price)) return null;
    const all = [...rows, { kind: 'now', label: 'Price now', v: s.price }].sort((a, b) => b.v - a.v);
    const table = rows.length ? h('table', { class: 'leveltable' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Level'), h('th', { scope: 'col', class: 'num' }, 'Price'), h('th', { scope: 'col', class: 'num' }, 'From price'))),
      h('tbody', null, all.map((r) => h('tr', { class: `l-${r.kind}` },
        h('th', { scope: 'row' }, r.label), h('td', { class: 'num' }, money(r.v, s.currency)),
        h('td', { class: 'num gap' }, r.kind === 'now' ? `as of ${shortDay(s.as_of)}` : signed((r.v / s.price - 1) * 100, 1)))))) : null;
    let range = null;
    if (hasRange) {
      const pos = clamp((s.price - i.lo252) / (i.hi252 - i.lo252) * 100, 0, 100);
      range = h('div', { class: 'rangebox' },
        h('div', { class: 'rangetop small' }, h('span', { class: 'muted' }, '52-week range'), h('span', null, `${pos.toFixed(0)}% of the way up`)),
        h('div', { class: 'rangebar', role: 'img', 'aria-label': `Price is at ${pos.toFixed(0)} percent of its 52-week range` }, h('i', { style: `left:${pos}%` })),
        h('div', { class: 'rangeends small' }, h('span', null, money(i.lo252, s.currency)), h('span', null, money(i.hi252, s.currency))));
    }
    const help = rows.length ? h('details', { class: 'mapnote small' }, h('summary', null, 'What do these levels mean?'),
      h('p', { class: 'muted', style: 'margin:4px 0 0' }, 'Resistance: rises have stalled here before. Support: falls have paused here before. Risk level: two average daily ranges below the price. They come from fixed rules on past prices and are references, not advice.')) : null;
    return h('div', { class: 'pmap' }, table, range, help);
  }

  function positionCalculator(s) {
    const out = h('p', { class: 'small', 'aria-live': 'polite' });
    const capital = h('input', { type: 'number', min: '0', step: 'any', value: S.calc.capital, 'aria-label': 'Capital' });
    const risk = h('input', { type: 'number', min: '0.1', max: '100', step: '0.1', value: S.calc.risk, 'aria-label': 'Risk per trade in percent' });
    const update = () => {
      const cap = parseFloat(capital.value), pct = parseFloat(risk.value);
      if (!(cap > 0) || !(pct > 0 && pct <= 100)) { out.textContent = 'Enter a capital above 0 and a risk between 0.1% and 100%.'; return; }
      S.calc = { capital: cap, risk: pct }; store.set('calc', S.calc);
      if (!s.stop) { out.textContent = 'No volatility risk level is available for this stock, so a position size cannot be worked out.'; return; }
      const perShare = s.price - s.stop.price;
      const shares = Math.floor((cap * pct / 100) / perShare);
      const value = shares * s.price;
      out.textContent = `If the price reaches the volatility risk level at ${money(s.stop.price, s.currency)} you lose about ${money(cap * pct / 100, s.currency)} (${pct}% of capital). That allows ${shares} share${shares === 1 ? '' : 's'}, a position of ${money(value, s.currency)} (${(value / cap * 100).toFixed(0)}% of capital).` + (value > cap ? ' This is larger than your capital: use a smaller risk or a wider risk level.' : '');
    };
    capital.addEventListener('input', update); risk.addEventListener('input', update);
    update();
    return section('Position size', h('div', { class: 'row' },
      h('label', { class: 'field' }, `Capital (${CURRENCY[s.currency] || s.currency || ''})`, capital), h('label', { class: 'field' }, 'Risk per trade (%)', risk)), out,
      h('p', { class: 'muted small' }, 'Prices can gap past the risk level, so the real loss can be bigger.'));
  }

  function backtestBlock(b, title) {
    if (!b) return h('div', null, h('h3', null, title), h('p', { class: 'muted small' }, 'Not enough history to test this rule.'));
    const o = b.oos;
    const years = Math.round(b.tested_days / 252 * 10) / 10;
    const verdict = b.total_return > b.buy_hold_return
      ? `Over the whole ${years} years the rule beat buy-and-hold.` : `Over the whole ${years} years the rule did NOT beat buy-and-hold.`;
    const recent = o.total_return > o.buy_hold_return ? 'It also beat it in the most recent 40%.' : 'It did not beat it in the most recent 40% either.';
    const both = (b.total_return > b.buy_hold_return) === (o.total_return > o.buy_hold_return);
    return h('div', null, h('h3', null, title),
      h('p', { class: b.total_return > b.buy_hold_return ? 'pos' : 'neg' }, verdict, ' ', both ? recent : (o.total_return > o.buy_hold_return ? 'It did beat it in the most recent 40%.' : 'It did not beat it in the most recent 40%.')),
      b.few_trades ? h('p', { class: 'tag warn' }, 'Few trades, so the result is not reliable') : null,
      h('table', null, h('thead', null, h('tr', null, h('th', null, ''), h('th', { class: 'num' }, 'Rule (after costs)'), h('th', { class: 'num' }, 'Buy and hold'))),
        h('tbody', null,
          h('tr', null, h('td', null, `Whole test, total`), h('td', { class: 'num' }, percent(b.total_return)), h('td', { class: 'num' }, percent(b.buy_hold_return))),
          h('tr', null, h('td', null, 'Whole test, per year'), h('td', { class: 'num' }, percent(b.cagr)), h('td', { class: 'num' }, percent(b.buy_hold_cagr))),
          h('tr', null, h('td', null, 'Recent 40%, total'), h('td', { class: 'num' }, percent(o.total_return)), h('td', { class: 'num' }, percent(o.buy_hold_return))),
          b.total ? [h('tr', { class: 'divrow' }, h('td', null, 'With dividends: whole test'), h('td', { class: 'num' }, percent(b.total.total_return)), h('td', { class: 'num' }, percent(b.total.buy_hold_return))),
            h('tr', { class: 'divrow' }, h('td', null, 'With dividends: recent 40%'), h('td', { class: 'num' }, percent(b.total.recent_total_return)), h('td', { class: 'num' }, percent(b.total.recent_buy_hold_return)))] : null)),
      kv([['Before costs', percent(b.gross_return)], ['Cost drag', percent(b.cost_drag)], ['Trades', b.trades], ['Win rate', percent(b.win_rate, 0)], ['Profit factor', number(b.profit_factor)],
        ['Average trade', percent(b.expectancy)], ['Worst fall', percent(b.max_drawdown, 0)], ['Sharpe', number(b.sharpe)], ['Sortino', number(b.sortino)], ['Time invested', percent(b.exposure, 0)]]),
      b.open_at_end ? h('p', { class: 'muted small' }, 'A position was still open at the end; it is counted as sold at the last close, less costs.') : null);
  }

  /* The assumptions and limits behind every backtest figure, published with the data. */
  function backtestMethod(s) {
    const m = S.data.backtest_method;
    if (!m) return null;
    const c = (m.costs || {})[s.market === 'IN' ? 'IN' : 'US'];
    return h('details', { class: 'card wide' }, h('summary', null, 'Assumptions and limits of these tests'),
      c ? h('p', { class: 'small' }, `Costs assumed for this market: ${c.entry_pct}% to buy and ${c.exit_pct}% to sell (${c.round_trip_pct}% for a round trip), including taxes, fees and slippage. These are assumptions: your broker's charges differ.`) : null,
      h('ul', { class: 'clean small' }, (m.notes || []).map((n) => h('li', null, n))));
  }

  function fundamentalsBlock(s) {
    const f = s.fundamentals || {};
    if (!Object.keys(f).length && !isNum(s.dividend_yield)) return h('p', { class: 'muted small' }, 'Fundamentals are not available for this stock right now.');
    const cur = s.currency;
    const group = (title, pairs) => h('div', { style: 'margin-bottom:10px' }, h('h4', { style: 'margin:8px 0 4px' }, title), kv(pairs));
    return h('div', null,
      group('Valuation', [['Market cap', big(f.market_cap, cur)], ['Enterprise value', big(f.enterprise_value, cur)], ['P/E (trailing)', number(f.pe, 1)], ['Forward P/E', number(f.forward_pe, 1)],
        ['PEG', number(f.peg, 2)], ['Price/Book', number(f.pb, 1)], ['Price/Sales', number(f.price_to_sales, 1)], ['EV/EBITDA', number(f.ev_ebitda, 1)], ['EV/Revenue', number(f.ev_revenue, 1)]]),
      group('Earnings and margins', [['Revenue', big(f.revenue, cur)], ['EBITDA', big(f.ebitda, cur)], ['EBITDA margin', percent(f.ebitda_margin)], ['Operating margin', percent(f.operating_margin)],
        ['Gross margin', percent(f.gross_margin)], ['Profit margin', percent(f.profit_margin)], ['EPS (trailing)', number(f.eps, 2)], ['Revenue growth', percent(f.revenue_growth)],
        ['Earnings growth', percent(f.earnings_growth)]]),
      group('Balance sheet and returns', [['Return on equity', percent(f.roe)], ['Return on assets', percent(f.roa)], ['Debt/Equity', number(f.debt_to_equity)], ['Total debt', big(f.total_debt, cur)],
        ['Cash', big(f.total_cash, cur)], ['Current ratio', number(f.current_ratio, 2)], ['Free cash flow', big(f.free_cash_flow, cur)], ['Operating cash flow', big(f.operating_cash_flow, cur)],
        ['Book value per share', number(f.book_value, 2)]]),
      group('Shareholder returns', [['Dividend yield (12 months)', percent(s.dividend_yield, 2)], ['Payout ratio', percent(f.payout_ratio, 0)]]),
      h('p', { class: 'muted small' }, 'Figures are as reported by Yahoo Finance for the last twelve months, in the company\'s reporting currency. A blank (n/a) means Yahoo has no value; nothing is estimated. They are shown as they are: no rating or "cheap/expensive" label is made from them.'));
  }

  function dividendBlock(s) {
    const d = s.dividend;
    if (!d) return h('p', { class: 'muted small' }, 'No dividend was paid in the last two years.');
    const per = d.payments_per_year;
    const plural = (n) => (n === 1 ? '' : 's');
    return h('div', null,
      kv([
        ['Payment frequency', d.frequency + (per ? ` (${per} a year)` : '')],
        ['Paid in last 12 months', `${d.ttm_count} payment${plural(d.ttm_count)}, ${money(d.ttm_amount, s.currency)} per share`],
        ['Yield on 12-month payments', percent(d.ttm_yield, 2)],
        ['Recurring yearly dividend', isNum(d.forward_amount) ? `${money(d.forward_amount, s.currency)} per share` : 'Cannot be estimated'],
        ['Yield on recurring dividend', isNum(d.forward_yield) ? percent(d.forward_yield, 2) : '–'],
        ['Last payment', `${money(d.last_amount, s.currency)} on ${d.last_date} (${percent(d.last_pct_of_price, 2)} of price)`],
        ['Change vs previous 12 months', isNum(d.growth) ? signed(d.growth * 100, 1) : 'Not comparable'],
        ['Payout ratio', percent((s.fundamentals || {}).payout_ratio, 0)]]),
      d.overdue ? h('p', { class: 'tag warn' }, 'A payment looks overdue. The usual schedule says one should have been paid by now, so the dividend may have been cut or paused.') : null,
      d.special_count ? h('p', { class: 'tag' }, `${d.special_count} one-off special dividend${plural(d.special_count)} in the last 12 months, left out of the recurring figure`) : null,
      h('table', null, h('thead', null, h('tr', null, h('th', null, 'Paid on'), h('th', { class: 'num' }, 'Per share'), h('th', null, 'Type'))),
        h('tbody', null, (d.history || []).map((p) => h('tr', null, h('td', null, p.date), h('td', { class: 'num' }, money(p.amount, s.currency)), h('td', null, p.special ? 'Special (one-off)' : 'Regular'))))),
      h('p', { class: 'muted small' }, 'Worked out from payments actually made: amount per payment times the number of payments, divided by the current price. The yield moves with the share price, and a past dividend can be cut.'));
  }

  function peersBlock(s) {
    if (s.sector === 'Other') return h('p', { class: 'muted small' }, 'The exchange does not publish a sector for this stock, so there is no peer group to compare.');
    const peers = S.data.stocks.filter((p) => p.sector === s.sector && p.market === s.market && p.symbol !== s.symbol)
      .sort((a, b) => ((b.fundamentals || {}).market_cap || 0) - ((a.fundamentals || {}).market_cap || 0)).slice(0, 5);
    if (!peers.length) return h('p', { class: 'muted small' }, 'No peers in the same sector.');
    const row = (p, me) => h('tr', { style: me ? 'font-weight:700' : '' }, h('td', null, p.name), h('td', { class: 'num' }, number((p.fundamentals || {}).pe, 1)),
      h('td', { class: 'num hide-sm' }, percent((p.fundamentals || {}).roe, 0)), h('td', { class: 'num hide-sm' }, percent(p.dividend_yield)), h('td', null, badge(p.long)));
    return h('div', { style: 'overflow-x:auto' }, h('table', null,
      h('thead', null, h('tr', null, h('th', null, 'Company'), h('th', { class: 'num' }, 'P/E'), h('th', { class: 'num hide-sm' }, 'ROE'), h('th', { class: 'num hide-sm' }, 'Dividend'), h('th', null, 'Long-term'))),
      h('tbody', null, row(s, true), peers.map((p) => row(p, false)))));
  }

  function pressureBlock(s) {
    const parts = [];
    if (s.pressure) {
      parts.push(h('p', null, h('b', null, s.pressure.label), ` over the last 20 days. Money flow ${number(s.pressure.money_flow)} (from -1 selling to +1 buying); ${percent(s.pressure.up_volume_share, 0)} of volume traded on rising days.`));
    } else parts.push(h('p', { class: 'muted' }, 'No volume data to measure pressure.'));
    if (s.delivery) parts.push(h('p', { class: s.delivery.tone }, `NSE delivery: ${s.delivery.latest.toFixed(0)}% of shares were taken for delivery on ${s.delivery.date} (recent average ${s.delivery.average.toFixed(0)}%). ${s.delivery.text}.`));
    if (s.deals) {
      parts.push(h('p', null, `Exchange bulk/block deals on ${s.deals.date}: ${big(s.deals.buy_value, s.currency)} bought, ${big(s.deals.sell_value, s.currency)} sold.`));
      parts.push(h('ul', { class: 'clean small' }, s.deals.deals.map((d) => h('li', null, `${d.side === 'BUY' ? 'Bought' : 'Sold'} ${d.qty.toLocaleString()} at ${d.price} by ${d.client} (${d.kind})`))));
    }
    parts.push(h('p', { class: 'muted small' }, 'This is volume-based evidence of buying and selling. The exchange order book (bid and ask depth) is not published for free, so it is not shown.'));
    return parts;
  }

  function listsBlock(s) {
    const names = Object.keys(S.lists);
    const boxes = names.map((name) => {
      const input = h('input', { type: 'checkbox', checked: S.lists[name].includes(s.symbol), onchange: (e) => {
        const set = new Set(S.lists[name]); e.target.checked ? set.add(s.symbol) : set.delete(s.symbol);
        S.lists[name] = [...set]; store.set('lists', S.lists); updateSignals();
      } });
      return h('label', { class: 'chip' }, input, ' ', name);
    });
    const watch = h('label', { class: 'chip' }, h('input', { type: 'checkbox', checked: S.watch.has(s.symbol), onchange: (e) => {
      e.target.checked ? S.watch.add(s.symbol) : S.watch.delete(s.symbol); saveWatch(); updateSignals();
    } }), ' My watchlist (alerts)');
    return h('div', { class: 'row', style: 'gap:6px' }, watch, boxes, !names.length ? h('span', { class: 'muted small' }, 'Create more lists on the Signals tab with "+ New list".') : null);
  }

  /* ---------- stock page: glance first, then tabs ---------- */
  const DETAIL_TABS = [['overview', 'Overview'], ['technicals', 'Technicals'], ['fundamentals', 'Fundamentals'], ['news', 'News & events'], ['backtest', 'Backtest']];
  const SECTION_TAB = { signals: 'overview', 'price-chart': 'overview', 'recent-alerts-for-this-stock': 'overview',
    'buying-and-selling-pressure': 'technicals', 'position-size': 'technicals', 'levels-and-risk': 'technicals', patterns: 'technicals',
    fundamentals: 'fundamentals', dividends: 'fundamentals', 'peers-in-the-same-sector': 'fundamentals', lists: 'fundamentals',
    news: 'news', 'upcoming-events': 'news', 'latest-headlines': 'news', 'how-have-these-rules-worked-on-this-stock': 'backtest' };

  function activateDetailTab(tab) {
    const dlg = $('#detail');
    for (const b of dlg.querySelectorAll('.dtab')) b.setAttribute('aria-selected', String(b.dataset.tab === tab));
    for (const p of dlg.querySelectorAll('.tabpanel')) p.hidden = p.dataset.tab !== tab;
    S.detailTab = tab;
  }

  const changeChip = (pct) => (isNum(pct) ? h('span', { class: `chg ${pct >= 0 ? 'up' : 'down'}` }, `${pct >= 0 ? '▲' : '▼'} ${signed(pct)}`) : null);

  function verdictCard(title, sig) {
    const pos = isNum(sig.score) ? clamp((sig.score + 100) / 2, 0, 100) : 50;
    return h('button', { type: 'button', class: 'verdict', onclick: () => { activateDetailTab('overview'); const t = document.getElementById('d-signals'); if (t) t.scrollIntoView({ block: 'start', behavior: 'smooth' }); } },
      h('span', { class: 'muted small' }, title), badge(sig),
      isNum(sig.score) ? h('div', { class: 'meter', 'aria-hidden': 'true' }, h('i', { style: `left:${pos}%` })) : null);
  }

  /* One line that answers "which way do the checks lean?": both views, and how many of the fixed checks point up, down or neither. */
  function verdictBlock(s) {
    const checks = [...(s.short.reasons || []), ...(s.long.reasons || [])];
    const up = checks.filter((c) => c.tone === 'pos').length, down = checks.filter((c) => c.tone === 'neg').length, flat = checks.length - up - down;
    const sides = [side(s.short.label), side(s.long.label)];
    const lean = sides[0] === sides[1] ? { buy: 'Both views aligned bullish', sell: 'Both views aligned bearish', hold: 'Both views mixed' }[sides[0]] : 'The two views differ';
    const part = (n, k) => (n ? h('i', { class: k, style: `flex:${n}` }) : null);
    return h('div', { class: 'vblock' },
      h('div', { class: 'verdicts' }, verdictCard('Weeks view', s.short), verdictCard('Months-to-years view', s.long)),
      checks.length ? h('div', { class: 'vsum' },
        h('div', { class: 'small' }, h('b', null, lean), h('span', { class: 'muted' }, ` \u00B7 ${up} of ${checks.length} checks point up, ${down} down, ${flat} neutral`)),
        h('div', { class: 'stackbar', role: 'img', 'aria-label': `${up} checks point up, ${down} point down, ${flat} neutral` }, part(up, 'u'), part(flat, 'n'), part(down, 'd'))) : null);
  }

  /* Small data-check tag for the top of the page; the full sentence is at the bottom of the Overview tab. */
  function qualityTag(s) {
    const q = s.quality;
    if (!q) return h('span', { class: 'tag', title: 'No second price source covers this stock' }, 'Price not cross-checked');
    const [cls, text] = q.status === 'match' ? ['buy', 'Price cross-checked'] : q.status === 'minor' ? ['', 'Small price difference'] : ['warn', 'Prices disagree'];
    return h('span', { class: `tag ${cls}`, title: `${q.max_diff_pct}% largest difference against ${q.source}` }, text);
  }

  /* A figure that is not available is shown as "n/a" with the reason, never as zero. */
  const naOr = (v) => (v === DASH || v === '–' ? h('span', { class: 'na', title: 'Not available from the data source for this stock right now' }, 'n/a') : v);

  function statsGrid(s) {
    const f = s.fundamentals || {};
    const items = [['Market cap', big(f.market_cap, s.currency)], ['P/E', number(f.pe, 1)], ['Dividend yield', percent(s.dividend_yield, 2)],
      ['ROE', percent(f.roe, 0)], ['Beta', s.risk ? number(s.risk.beta) : DASH], ['Volatility / yr', s.risk ? percent(s.risk.annual_vol, 0) : DASH]];
    return h('div', { class: 'stats' }, items.map(([k, v]) => h('div', null, h('span', { class: 'muted small' }, k), h('b', null, naOr(v)))));
  }

  function glanceChips(s) {
    const chips = [];
    const next = ((S.data.events || {}).items || []).find((e) => e.symbol === s.symbol && daysUntil(e.until || e.date) >= 0);
    if (next) chips.push(h('button', { type: 'button', class: 'chip', onclick: () => activateDetailTab('news') }, `Next: ${EVENT_LABELS[next.type] || next.type} ${inDays(Math.max(0, daysUntil(next.date)))}`));
    if (s.news.fresh.length) chips.push(h('button', { type: 'button', class: 'chip', onclick: () => activateDetailTab('news') }, `${s.news.fresh.length} new headline${s.news.fresh.length === 1 ? '' : 's'}`));
    if (s.dividend && s.dividend.overdue) chips.push(h('span', { class: 'tag warn' }, 'Dividend overdue'));
    if (s.new) chips.push(h('span', { class: 'tag warn' }, 'New listing: few signals'));
    if (s.stale) chips.push(h('span', { class: 'tag warn' }, 'Price looks stale'));
    if (s.provisional) chips.push(h('span', { class: 'tag warn', title: "Today's candle is unfinished. Readings use the last completed day." }, 'Market open: provisional'));
    return chips.length ? h('div', { class: 'chips-row' }, chips) : null;
  }

  function detailView(s) {
    const bt = s.backtest || {};
    const r = s.risk;
    const hero = h('div', { class: 'hero' },
      h('div', { class: 'herotags chips-row' }, h('span', { class: 'tag' }, s.market === 'IN' ? 'India' : 'US'), s.exchange ? h('span', { class: 'tag' }, s.exchange) : null, s.sector ? h('span', { class: 'tag' }, s.sector) : null,
        qualityTag(s)),
      verdictBlock(s),
      priceMap(s), statsGrid(s), glanceChips(s));

    const panels = {
      overview: [
        h('p', { class: 'advisory wide' }, h('strong', null, 'Advisory: '), 'Not investment advice. Readings and levels come from fixed rules on past prices; the publisher is not a SEBI-registered adviser. ', h('a', { href: 'advisory.html', target: '_blank', rel: 'noopener' }, 'Full disclaimer')),
        alertsForStock(s),
        h('div', { class: 'grid wide', id: 'd-signals' }, h('section', { class: 'card' }, signalCard('Short-term view (weeks)', s.short)), h('section', { class: 'card' }, signalCard('Long-term view (months to years)', s.long))),
        dataNote(s),
        h('div', { class: 'wide' }, qualityLine(s))],
      technicals: [
        section('Buying and selling pressure', ...pressureBlock(s)),
        positionCalculator(s),
        section('Levels and risk', h('p', { class: 'small muted' }, 'The resistance, support and risk levels are listed at the top of the page. They come from fixed rules on past prices and are references, not promises or advice. Below are the volatility figures.'), kv([
          ['Yearly volatility', r ? percent(r.annual_vol, 0) : '–'], ['1-day loss (95% VaR)', r ? percent(r.var95) : '–'],
          ['Bad-day average loss', r ? percent(r.expected_shortfall95) : '–'], ['Worst fall (1 year)', r ? percent(r.max_drawdown, 0) : '–'], ['Beta', r ? number(r.beta) : '–']])),
        section('Patterns', (s.patterns || []).length ? (s.patterns).map((p) => h('div', { title: p.note, style: 'margin-bottom:6px' }, h('span', { class: `tag ${p.bias === 'bearish' ? 'warn' : ''}` }, `${p.name} (${p.bias})`), h('span', { class: 'muted small' }, p.note))) : h('p', { class: 'muted small' }, 'No notable pattern on the latest candles.'))],
      fundamentals: [
        wide(section('Fundamentals', fundamentalsBlock(s))),
        wide(section('Dividends', dividendBlock(s))),
        wide(section('Peers in the same sector', peersBlock(s))),
        section('Lists', listsBlock(s))],
      news: [
        s.news.fresh.length ? h('section', { class: 'card wide', id: 'd-latest-headlines' }, h('h3', null, 'Latest headlines'), newsList(s.news.fresh)) : null,
        wide(section('Upcoming events', stockEvents(s))),
        section('News', s.news.error && !s.news.items.length
          ? h('p', { class: 'muted small' }, 'Headlines are not looked up for this stock on every run. When one is published it appears in the alerts, and in Telegram if the stock is covered there.')
          : newsList(s.news.items))],
      backtest: [
        wide(section('How have these rules worked on this stock?', h('div', { class: 'grid' }, backtestBlock(bt.short, 'Short-term rule'), backtestBlock(bt.long, 'Long-term rule')),
          h('p', { class: 'muted small' }, 'Fixed rules tested on up to 5 years of this stock, trading at the next open with assumed costs. Past results do not guarantee future results.'))),
        backtestMethod(s)],
    };
    return h('div', { class: 'detail' },
      h('div', { class: 'dbar' },
        h('div', { class: 'dname' }, h('b', null, s.name), ' ', h('span', { class: 'muted small' }, s.symbol)),
        h('div', { class: 'dpx' }, h('span', { class: 'px' }, money(s.price, s.currency)), changeChip(s.change_pct)),
        h('button', { type: 'button', class: 'dclose', 'aria-label': 'Close', onclick: closeDetail }, '✕')),
      hero,
      h('div', { class: 'dtabs', role: 'tablist', 'aria-label': 'Sections' }, DETAIL_TABS.map(([id, label]) => h('button', {
        type: 'button', class: 'dtab', role: 'tab', 'data-tab': id, 'aria-selected': 'false',
        onclick: () => { activateDetailTab(id); const d = $('#detail'); const bar = d.querySelector('.dtabs'); if (bar && d.scrollTop > bar.offsetTop) d.scrollTo({ top: bar.offsetTop - 48 }); } }, label))),
      DETAIL_TABS.map(([id]) => h('div', { class: 'dbody tabpanel', 'data-tab': id, role: 'tabpanel', hidden: true }, panels[id])));
  }

  /* ---------- suggestions tab ---------- */
  function suggestions() {
    const p = S.profile;
    const maxVol = { low: 0.30, medium: 0.45, high: 9 }[p.risk];
    const picks = [];
    for (const s of S.data.stocks) {
      if (p.market !== 'ALL' && s.market !== p.market) continue;
      if (s.stale || s.new || s.bars < 260 || !isNum(s.short.score) || !isNum(s.long.score)) continue;
      const vol = s.risk && s.risk.annual_vol;
      if (!isNum(vol) || vol > maxVol) continue;
      const blend = p.horizon === 'short' ? 0.7 * s.short.score + 0.3 * s.long.score : 0.3 * s.short.score + 0.7 * s.long.score;
      if (blend < 15) continue;
      const f = s.fundamentals || {};
      const quality = IPO.qualityFromFundamentals(f, null);
      const q = isNum(quality.score) ? quality.score / 100 : null;       // null = fundamentals unavailable: left out, not scored as zero
      // Cautious yield: the lower of what was paid and what the recurring payments imply. Overdue payers count as zero.
      const div = s.dividend;
      const cautious = div && isNum(div.forward_yield) ? Math.min(s.dividend_yield || 0, div.forward_yield) : (s.dividend_yield || 0);
      const dividend = div && div.overdue ? 0 : Math.min(cautious / 0.06, 1);
      const growth = isNum(f.revenue_growth) ? clamp(f.revenue_growth / 0.3, -1, 1) : null;
      const momentumRaw = (s.indicators || {}).momentum126;
      const momentum = isNum(momentumRaw) ? clamp(momentumRaw / 0.3, -1, 1) : null;
      const calm = clamp(1 - vol / 0.5, 0, 1);
      // A missing figure is left out and the weights of the others are scaled up, so unavailable data neither helps nor hurts a stock.
      const mix = (parts) => { const have = parts.filter(([, x]) => x !== null); return have.reduce((a, [w, x]) => a + w * x, 0) / have.reduce((a, [w]) => a + w, 0); };
      const income = mix([[0.3, dividend], [0.3, blend / 100], [0.2, calm], [0.2, q]]);
      const growing = mix([[0.3, blend / 100], [0.25, momentum], [0.2, growth], [0.25, q]]);
      const score = p.goal === 'income' ? income : p.goal === 'growth' ? growing : (income + growing) / 2;
      picks.push({ s, score, blend, vol, quality, dividend: div && div.overdue ? 0 : cautious, growthRaw: f.revenue_growth });
    }
    return picks.sort((a, b) => b.score - a.score).slice(0, 10);
  }

  function suggestionCard(pick, rank) {
    const { s } = pick;
    const why = [`${S.profile.horizon === 'short' ? 'Short' : 'Long'}-term reading: ${labelText(s[S.profile.horizon].label)}`];
    if (pick.dividend >= 0.02) why.push(`Dividend yield ${percent(pick.dividend, 1)}${pick.s.dividend && pick.s.dividend.payments_per_year ? ' (' + pick.s.dividend.frequency.toLowerCase() + ')' : ''}`);
    if (pick.vol < 0.25) why.push(`Low volatility (${percent(pick.vol, 0)})`);
    if (isNum(pick.growthRaw) && pick.growthRaw > 0.1) why.push(`Revenue growth ${percent(pick.growthRaw, 0)}`);
    if (pick.quality.score !== null && pick.quality.score >= 30) why.push('Healthy fundamentals');
    if (!isNum(pick.quality.score)) why.push('Fundamentals unavailable, so they were left out of the ranking');
    if (s.pressure && s.pressure.label.startsWith('Buying')) why.push('Buying pressure');
    if (s.lists.includes('Strong delivery buying')) why.push('Strong delivery buying');
    const risks = [`Moves about ${percent(pick.vol, 0)} a year`];
    if (s.stop) risks.push(`volatility risk level ${signed(s.stop.pct, 1)} below the price`);
    const shortTest = (s.backtest || {}).short;
    if (shortTest && !shortTest.beats_buy_hold_oos) risks.push('the short-term rule has lagged buy-and-hold recently');
    const card = h('div', { class: 'card', tabindex: '0', style: 'cursor:pointer' },
      h('div', { class: 'row', style: 'justify-content:space-between;align-items:center' },
        h('div', null, h('b', null, `${rank}. ${s.name}`), h('span', { class: 'muted small' }, ` ${s.symbol}`)), badge(s[S.profile.horizon])),
      h('div', null, why.map((w) => h('span', { class: 'tag' }, w))),
      h('p', { class: 'small muted', style: 'margin:6px 0 0' }, 'Main risks: ' + risks.join('; ') + '.'));
    card.addEventListener('click', () => openDetail(s.symbol, card));
    card.addEventListener('keydown', (e) => { if (e.key === 'Enter') openDetail(s.symbol, card); });
    return card;
  }

  function viewSuggest() {
    const p = S.profile;
    const results = h('div');
    const draw = () => {
      store.set('profile', S.profile);
      const picks = suggestions();
      results.replaceChildren(...(picks.length ? picks.map((pick, i) => suggestionCard(pick, i + 1))
        : [h('div', { class: 'empty' }, 'No stock passes your filters right now. Try a higher risk level or both markets. The app skips stocks with a weak signal or too little history.')]));
    };
    const form = h('div', { class: 'card' }, h('div', { class: 'row' },
      select('My goal', p.goal, [['income', 'Regular income (dividends)'], ['growth', 'Growth'], ['balanced', 'Balanced']], (v) => { p.goal = v; draw(); }),
      select('Time horizon', p.horizon, [['short', 'Short (weeks)'], ['long', 'Long (months to years)']], (v) => { p.horizon = v; draw(); }),
      select('Risk I can accept', p.risk, [['low', 'Low (steady stocks)'], ['medium', 'Medium'], ['high', 'High']], (v) => { p.risk = v; draw(); }),
      select('Market', p.market, [['ALL', 'Both'], ['IN', 'India'], ['US', 'United States']], (v) => { p.market = v; draw(); })),
      h('p', { class: 'muted small', style: 'margin-bottom:0' }, 'A ranked screen using your answers, signals, fundamentals and risk. It is not personal advice. Check the stock page and your own situation before acting.'));
    queueMicrotask(draw);
    return h('div', null, h('h2', null, 'Stock screens'), form, results);
  }

  /* ---------- IPO tab ---------- */
  function ipoChecker() {
    const sectors = [...new Set(S.data.stocks.map((s) => s.sector))].filter((n) => n !== 'Other' && n !== 'New listings').sort();
    const fields = {};
    const input = (key, label, hint, extra) => {
      fields[key] = h('input', Object.assign({ type: 'number', step: 'any', id: 'ipo-' + key, 'aria-label': label, 'aria-describedby': 'ipo-' + key + '-hint' }, extra || {}));
      return h('label', { class: 'field' }, label, fields[key], h('span', { id: 'ipo-' + key + '-hint', class: 'small' }, hint));
    };
    const result = h('div', { id: 'ipoResult', 'aria-live': 'polite' });
    const dateField = (key, label) => {
      fields[key] = h('input', { type: 'date', id: 'ipo-' + key, 'aria-label': label });
      return h('label', { class: 'field' }, label, fields[key]);
    };
    const sector = h('select', { 'aria-label': 'Sector for peer P/E' }, h('option', { value: '' }, 'Pick a sector'), sectors.map((n) => h('option', { value: n }, n)));
    const marketSel = h('select', { 'aria-label': 'Market for peer P/E' }, h('option', { value: 'IN' }, 'India'), h('option', { value: 'US' }, 'United States'));
    const fillPeer = () => {
      const pes = S.data.stocks.filter((s) => s.sector === sector.value && s.market === marketSel.value).map((s) => (s.fundamentals || {}).pe);
      const m = IPO.median(pes);
      if (m === null) { toast('No peer P/E data for that sector'); return; }
      fields.peerPe.value = m.toFixed(1);
    };
    const read = (key) => { const v = parseFloat(fields[key].value); return Number.isFinite(v) ? v : null; };
    const run = (e) => {
      e.preventDefault();
      const draft = { name: S.ipoName, open: fields.open.value, close: fields.close.value, allotment: fields.allotment.value, listing: fields.listing.value,
        priceHigh: read('priceHigh'), lot: read('lot'), gmp: read('gmp') };
      const gmpPct = draft.gmp !== null && draft.priceHigh > 0 ? (draft.gmp / draft.priceHigh) * 100 : null;
      const input = { revenueGrowth: read('revenueGrowth'), profitMargin: read('profitMargin'), debtToEquity: read('debtToEquity'), roe: read('roe'),
        pe: read('pe'), peerPe: read('peerPe'), ofsShare: read('ofsShare'), qibSubscription: read('qibSubscription'), gmp: gmpPct };
      const v = IPO.ipoVerdict(input);
      const cls = v.label === 'Subscribe' ? 'buy' : v.label === 'Avoid' ? 'sell' : '';
      const names = { profitability: 'profit margin', growth: 'revenue growth', leverage: 'debt', returns: 'return on equity', valuation: 'valuation (P/E and peer P/E)', promoterExit: 'offer-for-sale share', demand: 'QIB subscription', grey: 'grey-market premium' };
      result.replaceChildren(h('div', { class: `card result ${cls}` },
        h('h3', null, `${S.ipoName ? S.ipoName + ': ' : ''}${labelText(v.label)}`, isNum(v.score) ? ` (score ${v.score > 0 ? '+' : ''}${v.score})` : ''),
        isNum(v.score) ? h('p', null, 'Listing-day and short-term view: ', h('b', null, labelText(v.label)), '. Long-term holding view: ', badge({ label: v.longTerm.label, score: v.longTerm.score }), '.') : h('p', null, 'Fill in at least profit margin, revenue growth, debt or return on equity, plus the valuation fields, to get a verdict.'),
        reasonsList(v.reasons),
        v.missing.length ? h('p', { class: 'muted small' }, 'Not used (no input): ' + v.missing.map((k) => names[k]).join(', ') + '.') : null,
        h('p', { class: 'muted small' }, 'Take the figures from the red herring prospectus (RHP) or the issue note. Peer P/E is the median of listed companies in the same sector. Subscribe means the numbers look healthy, not that the listing price will rise.')));
      result.append(ipoDetails(draft, v));
    };
    const name = h('input', { type: 'text', maxlength: '60', placeholder: 'Optional', 'aria-label': 'IPO name', oninput: (e) => { S.ipoName = e.target.value.trim(); } });
    name.value = S.ipoName;
    return h('form', { class: 'card', onsubmit: run },
      h('h2', null, 'IPO checker'),
      h('p', { class: 'muted small' }, 'Free sources do not publish an unlisted company\'s financials, so enter the figures from the IPO documents. The same rules score every IPO in the same way.'),
      h('div', { class: 'row', style: 'align-items:start' }, h('label', { class: 'field' }, 'IPO name', name),
        input('revenueGrowth', 'Revenue growth (%)', 'Last year over the year before'), input('profitMargin', 'Profit margin (%)', 'Profit after tax / revenue; negative if loss'),
        input('debtToEquity', 'Debt / equity (x)', 'For example 0.4'), input('roe', 'Return on equity (%)', 'Profit / shareholders\' funds'),
        input('pe', 'P/E at upper price band', 'Price / earnings per share'), input('peerPe', 'Peer P/E', 'Use the sector median below'),
        input('ofsShare', 'Offer for sale (% of issue)', 'Existing holders selling'), input('qibSubscription', 'QIB subscription (x)', 'Fill after the book closes')),
      h('h3', { style: 'margin-top:14px' }, 'Dates, price and grey-market premium'),
      h('p', { class: 'muted small' }, 'Optional. Take the dates, price band and lot size from the issue notice. Allotment and listing dates are estimated if left blank.'),
      h('div', { class: 'row', style: 'align-items:start' },
        dateField('open', 'Opens'), dateField('close', 'Closes'), dateField('allotment', 'Allotment (optional)'), dateField('listing', 'Listing (optional)'),
        input('priceHigh', 'Upper price band', 'Price per share'), input('lot', 'Lot size (shares)', 'Minimum shares per application'),
        input('gmp', 'Grey-market premium per share', 'Unofficial and can be wrong; negative if a discount')),
      h('div', { class: 'row', style: 'margin-top:10px' }, h('label', { class: 'field' }, 'Sector', sector), h('label', { class: 'field' }, 'Market', marketSel),
        h('button', { type: 'button', class: 'btn ghost', onclick: fillPeer }, 'Use sector median P/E'), h('button', { type: 'submit', class: 'btn' }, 'Check this IPO')),
      result);
  }

  const todayIso = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };
  const gainClass = (v) => (isNum(v) ? (v > 0 ? 'pos' : v < 0 ? 'neg' : '') : '');
  const IPO_CURRENCY = '₹';
  const rupees = (v) => (isNum(v) ? `${v < 0 ? '-' : ''}${IPO_CURRENCY}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}` : '–');
  const loadTracker = () => { const list = store.get('ipoTracker', []); return Array.isArray(list) ? list : []; };

  function ipoDetails(entry, verdict) {
    const t = IPO.timeline(entry, todayIso());
    const est = IPO.estimate(entry);
    const saveError = h('span', { class: 'small neg', role: 'alert' });
    const noDates = !entry.open && !entry.close;
    const timeline = t.valid
      ? h('div', { style: 'overflow-x:auto' }, h('p', null, h('span', { class: 'tag' }, t.status)),
        h('table', null, h('thead', null, h('tr', null, ['Opens', 'Closes', 'Allotment', 'Listing'].map((x) => h('th', null, x)))),
          h('tbody', null, h('tr', null, h('td', null, t.open), h('td', null, t.close),
            h('td', null, t.allotment + (t.allotmentEstimated ? ' (est.)' : '')), h('td', null, t.listing + (t.listingEstimated ? ' (est.)' : ''))))))
      : h('p', { class: 'muted small' }, noDates ? 'Add the open and close dates to see the allotment and listing dates.' : `Cannot work out the timeline: ${t.error}`);
    const profit = isNum(est.gainPerShare)
      ? kv([['Upper price band', rupees(entry.priceHigh)], ['Minimum application', isNum(est.applyAmount) ? `${rupees(est.applyAmount)} (${entry.lot} shares)` : 'Enter the lot size'],
        ['Grey-market premium', `${rupees(est.gainPerShare)} per share (${signed(est.gainPct, 1)})`], ['Estimated listing price', rupees(est.listingPrice)],
        ['Estimated profit or loss per share', rupees(est.gainPerShare)], ['Estimated profit or loss per lot', isNum(est.gainPerLot) ? rupees(est.gainPerLot) : 'Enter the lot size']])
      : h('p', { class: 'muted small' }, 'Enter the upper price band and the grey-market premium to estimate the listing price and profit or loss.');
    if (isNum(est.gainPerLot)) profit.querySelectorAll('div b')[5].className = gainClass(est.gainPerLot);
    if (isNum(est.gainPerShare)) profit.querySelectorAll('div b')[4].className = gainClass(est.gainPerShare);
    const save = h('button', { type: 'button', class: 'btn ghost', onclick: () => {
      if (!entry.name) { saveError.textContent = 'Enter the IPO name above to save it.'; return; }
      if (!t.valid) { saveError.textContent = 'Enter valid open and close dates to save it.'; return; }
      saveError.textContent = '';
      const list = loadTracker();
      list.push(Object.assign({ id: Date.now(), label: verdict.label, score: verdict.score }, entry));
      store.set('ipoTracker', list);
      drawTracker();
      toast('Saved to My IPO tracker');
    } }, 'Save to my IPO tracker');
    return h('div', { class: 'card' }, h('h3', null, 'Dates and estimated profit or loss'), timeline, profit,
      h('p', { class: 'muted small' }, 'Estimated listing price is the upper price band plus the grey-market premium. The premium is unofficial, changes daily and can be wrong. Profit only arises if shares are allotted, and it ignores brokerage, taxes and moves on listing day. Estimated allotment and listing dates follow the usual schedule (2 and 3 working days after close, holidays ignored): confirm them in the issue notice.'),
      h('div', { class: 'row' }, save, saveError));
  }

  function drawTracker() {
    const box = $('#ipoTracker');
    if (!box) return;
    const list = loadTracker();
    if (!list.length) { box.replaceChildren(h('div', { class: 'empty card' }, 'No IPOs saved yet. Check an IPO above and save it here to follow its dates and estimated profit.')); return; }
    const today = todayIso();
    const save = (next) => { store.set('ipoTracker', next); drawTracker(); };
    const rows = list.map((item) => ({ item, t: IPO.timeline(item, today), est: IPO.estimate(item) }))
      .sort((a, b) => String(a.item.close).localeCompare(String(b.item.close)));
    box.replaceChildren(h('div', { class: 'card', style: 'padding:0;overflow-x:auto' }, h('table', null,
      h('thead', null, h('tr', null, ['IPO', 'Status', 'Opens', 'Closes', 'Allotment', 'Listing', 'Price band', 'Lot', 'Premium', 'Est. listing price', 'Est. profit or loss per lot', ''].map((x, i) => h('th', { class: i >= 6 && i <= 10 ? 'num' : '' }, x)))),
      h('tbody', null, rows.map(({ item, t, est }) => {
        const gmp = h('input', { type: 'number', step: 'any', value: isNum(item.gmp) ? item.gmp : '', style: 'width:84px', 'aria-label': `Grey-market premium for ${item.name}`,
          onchange: (e) => { const v = parseFloat(e.target.value); save(loadTracker().map((x) => (x.id === item.id ? Object.assign({}, x, { gmp: Number.isFinite(v) ? v : null }) : x))); } });
        return h('tr', null,
          h('td', { class: 'name' }, h('b', null, item.name), item.label ? h('span', null, `Check: ${labelText(item.label)}${isNum(item.score) ? ' (' + (item.score > 0 ? '+' : '') + item.score + ')' : ''}`) : null),
          h('td', null, t.valid ? h('span', { class: 'tag' }, t.status) : h('span', { class: 'tag warn' }, 'Check dates')),
          h('td', null, item.open || '–'), h('td', null, item.close || '–'),
          h('td', null, t.valid ? t.allotment + (t.allotmentEstimated ? ' (est.)' : '') : '–'), h('td', null, t.valid ? t.listing + (t.listingEstimated ? ' (est.)' : '') : '–'),
          h('td', { class: 'num' }, rupees(item.priceHigh)), h('td', { class: 'num' }, isNum(item.lot) ? item.lot : '–'),
          h('td', { class: 'num' }, gmp), h('td', { class: 'num' }, rupees(est.listingPrice)),
          h('td', { class: `num ${gainClass(est.gainPerLot)}` }, isNum(est.gainPerLot) ? `${rupees(est.gainPerLot)} (${signed(est.gainPct, 1)})` : '–'),
          h('td', null, h('button', { type: 'button', class: 'chip', 'aria-label': `Remove ${item.name} from the tracker`, onclick: () => { if (confirm(`Remove ${item.name} from your tracker?`)) save(loadTracker().filter((x) => x.id !== item.id)); } }, 'Remove')));
      })))),
      h('p', { class: 'muted small' }, 'Update the grey-market premium here as it changes. Estimates only: the premium is unofficial, and profit applies only if shares are allotted.'));
  }

  const PHASE_ORDER = { open: 0, upcoming: 1, closed: 2, listed: 3 };

  /* The source's premium, plus a cross-check from recent news headlines when one quotes a figure. */
  function gmpCell(row, listed) {
    if (listed) return '–';
    const parts = [isNum(row.gmp) ? `${rupees(row.gmp)} (${signed(row.gmp_pct, 1)})` : '–'];
    const n = row.news_gmp;
    if (n && (isNum(n.rupees) || isNum(n.percent))) {
      const verdict = n.agrees === true ? 'news agrees' : n.agrees === false ? 'news differs' : 'news only';
      const label = `${verdict}: ${isNum(n.rupees) ? rupees(n.rupees) : signed(n.percent, 1)}`;
      parts.push(h('div', { class: `small ${n.agrees === false ? 'neg' : 'muted'}`, title: n.title },
        safeUrl(n.link) ? h('a', { href: n.link, target: '_blank', rel: 'noopener noreferrer' }, label) : label));
    }
    return parts;
  }

  function indiaIpoSection(ipos) {
    const source = ipos.india_source || {};
    const view = (S.ipoView = S.ipoView || { phase: 'active', board: 'ALL' });
    const today = todayIso();
    const rows = (ipos.india || []).map((row) => ({
      row, t: IPO.timeline(row, today),
      est: IPO.estimate({ priceHigh: row.price, lot: row.lot, gmp: row.gmp }),
      demand: IPO.demand({ gmpPct: row.gmp_pct, subscription: row.subscription }),
    })).filter((x) => x.t.valid);
    const box = h('div');
    const conflicts = rows.filter(({ row, t }) => t.phase !== 'listed' && row.news_gmp && row.news_gmp.agrees === false).length;

    const chip = (label, active, onClick) => h('button', { type: 'button', class: 'chip' + (active ? ' on' : ''), 'aria-pressed': active ? 'true' : 'false', onclick: onClick }, label);

    const tableRow = ({ row, t, est, demand }) => {
      const listed = isNum(row.listed_price);
      const actualGainPerShare = listed ? row.listed_price - row.price : null;
      const actualPerLot = listed ? actualGainPerShare * row.lot : null;
      const name = safeUrl(row.link) ? h('a', { href: row.link, target: '_blank', rel: 'noopener noreferrer' }, row.name) : row.name;
      const profitCell = listed
        ? h('td', { class: `num ${gainClass(actualPerLot)}` }, `${rupees(actualPerLot)} (${signed(row.listed_gain_pct, 1)})`, h('div', { class: 'muted small' }, 'actual, at listing'))
        : h('td', { class: `num ${gainClass(est.gainPerLot)}` }, isNum(est.gainPerLot) ? [`${rupees(est.gainPerLot)} (${signed(est.gainPct, 1)})`, h('div', { class: 'muted small' }, 'estimate')] : '–');
      return h('tr', null,
        h('td', { class: 'name' }, h('b', null, name), h('span', null, [row.exchange, row.board === 'SME' ? null : 'Mainboard'].filter(Boolean).join(' '),
          row.board === 'SME' ? h('span', { class: 'tag warn', title: 'SME IPOs are riskier: smaller, less liquid companies and larger minimum investments' }, 'SME') : null,
          row.anchor ? h('span', { class: 'tag', title: 'Anchor investors took part' }, 'Anchor') : null)),
        h('td', null, h('span', { class: 'tag' }, t.status)),
        h('td', null, t.open), h('td', null, t.close), h('td', null, t.allotment), h('td', null, t.listing),
        h('td', { class: 'num' }, rupees(row.price), h('div', { class: 'muted small' }, `${row.lot} sh = ${rupees(est.applyAmount)}`)),
        h('td', { class: 'num' }, gmpCell(row, listed)),
        h('td', { class: 'num' }, listed ? rupees(row.listed_price) : rupees(est.listingPrice), listed ? h('div', { class: 'muted small' }, 'actual') : null),
        profitCell,
        h('td', { class: 'num hide-sm' }, isNum(row.subscription) ? `${row.subscription.toFixed(1)}x` : '–'),
        h('td', { class: 'hide-sm' }, listed ? '–' : h('span', { class: `tag ${demand.score !== null && demand.score <= -20 ? 'warn' : ''}`, title: demand.reasons.map((r) => r.text).join('; ') }, demand.label)));
    };

    const draw = () => {
      const shown = rows
        .filter(({ row, t }) => (view.board === 'ALL' || row.board === view.board)
          && (view.phase === 'all' || (view.phase === 'active' ? t.phase !== 'listed' : t.phase === 'listed')))
        .sort((a, b) => (PHASE_ORDER[a.t.phase] - PHASE_ORDER[b.t.phase]) || a.t.close.localeCompare(b.t.close));
      const controls = h('div', { class: 'row', style: 'gap:6px;margin-bottom:8px' },
        [['active', 'Open and upcoming'], ['listed', 'Recently listed'], ['all', 'All']].map(([k, label]) => chip(label, view.phase === k, () => { view.phase = k; draw(); })),
        h('span', { class: 'muted' }, '|'),
        [['ALL', 'All boards'], ['Mainboard', 'Mainboard'], ['SME', 'SME']].map(([k, label]) => chip(label, view.board === k, () => { view.board = k; draw(); })));
      const table = shown.length ? h('div', { class: 'card', style: 'padding:0;overflow-x:auto' }, h('table', null,
        h('thead', null, h('tr', null, ['IPO', 'Status', 'Opens', 'Closes', 'Allotment', 'Listing', 'Price band (upper) and lot', 'GMP per share', 'Est. listing price', 'Est. profit or loss per lot', 'Subscribed', 'Demand']
          .map((x, i) => h('th', { class: (i >= 6 && i <= 10 ? 'num' : '') + (i >= 10 ? ' hide-sm' : '') }, x)))),
        h('tbody', null, shown.map(tableRow))))
        : h('div', { class: 'empty card' }, rows.length ? 'No IPOs match these filters.' : 'Indian IPO data could not be loaded this time.');
      box.replaceChildren(controls, table);
    };
    draw();

    const fetched = source.fetched_at ? Math.max(0, Math.round((Date.now() - Date.parse(source.fetched_at)) / 60000)) : null;
    return h('div', null,
      h('h2', null, 'India IPOs, dates and grey-market premium'),
      !ipos.india_ok && rows.length ? h('p', { class: 'tag warn' }, 'The source could not be refreshed. These figures may be out of date.') : null,
      conflicts ? h('p', { class: 'tag warn' }, `${conflicts} IPO${conflicts === 1 ? '' : 's'} below: the source and a recent news headline quote different premiums. Treat those estimates with extra caution.`) : null,
      box,
      h('p', { class: 'muted small' }, 'Source: ', safeUrl(source.url) ? h('a', { href: source.url, target: '_blank', rel: 'noopener noreferrer' }, source.name || 'IPO data site') : 'IPO data site',
        isNum(fetched) ? `, read ${ago(fetched)}` : '', '. Premiums are cross-checked against recent news headlines that quote a figure. Dates and price bands should be confirmed in the offer document and on the NSE or BSE site.'),
      h('details', { class: 'muted small' }, h('summary', null, 'About GMP and these estimates (unofficial, can be wrong)'), 'The grey-market premium (GMP) is an unofficial market rumour, not an exchange price. It can change daily or be wrong. Estimated profit is premium times lot size and applies only if shares are allotted; it ignores brokerage, taxes and moves on listing day. The demand label measures hype from the premium and subscription, not the quality of the business. SME IPOs are riskier, with larger minimum amounts and thin trading.'));
  }

  function viewIpo() {
    const ipos = S.data.ipos || { us: [], headlines: [], us_ok: false };
    const tag = (status) => h('span', { class: 'tag' }, status);
    const range = (d) => (isNum(d.price_low) ? (d.price_high !== d.price_low ? `$${d.price_low}–$${d.price_high}` : `$${d.price_low}`) : '–');
    const usTable = ipos.us.length ? h('div', { class: 'card', style: 'padding:0;overflow-x:auto' }, h('table', null,
      h('thead', null, h('tr', null, h('th', null, 'Company'), h('th', null, 'Status'), h('th', { class: 'num' }, 'Price range'), h('th', { class: 'num hide-sm', title: 'As published on the Nasdaq calendar; it can include an over-allotment option' }, 'Size (per Nasdaq)'), h('th', null, 'Date'))),
      h('tbody', null, ipos.us.map((d) => h('tr', null, h('td', { class: 'name' }, h('b', null, d.name), h('span', null, `${d.symbol || 'ticker TBD'} ${d.exchange ? '· ' + d.exchange : ''}`)),
        h('td', null, tag(d.status), d.stale ? h('span', { class: 'tag warn', title: 'From an old copy of the calendar, not current' }, 'Stale') : null), h('td', { class: 'num' }, range(d)), h('td', { class: 'num hide-sm' }, big(d.amount, 'USD')),
        h('td', null, d.date || '–', d.date_passed ? h('span', { class: 'tag warn', title: 'The date on the old list has already passed' }, 'Date passed') : null)))))) :
      h('div', { class: 'empty card' }, ipos.us_ok ? 'No US IPOs on the calendar right now.' : 'No current US IPO list is available (see the notice above).');
    const st = ipos.us_status;
    const usNotice = st && st.state !== 'current' ? h('div', { class: 'card staleipo', role: 'alert' },
      h('b', null, st.state === 'stale' ? 'This US IPO list is STALE, not current. ' : 'There is no current US IPO list. '),
      st.error ? `Why: ${st.error}. ` : '',
      st.last_success ? `Last successful refresh: ${st.last_success.slice(0, 16).replace('T', ' ')} UTC${isNum(st.age_days) ? ` (${st.age_days} day${st.age_days === 1 ? '' : 's'} ago)` : ''}. The records below are from that day (original data date ${st.original_date}) and may have changed. ` : 'It has not loaded successfully for a long time. ',
      st.last_attempt ? `Last attempt: ${st.last_attempt.slice(0, 16).replace('T', ' ')} UTC. ` : '',
      h('a', { href: st.page_url, target: '_blank', rel: 'noopener' }, 'Check the live Nasdaq calendar')) : null;
    const fresh = S.data.stocks.filter((s) => s.new).sort((a, b) => String(b.listed).localeCompare(String(a.listed)));
    const freshTable = fresh.length ? h('div', { class: 'card', style: 'padding:0;overflow-x:auto' }, h('table', null,
      h('thead', null, h('tr', null, h('th', null, 'Stock'), h('th', { class: 'num' }, 'Price'), h('th', null, 'Listed'), h('th', null, 'Signal'), h('th', null, 'Fundamentals check'))),
      h('tbody', null, fresh.map((s) => {
        const q = IPO.qualityFromFundamentals(s.fundamentals, null);
        const row = h('tr', { class: 'stock', tabindex: '0' }, h('td', { class: 'name' }, h('b', null, s.name), h('span', null, s.symbol)), h('td', { class: 'num' }, money(s.price, s.currency)),
          h('td', null, s.listed || '–'), h('td', null, badge(s.short)), h('td', null, isNum(q.score) ? badge({ label: q.longTerm.label === 'N/A' ? q.label : q.longTerm.label, score: q.score }) : h('span', { class: 'muted small' }, 'Not enough data yet')));
        row.addEventListener('click', () => openDetail(s.symbol, row));
        row.addEventListener('keydown', (e) => { if (e.key === 'Enter') openDetail(s.symbol, row); });
        return row;
      })))) : h('div', { class: 'empty card' }, 'No new listings found in the tracked window.');
    queueMicrotask(drawTracker);
    return h('div', null,
      indiaIpoSection(ipos),
      h('h2', null, 'Recently listed stocks (added automatically)'), h('p', { class: 'muted small' }, 'New listings found on the NSE list and the Nasdaq/NYSE symbol list are added on their own. Signals need about 60 trading days of history, and fundamentals appear once the company reports.'), freshTable,
      h('h2', null, 'US IPO calendar'), h('p', { class: 'muted small' }, 'From the Nasdaq calendar. Financials for companies that have not listed yet are not available here: use the optional checker at the bottom with figures from the prospectus.'),
      usNotice, usTable,
      h('h2', null, 'IPO headlines'), h('div', { class: 'card' }, newsList(ipos.headlines, true)),
      h('h2', null, 'Check an IPO yourself (optional)'),
      h('p', { class: 'muted small' }, 'Everything above updates by itself. Use this only for an IPO that is not in the table above, or to score a company\'s financials: it is the one place you type figures.'),
      ipoChecker(), h('h3', null, 'My IPO tracker'), h('div', { id: 'ipoTracker' }));
  }

  /* ---------- events ---------- */
  const EVENT_LABELS = { earnings: 'Results', ex_dividend: 'Ex-dividend', dividend_payment: 'Dividend paid', split: 'Split', bonus: 'Bonus', corporate_action: 'Corporate action' };
  const EVENT_SUBLABELS = { earnings: 'Results expected', ex_dividend: 'Ex-dividend date', dividend_payment: 'Dividend payment date', split: 'Stock split', bonus: 'Bonus issue', corporate_action: 'Record or ex-date' };
  const BASIS_TAGS = { yahoo: 'YAHOO', estimate: 'ESTIMATED', news: 'NEWS', nse: 'OFFICIAL', official: 'OFFICIAL' };
  const EVENT_CATEGORIES = [
    { id: 'results', title: 'Results', blurb: 'Quarterly and annual results', types: ['earnings'] },
    { id: 'dividends', title: 'Dividend dates', blurb: 'Ex-dividend and payment dates', types: ['ex_dividend', 'dividend_payment'] },
    { id: 'actions', title: 'Splits, bonus and other actions', blurb: 'Reported by prominent news outlets', types: ['split', 'bonus', 'corporate_action'] },
    { id: 'announced', title: 'Announced in the news', blurb: 'Announced, but no date stated', types: [] },
  ];
  const CARD_LIMIT = 8;
  S.ev = { market: 'ALL', category: 'ALL', mine: false, q: '', all: {} };

  const categoryOf = (type) => (EVENT_CATEGORIES.find((c) => c.types.includes(type)) || EVENT_CATEGORIES[3]).id;
  const todayUtc = () => new Date().toISOString().slice(0, 10);
  const dayMs = (iso) => Date.parse(iso + 'T00:00:00Z');
  const daysUntil = (iso) => Math.round((dayMs(iso) - dayMs(todayUtc())) / 86400000);
  const prettyDate = (iso) => new Date(dayMs(iso)).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' });
  const monthShort = (iso) => new Date(dayMs(iso)).toLocaleDateString('en-GB', { month: 'short', timeZone: 'UTC' });
  const inDays = (n) => (n <= 0 ? 'today' : n === 1 ? 'tomorrow' : `in ${n} days`);
  const dayLabel = (iso) => { const n = daysUntil(iso); return n <= 0 ? `Today · ${prettyDate(iso)}` : n === 1 ? `Tomorrow · ${prettyDate(iso)}` : prettyDate(iso); };

  function eventFigure(e) {
    if (e.type === 'ex_dividend' && isNum(e.announced_amount)) return [money(e.announced_amount, e.currency), h('small', null, 'per share · announced')];
    if (e.type === 'ex_dividend' && isNum(e.amount)) return [h('span', { class: 'muted' }, money(e.amount, e.currency)), h('small', null, 'per share · last paid')];
    if (e.ratio) return [e.ratio, h('small', null, 'ratio')];
    return null;
  }

  function eventDetail(e) {
    const bits = [];
    if (e.type === 'ex_dividend' && isNum(e.amount) && !isNum(e.announced_amount)) bits.push('The announced amount may differ from the last payment');
    if (e.until) bits.push(`Falls between ${prettyDate(e.date)} and ${prettyDate(e.until)}`);
    return [e.detail, ...bits].filter(Boolean).join('. ');
  }

  function evidenceLine(e) {
    const ev = e.evidence;
    if (!ev || !ev.headline) return null;
    const link = safeUrl(ev.link);
    return h('div', { class: 'muted small' }, 'News: ', link ? h('a', { href: link, target: '_blank', rel: 'noopener noreferrer' }, ev.headline) : ev.headline,
      ` (${[ev.source, isNum(ev.age_min) ? ago(ev.age_min) : null].filter(Boolean).join(', ')})`);
  }

  function stockName(e, withStock) {
    return withStock ? [h('b', null, e.name), ' ', h('span', { class: 'muted small' }, e.symbol)] : null;
  }

  function viewStockButton(e, withStock) {
    if (!withStock || !S.data.stocks.some((s) => s.symbol === e.symbol)) return null;
    return h('button', { type: 'button', class: 'chip evopen', onclick: (ev) => openDetail(e.symbol, ev.currentTarget) }, `View ${e.name}`);
  }

  /* One event as a compact, expandable row: date badge, what and who, the key figure, and the sources underneath. */
  /* Whether an official source backs this date: CONFIRMED, CONFLICT (the exchange says another date) or UNVERIFIED. */
  function verifyTag(e) {
    if (e.verification === 'CONFIRMED') return h('span', { class: 'tag buy', title: e.verification_note || '' }, 'Confirmed by NSE');
    if (e.verification === 'CONFLICT') return h('span', { class: 'tag warn', title: e.verification_note || '' }, `NSE says ${prettyDate((e.official || {}).date)}`);
    return h('span', { class: 'tag', title: e.verification_note || '' }, 'Unverified');
  }

  function verifyLine(e) {
    if (!e.verification) return null;
    return h('div', { class: 'small verifyline' }, verifyTag(e), ' ', e.verification_note || '', ' ',
      e.official_url ? h('a', { href: e.official_url, target: '_blank', rel: 'noopener' }, 'Official source') : null,
      e.last_checked ? h('span', { class: 'muted' }, ` \u00B7 last checked ${prettyDate(e.last_checked)}`) : h('span', { class: 'muted' }, ' \u00B7 not checked against an official source'));
  }

  function officialNote() {
    const o = (S.data.events || {}).official;
    if (!o) return null;
    const when = o.fetched_at ? `last read ${o.fetched_at.slice(0, 16).replace('T', ' ')} UTC` : 'not read successfully yet';
    return h('p', { class: `small ${o.ok ? 'muted' : 'warnline'}`, role: o.ok ? null : 'status' },
      o.ok ? `Indian dates are checked against the NSE's own announcement feeds (${when}): ${o.confirmed} confirmed, ${o.conflicts} with a different official date, ${o.unverified} unverified.`
        : `The NSE announcement feed could not be read (${o.error || 'no answer'}), so no date is confirmed; ${when}. US dates have no official machine-readable feed, so they are never confirmed here.`);
  }

  function eventItem(e, withStock) {
    const n = daysUntil(e.date);
    const category = categoryOf(e.type);
    const sub = e.until ? `${EVENT_SUBLABELS[e.type]} · to ${prettyDate(e.until)}` : `${EVENT_SUBLABELS[e.type] || e.type} · ${inDays(n)}`;
    return h('details', { class: `ev cat-${category}` },
      h('summary', null,
        h('div', { class: 'evdate' }, h('b', null, String(new Date(dayMs(e.date)).getUTCDate())), h('span', null, monthShort(e.date))),
        h('div', { class: 'evmain' }, h('div', { class: 'evtitle' }, withStock ? stockName(e, true) : EVENT_LABELS[e.type] || e.type), h('div', { class: 'muted small' }, sub)),
        h('div', { class: 'evfig' }, eventFigure(e))),
      h('div', { class: 'evbody' },
        h('div', null, (e.bases || []).map((b) => [src(BASIS_TAGS[b] || 'YAHOO'), ' ']), e.agree ? src('AGREED') : null,
          e.conflict ? h('span', { class: 'tag warn' }, 'differs from another source') : null),
        h('div', { class: 'muted small' }, eventDetail(e), ' ', h('span', null, `Source: ${e.source}.`)),
        verifyLine(e), evidenceLine(e), viewStockButton(e, withStock)));
  }

  function announcementItem(a, withStock) {
    const fig = isNum(a.amount) ? [money(a.amount, a.currency), h('small', null, 'per share')] : a.ratio ? [a.ratio, h('small', null, 'ratio')] : null;
    return h('details', { class: 'ev cat-announced' },
      h('summary', null,
        h('div', { class: 'evdate' }, h('b', null, '?'), h('span', null, 'no date')),
        h('div', { class: 'evmain' }, h('div', { class: 'evtitle' }, withStock ? stockName(a, true) : a.title), h('div', { class: 'muted small' }, a.title)),
        h('div', { class: 'evfig' }, fig)),
      h('div', { class: 'evbody' }, h('div', null, src('NEWS')), h('div', { class: 'small verifyline' }, h('span', { class: 'tag' }, 'Unverified'), ' No date stated. ', a.official_url ? h('a', { href: a.official_url, target: '_blank', rel: 'noopener' }, 'Official source') : null), evidenceLine(a), viewStockButton(a, withStock)));
  }

  function stockEvents(s) {
    const mine = ((S.data.events || {}).items || []).filter((e) => e.symbol === s.symbol && daysUntil(e.until || e.date) >= 0);
    const notes = ((S.data.events || {}).announcements || []).filter((a) => a.symbol === s.symbol);
    const splits = s.splits || [];
    return [
      mine.length || notes.length ? h('div', { class: 'evlist' }, mine.map((e) => eventItem(e, false)), notes.map((a) => announcementItem(a, false)))
        : h('p', { class: 'muted small' }, 'No upcoming results, dividend or corporate-action dates were found for this stock.'),
      splits.length ? h('p', { class: 'small' }, h('b', null, 'Recent splits: '), splits.map((x) => `${x.ratio} on ${x.date}`).join(', ')) : null,
      h('p', { class: 'muted small', style: 'margin-bottom:0' }, 'Dates come from Yahoo Finance, from headlines of prominent news outlets, or are estimated from past payments, and can change. The exchanges\' own corporate-action lists cannot be read for free, so check the exchange notice before acting on any date.')];
  }

  function estimateNote() {
    const c = (S.data.events || {}).estimate_check;
    const n = (S.data.events || {}).news_check;
    if (!c && !n) return null;
    return h('p', { class: 'muted small' },
      c ? `How good are the estimates? Where Yahoo has a real ex-dividend date (${c.n} stocks), the same method gave a date within ${c.median_abs_days} day${c.median_abs_days === 1 ? '' : 's'} at the median, and ${c.within_7_days_pct}% were within a week. Some were weeks off, so treat "estimated" as a rough guide. ` : null,
      n ? `Do Yahoo and the news agree? For ${n.n} stock${n.n === 1 ? '' : 's'} with both, ${n.agree_pct}% gave the same ex-dividend date (or a date range that contains it).` : null);
  }

  function viewEvents() {
    const data = S.data.events || {};
    const dated = (data.items || []).filter((e) => daysUntil(e.until || e.date) >= 0);
    const notes = data.announcements || [];
    const f = S.ev;
    const root = h('div', null);

    const passes = (e) => (f.market === 'ALL' || e.market === f.market) && (!f.mine || S.watch.has(e.symbol))
      && (!f.q || `${e.name} ${e.symbol}`.toLowerCase().includes(f.q.toLowerCase()));

    function draw() {
      const rows = (cat) => (cat.id === 'announced' ? notes : dated.filter((e) => cat.types.includes(e.type))).filter(passes);
      const counts = Object.fromEntries(EVENT_CATEGORIES.map((c) => [c.id, rows(c).length]));
      const total = EVENT_CATEGORIES.reduce((sum, c) => sum + counts[c.id], 0);

      const chips = h('div', { class: 'catchips', role: 'group', 'aria-label': 'Event categories' },
        [{ id: 'ALL', title: 'All events' }, ...EVENT_CATEGORIES].map((c) => h('button', {
          type: 'button', class: `chip catchip cat-${c.id}`, 'aria-pressed': String(f.category === c.id),
          onclick: () => { f.category = c.id; draw(); } }, c.title, ' ', h('span', { class: 'n' }, String(c.id === 'ALL' ? total : counts[c.id])))));

      const cards = EVENT_CATEGORIES.filter((c) => f.category === 'ALL' || f.category === c.id).map((c) => {
        const list = rows(c);
        if (!list.length) return f.category === c.id ? h('div', { class: 'empty' }, 'Nothing in this category matches the filters.') : null;
        const open = f.all[c.id];
        const shown = open ? list : list.slice(0, CARD_LIMIT);
        const first = list[0];
        const groups = [];
        shown.forEach((e) => {
          const key = c.id === 'announced' ? 'Reported recently' : e.date;
          const last = groups[groups.length - 1];
          if (last && last.key === key) last.items.push(e); else groups.push({ key, items: [e] });
        });
        return h('section', { class: `card evcard cat-${c.id}`, 'aria-label': c.title },
          h('header', { class: 'evhead' }, h('h3', null, c.title), h('span', { class: 'count' }, String(list.length)), h('span', { class: 'muted small' }, c.blurb),
            c.id !== 'announced' && first ? h('div', { class: 'muted small evnext' }, `Next: ${first.name} · ${dayLabel(first.date)}`) : null),
          groups.map((g) => [h('div', { class: 'evgroup' }, c.id === 'announced' ? g.key : dayLabel(g.key)),
            g.items.map((e) => (c.id === 'announced' ? announcementItem(e, true) : eventItem(e, true)))]),
          list.length > CARD_LIMIT ? h('button', { type: 'button', class: 'btn ghost evmore', onclick: () => { f.all[c.id] = !open; draw(); } }, open ? 'Show fewer' : `Show all ${list.length}`) : null);
      });

      const empty = !total ? h('div', { class: 'empty' }, f.mine ? 'None of the stocks on your watchlist has an upcoming event in the data. Turn off "My watchlist only" to see all.' : 'No upcoming events match these filters.') : null;
      const controls = h('div', { class: 'filterbar' }, h('div', { class: 'row' },
        h('label', { class: 'field' }, h('span', null, 'Search'), h('input', { type: 'search', placeholder: 'Name or symbol', value: f.q, 'aria-label': 'Search events',
          oninput: (ev) => { f.q = ev.target.value; clearTimeout(draw.timer); draw.timer = setTimeout(() => { draw(); const box = root.querySelector('input[type=search]'); if (box) { box.focus(); box.setSelectionRange(f.q.length, f.q.length); } }, 250); } })),
        h('label', { class: 'field' }, h('span', null, 'Market'), h('select', { onchange: (ev) => { f.market = ev.target.value; draw(); } },
          [['ALL', 'All markets'], ['IN', 'India'], ['US', 'United States']].map(([v, text]) => h('option', { value: v, selected: v === f.market }, text)))),
        h('label', { class: 'field' }, h('span', null, 'My watchlist only'), h('input', { type: 'checkbox', checked: f.mine, onchange: (ev) => { f.mine = ev.target.checked; draw(); } })),
        null));

      root.replaceChildren(...[h('h2', null, 'Upcoming events'), officialNote(), controls, chips, empty, h('div', { class: 'evcards' }, cards), estimateNote(),
        h('p', { class: 'muted small' }, 'Results, ex-dividend and payment dates for the stocks in this app, from Yahoo Finance and from headlines of prominent news outlets (CNBC TV18, Economic Times, Mint and similar), plus dividend dates estimated from each company\'s own payment pattern. Tap an event for its sources. Every date shows whether two agree, and news items show their headline. Dates can change, so check the exchange notice before acting. The NSE and BSE do not allow free scripted access to their own corporate-action lists, so splits and bonus issues appear only when a prominent outlet has reported them.')]
        .flat(Infinity).filter(Boolean));
      decorateHelp(root);
    }
    draw();
    return root;
  }

  /* ---------- market tab ---------- */
  const SRC_TEXT = {
    DELAYED: 'Free market data, up to about 15 minutes late.',
    DERIVED: 'Worked out by this app from the stocks in its own list, not from the whole exchange.',
    EXTERNAL: 'Headlines from outside sources. They are not checked or endorsed by this app.',
    YAHOO: 'A date taken from Yahoo Finance. The company confirms its dates by notice to the exchange, so check the official notice before relying on it.',
    OFFICIAL: 'Taken from, or checked against, the announcements that companies make to the National Stock Exchange, read from its public announcement feeds.',
    NEWS: 'Reported in a headline from a prominent news outlet (shown below). Official sources could not be read, so check the exchange notice before relying on it.',
    AGREED: 'Two independent sources give this date: for example Yahoo and a news headline, or an estimate and a news headline.',
    ESTIMATED: 'Worked out by this app from how often the company has paid in the past. The company may announce a different date.',
  };
  const src = (kind) => h('span', { class: 'src', title: SRC_TEXT[kind] }, kind);
  const moveClass = (v) => (isNum(v) ? (v >= 0 ? 'up' : 'down') : '');

  function miniChart(values) {
    const v = (values || []).filter(isNum);
    if (v.length < 3) return null;
    const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
    const pts = v.map((x, i) => `${(i / (v.length - 1) * 100).toFixed(1)},${(26 - (x - lo) / span * 24).toFixed(1)}`).join(' ');
    return svg('svg', { class: 'mini', viewBox: '0 0 100 28', preserveAspectRatio: 'none', role: 'img', 'aria-label': 'Last 30 sessions' },
      svg('polyline', { points: pts, fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6', 'vector-effect': 'non-scaling-stroke' }));
  }

  function conditionsCard(market, c) {
    const name = (MARKET_NAMES[market] || market).replace(' (NSE)', '');
    const r = c && c.regime;
    if (!r) return h('div', { class: 'card' }, h('h3', null, `${name} market conditions`), h('p', { class: 'muted small' }, 'Not available right now: the index history could not be loaded.'));
    const pos = clamp((r.score + 7) / 14 * 100, 0, 100);
    const tone = r.score >= 2 ? 'up' : r.score <= -2 ? 'down' : '';
    return h('div', { class: 'card' },
      h('div', { class: 'row', style: 'justify-content:space-between;gap:6px' }, h('h3', { style: 'margin:0' }, `${name} market conditions`), src('DERIVED')),
      h('div', { class: `regime ${tone}` }, r.label),
      h('div', { class: 'meter', role: 'img', 'aria-label': `Conditions score ${r.score} on a scale from minus 7 to plus 7` }, h('i', { style: `left:${pos}%` })),
      h('div', { class: 'muted small' }, `Score ${r.score > 0 ? '+' : ''}${r.score} of 7 · ${c.name || 'Index'} as of ${r.as_of}`),
      h('ul', { class: 'clean checks' }, r.checks.map((k) => h('li', null,
        h('span', { class: `chk ${k.score > 0 ? 'pos' : k.score < 0 ? 'neg' : ''}` }, k.score > 0 ? '+1' : k.score < 0 ? '−1' : '0'),
        h('span', null, h('b', null, k.label), h('div', { class: 'muted small' }, k.detail))))));
  }

  function breadthCard(market, c) {
    const b = c && c.breadth;
    const name = (MARKET_NAMES[market] || market).replace(' (NSE)', '');
    if (!b) return null;
    return h('div', { class: 'card' },
      h('div', { class: 'row', style: 'justify-content:space-between;gap:6px' }, h('h3', { style: 'margin:0' }, `${name} breadth`), src('DERIVED')),
      h('div', { class: 'row', style: 'gap:6px;margin:6px 0' },
        h('span', { class: 'tag buy' }, `${b.advancing} advancing`), h('span', { class: 'tag sell' }, `${b.declining} declining`), h('span', { class: 'tag' }, `${b.unchanged} unchanged`)),
      kv([['Above 20-day average', isNum(b.above20_pct) ? `${b.above20_pct.toFixed(0)}%` : '–'],
        ['Above 50-day average', isNum(b.above50_pct) ? `${b.above50_pct.toFixed(0)}%` : '–'],
        ['Above 200-day average', isNum(b.above200_pct) ? `${b.above200_pct.toFixed(0)}%` : '–'],
        ['New 20-day highs / lows', `${b.new_highs} / ${b.new_lows}`]]),
      h('p', { class: 'muted small', style: 'margin-bottom:0' }, `Counted over the ${b.n} stocks in this app's list that traded on ${b.as_of}. It is not the whole exchange.`));
  }

  function sectorCard(market, c) {
    const rows = (c && c.sectors) || [];
    if (!rows.length) return null;
    const name = (MARKET_NAMES[market] || market).replace(' (NSE)', '');
    const cell = (v) => h('td', { class: `num ${moveClass(v)}` }, signed(v, 1));
    return h('div', { class: 'card', style: 'padding:0;overflow-x:auto' },
      h('div', { class: 'row', style: 'justify-content:space-between;gap:6px;padding:10px 12px 0' }, h('h3', { style: 'margin:0' }, `${name} sectors`), src('DERIVED')),
      h('table', null, h('thead', null, h('tr', null, h('th', null, 'Sector'), h('th', { class: 'num' }, '1 day'), h('th', { class: 'num' }, '5 days'), h('th', { class: 'num' }, '20 days'), h('th', { class: 'num' }, 'Stocks'))),
        h('tbody', null, rows.map((s) => h('tr', null, h('td', null, s.sector), cell(s.d1), cell(s.d5), cell(s.d20), h('td', { class: 'num muted' }, String(s.n)))))),
      h('p', { class: 'muted small', style: 'padding:0 12px 8px;margin:6px 0 0' }, 'Average move of the stocks in each sector, every stock counted the same.'));
  }

  function accuracyCard(accuracy) {
    const rows = ['IN', 'US'].map((k) => (accuracy || {})[k]).filter(Boolean);
    if (!rows.length) return null;
    const pct = (v, d = 3) => (isNum(v) ? `${v.toFixed(d)}%` : '–');
    return h('div', null, h('h2', null, 'Price accuracy ', src('DERIVED')),
      h('div', { class: 'grid' }, rows.map((s) => h('div', { class: 'card' },
        h('h3', { style: 'margin:0 0 4px' }, `${s.market === 'IN' ? 'India' : 'United States'}: ${s.source}`),
        h('div', { class: `regime ${s.mismatch ? 'down' : 'up'}` }, isNum(s.agreement_pct) ? `${s.agreement_pct.toFixed(1)}% match` : 'Not checked'),
        kv([['Stocks checked', `${s.verified} of ${s.stocks}`], ['Match within 0.5%', String(s.match)], ['Differ 0.5% to 2%', String(s.minor)],
          ['Differ more than 2%', String(s.mismatch)], ['Median difference', pct(s.median_diff_pct)], ['Largest difference', pct(s.max_diff_pct, 2)],
          ['Latest session compared', s.as_of || '–'], ['Not covered', String(s.unverified)]]),
        s.worst && s.worst.length ? h('p', { class: 'muted small', style: 'margin-bottom:0' }, 'Largest differences: ', s.worst.map((w) => `${w.symbol} ${w.max_diff_pct}%`).join(', ')) : null))),
      h('p', { class: 'muted small' }, 'The Yahoo closing prices on the last completed sessions are compared with the source named above. Live prices during the session are Yahoo only: no free independent source exists.'));
  }

  function qualityLine(s) {
    const q = s.quality;
    if (!q) return h('div', { class: 'muted small' }, 'Prices not cross-checked: no second source covers this stock.');
    const detail = `largest difference ${q.max_diff_pct}% over ${q.n} session${q.n === 1 ? '' : 's'}`;
    if (q.status === 'match') return h('div', { class: 'muted small' }, h('span', { class: 'tag buy' }, 'Cross-checked'), ` against ${q.source}: ${detail}`);
    if (q.status === 'minor') return h('div', { class: 'muted small' }, h('span', { class: 'tag' }, 'Small difference'), ` against ${q.source}: ${detail}`);
    return h('div', { class: 'muted small' }, h('span', { class: 'tag warn' }, 'Prices disagree'), ` with ${q.source}: ${detail}. New alerts for this stock are paused.`);
  }

  function statusCard(status) {
    if (!status || !status.checks) return null;
    const word = { ok: 'OK', warn: 'Check', info: 'Info' };
    const good = status.overall === 'ok';
    return h('details', { class: 'card status' },
      h('summary', null, h('b', null, 'Data status: '), good ? 'all checks passed' : 'some checks need attention'),
      h('ul', { class: 'clean' }, status.checks.map((c) => h('li', null, h('span', { class: `state ${c.state}` }, word[c.state] || c.state), h('b', null, c.name), ' ', h('span', { class: 'muted small' }, c.detail)))),
      h('p', { class: 'muted small', style: 'margin-bottom:0' }, `Updated ${S.data.generated_at ? new Date(S.data.generated_at).toLocaleString() : ''}. These checks run every time the data is refreshed.`));
  }

  function viewMarket() {
    const m = S.data.market || { watch: [], news: [] };
    const conditions = S.data.conditions || {};
    const markets = ['IN', 'US'].filter((k) => conditions[k]);
    const cards = m.watch.map((w) => h('div', { class: 'card' }, h('div', { class: 'muted small' }, w.name),
      h('div', { style: 'font-size:20px;font-weight:700' }, number(w.price, w.price > 1000 ? 0 : 2)),
      h('div', { class: moveClass(w.change_pct) }, `${signed(w.change_pct)} today`),
      h('div', { class: 'muted small' }, `${signed(w.month_pct, 1)} in a month`),
      miniChart(w.spark),
      w.regime ? h('span', { class: `tag ${w.regime === 'Downtrend' || w.regime === 'Fearful' ? 'warn' : ''}` }, w.regime) : null));
    return h('div', null,
      h('h2', null, 'Market conditions'),
      markets.length ? h('div', { class: 'grid' }, markets.map((k) => conditionsCard(k, conditions[k]))) : null,
      markets.length ? h('p', { class: 'muted small' }, 'Seven simple checks on the main index, each worth +1, 0 or −1. They describe how the market is behaving now. They are not a forecast and not a call on any stock.') : null,
      h('h2', null, 'Indexes and global cues ', src('DELAYED')),
      h('div', { class: 'grid' }, cards.length ? cards : h('div', { class: 'empty' }, 'Market data unavailable.')),
      h('p', { class: 'muted small' }, 'Uptrend means the index is above its 50 and 200-day averages. A fearful VIX (above 25) means large swings: use smaller positions.'),
      markets.some((k) => conditions[k].breadth) ? [h('h2', null, 'Breadth and sectors'),
        h('div', { class: 'grid' }, markets.map((k) => [breadthCard(k, conditions[k]), sectorCard(k, conditions[k])]))] : null,
      (S.data.alerts || []).length ? [h('h2', null, 'Recent alerts'), h('div', { class: 'card' }, h('ul', { class: 'clean' },
        [...S.data.alerts].reverse().map((a) => h('li', null, h('span', { style: 'white-space:pre-line' }, a.text),
          h('div', { class: 'muted small' }, ago(Math.max(0, Math.round((Date.now() / 1000 - a.ts) / 60))), ' ',
            a.symbol && (S.data.stocks.some((s) => s.symbol === a.symbol) || S.others.some((s) => s.symbol === a.symbol)) ? h('button', { type: 'button', class: 'chip', onclick: (e) => openDetail(a.symbol, e.currentTarget) }, 'View analysis') : null)))))] : null,
      h('h2', null, 'World and market news ', src('EXTERNAL')), h('div', { class: 'card' }, newsList(m.news, true)),
      accuracyCard(S.data.accuracy),
      statusCard(S.data.status),
      h('p', { class: 'muted small' }, (S.data.exchange_data || {}).note || ''));
  }

  /* ---------- track record tab ---------- */
  const SIDE_TEXT = { buy: 'bullish', sell: 'bearish', hold: 'mixed' };
  const TERMS = [
    ['Success', 'The price reached the reference level before the volatility risk level or the time limit. Shown as Reference reached in the tables.'],
    ['Failure', 'The price reached the volatility risk level first. Shown as Risk level reached in the tables.'],
    ['Expired', 'Neither level was reached within 20 trading days (weeks view) or 120 (months-to-years view).'],
    ['Avg move', 'Average price move in the direction of the reading, after a 0.1% cost.'],
    ['vs index', 'Average return minus what the market index did over the same days.'],
  ];

  function statRow(label, g, min) {
    const range = (ci) => (ci ? ` (${ci[0].toFixed(0)}-${ci[1].toFixed(0)}%)` : '');
    const unsure = !g.reliable;
    return h('tr', { class: unsure ? 'unsure' : null },
      h('td', null, label, unsure ? h('span', { class: 'tag warn', title: `Fewer than ${min} resolved readings: too few to trust` }, 'few readings') : null),
      h('td', { class: 'num' }, String(g.n)),
      h('td', { class: 'num' }, isNum(g.target_pct) ? `${g.target_pct.toFixed(0)}%${range(g.target_ci)}` : '–'),
      h('td', { class: 'num' }, isNum(g.stop_pct) ? `${g.stop_pct.toFixed(0)}%` : '–'),
      h('td', { class: 'num' }, isNum(g.expired_pct) ? `${g.expired_pct.toFixed(0)}%` : '–'),
      h('td', { class: `num ${isNum(g.avg_return) ? (g.avg_return >= 0 ? 'up' : 'down') : ''}` }, signed(g.avg_return)),
      h('td', { class: `num ${isNum(g.avg_excess) ? (g.avg_excess >= 0 ? 'up' : 'down') : ''}` }, signed(g.avg_excess)));
  }

  function statTable(title, groups, min) {
    const entries = Object.entries(groups || {}).filter(([, g]) => g.n > 0);
    if (!entries.length) return null;
    return [h('h3', null, title), h('div', { class: 'card', style: 'padding:0;overflow-x:auto' }, h('table', null,
      h('thead', null, h('tr', null, ['Group', 'Readings', 'Reference reached', 'Risk level reached', 'Expired', 'Avg move', 'vs index'].map((x, i) => h('th', { class: i ? 'num' : null }, x)))),
      h('tbody', null, entries.map(([label, g]) => statRow(label, g, min)))))];
  }

  /* One stacked bar: success (reference level reached first), failure (risk level reached first), expired. */
  function outcomeBar(g, tall) {
    const parts = [['ok', g.target_pct], ['bad', g.stop_pct], ['na', g.expired_pct]];
    return h('div', { class: `outbar${tall ? ' tall' : ''}`, role: 'img', 'aria-label': `${Math.round(g.target_pct || 0)} percent reached the reference level, ${Math.round(g.stop_pct || 0)} percent reached the risk level, ${Math.round(g.expired_pct || 0)} percent expired` },
      parts.map(([k, v]) => (isNum(v) && v > 0 ? h('i', { class: k, style: `flex:${v}` }) : null)));
  }

  const pct0 = (v) => (isNum(v) ? `${v.toFixed(0)}%` : '\u2013');

  function outcomeLegend(g) {
    return h('div', { class: 'outlegend' },
      h('span', null, h('i', { class: 'dot ok' }), `Success ${pct0(g.target_pct)}`), h('span', null, h('i', { class: 'dot bad' }), `Failure ${pct0(g.stop_pct)}`),
      h('span', null, h('i', { class: 'dot na' }), `Expired ${pct0(g.expired_pct)}`));
  }

  /* The headline: what share of settled readings succeeded, or how far off a trustworthy figure still is. */
  function scoreboard(t, min) {
    const o = t.overall || { n: 0 };
    if (!o.n) {
      const done = t.resolved || 0;
      return h('section', { class: 'card score' },
        h('div', { class: 'scoretitle' }, 'Too early for a success rate'),
        h('div', { class: 'big' }, String(done), h('span', { class: 'bigof' }, ` of ${min} readings settled`)),
        h('div', { class: 'outbar tall', role: 'img', 'aria-label': `${done} of ${min} readings settled` }, h('i', { class: 'ok', style: `flex:${Math.max(done, 0.001)}` }), h('i', { class: 'blank', style: `flex:${Math.max(min - done, 0)}` })),
        h('p', { class: 'small', style: 'margin:8px 0 0' }, `${t.open || 0} readings are open. One settles when its price reaches the reference level (success), reaches the risk level (failure) or runs out of time. The success rate appears once ${min} have settled; before that, any figure would be too unreliable to trust.`));
    }
    const move = isNum(o.avg_return) ? h('span', { class: o.avg_return >= 0 ? 'up' : 'down' }, `${signed(o.avg_return)} average move`) : null;
    const excess = isNum(o.avg_excess) ? h('span', { class: o.avg_excess >= 0 ? 'up' : 'down' }, `${signed(o.avg_excess)} against the index`) : null;
    return h('section', { class: 'card score' },
      h('div', { class: 'scoretitle' }, 'Success rate of settled readings', o.reliable ? null : h('span', { class: 'tag warn' }, 'early figure')),
      h('div', { class: 'big' }, pct0(o.target_pct), h('span', { class: 'bigof' }, ` of ${o.n} reached the reference level first`)),
      outcomeBar(o, true), outcomeLegend(o),
      h('div', { class: 'moves small' }, move, excess),
      o.reliable ? null : h('p', { class: 'muted small', style: 'margin:6px 0 0' }, `Only ${o.n} readings have settled. With fewer than ${min} the rate can swing a lot, so treat it as a first look.`));
  }

  function groupCards(t) {
    const entries = Object.entries(t.by_group || {}).filter(([, g]) => g.n > 0);
    if (!entries.length) return null;
    return [h('h3', null, 'By reading type'), h('div', { class: 'tgroups' }, entries.map(([label, g]) => h('div', { class: 'card tgroup' },
      h('div', { class: 'small muted' }, label), h('div', { class: 'gbig' }, pct0(g.target_pct), g.reliable ? null : h('span', { class: 'tag warn' }, 'few')),
      outcomeBar(g, false), h('div', { class: 'small muted' }, `${g.n} settled`, isNum(g.avg_return) ? ` \u00B7 ${signed(g.avg_return)} average move` : ''))))];
  }

  function viewTrack() {
    const t = S.data.track_record;
    if (!t) {
      return h('div', null, h('h2', null, 'Track record'),
        h('div', { class: 'empty' }, 'No track record yet. Every bullish or bearish alert is recorded with its reference level and volatility risk level, and checked against later prices. Results appear here once readings have resolved.'));
    }
    const min = t.min_sample || 30;
    const o = t.overall || { n: 0 };
    const notes = (t.suggestions || []).map((s) => h('li', null, s.text, ' ', h('span', { class: 'muted small' }, s.evidence ? `(${s.evidence})` : '')));
    const comps = (t.components || []).filter((c) => c.comparable);
    const compRows = comps.map((c) => h('tr', null,
      h('td', null, `${c.horizon === 'short' ? 'Weeks view' : 'Months-to-years view'}: ${c.component}`),
      h('td', { class: 'num' }, `${c.supporting.n}`), h('td', { class: 'num' }, signed(c.supporting.avg_return)),
      h('td', { class: 'num' }, `${c.against.n}`), h('td', { class: 'num' }, signed(c.against.avg_return))));
    const recent = (t.recent || []).map((r) => h('tr', null,
      h('td', { class: 'name' }, h('b', null, r.name || r.symbol), h('span', null, `${r.symbol}, ${r.horizon === 'short' ? 'weeks view' : 'months-to-years view'}, ${SIDE_TEXT[r.side] || r.side}`)),
      h('td', null, h('span', { class: `tag ${r.outcome === 'target' ? 'buy' : r.outcome === 'stop' ? 'sell' : ''}` }, r.outcome === 'target' ? 'Success' : r.outcome === 'stop' ? 'Failure' : 'Expired')),
      h('td', { class: `num ${r.return_pct >= 0 ? 'up' : 'down'}` }, signed(r.return_pct)),
      h('td', { class: 'num' }, `${shortDay(r.date)} to ${shortDay(r.resolved_date)}`)));
    const open = (t.open_calls || []).map((c) => h('tr', null,
      h('td', { class: 'name' }, h('b', null, c.name || c.symbol), h('span', null, `${c.symbol}, ${c.horizon === 'short' ? 'weeks view' : 'months-to-years view'}, ${SIDE_TEXT[c.side] || c.side}, since ${shortDay(c.date)}`)),
      h('td', { class: 'num' }, money(c.price, c.currency)), h('td', { class: 'num' }, money(c.target, c.currency)),
      h('td', { class: 'num' }, money(c.stop, c.currency))));
    const table = (heads, rows) => h('div', { class: 'card', style: 'padding:0;overflow-x:auto' }, h('table', null,
      h('thead', null, h('tr', null, heads.map((x, i) => h('th', { class: i ? 'num' : null }, x)))), h('tbody', null, rows)));
    const detail = [statTable('By indicator alignment', t.by_score_band, min), statTable('By market', t.by_market, min),
      compRows.length ? [h('h3', null, 'Which indicators lined up with the outcome'), table(['Indicator', 'Readings it supported', 'Avg move', 'Readings it opposed', 'Avg move'], compRows)] : null,
      h('h3', null, 'Ideas to review'), h('div', { class: 'card' }, notes.length ? h('ul', { class: 'clean' }, notes) : h('span', { class: 'muted' }, 'Nothing to suggest yet.'),
        h('p', { class: 'muted small' }, 'These are observations for you to judge. The app never changes its own rules.')),
      h('h3', null, 'What the words mean'), h('dl', { class: 'terms' }, TERMS.map(([k, v]) => [h('dt', null, h('b', null, k)), h('dd', null, v)]))];
    return h('div', null,
      h('h2', null, 'Track record'),
      scoreboard(t, min),
      h('p', { class: 'muted small' }, `${t.calls_total} readings recorded under the current rules: ${t.resolved} settled, ${t.open} open.${t.older_model_calls ? ` ${t.older_model_calls} more from earlier versions of the rules are kept in the record but not counted here.` : ''} A past record, not a forecast.`),
      groupCards(t),
      recent.length ? [h('h3', null, 'Latest results'), table(['Reading', 'Result', 'Move', 'Dates'], recent)] : null,
      open.length ? h('details', { class: 'card opencalls', open: !o.n }, h('summary', null, `Open readings (${t.open}${t.open > open.length ? `, latest ${open.length} listed` : ''})`),
        h('div', { style: 'overflow-x:auto' }, h('table', null, h('thead', null, h('tr', null, ['Reading', 'Price', 'Reference level', 'Risk level'].map((x, i) => h('th', { class: i ? 'num' : null }, x)))), h('tbody', null, open)))) : null,
      h('details', { class: 'card moredetail' }, h('summary', null, 'More detail'), detail),
      t.data_url ? h('p', { class: 'muted small' }, 'Every reading and outcome is stored as plain files for anyone to check: ', h('a', { href: t.data_url, target: '_blank', rel: 'noopener' }, 'the data branch on GitHub'), '.') : null);
  }

  /* ---------- shell ---------- */
  function render() {
    for (const b of document.querySelectorAll('.tabs button, .bottomnav button')) {
      if (b.dataset.tab === S.tab) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    }
    const view = $('#view');
    if (!S.data) { view.replaceChildren(h('div', { class: 'empty' }, S.error || 'Loading...')); return; }
    const builders = { signals: viewSignals, suggest: viewSuggest, ipo: viewIpo, market: viewMarket, events: viewEvents, track: viewTrack };
    view.replaceChildren((builders[S.tab] || viewSignals)());
    decorateHelp(view);
  }

  function setupNotifications() {
    const btn = $('#notify');
    const paint = () => { btn.setAttribute('aria-pressed', S.notify ? 'true' : 'false'); btn.textContent = S.notify ? 'Alerts on' : 'Alerts off'; };
    btn.addEventListener('click', async () => {
      if (S.notify) { S.notify = false; store.set('notify', false); paint(); return; }
      if ('Notification' in window && Notification.permission !== 'granted') {
        try { await Notification.requestPermission(); } catch (e) { /* ignore */ }
      }
      S.notify = true; store.set('notify', true); paint();
      toast('Alerts on for stocks in My watchlist while this page is open. For alerts when the app is closed, join the Telegram channel (link at the top of the page).');
    });
    paint();
  }

  function init() {
    for (const b of document.querySelectorAll('.tabs button, .bottomnav button')) b.addEventListener('click', () => { S.tab = b.dataset.tab; render(); $('#view').focus({ preventScroll: true }); });
    $('#refresh').addEventListener('click', () => load(true));
    const dlg = $('#detail');
    dlg.addEventListener('click', (e) => { if (e.target === dlg) closeDetail(); });
    dlg.addEventListener('close', () => { clearHash(); if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true }); });
    window.addEventListener('hashchange', openFromHash);
    setupNotifications();
    load(false);
    setInterval(() => { if (!document.hidden) load(false); }, REFRESH_MS);
    setInterval(updateHeader, 60 * 1000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) load(false); });
    if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost')) {
      navigator.serviceWorker.register('sw.js').catch(() => { /* offline support is optional */ });
    }
  }

  init();
})();
