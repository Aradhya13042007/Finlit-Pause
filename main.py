"""
FinLit Pause: a decision-moment financial co-pilot (built for FinLit Ventures)
Flow: Risk screen -> Decision check (sell / redeem / SIP pause) -> Grounded intervention -> Fact-check
The user always stays in control. Education only, never buy/sell advice.
"""
import json, os, re, sys, time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

SCREEN_MODEL = os.getenv("SCREEN_MODEL", "groq/openai/gpt-oss-20b")
MAIN_MODEL   = os.getenv("MAIN_MODEL",   "groq/openai/gpt-oss-20b")
CHECK_MODEL  = os.getenv("CHECK_MODEL",  "groq/openai/gpt-oss-20b")

FALLBACK = "I cannot verify that information."

SCREEN_PARAMS = dict(temperature=0, max_tokens=1000)
MAIN_PARAMS   = dict(temperature=0, max_tokens=2000)
CHECK_PARAMS  = dict(temperature=0, max_tokens=1000)

ALERTS = {
    "crypto": "Cryptocurrencies can lose most of their value quickly and are largely unregulated.",
    "leveraged": "Leverage, margin and derivatives can lose more than you put in.",
    "penny_or_microcap": "Penny and micro-cap stocks are illiquid, easy to manipulate and often fail.",
    "speculative_tip": "Hot tips and 'guaranteed' returns are common features of scams and pump-and-dump schemes.",
    "single_stock_concentration": "Putting a large share of your money in one asset can cause severe, permanent loss.",
    "other_high_risk": "This looks like a high-risk or speculative investment. You could lose some or all of your money.",
}

VERIFIED = {
    "S1": "The user's SIP in the Nifty 50 Index Fund is Rs 25,000 on the 5th of each month.",
    "S2": "The user's house purchase goal has a target of Rs 60,00,000, with Rs 18,00,000 saved, which is 30% of the target.",
    "S3": "A SIP buys more units when the price is lower, which lowers the average cost per unit.",
    "S4": "The Nifty 50 Index Fund is held as a Direct plan with the Growth option.",
    "S5": "An index fund tracks a market index such as the Nifty 50 instead of trying to beat it.",
    "S6": "The user's risk profile is moderate.",
}

# ---------------------------------------------------------------------------
# DEMO PORTFOLIO. Keep in sync with S1/S2 above. Replace with real user data.
# ---------------------------------------------------------------------------
PORTFOLIO = {
    "fund": "Nifty 50 Index Fund",
    "sip_amount": 25000,
    "goal_target": 6000000,
    "goal_saved": 1800000,
}

# ---------------------------------------------------------------------------
# USER PROFILE (self-declared, from onboarding). DEMO VALUES: replace.
# style: anxious | impulsive | analytical | avoidant
# The demo UI has a dropdown (POST /profile) so all four tones can be shown.
# ---------------------------------------------------------------------------
USER_PROFILE = {
    "style": "anxious",
    "goal": "buying a house",
    "horizon": "more than 5 years",
    "needs_money_soon": False,
    "past_panic_sold": True,
    "checks_portfolio": "daily",
}

STYLE_GUIDE = {
    "anxious": "Calm and reassuring. Short sentences. Lead with the user's own long-term goal.",
    "impulsive": "Slow them down. Firm but kind. Stress the pause and ask one clear question first.",
    "analytical": "Structured and factual. Lead with the numbers from the context.",
    "avoidant": "Gentle and low-pressure. Keep it very short and give one small next step.",
}

# ---------------------------------------------------------------------------
# COMPANY SENTIMENT: snapshot from web sources, checked 30 Sep 2026.
# Refresh regularly. Add companies only with real, sourced lines.
# ---------------------------------------------------------------------------
COMPANIES = {
    "RELIANCE INDUSTRIES": {
        "aliases": ["reliance"],
        "sentiment": [
            "Q1 FY27 (quarter ended 30 Jun 2026): revenue Rs 3,11,850 crore, up 25.41% YoY. Reported net profit Rs 20,946 crore, down 22.40% YoY because the prior year included a one-time gain from an Asian Paints stake sale; profit was up 15.9% excluding exceptional items (Sahi.com).",
            "Consolidated debt was Rs 3,69,705 crore on 30 Jun 2026, down from Rs 3,74,421 crore on 31 Mar 2026 (Sahi.com).",
            "SEBI cleared the draft prospectus for the Jio Platforms IPO on 28 Aug 2026 (Sahi.com).",
            "Analyst tone after Q3 FY26 results: brokerages trimmed earnings estimates by 1-3% on weaker retail, but most stayed positive on the medium term (Bloomberg, via Outlook Business).",
        ],
    },
    "TCS": {
        "aliases": ["tcs", "tata consultancy"],
        "sentiment": [
            "Q1 FY27 (quarter ended 30 Jun 2026): revenue Rs 72,275 crore, up 13.9% YoY. Net profit Rs 13,349 crore, up 4.6% YoY and down 2.7% QoQ (Muthoot Securities).",
            "New deal wins were $9.5 billion in the quarter, and annualised AI revenue reached $2.6 billion (Business Today, 10 Jul 2026).",
            "Brokerage tone after Q1 was mixed: some rated Buy, others kept Hold waiting for clearer earnings. MOFSL expects FY27 demand to stay muted, though it said management commentary was better than expected (Business Today, 10 Jul 2026).",
        ],
    },
}

