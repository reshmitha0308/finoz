"""
app.py
------
The web server. THIS IS THE FILE YOU RUN.

WHAT A WEB SERVER DOES:
Your browser asks for a web address. This file decides what to send back.
Each @app.route(...) below is one address the site responds to.

There are two kinds of routes here:
  PAGE routes   ("/", "/login", "/dashboard") send back an HTML page.
  API routes    ("/api/...") send back JSON data, which the JavaScript on
                the page uses to update the chart or show a verdict without
                reloading the whole page.

Run it with:   python app.py
Then open:     http://localhost:5000
"""

import json
import traceback
from functools import wraps

from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   session, url_for)

import agents
import auth
import autotrade
import config
import database
import market

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Create the tables on startup if they do not exist yet. Never deletes data.
try:
    database.init_db()
    print(f"[DATABASE] Ready, using {config.DB_TYPE.upper()}")
except Exception as startup_error:
    print(f"[DATABASE ERROR] {startup_error}")


# =====================================================================
# HELPERS
# =====================================================================

def login_required(view):
    """
    Put @login_required above a route and logged-out visitors get bounced
    to the login page instead of seeing it.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "error")
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)
    return wrapped


def current_user():
    """The logged-in user's database row, or None."""
    try:
        if "user_id" not in session:
            return None
        return database.get_user_by_id(session["user_id"])
    except Exception:
        return None


def build_profile(user):
    """
    Assemble everything the synthesis agent needs to know about this user:
    their risk setting, their cash, what they own, and how they have
    responded to the AI in the past.
    """
    holdings = database.get_holdings(user["id"])
    recommendations = database.get_recommendations(user["id"], limit=200)

    return {
        "username": user["username"],
        "risk_tolerance": user["risk_tolerance"],
        "cash": user["cash"],
        "holdings": holdings,
        "followed": sum(1 for r in recommendations if r["user_action"] == "FOLLOWED"),
        "ignored": sum(1 for r in recommendations if r["user_action"] == "IGNORED"),
        "total_recos": len(recommendations),
    }


def portfolio_rows(user):
    """
    Value every holding at the CURRENT price.

    Used by both the portfolio page and the 30-second refresh API, so the two
    can never disagree with each other.
    """
    holdings = database.get_holdings(user["id"])
    rows = []
    total_value = 0.0
    total_invested = 0.0

    for ticker, entry in holdings.items():
        try:
            price = market.get_price(ticker)
        except Exception:
            price = entry["invested"] / entry["quantity"]

        value = price * entry["quantity"]
        average = entry["invested"] / entry["quantity"]
        total_value += value
        total_invested += entry["invested"]

        rows.append({
            "ticker": ticker,
            "name": config.TICKER_NAMES.get(ticker, ticker),
            "quantity": entry["quantity"],
            "average_price": round(average, 2),
            "current_price": round(price, 2),
            "value": round(value, 2),
            "profit": round(value - entry["invested"], 2),
            "profit_pct": round((value - entry["invested"]) / entry["invested"] * 100, 2)
                          if entry["invested"] else 0.0,
        })

    return rows, round(total_value, 2), round(total_invested, 2)


def ai_performance(user_id):
    """
    The AI's own track record, fed back into the synthesis prompt.

    This is what "learning from the app's own history" means here: the agent
    is told how accurate its past BUY / HOLD / AVOID calls turned out to be,
    so it can calibrate rather than repeating a losing pattern.
    """
    try:
        recommendations = database.get_recommendations(user_id, limit=200)
        by_verdict = {"BUY": [0, 0], "HOLD": [0, 0], "AVOID": [0, 0]}  # [right, scored]
        recent = []

        for reco in recommendations:
            if reco["user_action"] == "PENDING":
                continue
            try:
                now = market.get_price(reco["ticker"])
                then = reco["price_at_reco"] or now
                change = (now - then) / then * 100

                if reco["verdict"] == "BUY":
                    right = change > 0
                elif reco["verdict"] == "AVOID":
                    right = change < 0
                else:
                    right = abs(change) < 2.0

                slot = by_verdict.setdefault(reco["verdict"], [0, 0])
                slot[1] += 1
                slot[0] += 1 if right else 0

                if len(recent) < 6:
                    recent.append({
                        "ticker": reco["ticker"],
                        "verdict": reco["verdict"],
                        "confidence": reco["confidence"],
                        "price_move_pct": round(change, 2),
                        "was_right": right,
                        "user_action": reco["user_action"],
                    })
            except Exception:
                continue

        accuracy = {
            verdict: {
                "scored": scored,
                "accuracy_pct": round(right / scored * 100) if scored else None,
            }
            for verdict, (right, scored) in by_verdict.items()
        }

        total_scored = sum(v["scored"] for v in accuracy.values())
        total_right = sum(by_verdict[v][0] for v in accuracy)

        return {
            "overall_accuracy_pct": round(total_right / total_scored * 100)
                                    if total_scored else None,
            "total_scored": total_scored,
            "by_verdict": accuracy,
            "recent_calls": recent,
        }
    except Exception:
        return {"overall_accuracy_pct": None, "total_scored": 0,
                "by_verdict": {}, "recent_calls": []}


