# Handoff: Stock Signals (market-signals-app): news intelligence, AI scoring, learning loops, data health
Status: open | Saved: 2026-11-20 evening | Repo: D:market-signals-app | Branch: main (all verified locally: 747 tests, full build fin8, browser; only push + live run remain)
Resume line: `resume handoff D:\market-signals-app\.claude\handoff\2026-11-20-1700-news-intelligence.md`
The user pushes with GitHub Desktop. `git status` shows MANY uncommitted files (nothing from this session after commit 52da06e is pushed). Never push for them.

## Project in one paragraph
Free PWA on GitHub Pages + GitHub Actions. Python-stdlib engine (`engine/`) builds `site/data.json`, `index.json`, `index_extra.json`, `detail/NN.json`, `state.json`;
static JS site (`site/app.js`, `site/ipo.js`, `site/index.html`). NSE only (US market removed). `update.yml` runs every ~10 min (started by `refresh-loop.yml`). Telegram alerts.
Prices: Yahoo, cross-checked against the NSE end-of-day file (UDiFF, fallback full bhavcopy) and the NSE live snapshot (`engine/nse_live.py`).

## User's standing rules
- Never commit/push unless asked. Secrets only in GitHub Secrets (never in repo/browser). Never work around 401/403/captcha/bot protection.
- No third-party notification services (Telegram only). No manual IPO data entry. No monetisation plan. No user email/personal data in outside requests.
- Headlines shown exactly as published with source, time and link; copy no article text. Neutral reading vocabulary ("Aligned bullish/Mixed/Aligned bearish"), no guarantees.
- PASS/FAIL/UNVERIFIED only on evidence. No threshold changes without statistical evidence; never tune to beat buy-and-hold.
- CHANGED this session by the user: news MAY be an additional low-weight part of readings (done: weight 5 of 105, short-term only) but must never create a signal alone ("validation layer").
- Test locally (full build + browser) before asking them to push; they want ONE clean push. Be honest: no LLM exists in the base pipeline; "AI" = deterministic rules unless the optional model job has scored a headline.
- Environment: Git Bash (heredocs break on some quotes/backslashes: write files with the Write tool, not shell heredocs); full local build ~8 min: run in background, wait with an until-loop on the log's `done` line (do not poll). Python 3.13 local, 3.12 in Actions.

## Already pushed and verified live (commit 52da06e, run succeeded)
Track-record void rule + v4 model; stale-price guards; US removal; NSE UDiFF + live snapshot; provenance; statements; quality card; Breakouts; horizon Short/Long/Both; learning outlook UI; cache v37. (A later registry fix for leftover US "new listings" may or may not be in that push: check `git status`/`git log`; `engine/discover.py` + `engine/build.py` handle it and tests exist.)

