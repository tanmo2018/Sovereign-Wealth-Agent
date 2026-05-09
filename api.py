"""
FastAPI backend — REST API for the Sovereign Wealth Agent UI
Run: uvicorn api:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import traceback

from database import (
    init_db, get_all_holdings, add_holding, update_holding, delete_holding,
    get_profile, update_profile, get_ltcg_used_this_fy,
    get_all_loans, add_loan, update_loan, delete_loan
)
from agent import run_agent

app = FastAPI(title="Sovereign Wealth Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    print("✅ Database initialized")


# ─── Holdings CRUD ────────────────────────────────────────────────────────────

class HoldingCreate(BaseModel):
    instrument_name: str
    instrument_type: str
    units: Optional[float] = None
    buy_price: Optional[float] = None
    current_price: Optional[float] = None
    invested_amount: float
    current_value: float
    buy_date: str   # YYYY-MM-DD
    exit_load_percent: float = 0
    exit_load_days: int = 0
    lock_in_end_date: Optional[str] = None
    notes: Optional[str] = None


@app.get("/holdings")
def list_holdings():
    holdings = get_all_holdings()
    total_invested = sum(h["invested_amount"] for h in holdings)
    total_current = sum(h["current_value"] for h in holdings)
    return {
        "holdings": holdings,
        "summary": {
            "total_invested": total_invested,
            "total_current_value": total_current,
            "total_gain": total_current - total_invested,
            "total_gain_pct": ((total_current - total_invested) / total_invested * 100) if total_invested else 0,
            "count": len(holdings),
            "ltcg_used_fy": get_ltcg_used_this_fy(),
            "ltcg_remaining": max(0, 125000 - get_ltcg_used_this_fy()),
        }
    }


@app.post("/holdings")
def create_holding(data: HoldingCreate):
    id_ = add_holding(data.model_dump())
    return {"id": id_, "message": "Holding added successfully"}


@app.put("/holdings/{id}")
def edit_holding(id: int, data: HoldingCreate):
    update_holding(id, data.model_dump())
    return {"message": "Updated"}


@app.delete("/holdings/{id}")
def remove_holding(id: int):
    delete_holding(id)
    return {"message": "Deleted"}


# ─── Profile ──────────────────────────────────────────────────────────────────

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    tax_regime: Optional[str] = None
    income_slab_rate: Optional[float] = None
    is_nri: Optional[int] = None
    age: Optional[int] = None
    monthly_salary: Optional[float] = None
    family_background: Optional[str] = None
    goals_json: Optional[str] = None
    monthly_expense: Optional[float] = None
    recommendation_mode: Optional[str] = None


class LoanCreate(BaseModel):
    loan_name: Optional[str] = "Loan"
    loan_amount: float
    taken_date: str
    duration_months: int
    monthly_emi: float
    interest_rate: float
    pending_months: int
    notes: Optional[str] = None


@app.get("/profile")
def read_profile():
    profile = get_profile()
    profile["loans"] = get_all_loans()
    if not profile.get("recommendation_mode"):
        profile["recommendation_mode"] = "weighted"
    return profile


@app.put("/profile")
def save_profile(data: ProfileUpdate):
    update_profile({k: v for k, v in data.model_dump().items() if v is not None})
    return {"message": "Profile updated"}


@app.get("/loans")
def list_loans():
    return {"loans": get_all_loans()}


@app.post("/loans")
def create_loan(data: LoanCreate):
    id_ = add_loan(data.model_dump())
    return {"id": id_, "message": "Loan added successfully"}


@app.put("/loans/{id}")
def edit_loan(id: int, data: LoanCreate):
    update_loan(id, data.model_dump())
    return {"message": "Loan updated"}


@app.delete("/loans/{id}")
def remove_loan(id: int):
    delete_loan(id)
    return {"message": "Loan deleted"}


# ─── Agent Chat ───────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str


@app.post("/chat")
def chat(req: ChatRequest):
    try:
        result = run_agent(req.query)
        return result
    except ValueError as e:
        if "GROQ_API_KEY" in str(e):
            raise HTTPException(
                status_code=400,
                detail="GROQ_API_KEY not set. Please add it to your .env file."
            )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ─── Tax Preview ──────────────────────────────────────────────────────────────

class WithdrawalPreviewRequest(BaseModel):
    amount: float
    days: int = 7


@app.post("/withdrawal-preview")
def withdrawal_preview(req: WithdrawalPreviewRequest):
    """Quick withdrawal plan without calling Groq — pure tax engine."""
    from tax_engine import classify_holdings, build_withdrawal_plan, InstrumentTaxProfile

    holdings = get_all_holdings()
    profile = get_profile()
    ltcg_used = get_ltcg_used_this_fy()

    profiles = classify_holdings(
        holdings,
        ltcg_used_this_fy=ltcg_used,
        income_slab_rate=profile.get("income_slab_rate", 30.0)
    )
    plan = build_withdrawal_plan(profiles, req.amount, req.days)
    return plan


@app.get("/health")
def health():
    return {"status": "ok", "model": "llama-3.3-70b-versatile", "provider": "groq"}

class SellRequest(BaseModel):
    units: float

@app.post("/holdings/{id}/sell")
def sell_holding_action(id: int, req: SellRequest):
    """Processes a sell transaction and updates LTCG/STCG trackers."""
    from database import process_partial_sell
    success = process_partial_sell(id, req.units)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid units or holding not found")
    return {"message": f"Successfully processed sell for {req.units} units"}