def ai_scorecard(user_id):
    """
    How good has the AI actually been?

    For every recommendation the user responded to, we compare the price now
    against the price when the advice was given:
        BUY   was right if the price went UP
        AVOID was right if the price went DOWN
        HOLD  was right if the price barely moved (within 2%)
    """
    try:
        recommendations = database.get_recommendations(user_id, limit=200)
        judged = [r for r in recommendations if r["user_action"] != "PENDING"]

        correct = 0
        scored = 0
        for reco in judged:
            try:
                now = market.get_price(reco["ticker"])
                then = reco["price_at_reco"] or now
                change = (now - then) / then * 100

                if reco["verdict"] == "BUY":
                    was_right = change > 0
                elif reco["verdict"] == "AVOID":
                    was_right = change < 0
                else:
                    was_right = abs(change) < 2.0

                scored += 1
                correct += 1 if was_right else 0
            except Exception:
                continue  # skip any stock we cannot price right now

        return {
            "total": len(recommendations),
            "responded": len(judged),
            "followed": sum(1 for r in judged if r["user_action"] == "FOLLOWED"),
            "ignored": sum(1 for r in judged if r["user_action"] == "IGNORED"),
            "scored": scored,
            "correct": correct,
            "accuracy": round(correct / scored * 100) if scored else None,
        }
    except Exception:
        return {"total": 0, "responded": 0, "followed": 0, "ignored": 0,
                "scored": 0, "correct": 0, "accuracy": None}


# =====================================================================
# PAGE ROUTES
# =====================================================================

@app.route("/")
def home():
    """The public homepage. Anyone can see this without logging in."""
    return render_template("home.html", user=current_user())


@app.route("/signup", methods=["GET", "POST"])
def signup_page():
    if request.method == "POST":
        user_id, code, error = auth.signup(
            request.form.get("username"),
            request.form.get("email"),
            request.form.get("password"),
            request.form.get("risk", "medium"),
        )
        if error:
            flash(error, "error")
            return render_template("signup.html", form=request.form)

        # Remember which email is being verified, then send them onward.
        session["pending_email"] = request.form.get("email", "").strip().lower()
        flash("Account created. Enter your confirmation code below.", "success")
        return redirect(url_for("verify_page"))

    return render_template("signup.html", form={})


@app.route("/verify", methods=["GET", "POST"])
def verify_page():
    email = session.get("pending_email", "")

    if request.method == "POST":
        email = request.form.get("email", email)
        user, error = auth.verify_code(email, request.form.get("code"))

        if error:
            flash(error, "error")
            return render_template("verify.html", email=email, demo_code=_demo_code(email))

        session.pop("pending_email", None)
        flash("Email verified. You can log in now.", "success")
        return redirect(url_for("login_page"))

    return render_template("verify.html", email=email, demo_code=_demo_code(email))