# Sourced lines added via POST /market and POST /history. Empty on purpose.
MARKET_NOTES = []
HISTORY_NOTES = []   # e.g. "Nifty 50 fell X% between <dates> and recovered by <date> (source)"

# Demo success metrics (in memory, reset on restart).
METRICS = {"interventions": 0, "waited": 0, "proceeded": 0}

def inr(n):
    s = str(int(n))
    if len(s) <= 3:
        return "Rs " + s
    last3, rest, parts = s[-3:], s[:-3], []
    while len(rest) > 2:
        parts.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        parts.insert(0, rest)
    return "Rs " + ",".join(parts + [last3])

def profile_facts():
    p, facts, n, lines = USER_PROFILE, {}, 0, []
    if p.get("goal"):
        lines.append(f"The user says their investing goal is: {p['goal']}.")
    if p.get("horizon"):
        lines.append(f"The user says their investing horizon is: {p['horizon']}.")
    if p.get("needs_money_soon") is not None:
        lines.append("The user says they " + ("do" if p["needs_money_soon"] else "do not") + " need this money soon.")
    if p.get("past_panic_sold"):
        lines.append("The user says they have sold in a panic before.")
    if p.get("checks_portfolio"):
        lines.append(f"The user says they check their portfolio: {p['checks_portfolio']}.")
    for t in lines:
        n += 1
        facts[f"P{n}"] = t
    return facts

def portfolio_facts():
    p = PORTFOLIO
    pct = round(p["goal_saved"] / p["goal_target"] * 100)
    remaining = p["goal_target"] - p["goal_saved"]
    return {
        "G1": f"Goal progress: {inr(p['goal_saved'])} saved of a {inr(p['goal_target'])} target ({pct}%), leaving {inr(remaining)} to go.",
        "G2": f"The monthly SIP is {inr(p['sip_amount'])}. Every month it is paused means {inr(p['sip_amount'])} is not invested toward the goal.",
    }

def company_facts(user_msg):
    for name, c in COMPANIES.items():
        for a in c["aliases"]:
            if re.search(r"\b" + re.escape(a) + r"\b", user_msg, re.I):
                return name, {f"C{i}": f"{name}: {line}" for i, line in enumerate(c["sentiment"], 1)}
    return None, {}

def market_facts():
    return {f"M{i}": t for i, t in enumerate(MARKET_NOTES, 1)}

def history_facts():
    return {f"H{i}": t for i, t in enumerate(HISTORY_NOTES, 1)}

def _call(model, messages, **params):
    t = time.perf_counter()

    api_key = (os.getenv("GROQ_API_KEY") or "").strip().strip('"').strip("'")
    if not api_key:
        raise ValueError("GROQ_API_KEY is missing. Add it to your .env file and restart the server.")

    actual_model = model.replace("groq/", "", 1)
    payload = {
        "model": actual_model,
        "messages": messages,
        "temperature": params.get("temperature", 0),
        "max_tokens": params.get("max_tokens", 1000),
    }
    if "gpt-oss" in actual_model:
        payload["reasoning_effort"] = "low"

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FinLitPause/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {body}") from None

    ms = int((time.perf_counter() - t) * 1000)
    return (result["choices"][0]["message"]["content"] or ""), ms

