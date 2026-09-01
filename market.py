"""
market.py
---------
Gets real stock data and turns it into (a) candlestick charts and
(b) three independent trading signals.

WHAT IS A CANDLESTICK?
Each candle covers one slice of time and shows four numbers:
  Open  - the price when the slice started
  High  - the highest price during it
  Low   - the lowest price during it
  Close - the price when the slice ended
Green means the price finished higher than it started, red means lower.

WHERE THE DATA COMES FROM:
yfinance, which is free and needs no API key or signup. During Indian
market hours it returns live intraday data. Outside market hours there is
no new data, so we add a small simulated tick to the last candle so the
chart still visibly moves during a demo. That is clearly labelled in the
UI as SIMULATED - we never pretend simulated data is live.
"""

import json
import random
import statistics
from datetime import datetime, timedelta, timezone

import config

# India Standard Time is UTC+5:30.
IST = timezone(timedelta(hours=5, minutes=30))

# The chart library always draws timestamps as if they were UTC. To make the
# time axis read real IST clock time, we add 5h30m (19800 seconds) to every
# candle timestamp before it goes to the browser. Nothing else uses these
# numbers, so shifting them here is safe.
IST_OFFSET_SECONDS = 5 * 3600 + 30 * 60

# Small in-memory cache so we do not hammer yfinance on every page refresh.
_CACHE = {}

# When a live feed fails we remember WHY, so the interface can tell the user
# the actual reason instead of just saying "simulated" and leaving them to
# guess. Keyed the same way as the cache.
_FAILURES = {}


def failure_reason(ticker, interval=None):
    """The last reason this symbol fell back to simulated data, if any."""
    if interval:
        return _FAILURES.get(f"candles:{ticker}:{clean_interval(interval)}")
    for key, reason in _FAILURES.items():
        if key.startswith(f"candles:{ticker}:"):
            return reason
    return None

# The two candle sizes the user can pick between.
#   "2m"  = one candle every 2 minutes  -> fast, shows intraday speed
#   "15m" = one candle every 15 minutes -> calmer, shows the wider trend
INTERVALS = {
    "2m": {"minutes": 2, "period": "5d", "cache_seconds": 45,
           "label": "2 minute candles"},
    "15m": {"minutes": 15, "period": "1mo", "cache_seconds": 90,
            "label": "15 minute candles"},
}
DEFAULT_INTERVAL = "15m"


def clean_interval(value):
    """Only ever allow a known interval through."""
    return value if value in INTERVALS else DEFAULT_INTERVAL


def ist_now_iso():
    """Current Indian time, for the clock shown in the interface."""
    return datetime.now(IST).isoformat(timespec="seconds")


def market_is_open():
    """NSE trades 09:15-15:30 IST, Monday to Friday."""
    now = datetime.now(IST)
    if now.weekday() >= 5:            # Saturday or Sunday
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= minutes <= (15 * 60 + 30)


# ------------------------------------------------------------- fake data

def _simulated_candles(ticker, interval=DEFAULT_INTERVAL, count=120):
    """
    Deterministic fake candles, used when the live feed cannot be reached.

    Seeded with the ticker name so it produces the SAME chart every time -
    your demo will look identical in rehearsal and in front of judges.
    """
    random.seed(ticker)

    # Keyed by company, not by symbol, so NSE and BSE share the same story.
    setup = {
        "RELIANCE": (1300.0, 0.0008),
        "TCS": (2350.0, 0.0022),
        "INFY": (1145.0, -0.0026),
        "HDFCBANK": (1680.0, 0.0012),
        "TMPV": (312.0, -0.0015),
        "ITC": (267.0, 0.0006),
        "BHARTIARTL": (1869.0, 0.0018),
        "SUNPHARMA": (1926.0, -0.0004),
        "LT": (4002.0, 0.0014),
        "SBIN": (1032.0, 0.0010),
    }
    base = ticker.split(".")[0]
    price, drift = setup.get(base, (1000.0, 0.0))

    # BSE quotes sit a fraction away from NSE for the same company, which is
    # true in the real market too.
    if ticker.endswith(".BO"):
        price *= 1.0015

    # Smaller candles cover less time each, so each one moves less.
    step = INTERVALS[clean_interval(interval)]["minutes"]
    scale = step / 15.0
    drift *= scale

    start = datetime.now(IST) - timedelta(minutes=step * count)
    candles = []
    for i in range(count):
        open_price = price
        close_price = open_price * (1 + drift + random.uniform(-0.008, 0.008) * scale)
        high = max(open_price, close_price) * (1 + random.uniform(0, 0.004) * scale)
        low = min(open_price, close_price) * (1 - random.uniform(0, 0.004) * scale)

        candles.append({
            "time": int((start + timedelta(minutes=step * i)).timestamp())
                    + IST_OFFSET_SECONDS,
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close_price, 2),
            "volume": int(random.uniform(400_000, 1_200_000)),
        })
        price = close_price

    return candles


