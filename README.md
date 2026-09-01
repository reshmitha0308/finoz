# FinOz — Spider Sense for Markets

**Multi-Agent Autonomous Financial Intelligence System for Retail Investors**
HACKVERSE: INTO THE WEB · Sprint 1 · PS-01 · VIT Chennai 2026

Three AI analysts research a stock **at the same time**, argue it out, and a
fourth agent synthesises one recommendation tailored to *your* risk profile —
showing its working at every step.

Original Website: https://finozfinal.vercel.app/
---

## Quick start (3 commands)

```bash
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5000**

> **Judges:** the required written summary of the agent architecture and
> decision logic is in [ARCHITECTURE.md](ARCHITECTURE.md), and is also served
> live inside the app at **`/architecture`** (linked in the top navigation).

To check everything works:

```bash
python test_app.py
```

You should see `129 passed, 0 failed`.

---

## What it does

| Feature | Where |
|---|---|
| Cinematic themed homepage | `/` |
| Signup with 6-digit email confirmation | `/signup` → `/verify` |
| Login / logout | `/login` |
| 5 companies × 2 exchanges, candlestick charts | `/dashboard` |
| 3 parallel AI agents + synthesis | "Run the agents" button |
| Follow or Ignore the AI | verdict card |
| Portfolio, trades, AI hit rate | `/portfolio` |
| Automatic buy / sell rules (opt-in) | dashboard + `/portfolio` |

**Ten companies across eight sectors:** Reliance (energy/retail), TCS and Infosys (IT), HDFC Bank and State Bank of India (private and public banking), Tata Motors Passenger Vehicles (auto — `TMPV`, the renamed entity after the demerger; the old `TATAMOTORS` symbol is delisted), ITC (FMCG), Bharti Airtel (telecom), Sun Pharmaceutical (pharma) and Larsen & Toubro (infrastructure).

Each company has its own synthetic filing in `data/docs/`, its own headline set, and its own BSE scrip code. Twenty symbols in total.

### Two exchanges, side by side

The same share trades on both Indian exchanges at slightly different prices,
so each company is charted twice:

| Exchange | Symbol suffix | Role |
|---|---|---|
| **NSE** — National Stock Exchange | `.NS` | Primary. Trading and AI analysis run here. |
| **BSE** — Bombay Stock Exchange | `.BO` | Comparison chart, with the live NSE↔BSE spread shown beneath. |

Twenty symbols in total. Both charts reload together.

### Keeping BSE from lagging

yfinance publishes NSE bars in real time but BSE bars roughly **15 minutes
late**, which left the BSE chart a full candle behind NSE. Two data sources
now feed the BSE chart:

| What | Source |
|---|---|
| Candle history (chart shape) | yfinance, `.BO` symbols |
| Newest candle's price | **BSE's own API** (`api.bseindia.com`), live |

`market.bse_live_price()` fetches the live last-traded price by BSE scrip
code (`config.BSE_SCRIP_CODES`), and `_freshen_with_live_bse()` either updates
the in-progress candle or opens a new one if enough time has passed. This runs
**outside the history cache**, so the BSE chart is current even when the
history behind it is cached.

Safeguards: 20-second quote cache, 4-second timeout, the last good price is
reused for up to 2 minutes if a request fails, and if BSE is unreachable the
chart silently falls back to the delayed history feed. The UI labels a
topped-up candle as *"newest candle from BSE live feed"*.

Measured before and after, at 13:32 IST:

```
before   NSE last candle 13:15 (14m old)   BSE 13:00 (29m old)
after    NSE last candle 13:30 ( 3m old)   BSE 13:30 ( 3m old)
```

The test suite fails the build if BSE ever drifts more than one candle behind
NSE on any company.

### Chart timing and refresh

- **All times are IST.** The chart library renders timestamps as UTC, so
  `market.py` adds 5h30m (`IST_OFFSET_SECONDS = 19800`) to every candle before
  it reaches the browser. Every company on both exchanges goes through the
  same path, so all ten charts share one time base. A live IST clock in the
  header is built from the computer's own clock, so the chart axis and the
  clock always agree.
- **Candle size is switchable: 2 minutes or 15 minutes.** The dropdown asks
  yfinance for that interval directly (`interval=2m` / `interval=15m`), so
  2-minute mode really does show finer intraday movement rather than just
  redrawing the same data. Each interval is cached separately.
- **Charts refresh every 2 minutes** (`REFRESH_MS` in `dashboard.js`).
- **The portfolio reprices every 30 seconds** (`PORTFOLIO_MS`, used by both
  `dashboard.js` and `portfolio.js`).
- **Both refresh immediately** on page load, when you return to the tab
  (`visibilitychange`), when the window regains focus, and when the network
  reconnects — so you never look at a stale number.

### Automatic buy / sell (opt-in)

Off by default. Tick **Automatic buy / sell** on the dashboard to reveal it;
the choice is remembered in the browser.

| Rule | Fires when | Measured against |
|---|---|---|
| **Take profit** | gain reaches +X% | your average buy price |
| **Stop loss** | loss reaches −X% | your average buy price |
| **Buy the dip** | price falls X% | the price when you set the rule |

Rules are checked on the **same 30-second cycle** that reprices the portfolio.
There is deliberately no background thread: Flask's auto-reloader runs two
copies of the program in development, which would fire every rule twice and
spend the cash twice. Rules therefore run while the app is open in a browser,
which the interface states plainly.

Every trade — button press or rule firing — goes through the single
`autotrade.execute_trade()` function, so an automatic trade can never behave
differently from a manual one. Automatic trades are tagged `AUTO` in the
transaction history. A rule whose target is met but which cannot be carried
out (cash spent, shares already sold) is closed as `FAILED` with the reason
rather than retrying forever, and a fired rule never fires twice.

### Chart ↔ portfolio synchronisation

Trades and portfolio valuations call `market.get_price()`, which reads the
**same cache** that draws the candlesticks. The price you trade at is the
price on the chart. After any buy or sell the position strip reprices
immediately rather than waiting for the next 30-second tick.

### Tables have no empty scrollbars

A horizontal scrollbar on a table that already fits just drags into blank
space, which looks broken. `.table-wrap` is therefore `overflow-x: visible`
on a normal screen, and the scrollbar only appears below 900px where the
content genuinely cannot fit. The company strip wraps onto a second line on
desktop instead of scrolling.

---

## Architecture

```
Browser  (HTML + CSS + JavaScript, Spider-Man theme)
   |   login · click Analyze · Follow/Ignore · Buy/Sell
   v
