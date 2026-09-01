"""
agents.py
---------
The AI research team. Four agents in total.

Three SPECIALISTS run AT THE SAME TIME, each looking at one thing only:
    1. Technical Analyst   - price momentum and volume
    2. Fundamental Analyst - retrieved SEBI filings   (this is the RAG agent)
    3. Sentiment Analyst   - news headlines

They all return the SAME shape of JSON. That fixed shape is the
"structured output contract" the problem statement asks for.

Then a SYNTHESIS agent reads all three plus THIS user's risk profile and
produces the final BUY / HOLD / AVOID call.

OFFLINE MODE: with no ANTHROPIC_API_KEY the app still works, using simple
rule-based stand-ins that return the identical JSON shape. That is your
safety net if the wifi dies during judging.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import config
import retrieval

AGENT_JSON_SHAPE = """Reply with ONLY a JSON object, no other text, in exactly this shape:
{
  "signal": "BULLISH" or "NEUTRAL" or "BEARISH",
  "confidence": a number between 0.0 and 1.0,
  "reasoning": "two or three sentences explaining your call",
  "evidence": ["the specific data points or document quotes you used"]
}"""

SPECIALISTS = [
    {
        "name": "Technical Analyst",
        "source": "PRICE + VOLUME",
        "system": "You are a technical analyst. You look only at price and volume "
                  "behaviour. You never speculate about news or fundamentals.",
    },
    {
        "name": "Fundamental Analyst",
        "source": "SEBI FILINGS",
        "system": "You are a fundamental analyst. You read regulatory filings and "
                  "earnings transcripts. You cite the source of every claim and "
                  "never state anything the documents do not support.",
    },
    {
        "name": "Sentiment Analyst",
        "source": "NEWS HEADLINES",
        "system": "You are a market sentiment analyst. You judge the mood of recent "
                  "news coverage. You never invent headlines you were not given.",
    },
]


def has_api_key():
    """True if we can talk to Claude; False means offline rule-based mode."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ------------------------------------------------------- talking to Claude

def _call_claude(system_prompt, user_prompt, effort="low"):
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=2000,
        system=system_prompt,
        output_config={"effort": effort},
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text")


def _extract_json(text, fallback):
    """Pull the JSON object out of the reply, tolerating extra prose or fences."""
    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    return fallback


# ---------------------------------------------------------------- prompts

def _technical_prompt(signals):
    momentum, volume = signals["momentum"], signals["volume"]
    return f"""Analyse {signals['name']} ({signals['ticker']}) using ONLY the data below.

Latest price: Rs {signals['latest_price']} ({signals['change_pct']:+.2f}% on the period)
Price momentum: {momentum['value']}{momentum['unit']} - classified {momentum['label']}
  {momentum['detail']}
Volume: {volume['value']}{volume['unit']} - classified {volume['label']}
  {volume['detail']}
Price feed quality: {signals['price_data_quality']}

{AGENT_JSON_SHAPE}"""


def _fundamental_prompt(signals, documents):
    if not documents:
        sources = "NO DOCUMENTS COULD BE RETRIEVED."
    else:
        sources = "\n\n".join(
            f"--- SOURCE: {d['filename']} (relevance {d['score']}) ---\n{d['snippet']}"
            for d in documents
        )

    return f"""Analyse {signals['name']} ({signals['ticker']}) using ONLY the sources below.

{sources}

Every claim in your reasoning must be traceable to one of these documents.
In "evidence", name the source file alongside the fact you took from it.
If the documents do not support a clear view, say so and return NEUTRAL.

{AGENT_JSON_SHAPE}"""


