"""
Indian Tax Engine — 2024-25 rules
Handles STCG, LTCG, income-slab taxation for all instrument types.
"""

from datetime import datetime, date
from typing import Dict, List, Tuple
from dataclasses import dataclass


LTCG_EXEMPTION_LIMIT = 125000  # ₹1.25 lakh per FY (post Budget 2024)
LTCG_TAX_RATE = 0.125          # 12.5% (post Budget 2024)
STCG_TAX_RATE = 0.20           # 20% flat on equity STCG (post Budget 2024)

# Days to qualify as Long Term
LONG_TERM_DAYS = {
    "equity_mf": 365,
    "stocks": 365,
    "gold_etf": 365,      # changed to 1yr post Budget 2024
    "debt_mf": 1095,      # 3 years (24 months rule removed)
    "liquid_mf": 1095,
    "fd": None,           # always income-slab (no capital gains)
    "savings": None,
    "ppf": None,          # tax-free
    "nps": None,          # partially taxable on exit
}


@dataclass
class InstrumentTaxProfile:
    holding_id: int
    name: str
    instrument_type: str
    invested_amount: float
    current_value: float
    unrealized_gain: float
    buy_date: str
    holding_days: int
    is_long_term: bool
    is_locked: bool
    has_exit_load: bool
    exit_load_percent: float

    # Tax cost to withdraw full amount
    tax_amount: float
    tax_rate_percent: float
    net_in_hand: float          # current_value - tax
    tax_efficiency_score: float  # lower = more tax-efficient (0 = tax-free)

    liquidity_days: int         # estimated days to settle
    notes: str


def classify_holdings(holdings: List[Dict], ltcg_used_this_fy: float, income_slab_rate: float) -> List[InstrumentTaxProfile]:
    """Classify each holding by tax type and compute tax cost."""
    profiles = []
    today = date.today()

    for h in holdings:
        buy_date = datetime.strptime(h["buy_date"], "%Y-%m-%d").date()
        holding_days = (today - buy_date).days
        itype = h["instrument_type"]

        current_value = h["current_value"]
        invested = h["invested_amount"]
        gain = current_value - invested
        is_locked = False
        has_exit_load = False

        # Check lock-in
        if h.get("lock_in_end_date"):
            lock_end = datetime.strptime(h["lock_in_end_date"], "%Y-%m-%d").date()
            if lock_end > today:
                is_locked = True

        # Check exit load
        if h.get("exit_load_percent", 0) > 0 and holding_days < h.get("exit_load_days", 0):
            has_exit_load = True

        # Determine long-term
        lt_days = LONG_TERM_DAYS.get(itype)
        is_long_term = lt_days is not None and holding_days >= lt_days

        # Compute tax
        tax_amount, tax_rate_pct, tax_note = _compute_tax(
            itype, gain, is_long_term, ltcg_used_this_fy, income_slab_rate, current_value
        )

        # Exit load cost
        exit_load_cost = 0
        if has_exit_load:
            exit_load_cost = current_value * (h.get("exit_load_percent", 0) / 100)
            tax_note += f" + exit load ₹{exit_load_cost:,.0f}"

        net_in_hand = current_value - tax_amount - exit_load_cost

        # Liquidity estimate
        liquidity = _liquidity_days(itype, is_locked)

        # Tax efficiency score: 0 = tax-free, higher = more tax drag
        score = (tax_amount + exit_load_cost) / current_value if current_value > 0 else 1.0
        if is_locked:
            score += 10  # heavily penalize locked instruments

        profiles.append(InstrumentTaxProfile(
            holding_id=h["id"],
            name=h["instrument_name"],
            instrument_type=itype,
            invested_amount=invested,
            current_value=current_value,
            unrealized_gain=gain,
            buy_date=h["buy_date"],
            holding_days=holding_days,
            is_long_term=is_long_term,
            is_locked=is_locked,
            has_exit_load=has_exit_load,
            exit_load_percent=h.get("exit_load_percent", 0),
            tax_amount=tax_amount,
            tax_rate_percent=tax_rate_pct,
            net_in_hand=net_in_hand,
            tax_efficiency_score=score,
            liquidity_days=liquidity,
            notes=tax_note,
        ))

    # Sort by tax efficiency (lowest tax drag first)
    profiles.sort(key=lambda x: (x.is_locked, x.tax_efficiency_score))
    return profiles


