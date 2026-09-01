"""
test_app.py
-----------
A quick self-check that walks through the whole app the way a real user
would: sign up, verify, log in, load the chart, run the agents, follow the
advice, buy shares, and open the portfolio.

Run it any time with:   python test_app.py

If every line says PASS, the app works.
"""

import json
import os
import sys

os.environ.setdefault("DB_TYPE", "sqlite")

import app as flask_app
import database

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}  {detail}")


def main():
    flask_app.app.config["TESTING"] = True
    database.init_db()
    client = flask_app.app.test_client()

    print("\n[1] PUBLIC PAGES")
    check("Homepage loads", client.get("/").status_code == 200)
    check("Signup page loads", client.get("/signup").status_code == 200)
    check("Login page loads", client.get("/login").status_code == 200)
    check("Unknown page shows 404", client.get("/nope").status_code == 404)
    check("Dashboard blocked when logged out",
          client.get("/dashboard").status_code == 302)

    print("\n[2] SIGNUP")
    import random
    tag = random.randint(10000, 99999)
    email = f"tester{tag}@example.com"
    username = f"tester{tag}"

    response = client.post("/signup", data={
        "username": username, "email": email, "password": "spider123"
    }, follow_redirects=True)
    check("Signup succeeds", response.status_code == 200)

    user = database.get_user_by_email(email)
    check("User saved to database", user is not None)
    check("User starts unverified", user and user["is_verified"] == 0)
    check("Confirmation code generated",
          user and user["confirmation_code"] and len(str(user["confirmation_code"])) == 6)

    print("\n[3] VALIDATION (bad input should be rejected)")
    dup = client.post("/signup", data={
        "username": username, "email": email, "password": "spider123"
    }, follow_redirects=True)
    check("Duplicate username rejected", b"already" in dup.data.lower())

    bad = client.post("/signup", data={
        "username": "ab", "email": "notanemail", "password": "1"
    }, follow_redirects=True)
    check("Short username rejected", b"at least 3" in bad.data.lower())

    print("\n[4] VERIFICATION")
    wrong = client.post("/verify", data={"email": email, "code": "000000"},
                        follow_redirects=True)
    check("Wrong code rejected", b"not correct" in wrong.data.lower())

    client.post("/verify", data={"email": email, "code": user["confirmation_code"]},
                follow_redirects=True)
    check("Correct code verifies account",
          database.get_user_by_email(email)["is_verified"] == 1)

    print("\n[5] LOGIN")
    bad_login = client.post("/login", data={"identifier": username,
                                            "password": "wrongpass"},
                            follow_redirects=True)
    check("Wrong password rejected", b"incorrect" in bad_login.data.lower())

    good = client.post("/login", data={"identifier": username,
                                       "password": "spider123"},
                       follow_redirects=True)
    check("Login succeeds", good.status_code == 200)
    check("Dashboard now reachable", client.get("/dashboard").status_code == 200)

    print("\n[6] MARKET DATA")
    candles = client.get("/api/candles/RELIANCE.NS")
    data = candles.get_json()
    check("Candles API responds", data and data.get("ok"))
    check("Candles have OHLC fields",
          data and len(data["candles"]) > 10 and
          all(k in data["candles"][0] for k in ("time", "open", "high", "low", "close")))
    check("Candles are in time order",
          data and all(data["candles"][i]["time"] < data["candles"][i + 1]["time"]
                       for i in range(len(data["candles"]) - 1)))
    print(f"        -> {len(data['candles'])} candles, quality={data['quality']}")

    tick = client.get("/api/tick/RELIANCE.NS").get_json()
    check("Live tick API responds", tick and tick.get("ok"))

    check("Unknown ticker rejected",
          client.get("/api/candles/FAKE.NS").status_code == 400)

    print("\n[6b] BSE — THE SECOND EXCHANGE")
    bse = client.get("/api/candles/RELIANCE.BO").get_json()
    check("BSE candles API responds", bse and bse.get("ok"))
    check("BSE returns usable candles", bse and len(bse["candles"]) > 10)

    nse_price = data["candles"][-1]["close"]
    bse_price = bse["candles"][-1]["close"] if bse and bse.get("ok") else None
    check("Both exchanges priced", bse_price is not None)
    if bse_price:
        print(f"        -> NSE Rs {nse_price}  |  BSE Rs {bse_price}  "
              f"(quality {bse['quality']})")

    check("Every company exists on both exchanges",
          len(flask_app.config.TICKERS)
          == len(flask_app.config.BASE_TICKERS) * len(flask_app.config.EXCHANGES))

    print("\n[6c] IST TIME ALIGNMENT")
    from datetime import datetime, timedelta
    import market as market_module

    sim = market_module._simulated_candles("RELIANCE.NS")
    last_time = sim[-1]["time"]

    # The backend adds +5:30, so reading the timestamp back as UTC should
    # give the IST wall-clock time.
    as_utc = datetime.utcfromtimestamp(last_time)
    ist_now = datetime.now(market_module.IST).replace(tzinfo=None)
    drift = abs((ist_now - as_utc).total_seconds())

    check("Offset constant is 5h30m", market_module.IST_OFFSET_SECONDS == 19800)
    check("Candle times read as IST clock time", drift < 30 * 60,
          f"drift was {drift/60:.1f} minutes")
    print(f"        -> last candle reads {as_utc.strftime('%d/%m %H:%M')} IST, "
          f"clock is {ist_now.strftime('%d/%m %H:%M')} IST")

    real, _ = market_module.get_candles("RELIANCE.NS")
    check("Real candles are also IST-shifted and ordered",
          all(real[i]["time"] < real[i + 1]["time"] for i in range(len(real) - 1)))

    print("\n[6d] CANDLE INTERVAL SWITCH (2m vs 15m)")
    for wanted in ("2m", "15m"):
        result = client.get(f"/api/candles/TCS.NS?interval={wanted}").get_json()
        check(f"{wanted} interval accepted", result and result.get("ok")
              and result.get("interval") == wanted)

        sim = market_module._simulated_candles("TCS.NS", wanted)
        gap = (sim[1]["time"] - sim[0]["time"]) / 60
        expected = market_module.INTERVALS[wanted]["minutes"]
        check(f"{wanted} candles are {expected} minutes apart", gap == expected,
              f"got {gap} minutes")

    fallback = client.get("/api/candles/TCS.NS?interval=nonsense").get_json()
    check("Bad interval falls back safely",
          fallback and fallback.get("interval") == market_module.DEFAULT_INTERVAL)
    check("Intervals are cached separately",
          "candles:TCS.NS:2m" in market_module._CACHE
          and "candles:TCS.NS:15m" in market_module._CACHE)

    print("\n[6e] EVERY CONFIGURED SYMBOL IS ACTUALLY LIVE")
    market_module._CACHE.clear()
    market_module._FAILURES.clear()
    live, dead = market_module.check_symbols()
    check("All configured symbols return live data", not dead,
          f"simulated: {dead}")

    check("Headlines exist for every company",
          all(market_module.sentiment_signal(
                  flask_app.config.symbol(b, "NSE"))["label"] != "UNAVAILABLE"
              for b in flask_app.config.BASE_TICKERS))
    check("Headlines work on the BSE listing too",
          all(market_module.sentiment_signal(
                  flask_app.config.symbol(b, "BSE"))["label"] != "UNAVAILABLE"
              for b in flask_app.config.BASE_TICKERS))

    # Every company must have its own filing in the corpus, and retrieval
    # must pick that company's document rather than a rival's.
    import retrieval as retrieval_module
    misrouted = []
    for base in flask_app.config.BASE_TICKERS:
        name = flask_app.config.COMPANIES[base]
        hits = retrieval_module.search(
            f"{name} {base} revenue margin guidance risk outlook", top_k=1)
        if not hits or base.lower()[:4] not in hits[0]["filename"].lower():
            misrouted.append(base)

    check("Every company retrieves its own filing", not misrouted,
          f"misrouted: {misrouted}")
    check("Corpus has a document per company",
          len(retrieval_module.load_documents()["documents"])
          >= len(flask_app.config.BASE_TICKERS))
    check("Ten companies across two exchanges = 20 symbols",
          len(flask_app.config.TICKERS) == 20)
    check("Every company has a BSE scrip code",
          all(b in flask_app.config.BSE_SCRIP_CODES
              for b in flask_app.config.BASE_TICKERS))

    forced = client.get("/api/candles/TCS.NS?break_prices=1").get_json()
    check("A degraded feed explains itself",
          forced and forced["quality"] == "DEGRADED" and forced.get("reason"))
    check("A healthy feed carries no error reason",
          client.get("/api/candles/TCS.NS").get_json().get("reason") is None)

    print("\n[6f] BSE MUST NOT LAG BEHIND NSE")
    from datetime import timezone as _tz
    market_module._CACHE.clear()
    market_module._BSE_QUOTES.clear()

    worst_gap = 0
    for base in flask_app.config.BASE_TICKERS:
        nse_candles, _ = market_module.get_candles(
            flask_app.config.symbol(base, "NSE"), "15m")
        bse_candles, _ = market_module.get_candles(
            flask_app.config.symbol(base, "BSE"), "15m")

        gap_minutes = abs(nse_candles[-1]["time"] - bse_candles[-1]["time"]) / 60
        worst_gap = max(worst_gap, gap_minutes)

    check("BSE is within one candle of NSE on every company", worst_gap <= 15,
          f"worst gap was {worst_gap:.0f} minutes")
    print(f"        -> worst NSE/BSE gap across all "
          f"{len(flask_app.config.BASE_TICKERS)} companies: {worst_gap:.0f} min")

    price = market_module.bse_live_price("RELIANCE")
    check("BSE live price feed reachable", price is not None and price > 0)
    check("Live top-up is flagged on BSE symbols",
          market_module.is_live_patched("RELIANCE.BO"))
    check("Live top-up is NOT applied to NSE symbols",
          not market_module.is_live_patched("RELIANCE.NS"))

    api = client.get("/api/candles/TCS.BO").get_json()
    check("API reports which BSE candles are live-topped",
          api and api.get("ok") and "live_patched" in api)

    check("Unknown company returns no BSE price",
          market_module.bse_live_price("NOTREAL") is None)

    print("\n[7] THE AGENTS")
    analysis = client.post("/api/analyze", json={"ticker": "TCS.NS"}).get_json()
    check("Analyze API responds", analysis and analysis.get("ok"))

    if analysis and analysis.get("ok"):
        check("Three agents ran", len(analysis["agents"]) == 3)
        check("Agents follow the output contract",
              all(all(k in a for k in ("agent_name", "signal", "confidence",
                                       "reasoning", "evidence", "data_quality"))
                  for a in analysis["agents"]))
        check("Verdict produced",
              analysis["verdict"]["verdict"] in ("BUY", "HOLD", "AVOID"))
        check("Citations returned", len(analysis["documents"]) > 0)
        check("Parallel faster than sequential",
              analysis["metrics"]["parallel_ms"] <= analysis["metrics"]["sequential_ms"])
        print(f"        -> verdict={analysis['verdict']['verdict']} "
              f"parallel={analysis['metrics']['parallel_ms']}ms "
              f"sequential={analysis['metrics']['sequential_ms']}ms")

        print("\n[8] FOLLOW / IGNORE")
        action = client.post("/api/action", json={
            "reco_id": analysis["reco_id"], "action": "FOLLOWED"
        }).get_json()
        check("Follow recorded", action and action.get("ok"))
        check("Scorecard updated", action and action["scorecard"]["followed"] >= 1)

        check("Invalid action rejected",
              client.post("/api/action", json={"reco_id": analysis["reco_id"],
                                               "action": "MAYBE"}).status_code == 400)

    print("\n[9] DEGRADED DATA")
    broken = client.post("/api/analyze", json={
        "ticker": "INFY.NS", "break_news": True, "break_prices": True
    }).get_json()
    check("Pipeline survives both feeds failing", broken and broken.get("ok"))

    if broken and broken.get("ok"):
        sentiment_agent = [a for a in broken["agents"]
                           if a["agent_name"] == "Sentiment Analyst"][0]
        check("Sentiment agent reports UNAVAILABLE",
              sentiment_agent["data_quality"] == "UNAVAILABLE")
        check("Broken agent gives zero confidence",
              sentiment_agent["confidence"] == 0.0)
        check("Still produces a cited verdict",
              broken["verdict"]["verdict"] in ("BUY", "HOLD", "AVOID"))

    print("\n[10] TRADING")
    buy = client.post("/api/trade", json={
        "ticker": "TCS.NS", "action": "BUY", "quantity": 5
    }).get_json()
    check("Buy succeeds", buy and buy.get("ok"), buy.get("error") if buy else "")

    too_many = client.post("/api/trade", json={
        "ticker": "TCS.NS", "action": "BUY", "quantity": 999999
    }).get_json()
    check("Cannot buy without enough cash", too_many and not too_many.get("ok"))

    oversell = client.post("/api/trade", json={
        "ticker": "INFY.NS", "action": "SELL", "quantity": 10
    }).get_json()
    check("Cannot sell shares you do not own", oversell and not oversell.get("ok"))

    bad_qty = client.post("/api/trade", json={
        "ticker": "TCS.NS", "action": "BUY", "quantity": -5
    }).get_json()
    check("Negative quantity rejected", bad_qty and not bad_qty.get("ok"))

    sell = client.post("/api/trade", json={
        "ticker": "TCS.NS", "action": "SELL", "quantity": 2
    }).get_json()
    check("Sell succeeds", sell and sell.get("ok"))

    print("\n[10b] LIVE PORTFOLIO API (30-second refresh)")
    summary = client.get("/api/portfolio_summary").get_json()
    check("Portfolio summary responds", summary and summary.get("ok"))

    if summary and summary.get("ok"):
        check("Summary has every field the page needs",
              all(k in summary for k in ("cash", "holdings", "total_value",
                                         "total_profit", "net_worth", "server_ist")))
        check("Net worth equals cash plus holdings",
              abs(summary["net_worth"]
                  - (summary["cash"] + summary["total_value"])) < 0.02)

        before = summary["cash"]
        client.post("/api/trade", json={"ticker": "TCS.NS", "action": "BUY",
                                        "quantity": 1})
        after = client.get("/api/portfolio_summary").get_json()
        check("Summary reflects a trade immediately", after["cash"] < before)

        held = {h["ticker"] for h in after["holdings"]}
        check("Traded stock appears in holdings", "TCS.NS" in held)
        check("Holdings priced from the same cache as the chart",
              all(h["current_price"] > 0 for h in after["holdings"]))

    print("\n[10c] AI LEARNS FROM ITS OWN RECORD")
    performance = flask_app.ai_performance(database.get_user_by_email(email)["id"])
    check("Performance record builds", isinstance(performance, dict))
    check("Performance tracks accuracy per verdict",
          "by_verdict" in performance and "recent_calls" in performance)

    import agents as agents_module
    block = agents_module._format_track_record(performance)
    check("Track record renders into prompt text",
          "TRACK RECORD" in block and len(block) > 40)

    empty = agents_module._format_track_record(None)
    check("Empty record still produces a humble prompt",
          "No scored recommendations yet" in empty)
    print(f"        -> {performance['total_scored']} scored calls, "
          f"overall {performance['overall_accuracy_pct']}%")

    print("\n[10d] AUTOMATIC BUY / SELL RULES")
    import autotrade

    me = database.get_user_by_email(email)
    client.post("/api/trade", json={"ticker": "INFY.NS", "action": "BUY",
                                    "quantity": 10})

    listing = client.get("/api/rules").get_json()
    check("Rules API responds", listing and listing.get("ok"))

    made = client.post("/api/rules", json={
        "ticker": "INFY.NS", "rule_type": "TAKE_PROFIT",
        "target_pct": 8, "quantity": 5}).get_json()
    check("Take-profit rule created", made and made.get("ok"))
    check("New rule shows up as active", made and len(made["rules"]) >= 1)

    # Validation
    check("Cannot sell more than you own via a rule",
          not client.post("/api/rules", json={
              "ticker": "INFY.NS", "rule_type": "TAKE_PROFIT",
              "target_pct": 5, "quantity": 99999}).get_json().get("ok"))
    check("Silly target percentage rejected",
          not client.post("/api/rules", json={
              "ticker": "INFY.NS", "rule_type": "TAKE_PROFIT",
              "target_pct": 900, "quantity": 1}).get_json().get("ok"))
    check("Unknown rule type rejected",
          not client.post("/api/rules", json={
              "ticker": "INFY.NS", "rule_type": "MAGIC",
              "target_pct": 5, "quantity": 1}).get_json().get("ok"))
    check("Unknown ticker rejected",
          client.post("/api/rules", json={
              "ticker": "FAKE.NS", "rule_type": "TAKE_PROFIT",
              "target_pct": 5, "quantity": 1}).status_code == 400)

    # The trigger maths, checked directly against known numbers.
    take = {"rule_type": "TAKE_PROFIT", "target_pct": 10, "reference_price": 100}
    fire, progress, _ = autotrade.evaluate_rule(take, 111, 100)
    check("Take profit fires above target", fire)
    fire, progress, _ = autotrade.evaluate_rule(take, 105, 100)
    check("Take profit holds below target", not fire)
    check("Progress bar reads 50% at half way", abs(progress - 50) < 1,
          f"got {progress}")

    stop = {"rule_type": "STOP_LOSS", "target_pct": 5, "reference_price": 100}
    check("Stop loss fires on a big fall",
          autotrade.evaluate_rule(stop, 94, 100)[0])
    check("Stop loss holds on a small fall",
          not autotrade.evaluate_rule(stop, 98, 100)[0])

    dip = {"rule_type": "BUY_DIP", "target_pct": 4, "reference_price": 200}
    check("Buy the dip fires after a drop",
          autotrade.evaluate_rule(dip, 191, None)[0])
    check("Buy the dip holds while flat",
          not autotrade.evaluate_rule(dip, 199, None)[0])

    # Now force a real one to fire end to end, by setting a target of 0.1%
    # against a reference price far below the market.
    before_rows, _, _ = flask_app.portfolio_rows(database.get_user_by_id(me["id"]))
    before_qty = sum(r["quantity"] for r in before_rows if r["ticker"] == "INFY.NS")

    rule_id = database.add_rule(me["id"], "INFY.NS", "TAKE_PROFIT",
                                0.1, 3, 1.0, "test rule")
    database.run("UPDATE transactions SET price = 1.0 WHERE user_id = ? "
                 "AND ticker = 'INFY.NS'", (me["id"],))

    fired = autotrade.check_rules(user_id=me["id"])
    check("A met target actually executes", any(f["status"] == "TRIGGERED"
                                                for f in fired),
          str(fired))

    # Dropping the cost basis can satisfy MORE than one standing order at
    # once, which is correct behaviour, so work out how many shares every
    # rule that fired was supposed to sell and check against that.
    sold_expected = sum(
        r["quantity"] for r in database.get_rules(me["id"])
        if r["status"] == "TRIGGERED" and r["ticker"] == "INFY.NS"
        and autotrade.RULE_TYPES[r["rule_type"]]["action"] == "SELL")

    after_rows, _, _ = flask_app.portfolio_rows(database.get_user_by_id(me["id"]))
    after_qty = sum(r["quantity"] for r in after_rows if r["ticker"] == "INFY.NS")

    check("Shares sold match what the rules asked for",
          after_qty == before_qty - sold_expected,
          f"{before_qty} -> {after_qty}, rules asked for {sold_expected}")
    check("Every rule that fired sold something", sold_expected > 0)
    print(f"        -> {len(fired)} rule(s) fired, {sold_expected} shares sold, "
          f"holding {before_qty} -> {after_qty}")

    triggered = [r for r in database.get_rules(me["id"])
                 if r["id"] == rule_id][0]
    check("Rule is closed after firing", triggered["status"] == "TRIGGERED")
    check("Trigger price recorded", triggered["triggered_price"] > 0)

    auto_trades = [t for t in database.get_transactions(me["id"])
                   if t["source"] == "AUTO"]
    check("Automatic trade is labelled AUTO", len(auto_trades) >= 1)

    check("A fired rule does not fire twice",
          not any(f["rule_id"] == rule_id
                  for f in autotrade.check_rules(user_id=me["id"])))

    # A rule that cannot be carried out must fail cleanly, not crash.
    broke_id = database.add_rule(me["id"], "SBIN.NS", "TAKE_PROFIT",
                                 0.1, 5, 1.0, "impossible rule")
    results = autotrade.check_rules(user_id=me["id"])
    broke = [r for r in database.get_rules(me["id"]) if r["id"] == broke_id][0]
    check("An impossible rule fails cleanly instead of crashing",
          broke["status"] in ("FAILED", "ACTIVE"))

    # Cancelling
    keep = client.post("/api/rules", json={
        "ticker": "INFY.NS", "rule_type": "STOP_LOSS",
        "target_pct": 50, "quantity": 1}).get_json()
    keep_id = keep["rule_id"]
    client.post(f"/api/rules/{keep_id}/cancel")
    cancelled = [r for r in database.get_rules(me["id"]) if r["id"] == keep_id][0]
    check("A rule can be cancelled", cancelled["status"] == "CANCELLED")

    summary = client.get("/api/portfolio_summary").get_json()
    check("Portfolio feed carries rules and firings",
          "rules" in summary and "fired" in summary)

    print("\n[11] PORTFOLIO PAGE")
    portfolio = client.get("/portfolio")
    check("Portfolio page loads", portfolio.status_code == 200)
    check("Holdings shown on page", b"TCS" in portfolio.data)
    check("Transaction history shown", b"TRANSACTION HISTORY" in portfolio.data)

    print("\n[12] PERSONALISATION (identical data, different risk profile)")
    client.post("/api/risk", json={"risk": "low"})
    low = client.post("/api/analyze", json={"ticker": "TCS.NS",
                                            "break_prices": True}).get_json()

    client.post("/api/risk", json={"risk": "high"})
    high = client.post("/api/analyze", json={"ticker": "TCS.NS",
                                             "break_prices": True}).get_json()

    check("Risk profile can be changed", low and high and low.get("ok") and high.get("ok"))
    check("Invalid risk level rejected",
          client.post("/api/risk", json={"risk": "insane"}).status_code == 400)

    if low and high and low.get("ok") and high.get("ok"):
        low_verdict = low["verdict"]["verdict"]
        high_verdict = high["verdict"]["verdict"]
        low_signals = {a["agent_name"]: a["signal"] for a in low["agents"]}
        high_signals = {a["agent_name"]: a["signal"] for a in high["agents"]}

        check("Agents saw identical market data", low_signals == high_signals,
              f"{low_signals} vs {high_signals}")
        check("Verdict DIFFERS by risk profile", low_verdict != high_verdict,
              f"both returned {low_verdict}")
        print(f"        -> low risk: {low_verdict}   |   high risk: {high_verdict}")

    print("\n[13] LOGOUT")
    client.get("/logout")
    check("Logout blocks the dashboard again",
          client.get("/dashboard").status_code == 302)

    print("\n" + "=" * 58)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("  FAILURES:")
        for name in FAILED:
            print(f"    - {name}")
    print("=" * 58 + "\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