def _sentiment_prompt(signals):
    sentiment = signals["sentiment"]

    if sentiment["label"] == "UNAVAILABLE":
        return f"""You were asked to analyse news sentiment for {signals['ticker']},
but the news feed is unavailable: {sentiment['detail']}

Do not guess. Return signal "NEUTRAL", confidence 0.0, and state plainly that no
sentiment data was available for this session.

{AGENT_JSON_SHAPE}"""

    headlines = "\n".join(
        f"- {h['headline']} (scored {h['score']})" for h in sentiment["headlines"]
    )
    return f"""Analyse news sentiment for {signals['name']} ({signals['ticker']}).

Recent headlines:
{headlines}

Average sentiment score: {sentiment['value']} ({sentiment['unit']})
Classified: {sentiment['label']}

{AGENT_JSON_SHAPE}"""


# ------------------------------------------------------- offline stand-ins

def _offline_specialist(agent_name, signals, documents):
    if agent_name == "Technical Analyst":
        m = signals["momentum"]
        return {
            "signal": m["label"] if m["label"] in ("BULLISH", "BEARISH") else "NEUTRAL",
            "confidence": m["confidence"],
            "reasoning": f"Price is {m['value']}{m['unit']}, classified {m['label']}. "
                         f"Volume is {signals['volume']['value']}x average "
                         f"({signals['volume']['label']}).",
            "evidence": [m["detail"], signals["volume"]["detail"]],
        }

    if agent_name == "Fundamental Analyst":
        if not documents:
            return {"signal": "NEUTRAL", "confidence": 0.0,
                    "reasoning": "No source documents could be retrieved.",
                    "evidence": []}
        return {
            "signal": "NEUTRAL",
            "confidence": 0.5,
            "reasoning": "Offline mode: filings were retrieved but not reasoned over. "
                         f"Top source was {documents[0]['filename']}.",
            "evidence": [d["filename"] for d in documents],
        }

    s = signals["sentiment"]
    if s["label"] == "UNAVAILABLE":
        return {"signal": "NEUTRAL", "confidence": 0.0,
                "reasoning": "No sentiment data was available for this session.",
                "evidence": []}
    return {
        "signal": s["label"] if s["label"] in ("BULLISH", "BEARISH") else "NEUTRAL",
        "confidence": s["confidence"],
        "reasoning": f"Average headline score {s['value']}, classified {s['label']} "
                     f"across {len(s['headlines'])} headlines.",
        "evidence": [h["headline"] for h in s["headlines"]],
    }


def _format_track_record(performance):
    """
    Turn the stored history of past calls into a block of text the synthesis
    agent can read. This is how the AI learns from its own results.
    """
    if not performance or not performance.get("total_scored"):
        return ("YOUR TRACK RECORD SO FAR:\n"
                "  No scored recommendations yet. Be appropriately humble and do "
                "not claim high confidence on thin evidence.")

    lines = ["YOUR TRACK RECORD SO FAR (scored against what the price actually did):"]
    lines.append(f"  Overall: {performance['overall_accuracy_pct']}% correct across "
                 f"{performance['total_scored']} scored calls.")

    for verdict, stats in performance.get("by_verdict", {}).items():
        if stats["scored"]:
            lines.append(f"  {verdict}: {stats['accuracy_pct']}% correct "
                         f"({stats['scored']} calls)")

    recent = performance.get("recent_calls", [])
    if recent:
        lines.append("  Most recent calls:")
        for call in recent:
            outcome = "CORRECT" if call["was_right"] else "WRONG"
            lines.append(
                f"    {call['ticker']} -> {call['verdict']} at "
                f"{call['confidence']:.0%} confidence; price then moved "
                f"{call['price_move_pct']:+.2f}% -> {outcome} "
                f"(user {call['user_action'].lower()})"
            )

    return "\n".join(lines)


