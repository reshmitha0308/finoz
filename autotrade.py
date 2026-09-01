"""
autotrade.py
------------
Automatic buying and selling.

WHAT THIS DOES:
You set a standing order once - "sell my TCS when I am 10% up" - and the app
carries it out for you the moment the price gets there. You do not have to sit
watching the chart.

THE THREE KINDS OF RULE:

  TAKE_PROFIT   Sell when your gain reaches +X%
                Measured against what you actually paid (your average buy price).

  STOP_LOSS     Sell when your loss reaches -X%
                The safety net. Limits how much a bad position can cost you.

  BUY_DIP       Buy when the price falls X% below where it was when you set
                the rule. For catching a pullback without watching all day.

WHEN RULES ARE CHECKED:
Every 30 seconds, on the same cycle that reprices your portfolio - so while
you have the app open in a browser. We deliberately do NOT run a background
thread: Flask's auto-reloader runs two copies of the program during
development, which would fire every rule twice and spend your cash twice.
Checking on a request is slower to react but is always correct.

EVERY RULE IS CHECKED SAFELY:
A rule that cannot be carried out - not enough cash, shares already sold -
is marked FAILED with the reason, never left silently half-done.
"""

import database
import market

RULE_TYPES = {
    "TAKE_PROFIT": {
        "action": "SELL",
        "label": "Take profit",
        "describe": lambda pct: f"Sell when I am up {pct:g}%",
    },
    "STOP_LOSS": {
        "action": "SELL",
        "label": "Stop loss",
        "describe": lambda pct: f"Sell if I fall {pct:g}% down",
    },
    "BUY_DIP": {
        "action": "BUY",
        "label": "Buy the dip",
        "describe": lambda pct: f"Buy if the price drops {pct:g}%",
    },
}


# ===================================================================
# EXECUTING A TRADE
# ===================================================================

def execute_trade(user, ticker, action, quantity, source="MANUAL"):
    """
    The ONE place a trade actually happens - whether you pressed a button or
    a rule fired. Having a single path means an automatic trade can never
    behave differently from a manual one.

    Returns (ok, message, price).
    """
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return False, "Quantity must be a whole number.", None

    if quantity <= 0:
        return False, "Quantity must be at least 1.", None

    try:
        price = market.get_price(ticker)
    except Exception as error:
        return False, f"Could not price {ticker}: {error}", None

    cost = round(quantity * price, 2)

    if action == "BUY":
        if cost > user["cash"]:
            return (False,
                    f"Not enough cash. This costs Rs {cost:,.2f} but you have "
                    f"Rs {user['cash']:,.2f}.", price)
        database.update_cash(user["id"], user["cash"] - cost)

    elif action == "SELL":
        owned = database.get_holdings(user["id"]).get(ticker, {}).get("quantity", 0)
        if quantity > owned:
            return False, f"You only own {owned} shares of {ticker}.", price
        database.update_cash(user["id"], user["cash"] + cost)

    else:
        return False, "Action must be BUY or SELL.", price

    database.add_transaction(user["id"], ticker, action, quantity, price, source)

    return (True,
            f"{action} {quantity} x {ticker} at Rs {price:,.2f} "
            f"(total Rs {cost:,.2f})", price)


# ===================================================================
# CREATING A RULE
# ===================================================================

def create_rule(user, ticker, rule_type, target_pct, quantity):
    """
    Validate and save a standing order. Returns (rule_id, error_message).

    We check up front that the rule could ever make sense - you cannot set a
    take-profit on shares you do not own - so you find out immediately rather
    than when it silently fails later.
    """
    if rule_type not in RULE_TYPES:
        return None, "Unknown rule type."

    try:
        target_pct = float(target_pct)
        quantity = int(quantity)
    except (TypeError, ValueError):
        return None, "Target and quantity must be numbers."

    if not (0.1 <= target_pct <= 100):
        return None, "Target must be between 0.1% and 100%."
    if quantity < 1:
        return None, "Quantity must be at least 1."

    holdings = database.get_holdings(user["id"])
    owned = holdings.get(ticker, {}).get("quantity", 0)

    if RULE_TYPES[rule_type]["action"] == "SELL" and owned < quantity:
        return None, (f"You only own {owned} shares of {ticker}, so this rule "
                      f"could never sell {quantity}.")

    try:
        reference_price = market.get_price(ticker)
    except Exception as error:
        return None, f"Could not price {ticker}: {error}"

    note = RULE_TYPES[rule_type]["describe"](target_pct)
    rule_id = database.add_rule(user["id"], ticker, rule_type, target_pct,
                                quantity, reference_price, note)
    return rule_id, None


# ===================================================================
# CHECKING WHETHER A RULE SHOULD FIRE
# ===================================================================