def _parse_json(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON in model output")
    return json.loads(m.group(0))

def _err(e):
    return f"{type(e).__name__}: {str(e)[:250]}"

def build_context(docs):
    return "\n".join(f"[{k}] {v}" for k, v in docs.items())

def used_tags(text, docs):
    return {t: docs[t] for t in set(re.findall(r"\[([A-Z]\d+)\]", text)) if t in docs}

# ---------------------------------------------------------------------------
# Step 1: risky-asset screen
# ---------------------------------------------------------------------------
SCREEN_SYSTEM_PROMPT = f"""You are a risk-intent classifier for a finance chatbot.
You do NOT answer the user. You only classify their message.

Flag the message as risky if the user asks about, or shows intent to buy, invest in, trade or borrow for any risky, volatile or speculative asset or strategy, such as: cryptocurrency/memecoins/NFTs, leveraged or margin trading, futures & options, penny or micro-cap stocks, forex, "guaranteed" returns, hot tips, going all-in on one asset.

Do NOT flag: general education questions, diversified index funds, SIPs, FDs, bonds.

Respond with ONLY a JSON object:
{{"risky": true|false, "category": one of {list(ALERTS)} or null, "reason": "<max 15 words>"}}"""

_RISKY_PATTERNS = {
    "crypto": r"\b(bitcoin|btc|ethereum|eth|crypto|doge|dogecoin|memecoin|nft|altcoin)\b",
    "leveraged": r"\b(leverage|margin trading|margin|futures|options trading|f&o|intraday|derivative)\b",
    "penny_or_microcap": r"\b(penny stocks?|micro-?caps?)\b",
    "speculative_tip": r"\b(guaranteed returns?|hot tip|double my money|get rich quick|100x)\b",
    # Concentration safety net: going all-in on a single asset.
    "single_stock_concentration": (
        r"\b(all-in|going all in|all my (savings|money) (in|into)|"
        r"entire (savings|portfolio) (in|into)|everything (in|into) (one|a single))\b"
    ),
}

def keyword_screen(text):
    for cat, pat in _RISKY_PATTERNS.items():
        if re.search(pat, text, re.I):
            return cat
    return None

@dataclass
class RiskResult:
    risky: bool
    category: str | None = None
    reason: str = ""

def screen_risk(user_msg, trace):
    kw = keyword_screen(user_msg)
    try:
        raw, ms = _call(
            SCREEN_MODEL,
            [{"role": "system", "content": SCREEN_SYSTEM_PROMPT},
             {"role": "user", "content": f"<user_message>\n{user_msg}\n</user_message>"}],
            **SCREEN_PARAMS,
        )
        data = _parse_json(raw)
        risky = bool(data.get("risky"))
        cat = data.get("category") if data.get("category") in ALERTS else "other_high_risk"
        reason = str(data.get("reason", ""))[:120]
        if kw and not risky:
            risky, cat, reason = True, kw, "keyword safety net"
        trace.append({"step": "1. Risk screen", "model": SCREEN_MODEL, "ms": ms,
                      "status": "RISKY" if risky else "clear", "detail": reason})
        return RiskResult(risky, cat if risky else None, reason)
    except Exception as e:
        trace.append({"step": "1. Risk screen", "model": SCREEN_MODEL, "ms": 0,
                      "status": "error (keyword fallback used)", "detail": _err(e)})
        return RiskResult(bool(kw), kw, "screen model unavailable")

def format_alert(r):
    return f"RISK ALERT: {ALERTS[r.category or 'other_high_risk']} This is education, not advice."

# ---------------------------------------------------------------------------
# Step 2: decision-moment detector (sell / redeem / exit / SIP pause)
# ---------------------------------------------------------------------------
_SELL_RE = r"\b(sell|selling|sold|exit|dump|liquidate|redeem|withdraw|book (a )?loss|cut (my )?losses|get out)\b"
_SIP_RE = (r"\b(pause|stop|cancel|reduce|skip|lower|halt|suspend)\b.{0,30}\bsip\b"
           r"|\bsip\b.{0,30}\b(pause|stop|cancel|reduce|skip|lower|halt|suspend)\b")
_PANIC_RE = r"\b(crash|crashing|panic|scared|afraid|worried|terrified|falling|dropping|plunge|bloodbath|lose everything|can'?t take|right now|immediately)\b"

def action_type(text):
    if re.search(_SIP_RE, text, re.I | re.S):
        return "sip_pause"
    if re.search(_SELL_RE, text, re.I):
        return "redeem_exit"
    return None

PANIC_SYSTEM_PROMPT = """You classify a message from an investor. You do NOT answer it.
Decide:
- sell_intent: true if the user wants to sell, exit or redeem an investment, or to pause, reduce, stop or cancel a SIP.
- panic_level: none | low | medium | high (fear, urgency, crash talk, loss aversion).
- emotion: fear | anger | regret | greed | calm | other
- needs_cash: true only if the user says they need the money for a real expense, false if they say they do not, null if unclear.
Respond with ONLY JSON:
{"sell_intent": true|false, "panic_level": "none|low|medium|high", "emotion": "...", "needs_cash": true|false|null, "reason": "<max 15 words>"}"""

@dataclass
class PanicResult:
    intervene: bool
    sell: bool = False
    level: str = "none"
    emotion: str = "calm"
    needs_cash: bool | None = None
    reason: str = ""
    action: str | None = None

def detect_panic(user_msg, trace):
    act = action_type(user_msg)
    sell_kw = act is not None
    panic_kw = bool(re.search(_PANIC_RE, user_msg, re.I))
    try:
        raw, ms = _call(
            SCREEN_MODEL,
            [{"role": "system", "content": PANIC_SYSTEM_PROMPT},
             {"role": "user", "content": f"<user_message>\n{user_msg}\n</user_message>"}],
            **SCREEN_PARAMS,
        )
        d = _parse_json(raw)
        sell = bool(d.get("sell_intent")) or act == "sip_pause"
        level = d.get("panic_level") if d.get("panic_level") in {"none", "low", "medium", "high"} else "low"
        emotion = str(d.get("emotion", "other"))[:20]
        needs_cash = d.get("needs_cash") if isinstance(d.get("needs_cash"), bool) else None
        reason = str(d.get("reason", ""))[:120]
        if sell and panic_kw and level == "none":
            level = "low"
        if sell and act is None:
            act = "redeem_exit"
        status = "RISKY" if (sell and level in {"medium", "high"}) else ("watch" if sell else "clear")
        trace.append({"step": "2. Decision check", "model": SCREEN_MODEL, "ms": ms, "status": status,
                      "detail": f"action={act}, sell={sell}, panic={level}, emotion={emotion}, needs_cash={needs_cash}. {reason}"})
    except Exception as e:
        sell = sell_kw
        level = "medium" if (sell_kw and panic_kw) else ("low" if sell_kw else "none")
        emotion = "fear" if panic_kw else "other"
        needs_cash = None
        reason = "keyword fallback"
        trace.append({"step": "2. Decision check", "model": SCREEN_MODEL, "ms": 0,
                      "status": "error (keyword fallback used)", "detail": _err(e)})

    intervene = sell and not (needs_cash is True and level == "none")
    return PanicResult(intervene, sell, level, emotion, needs_cash, reason, act)

# ---------------------------------------------------------------------------
# Step 3: grounded intervention
# ---------------------------------------------------------------------------
ACTION_NOTES = {
    "redeem_exit": "The user wants to sell, redeem or exit an investment.",
    "sip_pause": "The user wants to pause, reduce or stop a SIP. Use the goal and SIP facts to show what pausing means for their goal, without predicting returns.",
}

INTERVENTION_SYSTEM_PROMPT = """You are a calm money-behaviour coach inside a finance app. The user is at a decision moment and may act on emotion.

SITUATION: {action_note}

RULES:
1. For anything factual about the user, the goal, the company or the market, use ONLY facts inside <verified_context>. Add the source tag after every factual sentence, e.g. [G1].
2. Never tell the user to sell, not to sell, pause or not pause. Never predict prices or returns. Your job is to slow the decision down and help them think.
3. Structure:
   a) One sentence naming the feeling without judging it.
   b) Remind them of their own stated goal and progress.
   c) If company facts exist, describe the company's sentiment picture from them. If none exist, say there is no verified company sentiment available.
   d) Ask exactly 3 short check questions (do I need this money soon, has my goal changed, has the situation actually changed or only the price).
   e) Suggest a 24-hour pause before deciding.
4. Tone: {style}
5. Under 150 words, plain language.
6. If a verified historical fact exists in the context, use it to show how markets behaved in past falls. If none exists, do not mention history.

<verified_context>
{context}
</verified_context>"""

SAFE_PAUSE = ("Before you go ahead: pause for 24 hours. Ask yourself three things. "
              "Do I need this money soon? Has my goal changed? Has the situation actually changed, "
              "or has only the price moved? You can still act later if the answers say so.")

# Light path: the user states a genuine cash need and shows no panic.
# No coach/fact-check model call and no factual claims, so nothing needs fact-checking.
LIGHT_NOTE = ("That sounds like a genuine need, so no pause is needed. "
              "Double-check the amount and the fund, then go ahead whenever you are ready.")

INTERVENTION_CHECK_PROMPT = """You are a strict fact-checker.
Given a CONTEXT and a DRAFT, check every factual claim about the user, a company or the market.
A claim is SUPPORTED only if the CONTEXT states it explicitly.
Questions, feelings named gently, and suggestions to pause are NOT factual claims and need no support.
Respond with ONLY JSON:
{"verdict": "pass"|"fail", "unsupported_claims": ["..."]}"""

def generate_intervention(user_msg, panic, docs, trace):
    style = STYLE_GUIDE.get(USER_PROFILE.get("style"), STYLE_GUIDE["anxious"])
    note = ACTION_NOTES.get(panic.action or "redeem_exit", ACTION_NOTES["redeem_exit"])
    system = (INTERVENTION_SYSTEM_PROMPT
              .replace("{action_note}", note)
              .replace("{style}", style)
              .replace("{context}", build_context(docs)))
    raw, ms = _call(
        MAIN_MODEL,
        [{"role": "system", "content": system},
         {"role": "user", "content": f"<user_message>\n{user_msg}\n</user_message>\n<detected_emotion>{panic.emotion}, panic level {panic.level}</detected_emotion>"}],
        **MAIN_PARAMS,
    )
    draft = raw.strip()
    trace.append({"step": "3. Intervention", "model": MAIN_MODEL, "ms": ms,
                  "status": "drafted" if draft else "empty",
                  "detail": f"style={USER_PROFILE.get('style')}; {draft[:120]}"})
    return draft

def check_with(prompt, draft, docs, step, trace):
    try:
        raw, ms = _call(
            CHECK_MODEL,
            [{"role": "system", "content": prompt},
             {"role": "user", "content": f"<context>\n{build_context(docs)}\n</context>\n<draft>\n{draft}\n</draft>"}],
            **CHECK_PARAMS,
        )
        data = _parse_json(raw)
        ok = data.get("verdict") == "pass"
        problems = [str(x) for x in data.get("unsupported_claims", [])]
        trace.append({"step": step, "model": CHECK_MODEL, "ms": ms,
                      "status": "PASS" if ok else "FAIL",
                      "detail": "; ".join(problems)[:160] if problems else "all claims supported"})
        return ok, problems, False
    except Exception as e:
        trace.append({"step": step, "model": CHECK_MODEL, "ms": 0,
                      "status": "error", "detail": _err(e)})
        return False, [], True

# ---------------------------------------------------------------------------
# Normal grounded answer
# ---------------------------------------------------------------------------
MAIN_SYSTEM_PROMPT = f"""You are a summarizer of verified financial text. You are NOT a source of knowledge.

RULES:
1. Use ONLY facts stated inside <verified_context>.
2. If the answer is not fully contained in the context, reply with exactly: {FALLBACK}
3. After every factual sentence, add the source tag, e.g. [S2].
4. Keep it concise, plain language, under 120 words.

<verified_context>
{{context}}
</verified_context>"""

def generate_answer(user_msg, docs, trace):
    raw, ms = _call(
        MAIN_MODEL,
        [{"role": "system", "content": MAIN_SYSTEM_PROMPT.replace("{context}", build_context(docs))},
         {"role": "user", "content": f"<user_question>\n{user_msg}\n</user_question>"}],
        **MAIN_PARAMS,
    )
    draft = raw.strip() or FALLBACK
    trace.append({"step": "3. Grounded answer", "model": MAIN_MODEL, "ms": ms,
                  "status": "not in context" if draft.startswith(FALLBACK) else "drafted",
                  "detail": draft[:160]})
    return draft

CHECK_SYSTEM_PROMPT = """You are a strict fact-checker.
Given a CONTEXT and a DRAFT answer, check every factual claim in the DRAFT.
A claim is SUPPORTED only if the CONTEXT states it explicitly.
Respond with ONLY JSON:
{"verdict": "pass"|"fail", "unsupported_claims": ["..."]}"""

@dataclass
class BotReply:
    alert: str | None
    text: str
    status: str
    trace: list = field(default_factory=list)
    pause: str | None = None
    facts: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)