def _demo_code(email):
    """
    Show the confirmation code on the page.

    This exists because we are not sending real emails yet. When you switch
    on real email in auth.py, delete this function and the box in verify.html.
    """
    try:
        if not email:
            return None
        user = database.get_user_by_email(email)
        return user["confirmation_code"] if user and not user["is_verified"] else None
    except Exception:
        return None


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        user, error = auth.login(
            request.form.get("identifier"), request.form.get("password")
        )

        if error == "UNVERIFIED":
            session["pending_email"] = request.form.get("identifier", "")
            flash("Please verify your email address first.", "error")
            return redirect(url_for("verify_page"))

        if error:
            flash(error, "error")
            return render_template("login.html", form=request.form)

        session["user_id"] = user["id"]
        session["username"] = user["username"]
        return redirect(url_for("dashboard"))

    return render_template("login.html", form={})


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    return render_template(
        "dashboard.html",
        user=user,
        companies=config.COMPANIES,
        base_tickers=config.BASE_TICKERS,
        exchanges=config.EXCHANGES,
        primary=config.PRIMARY_EXCHANGE,
        scorecard=ai_scorecard(user["id"]),
        live_mode=agents.has_api_key(),
    )


@app.route("/portfolio")
@login_required
def portfolio_page():
    user = current_user()

    try:
        rows, total_value, total_invested = portfolio_rows(user)

        return render_template(
            "portfolio.html",
            user=user,
            holdings=rows,
            transactions=database.get_transactions(user["id"]),
            recommendations=database.get_recommendations(user["id"], limit=25),
            rules=autotrade.describe_rules(user["id"]),
            rule_history=[r for r in database.get_rules(user["id"], limit=40)
                          if r["status"] != "ACTIVE"],
            scorecard=ai_scorecard(user["id"]),
            total_value=total_value,
            total_invested=total_invested,
            total_profit=round(total_value - total_invested, 2),
        )

    except Exception as error:
        traceback.print_exc()
        flash(f"Could not load your portfolio: {error}", "error")
        return redirect(url_for("dashboard"))


# =====================================================================
# API ROUTES  (these return JSON, not pages)
# =====================================================================

@app.route("/api/candles/<ticker>")
@login_required
def api_candles(ticker):
    """
    Frontend sends: a ticker in the address.
    Backend does:   fetches the candlestick history.
    Backend returns: the candles, plus whether the data is real or simulated.
    Why we need it: it is what draws the chart.
    """
    try:
        if ticker not in config.TICKERS:
            return jsonify({"ok": False, "error": "Unknown ticker"}), 400

        break_prices = request.args.get("break_prices") == "1"
        interval = market.clean_interval(request.args.get("interval"))
        candles, quality = market.get_candles(ticker, interval,
                                              force_failure=break_prices)

        return jsonify({
            "ok": True,
            "ticker": ticker,
            "name": config.TICKER_NAMES.get(ticker, ticker),
            "interval": interval,
            "interval_label": market.INTERVALS[interval]["label"],
            "candles": candles[-150:],
            "quality": quality,
            "live_patched": market.is_live_patched(ticker),
            "reason": market.failure_reason(ticker, interval)
                      if quality == "DEGRADED" else None,
            "market_open": market.market_is_open(),
            "server_ist": market.ist_now_iso(),
        })
    except Exception as error:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(error)}), 500


@app.route("/api/tick/<ticker>")
@login_required
def api_tick(ticker):
    """
    Called every few seconds by the page to make the chart move.
    Returns one updated candle, and whether that update is simulated.
    """
    try:
        if ticker not in config.TICKERS:
            return jsonify({"ok": False, "error": "Unknown ticker"}), 400
        interval = market.clean_interval(request.args.get("interval"))
        return jsonify({"ok": True, **market.get_live_tick(ticker, interval)})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@app.route("/api/analyze", methods=["POST"])