def _offline_synthesis(agent_outputs, profile):
    usable = [a for a in agent_outputs if a["data_quality"] != "UNAVAILABLE"]
    bullish = sum(1 for a in usable if a["signal"] == "BULLISH")
    bearish = sum(1 for a in usable if a["signal"] == "BEARISH")
    cautious = profile.get("risk_tolerance") == "low"

    if bullish > bearish:
        verdict = "HOLD" if cautious else "BUY"
    elif bearish > bullish:
        verdict = "AVOID"
    else:
        verdict = "HOLD"

    confidence = min(0.85, 0.35 + 0.15 * len(usable))
    notes = []

    # Calibrate against the track record, the same way the live agent is told to.
    performance = profile.get("performance") or {}
    stats = (performance.get("by_verdict") or {}).get(verdict)
    if stats and stats.get("scored", 0) >= 3 and stats.get("accuracy_pct") is not None:
        accuracy = stats["accuracy_pct"]
        if accuracy < 50:
            confidence *= 0.75
            notes.append(f"Past {verdict} calls were only {accuracy}% correct across "
                         f"{stats['scored']} scored calls, so confidence is reduced.")
        elif accuracy >= 70:
            confidence = min(0.9, confidence * 1.1)
            notes.append(f"Past {verdict} calls were {accuracy}% correct across "
                         f"{stats['scored']} scored calls.")

    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "headline": f"{verdict} - {bullish} bullish against {bearish} bearish signals.",
        "reasoning": "Offline mode: this verdict is a vote count across the agents, "
                     "adjusted down for a low-risk profile and calibrated against the "
                     "system's own past accuracy. Set ANTHROPIC_API_KEY for real "
                     "synthesised reasoning. " + " ".join(notes),
        "personalisation_note": f"Adjusted for a {profile.get('risk_tolerance')} "
                                f"risk tolerance.",
        "risk_warnings": (["Offline mode - reasoning is rule-based, not AI-generated."]
                          + notes),
    }


# ----------------------------------------------------------- orchestration