def evaluate_rule(rule, current_price, average_buy_price):
    """
    Should this rule fire right now?

    Returns (should_fire, progress_pct, explanation) where progress_pct is how
    far along the way to the target we are, from 0 to 100, so the interface can
    show a progress bar.
    """
    target = rule["target_pct"]

    if rule["rule_type"] == "TAKE_PROFIT":
        if not average_buy_price:
            return False, 0, "No holding to measure a gain against."
        move = (current_price - average_buy_price) / average_buy_price * 100
        progress = max(0.0, min(100.0, move / target * 100)) if target else 0
        return (move >= target, progress,
                f"Currently {move:+.2f}% against a +{target:g}% target.")

    if rule["rule_type"] == "STOP_LOSS":
        if not average_buy_price:
            return False, 0, "No holding to measure a loss against."
        move = (current_price - average_buy_price) / average_buy_price * 100
        progress = max(0.0, min(100.0, (-move) / target * 100)) if target else 0
        return (move <= -target, progress,
                f"Currently {move:+.2f}% against a -{target:g}% trigger.")

    if rule["rule_type"] == "BUY_DIP":
        reference = rule["reference_price"] or current_price
        move = (current_price - reference) / reference * 100
        progress = max(0.0, min(100.0, (-move) / target * 100)) if target else 0
        return (move <= -target, progress,
                f"Price is {move:+.2f}% from Rs {reference:,.2f}; "
                f"buys at -{target:g}%.")

    return False, 0, "Unknown rule type."


def check_rules(user_id=None):
    """
    Look at every active rule and carry out the ones whose target is met.

    Returns a list of what happened, so the interface can tell the user
    "your stop loss on INFY just sold 10 shares".
    """
    fired = []

    try:
        rules = database.get_active_rules_for_all_users()
    except Exception as error:
        print(f"[AUTOTRADE] Could not read rules: {error}")
        return fired

    if user_id is not None:
        rules = [r for r in rules if r["user_id"] == user_id]

    for rule in rules:
        try:
            user = database.get_user_by_id(rule["user_id"])
            if not user:
                continue

            holdings = database.get_holdings(user["id"])
            entry = holdings.get(rule["ticker"])
            average_buy = (entry["invested"] / entry["quantity"]
                           if entry and entry["quantity"] else None)

            price = market.get_price(rule["ticker"])
            should_fire, _, explanation = evaluate_rule(rule, price, average_buy)

            if not should_fire:
                continue

            action = RULE_TYPES[rule["rule_type"]]["action"]
            ok, message, traded_price = execute_trade(
                user, rule["ticker"], action, rule["quantity"], source="AUTO")

            if ok:
                database.close_rule(rule["id"], "TRIGGERED",
                                    f"{explanation} {message}", traded_price)
                fired.append({
                    "rule_id": rule["id"],
                    "ticker": rule["ticker"],
                    "rule_type": rule["rule_type"],
                    "label": RULE_TYPES[rule["rule_type"]]["label"],
                    "status": "TRIGGERED",
                    "message": message,
                })
            else:
                # The target was hit but the trade could not be carried out.
                # Close the rule with the reason rather than retrying forever.
                database.close_rule(rule["id"], "FAILED", message, price)
                fired.append({
                    "rule_id": rule["id"],
                    "ticker": rule["ticker"],
                    "rule_type": rule["rule_type"],
                    "label": RULE_TYPES[rule["rule_type"]]["label"],
                    "status": "FAILED",
                    "message": message,
                })

        except Exception as error:
            print(f"[AUTOTRADE] Rule {rule.get('id')} errored: {error}")
            continue

    return fired


def describe_rules(user_id):
    """Active rules with live progress, for display on the page."""
    output = []

    try:
        holdings = database.get_holdings(user_id)
        for rule in database.get_rules(user_id, status="ACTIVE"):
            entry = holdings.get(rule["ticker"])
            average_buy = (entry["invested"] / entry["quantity"]
                           if entry and entry["quantity"] else None)
            try:
                price = market.get_price(rule["ticker"])
            except Exception:
                price = rule["reference_price"] or 0

            _, progress, explanation = evaluate_rule(rule, price, average_buy)

            output.append({
                "id": rule["id"],
                "ticker": rule["ticker"],
                "rule_type": rule["rule_type"],
                "label": RULE_TYPES[rule["rule_type"]]["label"],
                "action": RULE_TYPES[rule["rule_type"]]["action"],
                "target_pct": rule["target_pct"],
                "quantity": rule["quantity"],
                "current_price": round(price, 2),
                "progress": round(progress, 1),
                "explanation": explanation,
                "note": rule["note"],
            })
    except Exception as error:
        print(f"[AUTOTRADE] Could not describe rules: {error}")

    return output