## Done since then, UNCOMMITTED, all tests passing (740 Python tests)
- Learning: `engine/learning.py`, `engine/learn.py`, `.github/workflows/learn.yml` (weekly; never run live yet; result on 200 stocks: NO edge vs base rate at 5/20/60 days). Build reads `learning/outlook-table.json` from the data branch raw URL, falls back to `learning.json` in the previous publish.
- News tone word list `engine/sentiment.py`; `signals.short_detail(ind, i, news=(value,text))`, `NEWS_WEIGHT=5` optional (coverage unaffected).
- Data learning loop `engine/data_learning.py` (incident log, source reliability, quarantine 3 incidents in 6 runs, learned lag hours) wired in build; UI "Data health" card on Market tab.
- NEWS INTELLIGENCE (user's two long requirement messages):
  - `engine/news_intel.py`: event classifier, story scoring (sentiment -100..100, credibility, relevance, impact 0..100, short/long implications, risk), syndication dedupe (Jaccard 0.55 + same official filing on NSE and BSE), `analyse_stock` (label, interpretation Bullish/Bearish/Neutral/Mixed, fundamental impact, risks, explanation), `reaction` (price/volume/sector), `validate` (confirms/conflicts/neutral vs reading), `tone_component`, `sector_table`.
  - `engine/news_sources.py`: providers NSE announcements API (works, ~620 items/2 days), BSE announcements RSS `https://www.bseindia.com/data/xml/announcements.xml` (works, 6.7k items, map to NSE symbol by company name), RSS Business Standard/Mint/Economic Times/Moneycontrol/BusinessLine (work), optional NewsData.io (only with env `NEWSDATA_API_KEY`; untested; free plan 12h delayed, "testing & personal projects", needs attribution "Powered by NewsData.io API"). Each provider isolated (`_guard`: backoff on 401/403/429, 5-min min interval); `merge_archive`, `compact`, `NameIndex`, `map_items`. Smoke test on live data: 7,652 items in 6 s, mapped to 587 stocks.
  - Facts: NSE RSS file is EMPTY (API used instead). `api.bseindia.com` = 403 (not used). "Hypeless API" not found anywhere (not integrated; ask the user for docs).
  - AI layer (user asked "small LLM? can it run on GitHub?"): answer given: Pages cannot run a model; Actions can; chosen path = GitHub Models free tier via the job's GITHUB_TOKEN with `permissions: models: read`, endpoint https://models.github.ai/inference/chat/completions, model var `NEWS_AI_MODEL` (default openai/gpt-4o-mini); free-tier limits UNVERIFIED and the call is UNTESTED against the live API. FinBERT rejected (licence unclear, ~111 MB quantised, needs onnxruntime). Files: `engine/ai_sentiment.py`, `engine/news_ai.py` (dry run works: 255 headlines worth scoring), `.github/workflows/news-ai.yml` (every 30 min weekdays; off with repo var NEWS_AI=off). The build reads `news-ai/scores-recent.json` from the data branch (fallback `news_ai.json` in the previous publish).
  - Build wiring DONE in `engine/build.py`: `news_sources.collect` + archive + `NameIndex` before analysis -> `ctx["news_pool"]`, `ctx["news_ai"]`; `analyse()` builds stories (Google items + pool, with AI scores), `record["news_intel"]`, `record["volume_ratio"]`, component for `short_detail`; `apply_news_context` (reaction with sector d1 move, validation vs `record["short"]["label"]`); `news_dashboard(...)` -> `data["news_intel"]`; state keys `news_providers`, `news_archive`; site file `news_ai.json`. `catalog.lite()` keeps only `news_summary(...)`.
  - UI DONE in `site/app.js` + `site/index.html` (applied via a script, syntax-checked, NOT yet seen in a browser): News tab (top nav + bottom nav), `viewNews` (Sentiment Dashboard: market tone, sector table, most positive/negative, price-vs-news divergences, source status; filterable story feed), stock-page `newsIntelCard` (replaces old news-tone card) and `newsLine`, News column cell + News filter in Readings, Screens validation/risk text. Cache `signals-v40`.

## NOT done / next (in order)
1. Add tests for the new build functions (`apply_news_context`, `reading_side`, `news_dashboard`, lite `news_summary`) and a JS-level sanity check if easy.
2. Run a FULL local build: `python -m engine.build --out <scratch>/finN --prev <folder with learning.json>` (background ~8 min; a folder with learning.json from `python -m engine.learn --out <folder> --data-dir <folder2>` exists in the scratchpad only, regenerate if missing). Check the log line "News: ... providers: ...", `data.json` has `news_intel` and per-record `news_intel`, size of `state.json` (archive <=1000 items) and `data.json`, then `python tools/qa_sweep.py <folder>` and `python tools/verify_indicators.py --symbols TCS.NS INFY.NS ITC.NS --build <folder>` (weights now include news: already updated in the tool).
3. Browser check (serve the folder: `python -m http.server 8767` inside it; the in-app browser could not reach it earlier, retry or use another port): clear service worker + caches + localStorage, open every tab incl. News (dashboard, filters, links), a stock page (News intelligence card, News line), Readings News column and News filter, Screens cards, mobile width 375, no console errors.
4. README: document providers, licensing/attribution, the AI job (GitHub Models, optional, rule-based fallback), the validation-layer rule, and the 4 new workflows/jobs (`learn.yml`, `news-ai.yml`); `docs/release-checklist.md` (add: start `Learn from the past` and `News AI scores` workflows once by hand, set repo var `NEWS_AI` only if switching off).
5. Tell the user: what to push, that GitHub Models limits/permission (`models: read`) must be confirmed on first run (check the workflow log), that Hypeless needs docs, and that NewsData.io needs their licence decision + secret.

## Verify commands
`python -m unittest discover -s tests` (all OK, 740 at last run) · `node --check site/app.js` · `node --test tests/ipo.test.js` · `python -m engine.news_ai --dry-run` (needs network).
Live site: https://marketsignals-app.github.io/market-signals-app/ . Repo: marketsignals-app/market-signals-app.

## Open questions for the user
- Hypeless API docs/URL? NewsData.io: accept its licence for a public site, and add `NEWSDATA_API_KEY` as a GitHub Actions secret?
- OK to enable the GitHub Models job (`news-ai.yml`) once its permission works? One commit for all of the above after step 3.