# ------------------------------------------------- live BSE price feed
#
# WHY THIS EXISTS:
# Our candle-history provider publishes NSE bars in real time but BSE bars
# about 15 minutes late. That made the BSE chart visibly lag behind the NSE
# one. So we take the SHAPE of the BSE chart from the history provider, and
# the LATEST PRICE straight from BSE itself, which is live.
#
# If BSE cannot be reached we simply keep the history-provider data. The
# chart still works; it is just back to being delayed.

_BSE_QUOTES = {}
_BSE_QUOTE_SECONDS = 20        # do not ask BSE more than once every 20s
_BSE_TIMEOUT = 4               # never let a slow reply hold up the page
_LIVE_PATCHED = {}             # which symbols are currently being topped up


def bse_live_price(base):
    """
    The live last-traded price for one company on BSE.
    Returns a float, or None if the feed could not be reached.
    """
    code = config.BSE_SCRIP_CODES.get(base)
    if not code:
        return None

    cached = _BSE_QUOTES.get(base)
    if cached and (datetime.now() - cached["at"]).total_seconds() < _BSE_QUOTE_SECONDS:
        return cached["price"]

    try:
        import urllib.request

        url = ("https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w"
               f"?Debtflag=&scripcode={code}&seriesid=")
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://www.bseindia.com/",
            "Accept": "application/json, text/plain, */*",
        })

        with urllib.request.urlopen(request, timeout=_BSE_TIMEOUT) as response:
            payload = json.loads(response.read().decode())

        header = payload.get("Header", payload)
        price = float(header.get("LTP"))
        if price <= 0:
            raise ValueError("BSE returned a zero price")

        _BSE_QUOTES[base] = {"price": price, "at": datetime.now()}
        return price

    except Exception:
        # Keep serving the last good price briefly rather than dropping to
        # the delayed feed the instant one request fails.
        if cached and (datetime.now() - cached["at"]).total_seconds() < 120:
            return cached["price"]
        return None


def _freshen_with_live_bse(ticker, candles, interval):
    """
    Bring the newest BSE candle up to the live price.

    If enough time has passed for a new candle to have started, we add one.
    Otherwise we update the candle currently in progress. Either way the BSE
    chart ends at the same moment the NSE chart does.
    """
    if not ticker.endswith(".BO") or not candles:
        return candles, False

    price = bse_live_price(ticker.split(".")[0])
    if price is None:
        return candles, False

    step = INTERVALS[clean_interval(interval)]["minutes"] * 60
    now_shifted = int(datetime.now(IST).timestamp()) + IST_OFFSET_SECONDS
    last = candles[-1]

    steps = (now_shifted - last["time"]) // step
    if steps >= 1:
        # A new candle has begun since the history provider last published.
        new_candle = {
            "time": last["time"] + steps * step,
            "open": last["close"],
            "high": max(last["close"], price),
            "low": min(last["close"], price),
            "close": price,
            "volume": 0,
        }
        return candles + [new_candle], True

    # Still inside the current candle - update it in place.
    patched = dict(last)
    patched["close"] = price
    patched["high"] = max(patched["high"], price)
    patched["low"] = min(patched["low"], price)
    return candles[:-1] + [patched], True


# ------------------------------------------------------------- real data

def get_candles(ticker, interval=DEFAULT_INTERVAL, force_failure=False):
    """
    Returns (candles, quality).

    interval is "2m" or "15m" - how much time each candle covers.

    quality "OK"       = real data
    quality "DEGRADED" = live feed failed, showing the simulated series

    For BSE symbols the newest candle is topped up from BSE's own live feed,
    because our history provider publishes BSE bars around 15 minutes late.
    That top-up happens on EVERY call, outside the history cache, so the BSE
    chart is never stale even when the history behind it is cached.
    """
    interval = clean_interval(interval)
    candles, quality = _fetch_candles(ticker, interval, force_failure)

    if quality == "OK" and not force_failure:
        candles, patched = _freshen_with_live_bse(ticker, candles, interval)
        _LIVE_PATCHED[ticker] = patched

    return candles, quality