def _compute_tax(itype, gain, is_long_term, ltcg_used_fy, income_slab_rate, current_value) -> Tuple[float, float, str]:
    if gain <= 0:
        return 0.0, 0.0, "Loss position — no tax (harvest this!)"

    if itype == "ppf":
        return 0.0, 0.0, "Tax-free instrument"

    if itype == "savings":
        return 0.0, 0.0, "No capital gains tax on savings"

    if itype in ("fd",):
        tax = gain * (income_slab_rate / 100)
        return tax, income_slab_rate, f"Interest taxed at income slab ({income_slab_rate}%)"

    if itype == "nps":
        # 60% tax-free on withdrawal, 40% must be annuitized
        taxable = current_value * 0.40 * (income_slab_rate / 100)
        return taxable, income_slab_rate * 0.4, "NPS: 60% tax-free, 40% annuity (slab-taxed)"

    if itype in ("equity_mf", "stocks", "gold_etf", "debt_mf", "liquid_mf"):
        if not is_long_term:
            tax = gain * STCG_TAX_RATE
            return tax, STCG_TAX_RATE * 100, f"STCG @ 20%"

        # LTCG — check exemption remaining
        remaining_exemption = max(0, LTCG_EXEMPTION_LIMIT - ltcg_used_fy)
        taxable_gain = max(0, gain - remaining_exemption)
        tax = taxable_gain * LTCG_TAX_RATE
        exemption_used = min(gain, remaining_exemption)
        return (
            tax,
            LTCG_TAX_RATE * 100 if taxable_gain > 0 else 0,
            f"LTCG: ₹{exemption_used:,.0f} exempt + ₹{taxable_gain:,.0f} @ 12.5%"
        )

    return 0.0, 0.0, "Unknown instrument type"


def _liquidity_days(itype: str, is_locked: bool) -> int:
    if is_locked:
        return 9999
    return {
        "savings": 0,
        "liquid_mf": 1,
        "fd": 2,
        "stocks": 2,
        "gold_etf": 2,
        "equity_mf": 3,
        "debt_mf": 3,
        "ppf": 30,
        "nps": 60,
    }.get(itype, 3)


def build_withdrawal_plan(profiles: List[InstrumentTaxProfile], target_amount: float, max_days: int = 7) -> Dict:
    """
    Greedy algorithm: fill withdrawal target starting from most tax-efficient,
    respecting liquidity constraints.
    """
    plan = []
    remaining = target_amount
    total_tax = 0
    worst_case_tax = 0  # if user had broken FD for everything
    loss_harvest_opportunities = []

    eligible = [p for p in profiles if not p.is_locked and p.liquidity_days <= max_days]
    ineligible = [p for p in profiles if p.is_locked or p.liquidity_days > max_days]

    # Identify loss positions for harvesting
    for p in profiles:
        if p.unrealized_gain < 0 and not p.is_locked:
            loss_harvest_opportunities.append(p)

    for profile in eligible:
        if remaining <= 0:
            break

        withdraw_amount = min(profile.current_value, remaining)
        # Proportional tax
        proportion = withdraw_amount / profile.current_value if profile.current_value > 0 else 0
        tax_for_this = profile.tax_amount * proportion

        plan.append({
            "holding_id": profile.holding_id,
            "instrument": profile.name,
            "type": profile.instrument_type,
            "withdraw_amount": withdraw_amount,
            "tax_amount": tax_for_this,
            "net_received": withdraw_amount - tax_for_this,
            "tax_rate_pct": profile.tax_rate_percent,
            "settlement_days": profile.liquidity_days,
            "notes": profile.notes,
            "is_long_term": profile.is_long_term,
        })

        remaining -= withdraw_amount
        total_tax += tax_for_this
        # Worst case: if all was FD
        worst_case_tax += withdraw_amount * 0.30  # assuming 30% slab

    # Tax savings vs worst case
    tax_saved = worst_case_tax - total_tax
    shortfall = max(0, remaining)

    return {
        "target_amount": target_amount,
        "plan": plan,
        "total_tax": total_tax,
        "total_gross_withdrawal": sum(p["withdraw_amount"] for p in plan),
        "total_net_received": sum(p["net_received"] for p in plan),
        "tax_saved_vs_worst_case": tax_saved,
        "shortfall": shortfall,
        "loss_harvest_opportunities": [
            {"instrument": p.name, "loss": abs(p.unrealized_gain)} 
            for p in loss_harvest_opportunities
        ],
        "ineligible_holdings": [
            {"instrument": p.name, "reason": "Locked-in" if p.is_locked else f"Needs {p.liquidity_days} days"}
            for p in ineligible
        ],
        "max_settlement_days": max((p["settlement_days"] for p in plan), default=0),
    }
