# FinOz — Agent Architecture and Decision Logic

*Brief written summary for judges, to be read alongside the demo.*
**HACKVERSE: INTO THE WEB · Sprint 1 · PS-01 · VIT Chennai 2026**

---

## The idea in one paragraph

A hedge fund puts four analysts in a room, each researching a different
angle, and a senior partner reconciles them against a specific client's
position. A retail investor gets a price chart. FinOz builds that room out
of four AI agents: three specialists analyse a stock **simultaneously** from
independent evidence bases, and a synthesis agent reconciles their
disagreements against the individual user's risk profile — showing every
step of its working, and citing every claim.

---

## Architecture

```
                 Browser (Flask templates + vanilla JS)
                 profile · ticker · failure toggles
                                |
        ┌───────────────────────┴───────────────────────┐
        │   INPUTS GATHERED BEFORE ANY AGENT RUNS       │
        │   market.py     -> 3 numeric signals          │
        │   retrieval.py  -> top-3 filing chunks        │
        │   database.py   -> user profile + AI record   │
        └───────────────────────┬───────────────────────┘
                                |
   ═════════ 3 SPECIALISTS, ThreadPoolExecutor, IN PARALLEL ═════════
        │                       │                       │
 ┌──────▼───────┐      ┌────────▼────────┐     ┌────────▼───────┐
 │  TECHNICAL   │      │  FUNDAMENTAL    │     │   SENTIMENT    │
 │  ANALYST     │      │  ANALYST (RAG)  │     │   ANALYST      │
 │ price+volume │      │ SEBI filings    │     │ news headlines │
 └──────┬───────┘      └────────┬────────┘     └────────┬───────┘
        │                       │                       │
        └───────────────────────┼───────────────────────┘
                 identical JSON output contract
                                |
                      ┌─────────▼─────────┐
                      │  SYNTHESIS AGENT  │
                      │  + risk profile   │
                      │  + own track record│
                      └─────────┬─────────┘
                                |
        verdict · confidence · citations · Follow/Ignore · logged
```

**Why parallel matters.** The three specialists are dispatched together and
awaited together, so wall-clock time is the *slowest* agent, not the sum.
The interface prints both numbers side by side — typically ~300 ms parallel
against ~900 ms sequential — as direct evidence the concurrency is real
rather than claimed.

**Why they are isolated.** Each specialist sees only its own evidence. The
Technical Analyst is never shown the news; the Sentiment Analyst is never
shown the chart. This is deliberate: agents that share evidence converge on
the same answer, and three agents agreeing for the same reason is worth no
more than one. Isolation is what makes their agreement — or disagreement —
informative.

---

## The output contract

Every specialist returns the same JSON shape. This fixed contract is what
makes a synthesis layer possible at all:

| Field | Meaning |
|---|---|
| `agent_name` | which specialist produced this |
| `signal` | BULLISH / NEUTRAL / BEARISH |
| `confidence` | 0.0 – 1.0 |
| `reasoning` | 2–3 sentences of natural language |
| `evidence` | the exact data points or document quotes used |
| `data_quality` | OK / DEGRADED / UNAVAILABLE |

`data_quality` is the mechanism for graceful degradation. An agent whose
inputs are missing reports `UNAVAILABLE`, is excluded from the consensus
vote, and forces the synthesis agent to lower overall confidence and say so
in plain language.

---

## Decision logic

### 1. Signal classification — three independent dimensions

Computed in `market.py` **before** any agent runs, so every agent reasons
over the same auditable numbers rather than inventing its own:

| Dimension | Measure | Classification |
|---|---|---|
| Price momentum | latest close vs 20-period average, as % | ≥ +2% BULLISH · ≤ −2% BEARISH |
| Volume anomaly | latest volume ÷ 20-period average | ≥ 1.5× ANOMALY · ≤ 0.6× UNUSUALLY QUIET |
| News sentiment | mean headline score, −1 to +1 | ≥ +0.2 BULLISH · ≤ −0.2 BEARISH |

Each carries its own confidence, scaled by distance from neutral and capped
at 0.95 — the system never claims certainty.

### 2. Retrieval (RAG)

`retrieval.py` implements TF-IDF with cosine similarity **from scratch in
~60 lines** — no vector-database dependency to install or configure. The
corpus is fifteen synthetic SEBI Regulation 30 disclosures, earnings-call
transcripts and research notes in `data/docs/`, at least one per company.

