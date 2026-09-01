"""
database.py
-----------
Everything that touches the database lives in this one file.

WHAT IS A DATABASE?
Think of it as a set of spreadsheets that your program can read and write.
Each "table" is one spreadsheet. Each "row" is one record (one user, one
trade). Unlike a real spreadsheet, the program can ask precise questions
like "give me every trade by user 3, newest first".

WHY WE NEED ONE:
Without it, everything vanishes the moment you close the app. We need
accounts, portfolios and AI track records to survive a restart.

THE MYSQL SWITCH:
This file works with BOTH SQLite and MySQL. Change DB_TYPE in config.py
and everything else keeps working. The only real differences are how you
connect and a couple of words in the CREATE TABLE statements, both of
which are handled below.
"""

import sqlite3
from datetime import datetime

import config

# SQLite writes placeholders as ?, MySQL writes them as %s.
# We write every query with ? and translate when talking to MySQL.
PLACEHOLDER = "?" if config.DB_TYPE == "sqlite" else "%s"


def _translate(sql):
    """Convert our ? placeholders into whatever this database expects."""
    return sql if config.DB_TYPE == "sqlite" else sql.replace("?", "%s")


def get_connection():
    """
    Open a connection to the database.

    Raises a clear, readable error if MySQL is selected but not reachable,
    so you know to either start the MySQL service or switch back to sqlite.
    """
    if config.DB_TYPE == "sqlite":
        connection = sqlite3.connect(config.SQLITE_FILE)
        connection.row_factory = sqlite3.Row  # lets us read columns by name
        return connection

    try:
        import pymysql

        return pymysql.connect(
            host=config.MYSQL["host"],
            user=config.MYSQL["user"],
            password=config.MYSQL["password"],
            database=config.MYSQL["database"],
            port=config.MYSQL["port"],
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
    except ImportError:
        raise RuntimeError(
            "MySQL selected but pymysql is not installed. "
            "Run:  pip install pymysql    (or set DB_TYPE back to 'sqlite')"
        )
    except Exception as error:
        raise RuntimeError(
            f"Could not connect to MySQL: {error}\n"
            "Check that the MySQL service is running and that the database "
            "'hackverse2' exists. To keep working right now, set "
            "DB_TYPE = 'sqlite' in config.py."
        )


def run(sql, params=(), fetch=None):
    """
    The one function every other query goes through.

    fetch=None  -> for INSERT/UPDATE. Returns the new row's id.
    fetch="one" -> returns a single row as a dict, or None.
    fetch="all" -> returns a list of dicts (possibly empty).
    """
    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(_translate(sql), params)

        if fetch == "one":
            row = cursor.fetchone()
            return dict(row) if row else None
        if fetch == "all":
            return [dict(r) for r in cursor.fetchall()]

        connection.commit()
        return cursor.lastrowid

    except Exception as error:
        if connection:
            try:
                connection.rollback()
            except Exception:
                pass
        # Re-raise with the failing query attached so debugging is easy.
        raise RuntimeError(f"Database error: {error}\nQuery was: {sql}")

    finally:
        if connection:
            try:
                connection.close()
            except Exception:
                pass


# ===================================================================
# CREATING THE TABLES
# ===================================================================

def init_db():
    """
    Create the three tables if they do not already exist.
    Safe to run every time the app starts - it never deletes data.
    """
    if config.DB_TYPE == "sqlite":
        auto_id = "INTEGER PRIMARY KEY AUTOINCREMENT"
        text = "TEXT"
    else:
        auto_id = "INT AUTO_INCREMENT PRIMARY KEY"
        text = "VARCHAR(255)"

    tables = [
        # WHO the users are.
        f"""CREATE TABLE IF NOT EXISTS users (
            id {auto_id},
            username {text} NOT NULL UNIQUE,
            email {text} NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_verified INTEGER DEFAULT 0,
            confirmation_code {text},
            risk_tolerance {text} DEFAULT 'medium',
            cash REAL DEFAULT 100000,
            created_at {text}
        )""",

        # WHAT they bought and sold.
        f"""CREATE TABLE IF NOT EXISTS transactions (
            id {auto_id},
            user_id INTEGER NOT NULL,
            ticker {text} NOT NULL,
            action {text} NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            total REAL NOT NULL,
            created_at {text}
        )""",

        # STANDING ORDERS the user has set: "sell when I am 10% up".
        f"""CREATE TABLE IF NOT EXISTS auto_rules (
            id {auto_id},
            user_id INTEGER NOT NULL,
            ticker {text} NOT NULL,
            rule_type {text} NOT NULL,
            target_pct REAL NOT NULL,
            quantity INTEGER NOT NULL,
            reference_price REAL,
            status {text} DEFAULT 'ACTIVE',
            note TEXT,
            triggered_price REAL,
            triggered_at {text},
            created_at {text}
        )""",

        # WHAT the AI said, and whether the user listened.
        f"""CREATE TABLE IF NOT EXISTS recommendations (
            id {auto_id},
            user_id INTEGER NOT NULL,
            ticker {text} NOT NULL,
            verdict {text} NOT NULL,
            confidence REAL,
            headline TEXT,
            reasoning TEXT,
            agent_signals TEXT,
            citations TEXT,
            price_at_reco REAL,
            user_action {text} DEFAULT 'PENDING',
            consensus REAL,
            latency_ms INTEGER,
            created_at {text}
        )""",
    ]

    for statement in tables:
        run(statement)

    # Older databases were created before trades recorded whether they were
    # placed by hand or by an automatic rule. Add the column if missing,
    # rather than making you delete your database and start again.
    _ensure_column("transactions", "source", f"{text} DEFAULT 'MANUAL'")

    # Metrics the problem statement asks to be logged per session.
    _ensure_column("recommendations", "portfolio_concentration", "REAL")
    _ensure_column("recommendations", "signal_accuracy_30d", "REAL")


def _ensure_column(table, column, definition):
    """Add a column only if it is not already there. Safe to run repeatedly."""
    try:
        if config.DB_TYPE == "sqlite":
            existing = [r["name"] for r in run(f"PRAGMA table_info({table})",
                                               fetch="all")]
        else:
            existing = [list(r.values())[0]
                        for r in run(f"SHOW COLUMNS FROM {table}", fetch="all")]

        if column not in existing:
            run(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            print(f"[DATABASE] Added missing column {table}.{column}")

    except Exception as error:
        print(f"[DATABASE] Could not check {table}.{column}: {error}")


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ===================================================================
# USERS
# ===================================================================

def create_user(username, email, password_hash, code, risk="medium"):
    return run(
        "INSERT INTO users (username, email, password_hash, is_verified, "
        "confirmation_code, risk_tolerance, cash, created_at) "
        "VALUES (?, ?, ?, 0, ?, ?, ?, ?)",
        (username, email, password_hash, code, risk,
         config.STARTING_CAPITAL, _now()),
    )


def get_user_by_email(email):
    return run("SELECT * FROM users WHERE email = ?", (email,), fetch="one")


def get_user_by_username(username):
    return run("SELECT * FROM users WHERE username = ?", (username,), fetch="one")


def get_user_by_id(user_id):
    return run("SELECT * FROM users WHERE id = ?", (user_id,), fetch="one")


def mark_verified(user_id):
    run("UPDATE users SET is_verified = 1, confirmation_code = NULL WHERE id = ?",
        (user_id,))


def set_risk_tolerance(user_id, risk):
    run("UPDATE users SET risk_tolerance = ? WHERE id = ?", (risk, user_id))


def update_cash(user_id, new_cash):
    run("UPDATE users SET cash = ? WHERE id = ?", (round(new_cash, 2), user_id))


# ===================================================================
# TRANSACTIONS AND HOLDINGS
# ===================================================================

def add_transaction(user_id, ticker, action, quantity, price, source="MANUAL"):
    """source is 'MANUAL' for a button press, 'AUTO' for a rule firing."""
    total = round(quantity * price, 2)
    run(
        "INSERT INTO transactions (user_id, ticker, action, quantity, price, "
        "total, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, ticker, action, quantity, price, total, source, _now()),
    )
    return total


def get_transactions(user_id, limit=50):
    return run(
        "SELECT * FROM transactions WHERE user_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, limit),
        fetch="all",
    )


def get_holdings(user_id):
    """
    Work out what the user currently owns by replaying their transactions.

    We calculate this instead of storing it, so the holdings can never
    drift out of sync with the transaction history.
    """
    rows = run(
        "SELECT ticker, action, quantity, price FROM transactions "
        "WHERE user_id = ? ORDER BY id",
        (user_id,),
        fetch="all",
    )

    holdings = {}
    for row in rows:
        ticker = row["ticker"]
        entry = holdings.setdefault(ticker, {"quantity": 0, "invested": 0.0})

        if row["action"] == "BUY":
            entry["quantity"] += row["quantity"]
            entry["invested"] += row["quantity"] * row["price"]
        else:  # SELL
            # Remove the average cost of the shares being sold.
            if entry["quantity"] > 0:
                average = entry["invested"] / entry["quantity"]
                sold = min(row["quantity"], entry["quantity"])
                entry["quantity"] -= sold
                entry["invested"] -= average * sold

    # Drop anything fully sold off.
    return {t: v for t, v in holdings.items() if v["quantity"] > 0}


# ===================================================================
# AI RECOMMENDATIONS AND THEIR TRACK RECORD
# ===================================================================

def save_recommendation(user_id, ticker, verdict, confidence, headline,
                        reasoning, agent_signals, citations, price,
                        consensus, latency_ms, concentration=None,
                        signal_accuracy=None):
    """
    One row per analysis run. This IS the performance log the problem
    statement asks for: it carries agent latency, agent consensus, portfolio
    risk concentration and signal accuracy against 30-day forward return,
    alongside the reasoning and the citations behind the verdict.
    """
    return run(
        "INSERT INTO recommendations (user_id, ticker, verdict, confidence, "
        "headline, reasoning, agent_signals, citations, price_at_reco, "
        "user_action, consensus, latency_ms, portfolio_concentration, "
        "signal_accuracy_30d, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?)",
        (user_id, ticker, verdict, confidence, headline, reasoning,
         agent_signals, citations, price, consensus, latency_ms,
         concentration, signal_accuracy, _now()),
    )


def set_user_action(reco_id, user_id, action):
    """Record whether the user FOLLOWED or IGNORED the AI."""
    run(
        "UPDATE recommendations SET user_action = ? WHERE id = ? AND user_id = ?",
        (action, reco_id, user_id),
    )


# ===================================================================
# AUTOMATIC BUY / SELL RULES
# ===================================================================

def add_rule(user_id, ticker, rule_type, target_pct, quantity, reference_price,
             note):
    return run(
        "INSERT INTO auto_rules (user_id, ticker, rule_type, target_pct, "
        "quantity, reference_price, status, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
        (user_id, ticker, rule_type, target_pct, quantity, reference_price,
         note, _now()),
    )


def get_rules(user_id, status=None, limit=100):
    if status:
        return run(
            "SELECT * FROM auto_rules WHERE user_id = ? AND status = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, status, limit), fetch="all")
    return run(
        "SELECT * FROM auto_rules WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit), fetch="all")


def get_active_rules_for_all_users():
    """Every rule waiting to fire, across every user."""
    return run("SELECT * FROM auto_rules WHERE status = 'ACTIVE'", (), fetch="all")


def close_rule(rule_id, status, note, price=None):
    """Mark a rule TRIGGERED, FAILED or CANCELLED, and say why."""
    run(
        "UPDATE auto_rules SET status = ?, note = ?, triggered_price = ?, "
        "triggered_at = ? WHERE id = ?",
        (status, note, price, _now(), rule_id),
    )


def cancel_rule(rule_id, user_id):
    """Only the owner can cancel, and only a rule that has not fired."""
    run(
        "UPDATE auto_rules SET status = 'CANCELLED', triggered_at = ? "
        "WHERE id = ? AND user_id = ? AND status = 'ACTIVE'",
        (_now(), rule_id, user_id),
    )


def get_recommendations(user_id, limit=50):
    return run(
        "SELECT * FROM recommendations WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
        fetch="all",
    )
