"""
Sovereign Wealth Agent — LangGraph orchestration with Groq
Nodes: InputProcessor → DataFetcher → Router → [InvestmentAdvisor | WithdrawalOptimizer] 
       → MarketSentimentCritic → OutputStructurer → HITL
"""

import json
import os
import re
import time
from functools import lru_cache
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from typing import TypedDict, Literal, List, Dict, Any, Optional
from langgraph.graph import StateGraph, START, END

from groq import Groq
from database import get_all_holdings, get_profile, get_ltcg_used_this_fy, get_all_loans
from tax_engine import classify_holdings, build_withdrawal_plan

QUOTE_CACHE_TTL_SECONDS = 20
QUOTE_STALE_MAX_SECONDS = 15 * 60
_QUOTE_CACHE: Dict[str, Dict[str, Any]] = {}


# ─── State Schema ──────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # Input
    user_query: str
    intent: Optional[Literal["invest", "withdraw", "analyze", "market_data", "unknown"]]
    target_amount: Optional[float]
    time_horizon_days: Optional[int]

    # Data layer
    holdings: List[Dict]
    profile: Dict
    loans: List[Dict]
    loan_analysis: Dict
    recommendation_mode: Literal["weighted", "strict"]
    ltcg_used_fy: float
    realized_stcg: float
    classified_holdings: List[Dict]

    # Agent outputs
    withdrawal_plan: Optional[Dict]
    investment_advice: Optional[str]
    market_sentiment: Optional[str]
    market_quote: Optional[Dict]
    final_output: Optional[Dict]

    # Control
    needs_clarification: bool
    error: Optional[str]
    messages: List[Dict]   # Groq conversation history


# ─── Groq Client ──────────────────────────────────────────────────────────────

def get_groq_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable not set!")
    return Groq(api_key=api_key)


GROQ_MODEL = "llama-3.3-70b-versatile"   # Best free Groq model as of 2025


def call_groq(messages: List[Dict], system: str, max_tokens: int = 1024) -> str:
    client = get_groq_client()
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "system", "content": system}] + messages,
        max_tokens=max_tokens,
        temperature=0.3,
    )
    return resp.choices[0].message.content


# ─── Node 1: Input Processor ──────────────────────────────────────────────────

def input_processor(state: AgentState) -> AgentState:
    """Classify intent, extract amount and timeframe from user query."""
    
    system = """You are a financial intent classifier for Indian investors.
Extract from the user query:
- intent: "invest" (they have money to put in) | "withdraw" (they need money out) | "analyze" (portfolio review) | "market_data" (price/quote/index/rate query) | "unknown"
- target_amount: numeric INR amount if mentioned (null if not)
- time_horizon_days: days available (7 if "week", 30 if "month", null if not mentioned)

Respond ONLY with valid JSON, no other text:
{"intent": "...", "target_amount": null_or_number, "time_horizon_days": null_or_number, "needs_clarification": true_or_false}
"""
    messages = [{"role": "user", "content": state["user_query"]}]
    
    try:
        raw = call_groq(messages, system, max_tokens=200)
        # Strip markdown if present
        raw = raw.strip().replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        return {
            **state,
            "intent": parsed.get("intent", "unknown"),
            "target_amount": parsed.get("target_amount"),
            "time_horizon_days": parsed.get("time_horizon_days", 7),
            "needs_clarification": parsed.get("needs_clarification", False),
            "messages": messages,
        }
    except Exception as e:
        return {**state, "intent": "unknown", "error": f"Input parsing failed: {e}"}


# ─── Node 2: Data Fetcher ─────────────────────────────────────────────────────