def is_live_patched(ticker):
    """True if this symbol's newest candle came from BSE's live feed."""
    return bool(_LIVE_PATCHED.get(ticker))


def _fetch_candles(ticker, interval, force_failure=False):
    """The cached history lookup. Wrapped by get_candles above."""
    settings = INTERVALS[interval]

    cache_key = f"candles:{ticker}:{interval}"
    cached = _CACHE.get(cache_key)
    if cached and not force_failure:
        age = (datetime.now() - cached["at"]).total_seconds()
        if age < settings["cache_seconds"]:
            return cached["candles"], cached["quality"]

    if force_failure:
        _FAILURES[cache_key] = "Failure simulated from the dashboard toggle."
        return _simulated_candles(ticker, interval), "DEGRADED"

    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)

        # Ask for the interval the user picked. If the market has been closed
        # for a while that can come back empty, so fall back to daily candles
        # rather than showing nothing.
        history = stock.history(period=settings["period"], interval=interval)
        if history.empty or len(history) < 20:
            history = stock.history(period="6mo", interval="1d")
        if history.empty:
            raise ValueError("yfinance returned no rows")

        candles = []
        for timestamp, row in history.iterrows():
            candles.append({
                # +5:30 so the chart's time axis reads IST, not UTC.
                "time": int(timestamp.timestamp()) + IST_OFFSET_SECONDS,
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]) if row["Volume"] == row["Volume"] else 0,
            })

        # Charts require candles in ascending time order with no duplicates.
        candles.sort(key=lambda c: c["time"])
        unique = {c["time"]: c for c in candles}
        candles = list(unique.values())

        if not candles:
            raise ValueError("no usable candles after cleaning")

        _CACHE[cache_key] = {"candles": candles, "quality": "OK", "at": datetime.now()}
        _FAILURES.pop(cache_key, None)
        return candles, "OK"

    except Exception as error:
        # Any failure at all - no internet, rate limit, delisted or renamed
        # ticker - and we fall back rather than crash. We record the reason
        # so the interface can explain itself instead of just saying
        # "simulated" and leaving the user to guess.
        text = str(error)
        if "delisted" in text.lower() or "not found" in text.lower() \
                or "no rows" in text.lower():
            reason = (f"{ticker} was not found by the data provider. The symbol "
                      f"may have been renamed or delisted.")
        elif "429" in text or "rate" in text.lower():
            reason = "Data provider rate limit reached. It should recover shortly."
        elif "connection" in text.lower() or "timed out" in text.lower():
            reason = "Could not reach the data provider. Check your internet."
        else:
            reason = f"Live feed error: {text[:120]}"

        _FAILURES[cache_key] = reason
        candles = _simulated_candles(ticker, interval)
        _CACHE[cache_key] = {"candles": candles, "quality": "DEGRADED",
                             "at": datetime.now()}
        return candles, "DEGRADED"


def get_live_tick(ticker, interval=DEFAULT_INTERVAL):
    """
    One updated version of the most recent candle, for the live chart.

    When the market is open this re-reads the real feed. When it is closed
    we nudge the last candle slightly so the chart visibly moves during a
    demo, and we tell the UI it is simulated so it can label it honestly.
    """
    candles, quality = get_candles(ticker, interval)
    last = dict(candles[-1])

    if market_is_open() and quality == "OK":
        return {"candle": last, "simulated": False, "quality": quality}

    # Market closed: apply a small random walk to the closing price.
    move = last["close"] * random.uniform(-0.0015, 0.0015)
    last["close"] = round(last["close"] + move, 2)
    last["high"] = round(max(last["high"], last["close"]), 2)
    last["low"] = round(min(last["low"], last["close"]), 2)

    return {"candle": last, "simulated": True, "quality": quality}


# -------------------------------------------------------------- signals

def _classify(value, bull_above, bear_below):
    if value >= bull_above:
        return "BULLISH"
    if value <= bear_below:
        return "BEARISH"
    return "NEUTRAL"