Flask backend  (app.py)
   |
   |-- auth.py       signup, confirmation codes, password hashing
   |-- market.py     yfinance prices, candlesticks, 3 signals
   |-- retrieval.py  TF-IDF search over the filing corpus
   |-- agents.py     3 parallel agents + synthesis agent
   |-- database.py   users, transactions, recommendations
   v
Database  (SQLite by default, MySQL with a one-line switch)
```

### The agent pipeline

```
        gather inputs BEFORE any agent runs
        market.py -> 3 signals   retrieval.py -> top 3 filings
                          |
   ══════ 3 agents fired in parallel (ThreadPoolExecutor) ══════
        |                  |                    |
  Technical Analyst  Fundamental Analyst  Sentiment Analyst
  price + volume     retrieved filings    news headlines
                     (the RAG agent)
        |                  |                    |
        └──────────────────┼────────────────────┘
              identical JSON output contract
                           |
                  Synthesis Agent  + your risk profile
                           |
              verdict · citations · Follow/Ignore
```

### The output contract

Every specialist returns the same JSON shape, which is what makes synthesis
possible:

| Field | Meaning |
|---|---|
| `agent_name` | which specialist |
| `signal` | BULLISH / NEUTRAL / BEARISH |
| `confidence` | 0.0 – 1.0 |
| `reasoning` | 2–3 sentences |
| `evidence` | data points or document quotes used |
| `data_quality` | OK / DEGRADED / UNAVAILABLE |

`data_quality` drives graceful degradation. An agent with missing inputs
reports `UNAVAILABLE`, is dropped from the consensus vote, and forces the
synthesis agent to lower confidence and disclose the gap.

---

## Decision logic

**Signal classification — 3 independent dimensions**, computed in `market.py`
before any agent runs so every agent reasons over the same auditable numbers:

1. **Price momentum** — latest close vs 20-period average, as a percentage.
   Above +2% BULLISH, below −2% BEARISH.
2. **Volume anomaly** — latest volume ÷ 20-period average. Above 1.5× is an
   ANOMALY, below 0.6× UNUSUALLY QUIET.
3. **News sentiment** — mean headline score from −1 to +1.

**Retrieval.** `retrieval.py` implements TF-IDF with cosine similarity from
scratch in ~60 lines — no vector-database dependency. The corpus is fifteen
synthetic SEBI filings and earnings transcripts in `data/docs/`. The top three
are injected into the Fundamental Analyst's prompt, and every retrieved
snippet is rendered in the UI so users can read the source behind any claim.

**Personalisation.** The synthesis agent receives the user's risk tolerance,
cash, holdings, and their history of following or ignoring past advice. The
risk dropdown on the dashboard changes the verdict on identical market data —
this is verified by the test suite, not just claimed.

**AI scorecard.** Every recommendation stores the price at the time it was
given. A BUY counts as correct if the price later rose, an AVOID if it fell,
a HOLD if it moved less than 2%. The portfolio page shows the real hit rate.

**The AI learns from its own record.** `ai_performance()` in `app.py` scores
every past recommendation against what the price actually did, broken down by
verdict type, and `_format_track_record()` in `agents.py` turns that into a
block of text fed into the synthesis prompt. The agent is instructed to lower
its confidence when a verdict type has been performing badly, to treat a thin
record as weak evidence, and never to flip a verdict just to look better — the
market data still leads, the record only tunes confidence. The offline
rule-based path applies the same calibration numerically (×0.75 below 50%
accuracy, ×1.1 above 70%, and only once at least 3 calls have been scored).

---

## Metrics logged per session

Stored in the `recommendations` table:

| Metric | How it is computed |
|---|---|
| Parallel agent latency | wall-clock of the slowest of the three agents |
| Agent consensus | share of available agents holding the majority signal |
| AI accuracy | correct calls ÷ scored calls, priced against the market |

The dashboard also shows what **sequential** execution would have cost, which
is the direct evidence that the agents genuinely ran in parallel.

---

## Degraded-data handling

Two failures can be triggered on demand from the dashboard:

- **News feed down** → Sentiment Analyst returns `UNAVAILABLE` at confidence
  0.0, refuses to guess, drops out of the vote, synthesis confidence falls.
- **Price feed down** → `market.py` falls back to a deterministic simulated
  series, marks data `DEGRADED`, and the chart shows a `SIMULATED DATA` badge.

The same fallback fires automatically on a real yfinance failure, so no
network problem can break the demo.

---

## Switching to MySQL

The app runs on SQLite out of the box with zero setup. To use MySQL instead:

1. Install MySQL Server and start the service.
2. Create the database:
   ```sql
   CREATE DATABASE hackverse2;
   ```
3. Set these environment variables (PowerShell):
   ```powershell
   $env:DB_TYPE = "mysql"
   $env:MYSQL_USER = "root"
   $env:MYSQL_PASSWORD = "your-mysql-password"
   ```
4. `python app.py` — the tables are created automatically.

No other code changes. `database.py` handles the dialect differences. If
MySQL will not connect, the error message tells you exactly how to switch
back to SQLite.

---

## Enabling real AI reasoning

The app works with **no API key** using rule-based stand-in agents that follow
the identical JSON contract — this is the safety net if the wifi fails during
judging. For real Claude reasoning:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-your-key"
python app.py
```