def data_fetcher(state: AgentState) -> AgentState:
    """Load all portfolio data from local SQLite database."""
    holdings = get_all_holdings()
    profile = get_profile()
    loans = get_all_loans()
    
    realized_ltcg = get_ltcg_used_this_fy()
    realized_stcg = 0.0

    classified = classify_holdings(
        holdings,
        ltcg_used_this_fy=realized_ltcg,
        income_slab_rate=profile.get("income_slab_rate", 30.0)
    )

    return {
        **state,
        "holdings": holdings,
        "profile": profile,
        "loans": loans,
        "loan_analysis": _build_loan_analysis(profile, loans),
        "recommendation_mode": (profile.get("recommendation_mode") or "weighted").lower(),
        "classified_holdings": [vars(c) for c in classified],
        "ltcg_used_fy": realized_ltcg, # This now reflects REALIZED sales
        "realized_stcg": realized_stcg, 
    }


def _build_loan_analysis(profile: Dict[str, Any], loans: List[Dict[str, Any]]) -> Dict[str, Any]:
    age_raw = profile.get("age")
    try:
        age = int(age_raw) if age_raw is not None else 0
    except (TypeError, ValueError):
        age = 0

    monthly_expense = float(profile.get("monthly_expense") or 0.0)
    monthly_salary = float(profile.get("monthly_salary") or 0.0)
    ef_months = 3 if age and age <= 28 else 6
    emergency_fund_target = monthly_expense * ef_months

    total_emi = sum(float(l.get("monthly_emi") or 0.0) for l in loans)
    total_pending_months = sum(int(l.get("pending_months") or 0) for l in loans)
    total_outstanding = sum(
        float(l.get("monthly_emi") or 0.0) * int(l.get("pending_months") or 0)
        for l in loans
    )
    debt_to_income = (total_emi / monthly_salary) if monthly_salary > 0 else None
    high_interest_loans = [l for l in loans if float(l.get("interest_rate") or 0.0) > 9.0]

    return {
        "loan_count": len(loans),
        "total_emi": total_emi,
        "total_pending_months": total_pending_months,
        "estimated_total_outstanding": total_outstanding,
        "debt_to_income": debt_to_income,
        "has_high_interest_loan": len(high_interest_loans) > 0,
        "high_interest_threshold": 9.0,
        "emergency_fund_months": ef_months,
        "emergency_fund_target": emergency_fund_target,
        "high_interest_loans": high_interest_loans,
    }

  
# ─── Market Data Helpers ──────────────────────────────────────────────────────

def _resolve_market_symbol(query: str) -> Optional[Dict[str, str]]:
    q = query.lower()

    # Common India market requests + a few global references.
    mappings = [
        ("^NSEI", "NIFTY 50", "NSE", ["nifty", "nifty 50", "nifty50"]),
        ("^NSEBANK", "NIFTY BANK", "NSE", ["bank nifty", "nifty bank", "banknifty"]),
        ("^BSESN", "SENSEX", "BSE", ["sensex", "bse sensex"]),
        ("INR=X", "USD/INR", "FX", ["usdinr", "usd inr", "dollar rupee", "usd to inr"]),
        ("GC=F", "Gold Futures", "COMEX", ["gold rate", "gold price", "xauusd", "gold"]),
        ("SI=F", "Silver Futures", "COMEX", ["silver rate", "silver price", "silver"]),
    ]

    for yahoo_symbol, label, exchange, tokens in mappings:
        for token in tokens:
            if token in q:
                return {
                    "symbol": yahoo_symbol,
                    "label": label,
                    "exchange": exchange,
                }

    # Basic pattern support for direct symbol-like input, e.g. RELIANCE, TCS.NS
    m = re.search(r"\b([a-z]{2,10})(?:\.ns|\.bo)?\b", q)
    if m:
        raw = m.group(1).upper()
        return {"symbol": f"{raw}.NS", "label": raw, "exchange": "NSE"}

    return None


