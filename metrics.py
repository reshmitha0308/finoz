"""
metrics.py
----------
Measures whether our signals are actually any good.

THE PROBLEM WITH SCORING A LIVE RECOMMENDATION:
If the app tells you to BUY today, we cannot know for a month whether that
was right. So a live app can never show you a meaningful hit rate on its
first day.

THE FIX - LOOK BACKWARDS INSTEAD:
We replay history. For every day in the past six months we compute the
momentum signal using ONLY the data available on that day, then check what
the price actually did over the following 30 days. That gives a real
"signal accuracy against 30-day forward return" figure immediately, with
no waiting and no cheating - the signal never sees the future.

This is a backtest, and it is honest about its limits: it scores the
momentum dimension only, not the full multi-agent verdict.
"""

import statistics
from datetime import datetime

import config

_CACHE = {}
_CACHE_SECONDS = 1800          # this is slow, so remember it for 30 minutes

FORWARD_DAYS = 30              # how far ahead we score
LOOKBACK = 20                  # the averaging window the signal uses
BULL_THRESHOLD = 2.0           # matches market.momentum_signal
BEAR_THRESHOLD = -2.0


def _daily_closes(ticker):
    """Daily closing prices for the past six months, or None if unavailable."""
    try:
        import yfinance as yf

        history = yf.Ticker(ticker).history(period="6mo", interval="1d")
        if history.empty or len(history) < LOOKBACK + FORWARD_DAYS + 5:
            return None
        return [float(v) for v in history["Close"].tolist()]
    except Exception:
        return None


def backtest_ticker(ticker):
    """
    Score the momentum signal for one company.

    Returns a dict, or None if there was not enough history.
    """
    closes = _daily_closes(ticker)
    if not closes:
        return None

    correct = bullish = bearish = scored = 0

    # Stop FORWARD_DAYS before the end - beyond that we cannot know the answer.
    for t in range(LOOKBACK, len(closes) - FORWARD_DAYS):
        window = closes[t - LOOKBACK:t]
        average = statistics.mean(window)
        if not average:
            continue

        # The signal, using only what was known on day t.
        momentum = (closes[t] - average) / average * 100

        if momentum >= BULL_THRESHOLD:
            call = "BULLISH"
        elif momentum <= BEAR_THRESHOLD:
            call = "BEARISH"
        else:
            continue          # NEUTRAL makes no prediction, so it is not scored

        forward_return = (closes[t + FORWARD_DAYS] - closes[t]) / closes[t] * 100

        scored += 1
        if call == "BULLISH":
            bullish += 1
            correct += 1 if forward_return > 0 else 0
        else:
            bearish += 1
            correct += 1 if forward_return < 0 else 0

    if scored < 10:
        return None

    return {
        "ticker": ticker,
        "scored": scored,
        "correct": correct,
        "accuracy_pct": round(correct / scored * 100, 1),
        "bullish_calls": bullish,
        "bearish_calls": bearish,
    }


def signal_accuracy_30d(force=False):
    """
    The headline number: how often the momentum signal was right about the
    next 30 days, measured across every company we cover.

    A coin flip would score 50%. Anything meaningfully above that means the
    signal carries some information.
    """
    cached = _CACHE.get("accuracy")
    if cached and not force:
        age = (datetime.now() - cached["at"]).total_seconds()
        if age < _CACHE_SECONDS:
            return cached["value"]

    per_ticker = []
    total_scored = total_correct = 0

    for base in config.BASE_TICKERS:
        result = backtest_ticker(config.symbol(base, config.PRIMARY_EXCHANGE))
        if not result:
            continue
        per_ticker.append(result)
        total_scored += result["scored"]
        total_correct += result["correct"]

    value = {
        "accuracy_pct": round(total_correct / total_scored * 100, 1)
                        if total_scored else None,
        "scored": total_scored,
        "correct": total_correct,
        "companies": len(per_ticker),
        "forward_days": FORWARD_DAYS,
        "per_ticker": sorted(per_ticker, key=lambda r: -r["accuracy_pct"]),
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }

    _CACHE["accuracy"] = {"value": value, "at": datetime.now()}
    return value


def portfolio_concentration(holdings_rows):
    """
    Risk metric: what share of the portfolio sits in its single largest holding?

    1.00 means everything is in one stock, which is very risky.
    0.20 across five equal holdings is well spread.
    """
    if not holdings_rows:
        return 0.0
    values = [row["value"] for row in holdings_rows]
    total = sum(values)
    if total <= 0:
        return 0.0
    return round(max(values) / total, 3)