def chat(user_msg, docs=None):
    docs = VERIFIED if docs is None else docs
    trace = []
    risk = screen_risk(user_msg, trace)
    alert = format_alert(risk) if risk.risky else None

    panic = detect_panic(user_msg, trace)

    # Light path: a sell/SIP action with a genuine cash need and no panic.
    # No pause banner; Wait / Proceed stay visible because status starts with "panic_intervention".
    # Choices made here are NOT counted in the wait-rate metric (see /outcome).
    if panic.sell and not panic.intervene:
        meta = {"action": panic.action, "emotion": panic.emotion, "panic_level": panic.level,
                "style": USER_PROFILE.get("style"), "company": None}
        return BotReply(alert, LIGHT_NOTE, "panic_intervention_light", trace, None, {}, meta, docs)

    if panic.intervene:
        METRICS["interventions"] += 1
        pause = "Pause: consider waiting 24 hours before you confirm. You are not locked out; this is a check-in."
        company, cfacts = company_facts(user_msg)
        idocs = {**docs, **profile_facts(), **portfolio_facts(), **cfacts, **market_facts(), **history_facts()}
        meta = {"action": panic.action, "emotion": panic.emotion, "panic_level": panic.level,
                "style": USER_PROFILE.get("style"), "company": company}
        try:
            draft = generate_intervention(user_msg, panic, idocs, trace)
        except Exception as e:
            trace.append({"step": "3. Intervention", "model": MAIN_MODEL, "ms": 0,
                          "status": "error", "detail": _err(e)})
            return BotReply(alert, SAFE_PAUSE, "panic_intervention_basic", trace, pause, {}, meta, idocs)
        if not draft:
            return BotReply(alert, SAFE_PAUSE, "panic_intervention_basic", trace, pause, {}, meta, idocs)
        ok, problems, errored = check_with(INTERVENTION_CHECK_PROMPT, draft, idocs, "4. Fact-check", trace)
        if errored or not ok:
            return BotReply(alert, SAFE_PAUSE, "panic_intervention_basic", trace, pause, {}, meta, idocs)
        return BotReply(alert, draft, "panic_intervention", trace, pause, used_tags(draft, idocs), meta, idocs)

    if not docs:
        return BotReply(alert, FALLBACK, "not_in_context", trace)

    try:
        draft = generate_answer(user_msg, docs, trace)
    except Exception as e:
        trace.append({"step": "3. Grounded answer", "model": MAIN_MODEL, "ms": 0,
                      "status": "error", "detail": _err(e)})
        return BotReply(alert, FALLBACK, "model_error", trace)

    if draft.startswith(FALLBACK) and "\n" not in draft:
        trace.append({"step": "4. Fact-check", "model": CHECK_MODEL, "ms": 0,
                      "status": "skipped", "detail": "fallback needs no check"})
        return BotReply(alert, FALLBACK, "not_in_context", trace)

    ok, problems, errored = check_with(CHECK_SYSTEM_PROMPT, draft, docs, "4. Fact-check", trace)
    if errored:
        return BotReply(alert, FALLBACK, "checker_error", trace)
    if not ok:
        return BotReply(alert, FALLBACK, "blocked_by_factcheck", trace)
    return BotReply(alert, draft, "answered", trace, None, used_tags(draft, docs), {}, docs)

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
app = FastAPI(title="FinLit Pause API")