def _fetch_market_quote(symbol: str) -> Dict[str, Any]:
    now = time.time()
    cached = _QUOTE_CACHE.get(symbol)
    if cached and (now - cached["ts"] <= QUOTE_CACHE_TTL_SECONDS):
        return {**cached["data"], "is_stale": False}

    encoded = quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1m&range=1d"
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    payload = None
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            with urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except HTTPError as e:
            last_error = e
            if e.code != 429:
                raise
            time.sleep(0.8 * (attempt + 1))
        except URLError as e:
            last_error = e
            time.sleep(0.5 * (attempt + 1))

    if payload is None:
        if cached and (now - cached["ts"] <= QUOTE_STALE_MAX_SECONDS):
            return {**cached["data"], "is_stale": True}
        if isinstance(last_error, HTTPError) and last_error.code == 429:
            raise ValueError("Quote API is rate-limited right now. Please retry in a few seconds.")
        raise ValueError(f"Quote fetch failed: {last_error}")

    result = payload.get("chart", {}).get("result", [])
    if not result:
        raise ValueError("Quote feed unavailable for the requested symbol.")

    meta = result[0].get("meta", {})
    price = meta.get("regularMarketPrice")
    prev_close = meta.get("previousClose")
    ts = meta.get("regularMarketTime")

    if price is None:
        raise ValueError("Live price not available at the moment.")

    change = (price - prev_close) if prev_close not in (None, 0) else None
    change_pct = ((change / prev_close) * 100) if change is not None and prev_close else None
    updated_at = (
        datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        if ts else datetime.now(timezone.utc).isoformat()
    )

    quote_data = {
        "price": float(price),
        "previous_close": float(prev_close) if prev_close is not None else None,
        "change": float(change) if change is not None else None,
        "change_pct": float(change_pct) if change_pct is not None else None,
        "currency": meta.get("currency", "INR"),
        "updated_at": updated_at,
        "source": "Yahoo Finance",
        "is_stale": False,
    }
    _QUOTE_CACHE[symbol] = {"ts": now, "data": quote_data}
    return quote_data


# ─── Node 3c: Market Data Fetcher ─────────────────────────────────────────────

def market_data_fetcher(state: AgentState) -> AgentState:
    """Fetch quote-style market data for generic finance queries."""
    try:
        resolved = _resolve_market_symbol(state["user_query"])
        if not resolved:
            return {
                **state,
                "market_quote": {
                    "error": "I could not detect the market instrument. Try queries like 'NIFTY rate', 'USDINR rate', or 'Gold price'."
                },
            }

        quote_data = _fetch_market_quote(resolved["symbol"])
        market_quote = {
            **resolved,
            **quote_data,
        }
        return {**state, "market_quote": market_quote}
    except Exception as e:
        return {**state, "market_quote": {"error": f"Market data fetch failed: {str(e)}"}}


# ─── Node 3a: Withdrawal Optimizer ───────────────────────────────────────────

def withdrawal_optimizer(state: AgentState) -> AgentState:
    """Compute optimal withdrawal plan using tax engine."""
    from tax_engine import InstrumentTaxProfile
    
    classified_raw = state["classified_holdings"]
    # Reconstruct dataclass objects from dicts
    profiles = [InstrumentTaxProfile(**c) for c in classified_raw]
    
    target = state.get("target_amount") or 500000
    days = state.get("time_horizon_days") or 7

    plan = build_withdrawal_plan(profiles, target_amount=target, max_days=days)
    return {**state, "withdrawal_plan": plan}


# ─── Node 3b: Investment Advisor ──────────────────────────────────────────────