The dashboard badge flips from `AI: OFFLINE MODE` to `AI: LIVE`.

---

## Enabling real confirmation emails

Currently the code is shown on the verification page and printed to your
terminal. To send real email, open `auth.py`, find
`send_confirmation_email()`, and follow the commented instructions (needs a
Gmail App Password in an environment variable — never in the code).

---

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask server: all pages and JSON APIs. **Run this.** |
| `auth.py` | Signup, confirmation codes, login, password hashing |
| `agents.py` | Four agents, parallel executor, prompts |
| `autotrade.py` | Trade execution plus automatic buy/sell rules |
| `metrics.py` | 30-day forward-return backtest, portfolio concentration |
| `ARCHITECTURE.md` | **Judge-facing summary of agents and decision logic** |
| `market.py` | yfinance data, candlesticks, three signals |
| `retrieval.py` | TF-IDF search over the filing corpus |
| `database.py` | All SQL. SQLite/MySQL switch lives here |
| `config.py` | Tickers, model, DB choice, paths |
| `test_app.py` | 129 automated checks, including a PS-01 requirements audit |
| `templates/` | The seven HTML pages |
| `static/css/style.css` | The Spider-Man theme |
| `static/js/dashboard.js` | Charts, 2-minute refresh, agents, trading |
| `static/js/portfolio.js` | 30-second live portfolio repricing |
| `static/img/` | Logo (nav, hero, 4x master), spider icon, favicon |
| `data/docs/` | Ten synthetic filings and research notes |
| `data/headlines.json` | News headlines with sentiment scores |

---

## Security

`.gitignore` already excludes `.env`, `*.db`, and `__pycache__`. **Never
commit API keys, passwords, or the database file** — the database contains
password hashes and user emails.

Passwords are hashed with `werkzeug.security` (never stored in plain text).
Login failures return an identical message for a wrong username and a wrong
password, so the page cannot be used to discover which accounts exist.

`SECRET_KEY` in `config.py` is a development default. For a real deployment,
set it as an environment variable.

---

## Scope and honesty

- The document corpus is **synthetic**, modelled on the structure of real SEBI
  Regulation 30 disclosures and earnings transcripts.
- Price data is real, from yfinance. Outside NSE trading hours the live ticks
  are simulated movement on the last real candle, and the UI says so.
- Trading uses **virtual money**. No real orders are placed anywhere.
- This is a prototype. Output is educational analysis, not investment advice.