The top three chunks are injected into the Fundamental Analyst's prompt,
which is instructed that every claim must be traceable to a supplied
document and to return NEUTRAL if the documents do not support a view. Each
retrieved snippet is rendered in the interface, so any user can read the
source behind any sentence.

### 3. Personalisation

The synthesis agent receives the user's risk tolerance, available cash,
current holdings, and their history of following or ignoring past advice.
It is instructed that a low-risk investor and a high-risk investor **must
not** receive the same answer from the same data.

This is verified by the automated test suite, not merely asserted: the same
stock with identical agent signals returns **HOLD** on a low-risk profile
and **BUY** on a high-risk one.

### 4. Disagreement handling

The synthesis prompt requires the agent to name any conflict between
specialists, state which it weighted more heavily, and explain why.
Agreement is also quantified as a consensus score — 1.00 unanimous, 0.33
three-way split — and low consensus is surfaced to the user as a warning
rather than hidden.

### 5. Calibration against its own record

Every past recommendation is scored against what the price actually did.
That record — broken down by verdict type — is fed back into the synthesis
prompt, which is told to lower its confidence where a verdict type has been
performing badly, to treat a thin record as weak evidence, and **never to
flip a verdict merely to look better**. Market data leads; the record only
tunes confidence.

---

## Metrics logged per session

Written to the `recommendations` table on every run:

| Metric | How it is computed |
|---|---|
| Agent response latency | wall-clock of the slowest of the three parallel agents |
| Agent consensus | share of available agents holding the majority signal |
| Portfolio risk concentration | largest holding ÷ total portfolio value |
| Signal accuracy vs 30-day forward return | walk-forward backtest, see below |

### On that accuracy number — an honest result

`metrics.py` replays six months of daily history. For every past day it
computes the momentum signal using **only data available on that day**, then
checks the actual return over the following 30 days. No look-ahead.

**Measured result: 36.2% accuracy across 503 scored calls on 10 companies.**

That is *worse* than a coin flip, and we report it rather than hide it. Over
this window these large-caps were mean-reverting, so naive momentum was
actively anti-predictive.

We consider this a finding, not an embarrassment, and it is precisely the
argument for the architecture: **momentum is one of three inputs weighted by
a reasoning agent, never a signal followed blindly.** A system that surfaced
momentum alone as a recommendation — which is what most retail screeners do
— would have been wrong most of the time. Two caveats we state plainly:
overlapping 30-day windows mean the samples are not independent, and this
scores the momentum dimension only, not the full multi-agent verdict.

---

## Graceful degradation

Two failures can be triggered on demand from the dashboard, and both occur
naturally in production:

| Failure | Behaviour |
|---|---|
| News feed down | Sentiment Analyst returns `UNAVAILABLE` at confidence 0.0, refuses to guess, is dropped from the consensus vote, synthesis confidence falls and the gap is disclosed |
| Price feed down | Falls back to a deterministic simulated series, marks data `DEGRADED`, and the chart shows a `SIMULATED` badge naming the actual cause |

The same paths fire automatically on a real upstream failure. **No output is
ever produced uncited, and the pipeline never fails.**

The system also survives having no LLM at all: with no API key the agents
fall back to rule-based stand-ins returning the identical JSON contract, so
parallel execution, citations, personalisation and degradation all still
demonstrate.

---

## Data engineering notes

Two upstream problems were found by measurement and fixed:

- **A delisted ticker.** `TATAMOTORS` returned HTTP 404 — it was renamed
  `TMPV` after the demerger. A startup check now validates all 20 symbols
  and names any that are not live, so this cannot recur silently.
- **BSE lagged NSE by a full candle.** yfinance publishes BSE intraday bars
  ~15 minutes late. The BSE chart now takes its *history* from yfinance and
  its *newest price* from BSE's own API, closing the gap from 15 minutes to
  zero.

All chart timestamps are shifted to IST so the axis reads Indian clock time.

---

## Scope and honesty

- The document corpus is **synthetic**, modelled on the structure of real
  SEBI Regulation 30 disclosures and earnings transcripts.
- Price data is **real**, from NSE and BSE.
- Trading uses **virtual money**; no real orders are placed anywhere.
- This is a prototype. Output is educational analysis, not investment advice,
  and the system is not a registered advisory service.

**Verification:** `python test_app.py` — 109 automated checks covering
authentication, the agent pipeline, personalisation, degraded data, trading,
automatic rules and the metrics above.