class Msg(BaseModel):
    text: str

class Note(BaseModel):
    text: str

class Outcome(BaseModel):
    choice: str
    light: bool = False      # True when the choice was made after a light-path reply

class Profile(BaseModel):
    style: str

def metrics_payload():
    done = METRICS["waited"] + METRICS["proceeded"]
    rate = round(METRICS["waited"] / done * 100) if done else None
    return {**METRICS, "wait_rate": rate}

@app.post("/chat")
def chat_endpoint(m: Msg):
    r = chat(m.text)
    return {"alert": r.alert, "reply": r.text, "status": r.status, "trace": r.trace,
            "pause": r.pause, "facts": r.facts, "meta": r.meta, "context": r.context}

@app.post("/outcome")
def outcome(o: Outcome):
    # Wait rate covers coached check-ins only; light-path choices are not counted.
    if not o.light:
        if o.choice == "wait":
            METRICS["waited"] += 1
        elif o.choice == "proceed":
            METRICS["proceeded"] += 1
    return metrics_payload()

@app.get("/metrics")
def metrics():
    return metrics_payload()

@app.post("/profile")
def set_profile(p: Profile):
    # Demo switch so all four tones can be shown. In production the style comes from onboarding.
    if p.style in STYLE_GUIDE:
        USER_PROFILE["style"] = p.style
    return {"style": USER_PROFILE["style"]}