def investment_advisor(state: AgentState) -> AgentState:
    """Generate investment advice using Groq based on portfolio + profile."""
    profile = state["profile"]
    
    # Normalize age to a usable int before risk-allocation math.
    raw_age = profile.get("age")
    try:
        age = int(raw_age) if raw_age is not None else 24
    except (TypeError, ValueError):
        age = 24
    equity_ratio = max(20, min(90, 100 - age))

    loan_analysis = state.get("loan_analysis", {})
    rec_mode = state.get("recommendation_mode", "weighted")

    system = f"""You are a SEBI-registered expert financial advisor specializing in Indian personal finance. 
    You are advising a {age}-year-old professional.
    
    CORE STRATEGY:
    1. Equity Allocation: Target {equity_ratio}% due to young age.
    2. Emergency Fund: Keep at least {loan_analysis.get('emergency_fund_months', 6)} months of expenses before aggressive investing.
    3. Goal Alignment: If goals in '{profile.get('goals_json')}' are <3 years away, suggest Liquid/Arbitrage funds. 
    If >5 years, suggest Index/Flexi-cap funds.
    4. Loan-aware policy:
       - Multiple loans may exist.
       - If any loan interest rate is above 9%, prioritize repayment recommendation before fresh investment.
       - Never suggest withdrawals/investments that risk missing EMI obligations.
    5. Recommendation mode is '{rec_mode}'.
       - strict: clear rule-based decision and direct action.
       - weighted: score trade-offs between debt repayment, emergency fund, and investing, then suggest allocation.
    """

    portfolio_summary = []
    total_value = 0
    for h in state["holdings"]:
        gain = h["current_value"] - h["invested_amount"]
        gain_pct = (gain / h["invested_amount"] * 100) if h["invested_amount"] else 0
        portfolio_summary.append(
            f"- {h['instrument_name']} ({h['instrument_type']}): "
            f"Invested ₹{h['invested_amount']:,.0f} → Current ₹{h['current_value']:,.0f} "
            f"(gain: {gain_pct:+.1f}%)"
        )
        total_value += h["current_value"]

    system += """Give specific, actionable advice. Use Indian financial instruments (SIP, ELSS, NPS, PPF, Nifty etc).
Consider tax efficiency, Indian tax laws (LTCG, STCG, Section 80C), and risk-adjusted returns.
Be concise but thorough. Format with clear sections."""

    portfolio_str = "\n".join(portfolio_summary)

    ltcg_limit = 125000 # Post-Budget 2024
    remaining_ltcg = max(0, ltcg_limit - state["ltcg_used_fy"])
    user_msg = f"""
    Future Goals & Timelines: {profile.get('goals_json', 'Not specified')}
    
    User Query: {state['user_query']}

Portfolio (Total: ₹{total_value:,.0f}):
{portfolio_str}

Profile:
- Tax Regime: {profile.get('tax_regime', 'new')}
- Income Slab: {profile.get('income_slab_rate', 30)}%
- TAX STANDING (Current FY):
    - Realized LTCG so far: ₹{state['ltcg_used_fy']:,.0f}
    - Realized STCG so far: ₹{state.get('realized_stcg', 0):,.0f}
    - REMAINING LTCG Exemption: ₹{remaining_ltcg:,.0f}    
    Note: If the user wants to 'sell' or 'withdraw', check if they have 
    unrealized gains that can fit into the remaining ₹{remaining_ltcg:,.0f} exemption.

Amount available: ₹{state.get('target_amount', 'Not specified')}
Recommendation Mode: {rec_mode}
Loan Analysis Snapshot:
- Loan count: {loan_analysis.get('loan_count', 0)}
- Total monthly EMI: ₹{loan_analysis.get('total_emi', 0):,.0f}
- Estimated outstanding (EMI x pending months): ₹{loan_analysis.get('estimated_total_outstanding', 0):,.0f}
- Any loan >9%: {loan_analysis.get('has_high_interest_loan', False)}
- Emergency fund target: ₹{loan_analysis.get('emergency_fund_target', 0):,.0f}
Active Loans:
{json.dumps(state.get('loans', []), default=str)}

Please provide: 1) Asset allocation advice 2) Specific fund/instrument recommendations 3) Tax optimization tips"""

    messages = state.get("messages", []) + [{"role": "user", "content": user_msg}]
    
    advice = call_groq(messages, system, max_tokens=1200)
    return {**state, "investment_advice": advice, "messages": messages}


# ─── Node 4: Market Sentiment Critic ─────────────────────────────────────────