def run_specialists(signals):
    """
    Run all three specialists AT THE SAME TIME.

    ThreadPoolExecutor starts all three, then waits for all three, so the
    total time is roughly the SLOWEST agent rather than the sum of all of
    them. The UI shows both numbers to prove it.
    """
    ticker = signals["ticker"]
    query = (f"{signals['name']} {ticker} revenue guidance margin outlook risk "
             f"buyback capex deposits volumes")

    try:
        documents = retrieval.search(query, top_k=3)
    except Exception:
        documents = []

    prompts = {
        "Technical Analyst": _technical_prompt(signals),
        "Fundamental Analyst": _fundamental_prompt(signals, documents),
        "Sentiment Analyst": _sentiment_prompt(signals),
    }

    offline = not has_api_key()

    def run_one(spec):
        started = time.time()
        fallback = _offline_specialist(spec["name"], signals, documents)

        if offline:
            result = fallback
            time.sleep(0.3)  # so parallel timings are visible in the demo
        else:
            try:
                reply = _call_claude(spec["system"], prompts[spec["name"]])
                result = _extract_json(reply, fallback)
            except Exception as error:
                result = dict(fallback)
                result["reasoning"] = f"Agent call failed ({error}). {fallback['reasoning']}"

        # How much do we trust this agent's inputs?
        if spec["name"] == "Sentiment Analyst" and signals["sentiment"]["label"] == "UNAVAILABLE":
            quality = "UNAVAILABLE"
        elif spec["name"] == "Technical Analyst" and signals["price_data_quality"] == "DEGRADED":
            quality = "DEGRADED"
        elif spec["name"] == "Fundamental Analyst" and not documents:
            quality = "UNAVAILABLE"
        else:
            quality = "OK"

        return {
            "agent_name": spec["name"],
            "source": spec["source"],
            "signal": result.get("signal", "NEUTRAL"),
            "confidence": float(result.get("confidence", 0.0) or 0.0),
            "reasoning": result.get("reasoning", ""),
            "evidence": result.get("evidence", []),
            "data_quality": quality,
            "latency_ms": int((time.time() - started) * 1000),
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        outputs = list(pool.map(run_one, SPECIALISTS))

    return outputs, documents


def synthesize(signals, agent_outputs, documents, profile):
    """
    The fourth agent. Reads the three specialist outputs AND this specific
    user's profile, then produces one recommendation.

    This is where personalisation happens: the same market data plus a
    different risk profile must produce a different answer.
    """
    started = time.time()
    fallback = _offline_synthesis(agent_outputs, profile)

    if not has_api_key():
        time.sleep(0.3)
        fallback["latency_ms"] = int((time.time() - started) * 1000)
        fallback["citations"] = [d["filename"] for d in documents]
        return fallback

    agent_block = "\n\n".join(
        f"AGENT: {a['agent_name']}\n"
        f"  signal: {a['signal']}\n"
        f"  confidence: {a['confidence']}\n"
        f"  data quality: {a['data_quality']}\n"
        f"  reasoning: {a['reasoning']}\n"
        f"  evidence: {a['evidence']}"
        for a in agent_outputs
    )

    holdings = profile.get("holdings", {})
    already_holds = holdings.get(signals["ticker"], {}).get("quantity", 0)
    track_record = _format_track_record(profile.get("performance"))

    user_prompt = f"""Three specialist agents analysed {signals['name']} ({signals['ticker']}).
Chart interval in use: {signals.get('interval', '15m')} candles.

{agent_block}

{track_record}

THE INVESTOR YOU ARE ADVISING:
  Username: {profile.get('username')}
  Risk tolerance: {profile.get('risk_tolerance')}
  Available cash: Rs {profile.get('cash', 0):,.0f}
  Current holdings: {holdings}
  Shares already held in {signals['ticker']}: {already_holds}
  Past behaviour: has followed the AI {profile.get('followed', 0)} times and
  ignored it {profile.get('ignored', 0)} times across {profile.get('total_recos', 0)}
  recommendations.

Your job:
1. Weigh the three agent signals. Where they DISAGREE, say so openly and explain
   which you weighted more heavily and why.
2. Where an agent reported data quality DEGRADED or UNAVAILABLE, lower your overall
   confidence and say so explicitly. Never present a conclusion as more certain
   than the underlying data allows.
3. Tailor the verdict to THIS investor. A low-risk investor and a high-risk investor
   must not receive the same answer from the same data.
4. Flag concentration risk if this investor already holds a large position here.
5. CALIBRATE AGAINST YOUR OWN TRACK RECORD above. If a particular verdict type has
   been performing badly, lower your confidence when reaching for it again and say
   in your reasoning that you have done so. If you have very few scored calls, treat
   the record as weak evidence rather than proof. Do not flip a verdict purely to
   look better - the market data still leads, the record only tunes confidence.

Reply with ONLY a JSON object, no other text, in exactly this shape:
{{
  "verdict": "BUY" or "HOLD" or "AVOID",
  "confidence": a number between 0.0 and 1.0,
  "headline": "one short sentence a beginner would understand",
  "reasoning": "four to six sentences, including how you resolved any disagreement",
  "personalisation_note": "one or two sentences on how this investor's profile changed the answer",
  "risk_warnings": ["short, specific warnings for this investor"]
}}"""

    system_prompt = (
        "You are the chief investment analyst synthesising a research team's work "
        "for an Indian retail investor. You are cautious, you never overstate "
        "confidence, and you always explain how this specific investor's profile "
        "changed your conclusion. Your output is educational analysis, not a "
        "personal recommendation to trade."
    )

    try:
        reply = _call_claude(system_prompt, user_prompt, effort="medium")
        result = _extract_json(reply, fallback)
    except Exception as error:
        result = dict(fallback)
        result["reasoning"] = f"Synthesis call failed ({error}). {fallback['reasoning']}"

    result["latency_ms"] = int((time.time() - started) * 1000)
    result["citations"] = [d["filename"] for d in documents]
    return result


def consensus_score(agent_outputs):
    """
    Metric: how many agents agreed?
    1.00 = unanimous. 0.33 = all three disagreed.
    """
    signals = [a["signal"] for a in agent_outputs
               if a.get("data_quality") != "UNAVAILABLE"]
    if not signals:
        return 0.0
    return round(max(signals.count(s) for s in set(signals)) / len(signals), 2)