@login_required
def api_analyze():
    """
    The main event.

    Frontend sends: which ticker, and whether to simulate a broken feed.
    Backend does:   computes signals, retrieves filings, runs three agents in
                    parallel, synthesises a verdict against this user's profile,
                    and saves the whole thing to the database.
    Backend returns: the signals, all three agent traces, the verdict, the
                    citations, and the metrics.
    """
    try:
        payload = request.get_json(silent=True) or {}
        ticker = payload.get("ticker")

        if ticker not in config.TICKERS:
            return jsonify({"ok": False, "error": "Unknown ticker"}), 400

        user = current_user()
        if not user:
            return jsonify({"ok": False, "error": "Not logged in"}), 401

        signals = market.get_all_signals(
            ticker,
            interval=market.clean_interval(payload.get("interval")),
            break_prices=bool(payload.get("break_prices")),
            break_news=bool(payload.get("break_news")),
        )

        agent_outputs, documents = agents.run_specialists(signals)
        profile = build_profile(user)
        profile["performance"] = ai_performance(user["id"])
        verdict = agents.synthesize(signals, agent_outputs, documents, profile)

        consensus = agents.consensus_score(agent_outputs)
        parallel_ms = max(a["latency_ms"] for a in agent_outputs)
        sequential_ms = sum(a["latency_ms"] for a in agent_outputs)

        reco_id = database.save_recommendation(
            user_id=user["id"],
            ticker=ticker,
            verdict=verdict.get("verdict", "HOLD"),
            confidence=verdict.get("confidence", 0.0),
            headline=verdict.get("headline", ""),
            reasoning=verdict.get("reasoning", ""),
            agent_signals=json.dumps({a["agent_name"]: a["signal"] for a in agent_outputs}),
            citations=json.dumps(verdict.get("citations", [])),
            price=signals["latest_price"],
            consensus=consensus,
            latency_ms=parallel_ms + verdict.get("latency_ms", 0),
        )

        return jsonify({
            "ok": True,
            "reco_id": reco_id,
            "ticker": ticker,
            "name": signals["name"],
            "interval": signals["interval"],
            "price": signals["latest_price"],
            "signals": {
                "momentum": signals["momentum"],
                "volume": signals["volume"],
                "sentiment": signals["sentiment"],
            },
            "price_data_quality": signals["price_data_quality"],
            "agents": agent_outputs,
            "verdict": verdict,
            "documents": documents,
            "metrics": {
                "parallel_ms": parallel_ms,
                "sequential_ms": sequential_ms,
                "synthesis_ms": verdict.get("latency_ms", 0),
                "consensus": consensus,
            },
        })

    except Exception as error:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(error)}), 500


@app.route("/api/action", methods=["POST"])
@login_required
def api_action():
    """
    Records whether the user chose to FOLLOW or IGNORE the AI's advice.
    This is what makes the success-rate scoreboard possible.
    """
    try:
        payload = request.get_json(silent=True) or {}
        action = payload.get("action")

        if action not in ("FOLLOWED", "IGNORED"):
            return jsonify({"ok": False, "error": "Invalid action"}), 400

        database.set_user_action(payload.get("reco_id"), session["user_id"], action)
        return jsonify({"ok": True, "scorecard": ai_scorecard(session["user_id"])})

    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@app.route("/api/trade", methods=["POST"])
@login_required
def api_trade():
    """
    Buys or sells shares using the user's virtual cash.

    Checks you can afford a buy, and that you own enough to sell, before
    changing anything.
    """
    try:
        payload = request.get_json(silent=True) or {}
        ticker = payload.get("ticker")
        action = payload.get("action")

        if ticker not in config.TICKERS:
            return jsonify({"ok": False, "error": "Unknown ticker"}), 400
        if action not in ("BUY", "SELL"):
            return jsonify({"ok": False, "error": "Action must be BUY or SELL"}), 400

        # Manual trades and automatic ones both go through execute_trade, so
        # they can never behave differently from each other.
        ok, message, _ = autotrade.execute_trade(
            current_user(), ticker, action, payload.get("quantity", 0),
            source="MANUAL")

        if not ok:
            return jsonify({"ok": False, "error": message}), 400

        return jsonify({"ok": True, "message": message,
                        "cash": current_user()["cash"]})

    except Exception as error:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(error)}), 500


# ------------------------------------------------- automatic buy / sell

@app.route("/api/rules", methods=["GET", "POST"])
@login_required
def api_rules():
    """
    GET  - list the user's active rules with live progress toward each target.
    POST - create a new rule.

    Frontend sends (POST): ticker, rule_type, target_pct, quantity.
    Backend does:  validates it could ever fire, records today's price as the
                   reference point, and saves it.
    Backend returns: the updated rule list.
    """
    user = current_user()

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        ticker = payload.get("ticker")

        if ticker not in config.TICKERS:
            return jsonify({"ok": False, "error": "Unknown ticker"}), 400

        rule_id, error = autotrade.create_rule(
            user, ticker,
            payload.get("rule_type"),
            payload.get("target_pct"),
            payload.get("quantity"),
        )
        if error:
            return jsonify({"ok": False, "error": error}), 400

        return jsonify({"ok": True, "rule_id": rule_id,
                        "rules": autotrade.describe_rules(user["id"])})

    return jsonify({"ok": True, "rules": autotrade.describe_rules(user["id"]),
                    "types": {k: v["label"]
                              for k, v in autotrade.RULE_TYPES.items()}})