def momentum_signal(closes):
    """Dimension 1: price versus its own recent average."""
    latest = closes[-1]
    average = statistics.mean(closes[-20:])
    pct = (latest - average) / average * 100
    return {
        "dimension": "Price Momentum",
        "value": round(pct, 2),
        "unit": "% vs 20-period average",
        "label": _classify(pct, 2.0, -2.0),
        "confidence": round(min(0.95, 0.45 + abs(pct) / 12), 2),
        "detail": f"Last close {latest} against a 20-period average of {average:.2f}.",
    }


def volume_signal(volumes):
    """Dimension 2: is trading activity unusual?"""
    latest = volumes[-1]
    average = statistics.mean(volumes[-20:]) or 1
    ratio = latest / average

    if ratio >= 1.5:
        label = "ANOMALY"
    elif ratio <= 0.6:
        label = "UNUSUALLY QUIET"
    else:
        label = "NORMAL"

    return {
        "dimension": "Volume Anomaly",
        "value": round(ratio, 2),
        "unit": "x the 20-period average",
        "label": label,
        "confidence": round(min(0.95, 0.40 + abs(ratio - 1) / 2), 2),
        "detail": f"Latest volume {latest:,} against an average of {average:,.0f}.",
    }


def sentiment_signal(ticker, force_failure=False):
    """Dimension 3: the mood of recent news coverage."""
    if force_failure:
        return {
            "dimension": "News Sentiment",
            "value": None,
            "unit": "average headline score",
            "label": "UNAVAILABLE",
            "confidence": 0.0,
            "detail": "News feed unreachable for this session.",
            "headlines": [],
        }

    try:
        with open(config.HEADLINES_FILE, "r", encoding="utf-8") as f:
            all_headlines = json.load(f)

        # Headlines are stored per COMPANY, not per symbol, so the same news
        # applies whether you are looking at the NSE or the BSE listing.
        headlines = all_headlines.get(ticker.split(".")[0], [])
        if not headlines:
            raise ValueError("no headlines for this company")

        score = statistics.mean(h["score"] for h in headlines)
        return {
            "dimension": "News Sentiment",
            "value": round(score, 2),
            "unit": "average headline score (-1 to +1)",
            "label": _classify(score, 0.2, -0.2),
            "confidence": round(min(0.95, 0.40 + abs(score)), 2),
            "detail": f"Averaged across {len(headlines)} recent headlines.",
            "headlines": headlines,
        }

    except Exception as error:
        return {
            "dimension": "News Sentiment",
            "value": None,
            "unit": "average headline score",
            "label": "UNAVAILABLE",
            "confidence": 0.0,
            "detail": f"Could not read headlines: {error}",
            "headlines": [],
        }


def get_all_signals(ticker, interval=DEFAULT_INTERVAL,
                    break_prices=False, break_news=False):
    """Everything the agents need to know about one stock."""
    interval = clean_interval(interval)
    candles, quality = get_candles(ticker, interval, force_failure=break_prices)
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    return {
        "ticker": ticker,
        "name": config.TICKER_NAMES.get(ticker, ticker),
        "interval": interval,
        "candles": candles,
        "latest_price": closes[-1],
        "previous_close": closes[-2] if len(closes) > 1 else closes[-1],
        "change_pct": round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)
                      if len(closes) > 1 else 0.0,
        "price_data_quality": quality,
        "market_open": market_is_open(),
        "momentum": momentum_signal(closes),
        "volume": volume_signal(volumes),
        "sentiment": sentiment_signal(ticker, force_failure=break_news),
    }


def check_symbols():
    """
    Ask the data provider about every configured symbol once, at startup.

    Tickers get renamed and delisted - TATAMOTORS became TMPV after its
    demerger, for example - and a stale symbol silently falls back to
    simulated data. This prints a clear warning at boot instead of leaving
    you to wonder why one company looks different from the others.
    """
    live, dead = [], []
    for ticker in config.TICKERS:
        candles, quality = get_candles(ticker)
        (live if quality == "OK" else dead).append(ticker)

    print(f"[MARKET] {len(live)}/{len(config.TICKERS)} symbols returning live data")
    for ticker in dead:
        print(f"[MARKET] SIMULATED: {ticker} - {failure_reason(ticker)}")

    return live, dead


def get_price(ticker):
    """
    Just the latest price - used when recording a buy or a sell, and when
    valuing the portfolio.

    It reads from the SAME cache the chart uses, so the price you trade at is
    the price you saw on the candlestick. That is what keeps the chart and the
    portfolio in sync.
    """
    candles, _ = get_candles(ticker, DEFAULT_INTERVAL)
    return candles[-1]["close"]