def market_sentiment_critic(state: AgentState) -> AgentState:
    """Add market sentiment commentary on the plan."""
    
    intent = state.get("intent")
    if intent == "market_data":
        return {**state, "market_sentiment": ""}

    plan_summary = ""
    
    if intent == "withdraw" and state.get("withdrawal_plan"):
        plan = state["withdrawal_plan"]
        instruments = [p["instrument"] for p in plan.get("plan", [])]
        plan_summary = f"Withdrawal plan involves: {', '.join(instruments)}"
    elif state.get("investment_advice"):
        plan_summary = "Investment advice was generated above."

    system = """You are a market sentiment analyst for Indian markets.
Give a brief (3-4 sentences) current market context that's relevant to the decision.
Consider: Nifty valuation levels, interest rate environment (RBI policy), FII flows, INR, gold trends.
Be objective — mention both risks and opportunities. Keep it concise."""

    user_msg = f"""Action being considered: {state['user_query']}
{plan_summary}
What's the relevant market context an Indian investor should know right now?"""

    messages = [{"role": "user", "content": user_msg}]
    sentiment = call_groq(messages, system, max_tokens=300)
    return {**state, "market_sentiment": sentiment}


# ─── Node 5: Output Structurer ────────────────────────────────────────────────

def output_structurer(state: AgentState) -> AgentState:
    """Assemble final structured output for the UI."""
    intent=state["intent"]
    output = {
        "response_type": "advice",
        "intent": state["intent"],
        "query": state["user_query"],
        "profile": state["profile"],
        "portfolio_summary": {
            "total_invested": sum(h["invested_amount"] for h in state["holdings"]),
            "total_current_value": sum(h["current_value"] for h in state["holdings"]),
            "holding_count": len(state["holdings"]),
            "realized_ltcg_fy": state["ltcg_used_fy"], # Linked to tracker
            "ltcg_used_fy": state["ltcg_used_fy"],  # Backward-compatible key used by UI
            "realized_stcg_fy": state.get("realized_stcg", 0),
            "ltcg_remaining": max(0, 125000 - state["ltcg_used_fy"]),
        },
        "loan_summary": state.get("loan_analysis", {}),
        "recommendation_mode": state.get("recommendation_mode", "weighted"),
        "loans": state.get("loans", []),
        "market_sentiment": state.get("market_sentiment", ""),
    }

    if intent == "withdraw":
        output["withdrawal_plan"] = state.get("withdrawal_plan")
        output["loan_advisory"] = _build_loan_advisory(state, "withdraw")
        output["type"] = "withdrawal"
        output["response_type"] = "withdrawal_plan"
    elif intent == "market_data":
        output["type"] = "market"
        output["response_type"] = "market_quote"
        output["market_quote"] = state.get("market_quote")
    else:
        output["investment_advice"] = state.get("investment_advice", "")
        output["loan_advisory"] = _build_loan_advisory(state, "invest")
        output["type"] = "investment"
        output["response_type"] = "advice"

    return {**state, "final_output": output}


def _build_loan_advisory(state: AgentState, intent: str) -> Dict[str, Any]:
    profile = state.get("profile", {})
    loans = state.get("loans", [])
    analysis = state.get("loan_analysis", {})
    recommendation_mode = state.get("recommendation_mode", "weighted")

    salary = float(profile.get("monthly_salary") or 0.0)
    expense = float(profile.get("monthly_expense") or 0.0)
    monthly_surplus = max(0.0, salary - expense - float(analysis.get("total_emi") or 0.0))
    high_interest = bool(analysis.get("has_high_interest_loan"))
    emergency_fund_target = float(analysis.get("emergency_fund_target") or 0.0)

    if not loans:
        return {
            "has_loan": False,
            "priority": "none",
            "message": "No active loans found. Standard investment/withdrawal guidance applies.",
        }

    if recommendation_mode == "strict":
        if high_interest:
            message = "Strict mode: prioritize loan prepayment for loans above 9% before new investments."
            priority = "loan_repayment"
        else:
            message = "Strict mode: maintain emergency fund target first, then continue balanced investing."
            priority = "emergency_fund_then_invest"
    else:
        if high_interest and monthly_surplus > 0:
            message = "Weighted mode: allocate higher share of surplus toward loan prepayment while continuing limited investing."
            priority = "debt_and_invest_mix"
        elif high_interest:
            message = "Weighted mode: focus on EMI continuity and avoid additional withdrawals that stress cash flow."
            priority = "emi_protection"
        else:
            message = "Weighted mode: continue investing, with periodic prepayment if debt-to-income rises."
            priority = "balanced"

    if intent == "withdraw":
        message += " Ensure at least upcoming EMI obligations remain funded after withdrawal."

    return {
        "has_loan": True,
        "loan_count": len(loans),
        "recommendation_mode": recommendation_mode,
        "priority": priority,
        "high_interest_loan": high_interest,
        "total_emi": analysis.get("total_emi", 0.0),
        "debt_to_income": analysis.get("debt_to_income"),
        "monthly_surplus_after_emi": monthly_surplus,
        "emergency_fund_target": emergency_fund_target,
        "message": message,
    }