@app.route("/api/rules/<int:rule_id>/cancel", methods=["POST"])
@login_required
def api_cancel_rule(rule_id):
    """Turn off a standing order the user no longer wants."""
    try:
        database.cancel_rule(rule_id, session["user_id"])
        return jsonify({"ok": True,
                        "rules": autotrade.describe_rules(session["user_id"])})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@app.route("/api/portfolio_summary")
@login_required
def api_portfolio_summary():
    """
    The live portfolio, refreshed by the page every 30 seconds.

    Frontend sends: nothing.
    Backend does:   re-prices every holding from the same cache the chart uses.
    Backend returns: cash, each holding's current value, and total profit.
    Why we need it: so your portfolio numbers move with the candlesticks
                    instead of going stale until you reload the page.
    """
    try:
        user = current_user()

        # This runs on the 30-second cycle, so standing orders are checked
        # every time the portfolio is repriced. Anything that fired is
        # returned so the page can tell the user about it.
        fired = autotrade.check_rules(user_id=session["user_id"])
        if fired:
            user = current_user()          # cash and holdings just changed

        rows, total_value, total_invested = portfolio_rows(user)

        return jsonify({
            "ok": True,
            "fired": fired,
            "rules": autotrade.describe_rules(user["id"]),
            "cash": user["cash"],
            "holdings": rows,
            "total_value": total_value,
            "total_invested": total_invested,
            "total_profit": round(total_value - total_invested, 2),
            "net_worth": round(user["cash"] + total_value, 2),
            "scorecard": ai_scorecard(user["id"]),
            "server_ist": market.ist_now_iso(),
        })
    except Exception as error:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(error)}), 500


@app.route("/api/risk", methods=["POST"])
@login_required
def api_risk():
    """
    Changes this user's risk tolerance.

    This is what makes personalisation demonstrable: switch it, re-run the
    same stock, and the synthesis agent reaches a different verdict from
    identical market data.
    """
    try:
        risk = (request.get_json(silent=True) or {}).get("risk")
        if risk not in ("low", "medium", "high"):
            return jsonify({"ok": False, "error": "Invalid risk level"}), 400

        database.set_risk_tolerance(session["user_id"], risk)
        return jsonify({"ok": True, "risk": risk})

    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@app.route("/api/prices")
@login_required
def api_prices():
    """
    The price strip at the top of the dashboard.

    One row per company, quoted on the primary exchange (NSE). The BSE price
    for the selected company is shown on its own chart below.
    """
    out = []
    for base in config.BASE_TICKERS:
        ticker = config.symbol(base, config.PRIMARY_EXCHANGE)
        try:
            candles, quality = market.get_candles(ticker)
            latest = candles[-1]["close"]
            previous = candles[-2]["close"] if len(candles) > 1 else latest
            out.append({
                "base": base,
                "name": config.COMPANIES.get(base, base),
                "price": latest,
                "change_pct": round((latest - previous) / previous * 100, 2)
                              if previous else 0.0,
                "quality": quality,
            })
        except Exception:
            out.append({"base": base, "name": config.COMPANIES.get(base, base),
                        "price": None, "change_pct": 0.0, "quality": "DEGRADED"})
    return jsonify({"ok": True, "prices": out})


# =====================================================================
# ERROR PAGES
# =====================================================================

@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404,
                           message="That page does not exist."), 404


@app.errorhandler(500)
def server_error(_):
    return render_template("error.html", code=500,
                           message="Something went wrong on the server. "
                                   "Check your terminal for the full error."), 500


if __name__ == "__main__":
    print("\n" + "=" * 58)
    print("  FinOz  -  open http://localhost:5000 in your browser")
    print("=" * 58)

    # Tell you up front which symbols are actually live, so a renamed or
    # delisted ticker never silently shows as "simulated" without explanation.
    try:
        market.check_symbols()
    except Exception as check_error:
        print(f"[MARKET] Startup symbol check skipped: {check_error}")
    print()

    app.run(debug=True, port=5000)