@app.get("/portfolio")
def portfolio():
    p = PORTFOLIO
    return {"fund": p["fund"], "sip": inr(p["sip_amount"]), "saved": inr(p["goal_saved"]),
            "target": inr(p["goal_target"]), "pct": round(p["goal_saved"] / p["goal_target"] * 100),
            "goal": USER_PROFILE["goal"], "horizon": USER_PROFILE["horizon"],
            "style": USER_PROFILE["style"]}

@app.post("/market")
def add_market_note(n: Note):
    MARKET_NOTES.append(n.text.strip()[:300])
    return {"notes": MARKET_NOTES}

@app.post("/history")
def add_history_note(n: Note):
    HISTORY_NOTES.append(n.text.strip()[:300])
    return {"notes": HISTORY_NOTES}

@app.get("/")
def home():
    return HTMLResponse(DEMO_HTML)

# Logo: put logo.png in the same folder as this file.
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")

@app.get("/logo.png")
def logo():
    return FileResponse(LOGO_PATH, media_type="image/png")

DEMO_HTML = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FinLit Pause</title>
<link rel="icon" href="/logo.png">
<script src="https://cdn.tailwindcss.com"></script>
<style>
body{background:#f1f5f9}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:1rem;box-shadow:0 1px 3px rgba(15,23,42,.05)}
.chip{display:inline-block;background:#e0e7ff;color:#3730a3;font-size:.7rem;font-weight:700;padding:1px 6px;margin:0 2px;border-radius:.5rem;cursor:help;vertical-align:middle}
.pill{display:inline-block;font-size:.75rem;font-weight:600;padding:3px 10px;border-radius:999px;border:1px solid}
.banner{padding:1rem;border-left:4px solid;border-radius:0 .75rem .75rem 0;font-weight:600;margin-bottom:1rem}
.banner.red{background:#fef2f2;border-color:#ef4444;color:#991b1b}
.banner.amber{background:#fffbeb;border-color:#f59e0b;color:#78350f}
.loader{border-top-color:#4f46e5;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.act{transition:.15s}.act:hover{transform:translateY(-1px)}
</style></head>
<body class="text-slate-800 font-sans min-h-screen">
<header class="bg-gradient-to-r from-slate-900 via-indigo-900 to-slate-900 text-white">
  <div class="max-w-6xl mx-auto px-6 py-5 flex flex-wrap items-center justify-between gap-4">
    <div class="flex items-center gap-3">
      <img src="/logo.png" alt="FinLit" class="h-14 w-auto">
      <div>
        <h1 class="text-xl font-bold leading-tight">FinLit Pause</h1>
        <p class="text-xs text-indigo-200">Decision-moment co-pilot &middot; built for FinLit Ventures</p>
      </div>
    </div>
    <div class="flex gap-3 text-center">
      <div class="bg-white/10 rounded-xl px-4 py-2"><div id="m_int" class="text-lg font-bold">0</div><div class="text-[11px] text-indigo-200">Check-ins</div></div>
      <div class="bg-white/10 rounded-xl px-4 py-2"><div id="m_wait" class="text-lg font-bold">0</div><div class="text-[11px] text-indigo-200">Chose to wait</div></div>
      <div class="bg-white/10 rounded-xl px-4 py-2"><div id="m_rate" class="text-lg font-bold">-</div><div class="text-[11px] text-indigo-200">Wait rate</div></div>
    </div>
  </div>
</header>

<main class="max-w-6xl mx-auto px-6 py-8 grid lg:grid-cols-5 gap-6">
  <section class="lg:col-span-2 space-y-4">
    <div class="card p-6">
      <p class="text-xs font-semibold uppercase tracking-wider text-slate-500">Your portfolio (demo)</p>
      <div id="pf" class="mt-3 text-slate-500">Loading...</div>
      <div class="mt-4 pt-4 border-t border-slate-200">
        <label for="sty" class="text-xs font-semibold uppercase tracking-wider text-slate-500">Tone style (self-declared, demo switch)</label>
        <select id="sty" class="mt-2 w-full p-2 border border-slate-300 rounded-lg text-sm bg-white" onchange="setStyle(this.value)">
          <option value="anxious">Anxious: calm, goal first</option>
          <option value="impulsive">Impulsive: firm pause, one question</option>
          <option value="analytical">Analytical: numbers first</option>
          <option value="avoidant">Avoidant: short, one small step</option>
        </select>
      </div>
    </div>
    <div class="card p-6">
      <p class="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">Try an action</p>
      <p class="text-sm text-slate-500 mb-4">The co-pilot steps in before the action is confirmed.</p>
      <div class="grid gap-3">
        <button class="act w-full py-3 rounded-xl bg-rose-600 hover:bg-rose-700 text-white font-semibold" onclick="startAction('redeem')">Redeem units</button>
        <button class="act w-full py-3 rounded-xl bg-amber-500 hover:bg-amber-600 text-white font-semibold" onclick="startAction('pause')">Pause SIP</button>
        <button class="act w-full py-3 rounded-xl bg-slate-700 hover:bg-slate-800 text-white font-semibold" onclick="startAction('reduce')">Reduce SIP</button>
      </div>
    </div>
  </section>

  <section class="lg:col-span-3 space-y-4">
    <div class="card p-6">
      <p class="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">Ask the co-pilot</p>
      <input id="q" class="w-full p-4 border border-slate-300 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none shadow-sm" placeholder="Ask a question or describe what you are about to do..." onkeydown="if(event.key==='Enter')ask()">
      <div class="flex flex-wrap gap-2 mt-3">
        <button class="px-3 py-2 bg-slate-100 hover:bg-slate-200 rounded-lg text-sm font-medium" onclick="go('Is my Nifty 50 fund regular or direct, and how does a SIP help me?')">Knowledge</button>
        <button class="px-3 py-2 bg-red-50 hover:bg-red-100 text-red-700 border border-red-100 rounded-lg text-sm font-medium" onclick="go('Should I invest in Dogecoin?')">Risk filter</button>
        <button class="px-3 py-2 bg-amber-50 hover:bg-amber-100 text-amber-800 border border-amber-200 rounded-lg text-sm font-medium" onclick="go('The market is crashing and I am scared, I want to sell my Nifty fund right now')">Panic sell</button>
        <button class="px-3 py-2 bg-amber-50 hover:bg-amber-100 text-amber-800 border border-amber-200 rounded-lg text-sm font-medium" onclick="go('Reliance shares are falling and I am scared. I want to sell everything now.')">Company sentiment</button>
        <button class="px-3 py-2 bg-amber-50 hover:bg-amber-100 text-amber-800 border border-amber-200 rounded-lg text-sm font-medium" onclick="go('Markets are volatile, I want to pause my SIP for a few months.')">SIP pause</button>
      </div>
      <button class="act w-full mt-4 bg-indigo-600 text-white font-semibold py-3 rounded-xl hover:bg-indigo-700 shadow-sm" onclick="ask()">Run co-pilot</button>
    </div>
    <div id="out"></div>
  </section>
</main>

<footer class="max-w-6xl mx-auto px-6 pb-10 text-xs text-slate-500">
  FinLit Pause gives education and prompts reflection. It is not investment advice and never tells you to buy or sell. You always make the final decision. Demo data; no real transactions.
</footer>

<script>
const $=id=>document.getElementById(id);
window._light=false;
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function chips(text,facts){return esc(text).replace(/\[([A-Z]\d+)\]/g,(m,id)=>facts&&facts[id]?`<span class="chip" title="${esc(facts[id])}">${id}</span>`:'')}

const ACTIONS={
  redeem:{title:'Redeem units',phrase:'redeem my Nifty 50 Index Fund units'},
  pause:{title:'Pause SIP',phrase:'pause my SIP'},
  reduce:{title:'Reduce SIP',phrase:'reduce my SIP'}
};
const ACTION_LABEL={redeem_exit:'Redeem / exit',sip_pause:'SIP change'};

async function loadPortfolio(){
  try{
    const p=await (await fetch('/portfolio')).json();
    $('pf').innerHTML=`
      <h2 class="text-lg font-bold text-slate-900">${esc(p.fund)}</h2>
      <p class="text-sm text-slate-500">SIP ${esc(p.sip)} per month</p>
      <div class="mt-4"><div class="flex justify-between text-sm mb-1"><span>Goal: ${esc(p.goal)}</span><span class="font-semibold">${p.pct}%</span></div>
      <div class="h-3 bg-slate-200 rounded-full overflow-hidden"><div class="h-3 bg-indigo-500 rounded-full" style="width:${p.pct}%"></div></div>
      <p class="text-xs text-slate-500 mt-2">${esc(p.saved)} of ${esc(p.target)} &middot; horizon: ${esc(p.horizon)}</p></div>`;
    if(p.style) $('sty').value=p.style;
  }catch(e){$('pf').textContent='Could not load portfolio.'}
}
async function setStyle(s){
  try{await fetch('/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({style:s})});}catch(e){}
}
async function loadMetrics(){
  try{
    const m=await (await fetch('/metrics')).json();
    $('m_int').textContent=m.interventions; $('m_wait').textContent=m.waited;
    $('m_rate').textContent=m.wait_rate===null?'-':m.wait_rate+'%';
  }catch(e){}
}

function startAction(k){
  const a=ACTIONS[k];
  $('out').innerHTML=`<div class="card p-6">
    <p class="text-xs font-semibold uppercase tracking-wider text-slate-500">Before you confirm</p>
    <h3 class="text-lg font-bold mt-1">You are about to: ${a.title}</h3>
    <p class="text-sm text-slate-500 mt-2 mb-3">Optional: tell us what is behind this. It helps the co-pilot respond to how you feel.</p>
    <textarea id="why" rows="3" class="w-full p-3 border border-slate-300 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none" placeholder="e.g. The market is falling and I am worried"></textarea>
    <button class="act mt-3 px-5 py-3 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-xl" onclick="confirmAction('${k}')">Continue</button>
  </div>`;
}
function confirmAction(k){
  const why=$('why').value.trim();
  const msg=`I want to ${ACTIONS[k].phrase}.`+(why?` ${why}`:'');
  $('q').value=msg; run(msg);
}
function go(t){$('q').value=t;run(t)}
function ask(){const q=$('q').value.trim(); if(q)run(q)}

async function decide(choice){
  try{await fetch('/outcome',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({choice,light:!!window._light})});}catch(e){}
  $('decision').innerHTML = choice==='wait'
    ? '<div class="p-4 rounded-xl bg-emerald-50 border border-emerald-200 text-emerald-800 font-medium">Good call. Take the 24 hours. Nothing was changed.</div>'
    : '<div class="p-4 rounded-xl bg-slate-100 border border-slate-200 text-slate-700 font-medium">It is your decision. In a live app the action would continue here. This demo makes no real transaction.</div>';
  loadMetrics();
}

async function run(q){
  $('out').innerHTML='<div class="card p-8 flex items-center justify-center gap-3 text-slate-500"><div class="loader rounded-full border-4 border-slate-200 h-8 w-8"></div><span class="font-medium">Checking with verified facts...</span></div>';
  try{
    const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:q})});
    const d=await r.json(); render(d); loadMetrics();
  }catch(e){
    $('out').innerHTML=`<div class="p-5 bg-red-50 text-red-700 rounded-xl border border-red-200 font-medium">Network or server error: ${esc(String(e))}</div>`;
  }
}

function render(d){
  window._light=(d.status==='panic_intervention_light');
  let h='';
  if(d.alert) h+=`<div class="banner red">${esc(d.alert)}</div>`;
  if(d.pause) h+=`<div class="banner amber">${esc(d.pause)}</div>`;
  const m=d.meta||{};
  if(m.action){
    h+='<div class="flex flex-wrap gap-2 mb-4">';
    h+=`<span class="pill bg-indigo-50 text-indigo-700 border-indigo-200">${esc(ACTION_LABEL[m.action]||m.action)}</span>`;
    h+=`<span class="pill bg-rose-50 text-rose-700 border-rose-200">Feeling: ${esc(m.emotion)}</span>`;
    h+=`<span class="pill bg-amber-50 text-amber-800 border-amber-200">Panic level: ${esc(m.panic_level)}</span>`;
    h+=`<span class="pill bg-slate-100 text-slate-700 border-slate-300">Profile: ${esc(m.style)}</span>`;
    if(m.company) h+=`<span class="pill bg-emerald-50 text-emerald-700 border-emerald-200">Company: ${esc(m.company)}</span>`;
    h+='</div>';
  }
  let sc='bg-yellow-100 text-yellow-800 border-yellow-200';
  if(d.status==='answered') sc='bg-green-100 text-green-800 border-green-200';
  else if(d.status.startsWith('panic')) sc='bg-indigo-100 text-indigo-800 border-indigo-200';
  if(d.status.includes('error')) sc='bg-red-100 text-red-800 border-red-200';
  h+=`<div class="card p-6 mb-4 relative"><span class="absolute -top-3 left-4 px-3 py-1 text-xs font-bold uppercase tracking-wider rounded-full border ${sc}">${esc(d.status.replace(/_/g,' '))}</span>
      <p class="text-lg leading-relaxed mt-2 whitespace-pre-line">${chips(d.reply,d.facts)}</p>
      <div id="decision" class="mt-5">`;
  if(d.status.startsWith('panic_intervention')){
    h+=`<div class="flex flex-wrap gap-3">
      <button class="act px-5 py-3 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold rounded-xl" onclick="decide('wait')">I will wait 24 hours</button>
      <button class="act px-5 py-3 bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 font-semibold rounded-xl" onclick="decide('proceed')">Proceed anyway</button></div>`;
  }
  h+='</div></div>';
  const ctx=d.context||{}, keys=Object.keys(ctx);
  if(keys.length){
    h+=`<details class="card p-5 mb-4"><summary class="cursor-pointer font-semibold text-sm text-slate-700">Verified facts the co-pilot could use (${keys.length})</summary><ul class="mt-3 space-y-2 text-sm text-slate-600">`;
    for(const k of keys) h+=`<li><span class="chip">${esc(k)}</span> ${esc(ctx[k])}</li>`;
    h+='</ul></details>';
  }
  h+=`<details class="card p-5"><summary class="cursor-pointer font-semibold text-sm text-slate-700">How this was decided (execution trace)</summary>
    <div class="overflow-x-auto mt-3"><table class="w-full text-sm text-left"><thead class="text-slate-500 border-b border-slate-200"><tr><th class="py-2 pr-4">Step</th><th class="py-2 pr-4">Latency</th><th class="py-2">Verdict</th></tr></thead><tbody class="divide-y divide-slate-100">`;
  for(const t of d.trace){
    const st=t.status||''; 
    const col=(st==='PASS'||st==='clear')?'text-green-600':((st==='RISKY'||st==='FAIL'||st.includes('error'))?'text-red-600':'text-slate-700');
    h+=`<tr><td class="py-3 pr-4 font-medium whitespace-nowrap">${esc(t.step)}</td><td class="py-3 pr-4 font-mono text-slate-500 whitespace-nowrap">${t.ms} ms</td><td class="py-3"><span class="font-bold ${col}">${esc(st)}</span><span class="block text-xs text-slate-500 mt-1">${esc(t.detail)}</span></td></tr>`;
  }
  h+='</tbody></table></div></details>';
  $('out').innerHTML=h;
}
loadPortfolio(); loadMetrics();
</script></body></html>"""

if __name__ == "__main__":
    while True:
        q = input("\nYou: ").strip()
        if q.lower() in {"quit", "exit"}:
            break
        r = chat(q)
        if r.alert:
            print(r.alert)
        if r.pause:
            print(r.pause)
        print(f"Bot [{r.status}]: {r.text}")
        for t in r.trace:
            print(f"   - {t['step']:20} {t['model']:32} {t['ms']:>5} ms  {t['status']}  {t['detail'][:70]}")