# ─── Router ───────────────────────────────────────────────────────────────────

def route_intent(state: AgentState) -> str:
    intent = state.get("intent", "unknown")
    if intent == "withdraw":
        return "withdrawal"
    elif intent == "market_data":
        return "market_data"
    elif intent in ("invest", "analyze"):
        return "investment"
    else:
        return "investment"   # default to analysis


def _route_for_graph(state: AgentState) -> str:
    """Map intent to LangGraph branch names."""
    route = route_intent(state)
    if route == "withdrawal":
        return "withdrawal_optimizer"
    if route == "market_data":
        return "market_data_fetcher"
    return "investment_advisor"


@lru_cache(maxsize=1)
def get_agent_graph():
    """Build and cache the LangGraph workflow for the agent."""
    graph = StateGraph(AgentState)

    graph.add_node("input_processor", input_processor)
    graph.add_node("data_fetcher", data_fetcher)
    graph.add_node("withdrawal_optimizer", withdrawal_optimizer)
    graph.add_node("investment_advisor", investment_advisor)
    graph.add_node("market_data_fetcher", market_data_fetcher)
    graph.add_node("market_sentiment_critic", market_sentiment_critic)
    graph.add_node("output_structurer", output_structurer)

    graph.add_edge(START, "input_processor")
    graph.add_edge("input_processor", "data_fetcher")
    graph.add_conditional_edges(
        "data_fetcher",
        _route_for_graph,
        {
            "withdrawal_optimizer": "withdrawal_optimizer",
            "investment_advisor": "investment_advisor",
            "market_data_fetcher": "market_data_fetcher",
        },
    )
    graph.add_edge("withdrawal_optimizer", "market_sentiment_critic")
    graph.add_edge("investment_advisor", "market_sentiment_critic")
    graph.add_edge("market_data_fetcher", "output_structurer")
    graph.add_edge("market_sentiment_critic", "output_structurer")
    graph.add_edge("output_structurer", END)

    return graph.compile()


# ─── Main Agent Runner ────────────────────────────────────────────────────────

def run_agent(user_query: str) -> Dict[str, Any]:
    """Run the LangGraph workflow and return structured output."""
    
    # Initialize graph state
    state: AgentState = {
        "user_query": user_query,
        "intent": None,
        "target_amount": None,
        "time_horizon_days": None,
        "holdings": [],
        "profile": {},
        "loans": [],
        "loan_analysis": {},
        "recommendation_mode": "weighted",
        "ltcg_used_fy": 0.0,
        "realized_stcg": 0.0,
        "classified_holdings": [],
        "withdrawal_plan": None,
        "investment_advice": None,
        "market_sentiment": None,
        "market_quote": None,
        "final_output": None,
        "needs_clarification": False,
        "error": None,
        "messages": [],
    }

    graph = get_agent_graph()
    final_state = graph.invoke(state)
    return final_state["final_output"]


if __name__ == "__main__":
    # Quick CLI test
    from database import init_db
    init_db()
    
    result = run_agent("I need 5 lakh rupees in a week. How should I withdraw?")
    print(json.dumps(result, indent=2, default=str))
