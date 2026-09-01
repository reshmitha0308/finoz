"""
config.py
---------
Every setting for the whole project lives here. If you need to change
something, change it HERE - not scattered through the code.
"""

import os

# ---------------------------------------------------------------- DATABASE
#
# "sqlite" = a single file, zero setup, works instantly.       <-- START HERE
# "mysql"  = a real MySQL server you installed yourself.
#
# To switch to MySQL: install MySQL Server, create the database with
#     CREATE DATABASE hackverse2;
# then change the line below to "mysql" and fill in your password.
# The tables and all the code are identical either way.

DB_TYPE = os.environ.get("DB_TYPE", "sqlite")

SQLITE_FILE = "hackverse2.db"

MYSQL = {
    "host": os.environ.get("MYSQL_HOST", "localhost"),
    "user": os.environ.get("MYSQL_USER", "root"),
    "password": os.environ.get("MYSQL_PASSWORD", ""),
    "database": os.environ.get("MYSQL_DATABASE", "hackverse2"),
    "port": int(os.environ.get("MYSQL_PORT", "3306")),
}

# ------------------------------------------------------------------ SECRET
# Flask uses this to sign login cookies. In a real product this would be a
# long random value kept out of the code. For a hackathon this is fine.
SECRET_KEY = os.environ.get("SECRET_KEY", "hackverse2-spider-sense-dev-key")

# ------------------------------------------------------------------ STOCKS
#
# India has two big stock exchanges and the SAME company trades on both,
# usually at slightly different prices. yfinance tells them apart with a
# suffix on the symbol:
#     RELIANCE.NS  ->  National Stock Exchange   (NSE)
#     RELIANCE.BO  ->  Bombay Stock Exchange     (BSE)

EXCHANGES = {
    "NSE": {"suffix": ".NS", "label": "National Stock Exchange"},
    "BSE": {"suffix": ".BO", "label": "Bombay Stock Exchange"},
}

# The ten companies, written once without any suffix.
COMPANIES = {
    "RELIANCE": "Reliance Industries",
    "TCS": "Tata Consultancy Services",
    "INFY": "Infosys",
    "HDFCBANK": "HDFC Bank",
    "TMPV": "Tata Motors Passenger Vehicles",
    "ITC": "ITC Limited",
    "BHARTIARTL": "Bharti Airtel",
    "SUNPHARMA": "Sun Pharmaceutical",
    "LT": "Larsen & Toubro",
    "SBIN": "State Bank of India",
}

BASE_TICKERS = list(COMPANIES.keys())

# Trades and AI analysis run against NSE. The BSE chart sits beside it for
# comparison, so you can see the price gap between the two exchanges.
PRIMARY_EXCHANGE = "NSE"

# Every valid symbol = every company on every exchange (10 in total).
TICKERS = [base + ex["suffix"] for base in BASE_TICKERS for ex in EXCHANGES.values()]

TICKER_NAMES = {
    base + ex["suffix"]: f"{name} ({code})"
    for base, name in COMPANIES.items()
    for code, ex in EXCHANGES.items()
}


def symbol(base, exchange="NSE"):
    """Turn 'RELIANCE' + 'NSE' into 'RELIANCE.NS'."""
    return base + EXCHANGES[exchange]["suffix"]


# BSE identifies companies by a numeric "scrip code" rather than a name.
# We need these to ask BSE directly for a live price, because our history
# provider publishes BSE intraday bars about 15 minutes late while NSE is
# effectively real time. See market.bse_live_price().
BSE_SCRIP_CODES = {
    "RELIANCE": "500325",
    "TCS": "532540",
    "INFY": "500209",
    "HDFCBANK": "500180",
    "TMPV": "500570",
    "ITC": "500875",
    "BHARTIARTL": "532454",
    "SUNPHARMA": "524715",
    "LT": "500510",
    "SBIN": "500112",
}

# ------------------------------------------------------------------- AGENTS
MODEL = "claude-opus-5"

DOCS_DIR = "data/docs"
HEADLINES_FILE = "data/headlines.json"

# Every new user starts with this much virtual money.
STARTING_CAPITAL = 100000
