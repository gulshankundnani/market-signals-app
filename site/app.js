'use strict';
(function () {
  const IPO = window.IPO;
  const REFRESH_MS = 5 * 60 * 1000;
  const CURRENCY = { INR: '₹', USD: '$' };
  const FREQ_CODE = { Monthly: 'M', Quarterly: 'Q', 'Half-yearly': 'H', Yearly: 'Y' };
  const FIXED_LISTS = ['Government-backed', 'High dividend', 'Low debt', 'Low volatility', 'Momentum leaders',
    'Strong long-term trend', 'Strong delivery buying', 'Oversold', 'Near 52-week high', 'New listings', 'US mega cap'];

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
  const toneOf = (score) => (score > 0.15 ? 'pos' : score < -0.15 ? 'neg' : 'neutral');
  const side = (label) => (label === 'Buy' || label === 'Strong Buy' ? 'buy' : label === 'Sell' || label === 'Strong Sell' ? 'sell' : 'hold');

  function badge(sig) {
    const label = (sig && sig.label) || 'N/A';
    const cls = label.includes('Buy') ? 'buy' : label.includes('Sell') ? 'sell' : label === 'Hold' ? 'hold' : 'na';
    return h('span', { class: `badge ${cls}` }, label, isNum(sig && sig.score) ? h('small', null, ` ${sig.score > 0 ? '+' : ''}${sig.score}`) : null);
  }

  /* ---------- state ---------- */
  const S = {
    data: null,
    error: null,
    tab: 'signals',
    filters: Object.assign({ market: 'ALL', horizon: 'short', signal: 'ALL', list: 'ALL', sort: 'score' }, store.get('filters', {})),
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
      current[s.symbol] = { short: s.short.label, long: s.long.label, news: s.news.alerts.map((a) => a.link || a.title) };
      const before = previous && previous[s.symbol];
      if (!before) continue;
      for (const [key, name] of [['short', 'short-term'], ['long', 'long-term']]) {
        const was = before[key];
        const now = current[s.symbol][key];
        if (was && was !== 'N/A' && now !== 'N/A' && side(was) !== side(now)) messages.push(`${s.name}: ${name} signal ${was} → ${now}`);
      }
      for (const a of s.news.alerts) {
        if (!before.news.includes(a.link || a.title)) messages.push(`${a.strong === 'neg' ? 'Negative' : 'Positive'} news, ${s.name}: ${a.title}`);
      }
    }
    store.set('lastSeen', current);
    messages.slice(0, 6).forEach(announce);
  }

  /* ---------- data loading ---------- */
  async function load(manual) {
    const button = $('#refresh');
    button.disabled = true;
    try {
      const response = await fetch('data.json', { cache: 'no-store' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      if (!data || !Array.isArray(data.stocks)) throw new Error('unexpected data');
      S.data = data;
      S.error = null;
      checkChanges();
      if (manual) toast('Data refreshed');
    } catch (err) {
      S.error = S.data ? 'Could not refresh. Showing the data already loaded.' : 'Could not load data. Check your connection and try again.';
    } finally {
      button.disabled = false;
    }
    updateHeader();
    render();
  }

  function updateHeader() {
    const banner = $('#banner');
    banner.hidden = !S.error;
    banner.textContent = S.error || '';
    const label = $('#updated');
    if (!S.data) { label.textContent = 'No data'; return; }
    const minutes = Math.max(0, Math.round((Date.now() - Date.parse(S.data.generated_at)) / 60000));
    label.textContent = `Updated ${ago(minutes)}` + (minutes > 180 ? ' (updates pause when markets are closed)' : '');
  }

  /* ---------- shared pieces ---------- */
  function newsList(items, withTopic) {
    if (!items || !items.length) return h('p', { class: 'muted small' }, 'No recent headlines found.');
    return h('ul', { class: 'clean' }, items.map((item) => {
      const url = safeUrl(item.link);
      const title = url ? h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, item.title) : item.title;
      return h('li', null,
        h('span', { class: `dot ${toneOf(item.sentiment)}`, title: `Sentiment ${number(item.sentiment)}` }),
        title,
        item.strong ? h('span', { class: `tag ${item.strong === 'neg' ? 'warn' : ''}` }, item.strong === 'neg' ? 'Breaking negative' : 'Breaking positive') : null,
        item.relevant === false ? h('span', { class: 'tag', title: 'The headline does not name this company, so it is not counted in the news tone' }, 'Related news') : null,
        h('div', { class: 'muted small' }, [item.source, ago(item.age_min), withTopic ? item.topic || item.market : null].filter(Boolean).join(' · ')));
    }));
  }

  function reasonsList(reasons) {
    return h('ul', { class: 'clean reasons' }, (reasons || []).map((r) => h('li', { class: r.tone }, r.text)));
  }

  function section(title, ...kids) {
    return h('section', { class: 'card' }, h('h3', null, title), ...kids);
  }

  function kv(pairs) {
    return h('div', { class: 'kv' }, pairs.map(([k, v]) => h('div', null, h('span', null, k), h('b', null, v))));
  }

  /* ---------- signals tab ---------- */
  function sortValue(s) {
    const f = S.filters;
    if (f.sort === 'change') return s.change_pct;
    if (f.sort === 'dividend') return s.dividend_yield;
    return s[f.horizon].score;
  }

  function visibleStocks() {
    const f = S.filters;
    const q = S.q.trim().toLowerCase();
    const rows = S.data.stocks.filter((s) => {
      if (f.market !== 'ALL' && s.market !== f.market) return false;
      if (f.list === 'WATCH') { if (!S.watch.has(s.symbol)) return false; }
      else if (f.list.startsWith('C:')) { if (!(S.lists[f.list.slice(2)] || []).includes(s.symbol)) return false; }
      else if (f.list !== 'ALL' && !s.lists.includes(f.list)) return false;
      const label = s[f.horizon].label;
      if (f.signal === 'BUY' && side(label) !== 'buy') return false;
      if (f.signal === 'HOLD' && label !== 'Hold') return false;
      if (f.signal === 'SELL' && side(label) !== 'sell') return false;
      if (q && !(s.name.toLowerCase().includes(q) || s.symbol.toLowerCase().includes(q))) return false;
      return true;
    });
    rows.sort((a, b) => {
      if (f.sort === 'name') return a.name.localeCompare(b.name);
      const av = sortValue(a), bv = sortValue(b);
      if (!isNum(av) && !isNum(bv)) return 0;
      if (!isNum(av)) return 1;
      if (!isNum(bv)) return -1;
      return bv - av;
    });
    return rows;
  }

  function listChips() {
    const f = S.filters;
    const pool = S.data.stocks.filter((s) => f.market === 'ALL' || s.market === f.market);
    const counts = new Map();
    for (const s of pool) for (const name of s.lists) counts.set(name, (counts.get(name) || 0) + 1);
    const chips = [['ALL', 'All', pool.length], ['WATCH', 'My watchlist', pool.filter((s) => S.watch.has(s.symbol)).length]];
    for (const name of Object.keys(S.lists)) chips.push(['C:' + name, name, pool.filter((s) => S.lists[name].includes(s.symbol)).length]);
    for (const name of FIXED_LISTS) if (counts.has(name)) chips.push([name, name, counts.get(name)]);
    const sectors = [...counts.keys()].filter((n) => !FIXED_LISTS.includes(n) && !['Other'].includes(n)).sort();
    for (const name of sectors) chips.push([name, name, counts.get(name)]);
    return chips.map(([key, label, count]) => h('button', {
      type: 'button', class: 'chip' + (f.list === key ? ' on' : ''), 'aria-pressed': f.list === key ? 'true' : 'false',
      onclick: () => { f.list = key; saveFilters(); updateSignals(); },
    }, `${label} (${count})`));
  }

  function stockRow(s) {
    const f = S.filters;
    const press = s.pressure ? s.pressure.label.replace(' pressure', '') : '–';
    const pressCls = s.pressure && s.pressure.label.startsWith('Buying') ? 'pos' : s.pressure && s.pressure.label.startsWith('Selling') ? 'neg' : '';
    const star = h('button', {
      type: 'button', class: 'star' + (S.watch.has(s.symbol) ? ' on' : ''), 'aria-label': `${S.watch.has(s.symbol) ? 'Remove' : 'Add'} ${s.name} ${S.watch.has(s.symbol) ? 'from' : 'to'} watchlist`,
      'aria-pressed': S.watch.has(s.symbol) ? 'true' : 'false',
      onclick: (e) => { e.stopPropagation(); S.watch.has(s.symbol) ? S.watch.delete(s.symbol) : S.watch.add(s.symbol); saveWatch(); updateSignals(); },
    }, S.watch.has(s.symbol) ? '★' : '☆');
    const row = h('tr', { class: 'stock', tabindex: '0', 'aria-label': `${s.name} details` },
      h('td', null, star),
      h('td', { class: 'name' }, h('b', null, s.name), h('span', null, s.symbol, s.new ? ' · new listing' : '', s.stale ? ' · stale price' : '')),
      h('td', { class: 'num' }, money(s.price, s.currency)),
      h('td', { class: `num ${isNum(s.change_pct) ? (s.change_pct >= 0 ? 'up' : 'down') : ''}` }, signed(s.change_pct)),
      h('td', null, badge(s[f.horizon])),
      h('td', { class: `hide-sm ${pressCls}` }, press),
      h('td', { class: 'num hide-sm' }, isNum(s.dividend_yield) && s.dividend_yield > 0
        ? [percent(s.dividend_yield, 2), s.dividend ? h('span', { class: 'muted small', title: `${s.dividend.frequency} dividend${s.dividend.overdue ? ', payment overdue' : ''}` }, ` ${FREQ_CODE[s.dividend.frequency] || ''}${s.dividend.overdue ? '!' : ''}`) : null]
        : '–'),
      h('td', { class: 'hide-sm' }, h('span', { class: `dot ${toneOf(s.news.score)}`, title: 'News tone' }), s.news.alerts.length ? h('span', { class: 'tag warn' }, '!') : null));
    row.addEventListener('click', () => openDetail(s.symbol, row));
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter') openDetail(s.symbol, row); });
    return row;
  }

  function updateSignals() {
    const chips = $('#chips'), results = $('#results');
    if (!chips || !results) return;
    chips.replaceChildren(...listChips());
    const rows = visibleStocks();
    const f = S.filters;
    const head = h('div', { class: 'row small muted', style: 'justify-content:space-between;margin-bottom:6px' },
      h('span', null, `${rows.length} stock${rows.length === 1 ? '' : 's'} · ${f.horizon === 'short' ? 'short-term (weeks)' : 'long-term (months to years)'} signal`),
      f.list.startsWith('C:') ? h('button', { type: 'button', class: 'chip', onclick: () => {
        if (!confirm(`Delete the list "${f.list.slice(2)}"? The stocks are not deleted.`)) return;
        delete S.lists[f.list.slice(2)]; store.set('lists', S.lists); f.list = 'ALL'; saveFilters(); updateSignals();
      } }, 'Delete this list') : null);
    if (!rows.length) {
      results.replaceChildren(head, h('div', { class: 'empty' }, f.list === 'WATCH' ? 'Your watchlist is empty. Tap the star next to a stock to add it.' : 'No stocks match these filters.'));
      return;
    }
    results.replaceChildren(head, h('div', { class: 'card', style: 'padding:0;overflow-x:auto' }, h('table', null,
      h('thead', null, h('tr', null, h('th', null, ''), h('th', null, 'Stock'), h('th', { class: 'num' }, 'Price'), h('th', { class: 'num' }, 'Day'),
        h('th', null, 'Signal'), h('th', { class: 'hide-sm' }, 'Pressure'), h('th', { class: 'num hide-sm' }, 'Dividend'), h('th', { class: 'hide-sm' }, 'News'))),
      h('tbody', null, rows.map(stockRow)))));
  }

  function select(label, value, options, onChange) {
    const el = h('select', { onchange: (e) => onChange(e.target.value) }, options.map(([v, text]) => h('option', { value: v, selected: v === value }, text)));
    return h('label', { class: 'field' }, label, el);
  }

  function viewSignals() {
    const f = S.filters;
    const search = h('input', { type: 'search', placeholder: 'Name or symbol', value: S.q, 'aria-label': 'Search stocks', oninput: (e) => { S.q = e.target.value; updateSignals(); } });
    const bar = h('div', { class: 'card' }, h('div', { class: 'row' },
      h('label', { class: 'field' }, 'Search', search),
      select('Market', f.market, [['ALL', 'All markets'], ['IN', 'India'], ['US', 'United States']], (v) => { f.market = v; f.list = 'ALL'; saveFilters(); updateSignals(); }),
      select('Signal', f.signal, [['ALL', 'Any'], ['BUY', 'Buy'], ['HOLD', 'Hold'], ['SELL', 'Sell']], (v) => { f.signal = v; saveFilters(); updateSignals(); }),
      select('Sort by', f.sort, [['score', 'Signal strength'], ['change', 'Day change'], ['dividend', 'Dividend yield'], ['name', 'Name']], (v) => { f.sort = v; saveFilters(); updateSignals(); }),
      h('div', { class: 'field' }, 'Horizon', h('div', { class: 'row', style: 'gap:6px' },
        ['short', 'long'].map((hz) => h('button', { type: 'button', class: 'chip' + (f.horizon === hz ? ' on' : ''), 'aria-pressed': f.horizon === hz ? 'true' : 'false',
          onclick: () => { f.horizon = hz; saveFilters(); render(); } }, hz === 'short' ? 'Short-term' : 'Long-term'))))));
    const makeList = h('button', { type: 'button', class: 'chip', onclick: () => {
      const name = (prompt('Name for the new list (for example "Retirement")') || '').trim().slice(0, 30);
      if (!name) return;
      if (S.lists[name]) { toast('A list with that name already exists'); return; }
      S.lists[name] = []; store.set('lists', S.lists); f.list = 'C:' + name; saveFilters(); updateSignals();
      toast('List created. Open a stock to add it to this list.');
    } }, '+ New list');
    const root = h('div', null, bar, h('div', { class: 'chips', id: 'chips', role: 'group', 'aria-label': 'Lists' }), h('div', { class: 'row', style: 'margin-bottom:8px' }, makeList), h('div', { id: 'results' }));
    queueMicrotask(updateSignals);
    return root;
  }

  /* ---------- detail dialog ---------- */
  let lastFocus = null;
  function openDetail(symbol, opener) {
    const s = S.data.stocks.find((x) => x.symbol === symbol);
    if (!s) return;
    lastFocus = opener || document.activeElement;
    const dlg = $('#detail');
    dlg.replaceChildren(detailView(s));
    if (!dlg.open) dlg.showModal();
    dlg.scrollTop = 0;
  }
  function closeDetail() { const dlg = $('#detail'); if (dlg.open) dlg.close(); }

  function signalCard(title, sig) {
    const pos = isNum(sig.score) ? clamp((sig.score + 100) / 2, 0, 100) : 50;
    return h('div', null, h('h3', null, title), badge(sig),
      isNum(sig.score) ? h('div', { class: 'meter', role: 'img', 'aria-label': `Score ${sig.score} on a scale from minus 100 to plus 100` }, h('i', { style: `left:${pos}%` })) : null,
      reasonsList(sig.reasons));
  }

  function chart(s) {
    const closes = s.spark.filter(isNum);
    if (closes.length < 5) return h('p', { class: 'muted small' }, 'Not enough price history for a chart.');
    const sma = s.spark_sma50;
    const stop = s.stop && s.stop.price;
    const zones = ((s.levels || {}).zones) || { support: [], resistance: [] };
    const lo0 = Math.min(...closes), hi0 = Math.max(...closes);
    const near = (v) => isNum(v) && v > lo0 * 0.88 && v < hi0 * 1.12;
    const overlays = [];
    if (near(stop)) overlays.push({ v: stop, color: 'var(--sell)', dash: '6 4', label: 'Stop-loss' });
    for (const z of zones.support.slice(0, 1)) if (near(z.level)) overlays.push({ v: z.level, color: 'var(--buy)', dash: '2 4', label: 'Support' });
    for (const z of zones.resistance.slice(0, 1)) if (near(z.level)) overlays.push({ v: z.level, color: 'var(--muted)', dash: '2 4', label: 'Resistance' });
    const all = closes.concat(overlays.map((o) => o.v));
    const lo = Math.min(...all), hi = Math.max(...all), pad = (hi - lo) * 0.08 || 1;
    const W = 640, H = 220, L = 8, R = 62, T = 10, B = 20;
    const x = (i) => L + (i / (closes.length - 1)) * (W - L - R);
    const y = (v) => T + (1 - (v - (lo - pad)) / (hi - lo + 2 * pad)) * (H - T - B);
    const line = (values) => values.map((v, i) => (isNum(v) ? `${i && isNum(values[i - 1]) ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}` : '')).join(' ');
    const g = svg('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `Price over the last ${closes.length} trading days, from ${closes[0].toFixed(2)} to ${closes[closes.length - 1].toFixed(2)}` });
    g.append(svg('path', { d: line(sma), fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.5, 'stroke-dasharray': '5 3' }));
    g.append(svg('path', { d: line(closes), fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2 }));
    for (const o of overlays) {
      g.append(svg('line', { x1: L, x2: W - R, y1: y(o.v), y2: y(o.v), stroke: o.color, 'stroke-width': 1.3, 'stroke-dasharray': o.dash }));
      g.append(svg('text', { x: W - R + 4, y: y(o.v) + 4, fill: o.color, 'font-size': 11 }, `${o.label} ${o.v.toFixed(2)}`));
    }
    g.append(svg('text', { x: W - R + 4, y: y(closes[closes.length - 1]) + 4, fill: 'var(--text)', 'font-size': 11, 'font-weight': 700 }, closes[closes.length - 1].toFixed(2)));
    g.append(svg('text', { x: L, y: H - 4, fill: 'var(--muted)', 'font-size': 11 }, `${closes.length} trading days · dashed grey = 50-day average`));
    return g;
  }

  function positionCalculator(s) {
    const out = h('p', { class: 'small', 'aria-live': 'polite' });
    const capital = h('input', { type: 'number', min: '0', step: 'any', value: S.calc.capital, 'aria-label': 'Capital' });
    const risk = h('input', { type: 'number', min: '0.1', max: '100', step: '0.1', value: S.calc.risk, 'aria-label': 'Risk per trade in percent' });
    const update = () => {
      const cap = parseFloat(capital.value), pct = parseFloat(risk.value);
      if (!(cap > 0) || !(pct > 0 && pct <= 100)) { out.textContent = 'Enter a capital above 0 and a risk between 0.1% and 100%.'; return; }
      S.calc = { capital: cap, risk: pct }; store.set('calc', S.calc);
      if (!s.stop) { out.textContent = 'No stop-loss level is available for this stock, so a position size cannot be worked out.'; return; }
      const perShare = s.price - s.stop.price;
      const shares = Math.floor((cap * pct / 100) / perShare);
      const value = shares * s.price;
      out.textContent = `If the stop-loss at ${money(s.stop.price, s.currency)} is hit you lose about ${money(cap * pct / 100, s.currency)} (${pct}% of capital). That allows ${shares} share${shares === 1 ? '' : 's'}, a position of ${money(value, s.currency)} (${(value / cap * 100).toFixed(0)}% of capital).` + (value > cap ? ' This is larger than your capital: use a smaller risk or a wider stop.' : '');
    };
    capital.addEventListener('input', update); risk.addEventListener('input', update);
    update();
    return section('Position size', h('div', { class: 'row' },
      h('label', { class: 'field' }, `Capital (${CURRENCY[s.currency] || s.currency || ''})`, capital), h('label', { class: 'field' }, 'Risk per trade (%)', risk)), out,
      h('p', { class: 'muted small' }, 'Prices can gap past a stop-loss, so the real loss can be bigger.'));
  }

  function backtestBlock(b, title) {
    if (!b) return h('div', null, h('h3', null, title), h('p', { class: 'muted small' }, 'Not enough history to test this rule.'));
    const o = b.oos;
    const verdict = o.total_return > o.buy_hold_return
      ? 'In the most recent 40% of the test, the rule beat buy-and-hold.'
      : 'In the most recent 40% of the test, the rule did NOT beat buy-and-hold.';
    return h('div', null, h('h3', null, title),
      h('p', { class: o.total_return > o.buy_hold_return ? 'pos' : 'neg' }, verdict),
      b.few_trades ? h('p', { class: 'tag warn' }, 'Few trades, so the result is not reliable') : null,
      h('table', null, h('thead', null, h('tr', null, h('th', null, ''), h('th', { class: 'num' }, 'Rule'), h('th', { class: 'num' }, 'Buy and hold'))),
        h('tbody', null,
          h('tr', null, h('td', null, `Whole test (${Math.round(b.tested_days / 252 * 10) / 10} years)`), h('td', { class: 'num' }, percent(b.total_return)), h('td', { class: 'num' }, percent(b.buy_hold_return))),
          h('tr', null, h('td', null, 'Recent 40%'), h('td', { class: 'num' }, percent(o.total_return)), h('td', { class: 'num' }, percent(o.buy_hold_return))))),
      kv([['Trades', b.trades], ['Win rate', percent(b.win_rate, 0)], ['Profit factor', number(b.profit_factor)], ['Worst fall', percent(b.max_drawdown, 0)], ['Sharpe', number(b.sharpe)], ['Time invested', percent(b.exposure, 0)]]));
  }

  function fundamentalsBlock(s) {
    const f = s.fundamentals || {};
    if (!Object.keys(f).length && !isNum(s.dividend_yield)) return h('p', { class: 'muted small' }, 'Fundamentals are not available for this stock right now.');
    return kv([['Market cap', big(f.market_cap, s.currency)], ['P/E', number(f.pe, 1)], ['Forward P/E', number(f.forward_pe, 1)], ['Price/Book', number(f.pb, 1)],
      ['Return on equity', percent(f.roe)], ['Profit margin', percent(f.profit_margin)], ['Revenue growth', percent(f.revenue_growth)], ['Earnings growth', percent(f.earnings_growth)],
      ['Debt/Equity', number(f.debt_to_equity)], ['Dividend yield (12 months)', percent(s.dividend_yield, 2)], ['Payout ratio', percent(f.payout_ratio, 0)]]);
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
        h('tbody', null, d.history.map((p) => h('tr', null, h('td', null, p.date), h('td', { class: 'num' }, money(p.amount, s.currency)), h('td', null, p.special ? 'Special (one-off)' : 'Regular'))))),
      h('p', { class: 'muted small' }, 'Worked out from payments actually made: amount per payment times the number of payments, divided by the current price. The yield moves with the share price, and a past dividend can be cut.'));
  }

  function peersBlock(s) {
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

  function detailView(s) {
    const bt = s.backtest || {};
    const r = s.risk;
    return h('div', null,
      h('div', { class: 'dhead' },
        h('div', null, h('h2', { style: 'margin:0' }, s.name), h('div', { class: 'muted small' }, `${s.symbol} · ${s.exchange || ''} · ${s.sector}`),
          h('div', { style: 'font-size:22px;font-weight:700' }, money(s.price, s.currency), ' ', h('span', { class: `small ${isNum(s.change_pct) && s.change_pct >= 0 ? 'up' : 'down'}` }, signed(s.change_pct))),
          h('div', { class: 'muted small' }, `Last price date ${s.as_of}`, s.stale ? ' · price looks stale, the stock may be halted' : '')),
        h('button', { type: 'button', class: 'btn ghost', onclick: closeDetail }, 'Close')),
      h('div', { class: 'dbody' },
        s.news.alerts.length ? h('section', { class: 'card', style: 'border-color:var(--sell)' }, h('h3', null, 'Breaking headlines'), newsList(s.news.alerts)) : null,
        h('div', { class: 'grid' }, h('section', { class: 'card' }, signalCard('Short-term view (weeks)', s.short)), h('section', { class: 'card' }, signalCard('Long-term view (months to years)', s.long))),
        s.new ? h('p', { class: 'tag warn' }, `New listing${s.listed ? ' (listed ' + s.listed + ')' : ''}: little price history, so signals are limited`) : null,
        section('Price chart', chart(s)),
        section('Buying and selling pressure', ...pressureBlock(s)),
        positionCalculator(s),
        section('Levels and risk', kv([
          ['Stop-loss', s.stop ? `${money(s.stop.price, s.currency)} (${signed(s.stop.pct, 1)})` : '–'],
          ['Support', ((s.levels || {}).zones || { support: [] }).support.map((z) => z.level.toFixed(2)).join(', ') || '–'],
          ['Resistance', ((s.levels || {}).zones || { resistance: [] }).resistance.map((z) => z.level.toFixed(2)).join(', ') || '–'],
          ['Yearly volatility', r ? percent(r.annual_vol, 0) : '–'], ['1-day loss (95% VaR)', r ? percent(r.var95) : '–'],
          ['Bad-day average loss', r ? percent(r.expected_shortfall95) : '–'], ['Worst fall (1 year)', r ? percent(r.max_drawdown, 0) : '–'], ['Beta', r ? number(r.beta) : '–']])),
        section('Patterns', (s.patterns || []).length ? (s.patterns).map((p) => h('div', { title: p.note, style: 'margin-bottom:6px' }, h('span', { class: `tag ${p.bias === 'bearish' ? 'warn' : ''}` }, `${p.name} (${p.bias})`), h('span', { class: 'muted small' }, p.note))) : h('p', { class: 'muted small' }, 'No notable pattern on the latest candles.')),
        section('Fundamentals', fundamentalsBlock(s)),
        section('Dividends', dividendBlock(s)),
        section('Peers in the same sector', peersBlock(s)),
        section('News', newsList(s.news.items)),
        section('How have these rules worked on this stock?', h('div', { class: 'grid' }, backtestBlock(bt.short, 'Short-term rule'), backtestBlock(bt.long, 'Long-term rule')),
          h('p', { class: 'muted small' }, 'Fixed rules tested on up to 5 years of this stock, trading at the next open with 0.1% cost per trade. Past results do not guarantee future results.')),
        section('Lists', listsBlock(s))));
  }

  /* ---------- suggestions tab ---------- */
  function suggestions() {
    const p = S.profile;
    const maxVol = { low: 0.30, medium: 0.45, high: 9 }[p.risk];
    const picks = [];
    for (const s of S.data.stocks) {
      if (p.market !== 'ALL' && s.market !== p.market) continue;
      if (s.stale || s.new || s.bars < 260 || !isNum(s.short.score) || !isNum(s.long.score)) continue;
      if (s.news.alerts.some((a) => a.strong === 'neg')) continue;
      const vol = s.risk && s.risk.annual_vol;
      if (!isNum(vol) || vol > maxVol) continue;
      const blend = p.horizon === 'short' ? 0.7 * s.short.score + 0.3 * s.long.score : 0.3 * s.short.score + 0.7 * s.long.score;
      if (blend < 15) continue;
      const f = s.fundamentals || {};
      const quality = IPO.qualityFromFundamentals(f, null);
      const q = isNum(quality.score) ? quality.score / 100 : 0;
      // Cautious yield: the lower of what was paid and what the recurring payments imply. Overdue payers count as zero.
      const div = s.dividend;
      const cautious = div && isNum(div.forward_yield) ? Math.min(s.dividend_yield || 0, div.forward_yield) : (s.dividend_yield || 0);
      const dividend = div && div.overdue ? 0 : Math.min(cautious / 0.06, 1);
      const growth = clamp((isNum(f.revenue_growth) ? f.revenue_growth : 0) / 0.3, -1, 1);
      const momentum = clamp(((s.indicators || {}).momentum126 || 0) / 0.3, -1, 1);
      const calm = clamp(1 - vol / 0.5, 0, 1);
      const income = 0.3 * dividend + 0.3 * (blend / 100) + 0.2 * calm + 0.2 * q;
      const growing = 0.3 * (blend / 100) + 0.25 * momentum + 0.2 * growth + 0.25 * q;
      const score = p.goal === 'income' ? income : p.goal === 'growth' ? growing : (income + growing) / 2;
      picks.push({ s, score, blend, vol, quality, dividend: div && div.overdue ? 0 : cautious, growthRaw: f.revenue_growth });
    }
    return picks.sort((a, b) => b.score - a.score).slice(0, 10);
  }

  function suggestionCard(pick, rank) {
    const { s } = pick;
    const why = [`${S.profile.horizon === 'short' ? 'Short' : 'Long'}-term signal: ${s[S.profile.horizon].label}`];
    if (pick.dividend >= 0.02) why.push(`Dividend yield ${percent(pick.dividend, 1)}${pick.s.dividend && pick.s.dividend.payments_per_year ? ' (' + pick.s.dividend.frequency.toLowerCase() + ')' : ''}`);
    if (pick.vol < 0.25) why.push(`Low volatility (${percent(pick.vol, 0)})`);
    if (isNum(pick.growthRaw) && pick.growthRaw > 0.1) why.push(`Revenue growth ${percent(pick.growthRaw, 0)}`);
    if (pick.quality.score !== null && pick.quality.score >= 30) why.push('Healthy fundamentals');
    if (s.pressure && s.pressure.label.startsWith('Buying')) why.push('Buying pressure');
    if (s.lists.includes('Strong delivery buying')) why.push('Strong delivery buying');
    const risks = [`Moves about ${percent(pick.vol, 0)} a year`];
    if (s.stop) risks.push(`stop-loss ${signed(s.stop.pct, 1)} below the price`);
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
        : [h('div', { class: 'empty' }, 'No stock passes your filters right now. Try a higher risk level or both markets. The app skips stocks with a weak signal, breaking negative news or too little history.')]));
    };
    const form = h('div', { class: 'card' }, h('div', { class: 'row' },
      select('My goal', p.goal, [['income', 'Regular income (dividends)'], ['growth', 'Growth'], ['balanced', 'Balanced']], (v) => { p.goal = v; draw(); }),
      select('Time horizon', p.horizon, [['short', 'Short (weeks)'], ['long', 'Long (months to years)']], (v) => { p.horizon = v; draw(); }),
      select('Risk I can accept', p.risk, [['low', 'Low (steady stocks)'], ['medium', 'Medium'], ['high', 'High']], (v) => { p.risk = v; draw(); }),
      select('Market', p.market, [['ALL', 'Both'], ['IN', 'India'], ['US', 'United States']], (v) => { p.market = v; draw(); })),
      h('p', { class: 'muted small', style: 'margin-bottom:0' }, 'A ranked screen using your answers, signals, fundamentals and risk. It is not personal advice. Check the stock page and your own situation before acting.'));
    queueMicrotask(draw);
    return h('div', null, form, results);
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
        h('h3', null, `${S.ipoName ? S.ipoName + ': ' : ''}${v.label}`, isNum(v.score) ? ` (score ${v.score > 0 ? '+' : ''}${v.score})` : ''),
        isNum(v.score) ? h('p', null, 'Listing-day and short-term view: ', h('b', null, v.label), '. Long-term holding view: ', badge({ label: v.longTerm.label, score: v.longTerm.score }), '.') : h('p', null, 'Fill in at least profit margin, revenue growth, debt or return on equity, plus the valuation fields, to get a verdict.'),
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
          h('td', { class: 'name' }, h('b', null, item.name), item.label ? h('span', null, `Check: ${item.label}${isNum(item.score) ? ' (' + (item.score > 0 ? '+' : '') + item.score + ')' : ''}`) : null),
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
      h('p', { class: 'muted small' }, 'The grey-market premium (GMP) is an unofficial market rumour, not an exchange price. It can change daily or be wrong. Estimated profit is premium times lot size and applies only if shares are allotted; it ignores brokerage, taxes and moves on listing day. The demand label measures hype from the premium and subscription, not the quality of the business. SME IPOs are riskier, with larger minimum amounts and thin trading.'));
  }

  function viewIpo() {
    const ipos = S.data.ipos || { us: [], headlines: [], us_ok: false };
    const tag = (status) => h('span', { class: 'tag' }, status);
    const range = (d) => (isNum(d.price_low) ? (d.price_high !== d.price_low ? `$${d.price_low}–$${d.price_high}` : `$${d.price_low}`) : '–');
    const usTable = ipos.us.length ? h('div', { class: 'card', style: 'padding:0;overflow-x:auto' }, h('table', null,
      h('thead', null, h('tr', null, h('th', null, 'Company'), h('th', null, 'Status'), h('th', { class: 'num' }, 'Price range'), h('th', { class: 'num hide-sm' }, 'Size'), h('th', null, 'Date'))),
      h('tbody', null, ipos.us.map((d) => h('tr', null, h('td', { class: 'name' }, h('b', null, d.name), h('span', null, `${d.symbol || 'ticker TBD'} ${d.exchange ? '· ' + d.exchange : ''}`)),
        h('td', null, tag(d.status)), h('td', { class: 'num' }, range(d)), h('td', { class: 'num hide-sm' }, big(d.amount, 'USD')), h('td', null, d.date || '–')))))) :
      h('div', { class: 'empty card' }, ipos.us_ok ? 'No US IPOs on the calendar right now.' : 'The US IPO calendar could not be loaded this time.');
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
      h('h2', null, 'US IPO calendar'), h('p', { class: 'muted small' }, 'From the Nasdaq calendar. Financials for companies that have not listed yet are not available here: use the optional checker at the bottom with figures from the prospectus.'), usTable,
      h('h2', null, 'IPO headlines'), h('div', { class: 'card' }, newsList(ipos.headlines, true)),
      h('h2', null, 'Check an IPO yourself (optional)'),
      h('p', { class: 'muted small' }, 'Everything above updates by itself. Use this only for an IPO that is not in the table above, or to score a company\'s financials: it is the one place you type figures.'),
      ipoChecker(), h('h3', null, 'My IPO tracker'), h('div', { id: 'ipoTracker' }));
  }

  /* ---------- market tab ---------- */
  function viewMarket() {
    const m = S.data.market || { watch: [], news: [] };
    const cards = m.watch.map((w) => h('div', { class: 'card' }, h('div', { class: 'muted small' }, w.name),
      h('div', { style: 'font-size:20px;font-weight:700' }, number(w.price, w.price > 1000 ? 0 : 2)),
      h('div', { class: isNum(w.change_pct) && w.change_pct >= 0 ? 'up' : 'down' }, `${signed(w.change_pct)} today`),
      h('div', { class: 'muted small' }, `${signed(w.month_pct, 1)} in a month`),
      w.regime ? h('span', { class: `tag ${w.regime === 'Downtrend' || w.regime === 'Fearful' ? 'warn' : ''}` }, w.regime) : null));
    return h('div', null,
      h('h2', null, 'Market conditions'), h('div', { class: 'grid' }, cards.length ? cards : h('div', { class: 'empty' }, 'Market data unavailable.')),
      h('p', { class: 'muted small' }, 'Uptrend means the index is above its 50 and 200-day averages. A fearful VIX (above 25) means large swings: use smaller positions.'),
      h('h2', null, 'World and market news'), h('div', { class: 'card' }, newsList(m.news, true)),
      h('p', { class: 'muted small' }, (S.data.exchange_data || {}).note || ''));
  }

  /* ---------- shell ---------- */
  function render() {
    for (const b of document.querySelectorAll('.tabs button')) {
      if (b.dataset.tab === S.tab) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    }
    const view = $('#view');
    if (!S.data) { view.replaceChildren(h('div', { class: 'empty' }, S.error || 'Loading...')); return; }
    const builders = { signals: viewSignals, suggest: viewSuggest, ipo: viewIpo, market: viewMarket };
    view.replaceChildren((builders[S.tab] || viewSignals)());
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
      toast('Alerts on for stocks in My watchlist while this page is open. For alerts when the app is closed, set up Telegram (see the README).');
    });
    paint();
  }

  function init() {
    for (const b of document.querySelectorAll('.tabs button')) b.addEventListener('click', () => { S.tab = b.dataset.tab; render(); $('#view').focus({ preventScroll: true }); });
    $('#refresh').addEventListener('click', () => load(true));
    const dlg = $('#detail');
    dlg.addEventListener('click', (e) => { if (e.target === dlg) closeDetail(); });
    dlg.addEventListener('close', () => { if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true }); });
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
