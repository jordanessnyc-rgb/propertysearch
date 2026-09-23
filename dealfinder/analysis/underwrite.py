"""Underwrite a listing: rents -> NOI -> cap rate, cash flow, cash-on-cash, max offer, score."""
from __future__ import annotations

from typing import Optional

from ..config import Settings, Underwriting
from ..db import Database
from ..models import Analysis, ConditionReport, Listing
from .condition import read_listing, rehab_estimate
from .rents import market_rent, section8_rent


def monthly_payment(principal: float, annual_rate: float, years: int) -> float:
    if principal <= 0:
        return 0.0
    n = years * 12
    r = annual_rate / 12
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** -n)


def _count_price_drops(history: list[dict]) -> int:
    return sum(1 for a, b in zip(history, history[1:]) if b["price"] < a["price"])


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_deal(cap_rate: float, coc: float, dscr: Optional[float], discount: float,
               one_pct: float, uw: Underwriting, penalties: float) -> float:
    s = 0.0
    s += 35 * _clamp(cap_rate / uw.target_cap_rate)           # full marks at target cap
    s += 25 * _clamp(coc / uw.target_cash_on_cash)
    s += 15 * _clamp((discount + 0.10) / 0.40)                # -10% .. +30% below max offer
    s += 10 * (1.0 if dscr is None else _clamp((dscr - 1.0) / (uw.min_dscr - 1.0 + 0.25)))
    s += 15 * _clamp(one_pct / 0.01)                           # the 1% rule
    return round(max(0.0, s - penalties), 1)


def grade(score: float) -> str:
    for cutoff, g in ((80, "A"), (65, "B"), (50, "C"), (35, "D")):
        if score >= cutoff:
            return g
    return "F"


def analyze_listing(db: Database, listing: Listing, settings: Settings,
                    condition: Optional[ConditionReport] = None) -> Optional[Analysis]:
    uw = settings.underwriting
    notes: list[str] = []
    if listing.price <= 0:
        return None

    if condition is None:
        condition = read_listing(listing, _count_price_drops(db.price_history(listing.key)))
        if settings.use_llm_reader:
            from .llm_reader import read_listing_llm
            condition = read_listing_llm(listing, settings.llm_model, condition)

    # ---- rents, per unit ------------------------------------------------
    market_total, s8_total, used_total = 0.0, 0.0, 0.0
    s8_available = True
    thin = False
    for beds in listing.beds_per_unit():
        m = market_rent(db, listing.zip, beds)
        s8 = section8_rent(db, listing.zip, beds, uw.section8_payment_standard_pct)
        if m.rent is None and s8 is None:
            notes.append(f"no rent data for {beds}BR in {listing.zip}")
            return None
        if m.comps < 3:
            thin = True
        mr = m.rent if m.rent is not None else s8
        market_total += mr
        if s8 is None:
            s8_available = False
        else:
            s8_total += s8
        if uw.rent_basis == "section8" and s8 is not None:
            used_total += s8
        elif uw.rent_basis == "best" and s8 is not None:
            used_total += max(mr, s8)
        else:
            used_total += mr
    basis = "market"
    if s8_available and used_total > market_total + 1:
        basis = "section8" if abs(used_total - s8_total) < 1 else "mixed"
        notes.append("Section 8 rent beats market rent here: needs a voucher tenant and a passed HQS inspection")
    if thin:
        notes.append("few rent comps in this zip: rent estimate is low confidence")

    # ---- costs ----------------------------------------------------------
    units = max(listing.units or 1, 1)
    rehab = rehab_estimate(condition, listing.sqft, units, uw.rehab_contingency_pct)
    closing = listing.price * uw.closing_cost_pct
    all_in = listing.price + closing + rehab

    gross_annual = used_total * 12
    egi = gross_annual * (1 - uw.vacancy_pct)
    taxes = listing.annual_taxes or (listing.price + rehab) * uw.property_tax_rate
    if not listing.annual_taxes:
        notes.append(f"taxes estimated at {uw.property_tax_rate:.1%} of price + rehab (no tax data on listing)")
    opex = (taxes
            + uw.insurance_annual_per_unit * units
            + egi * (uw.management_pct + uw.maintenance_pct + uw.capex_pct)
            + (listing.hoa_monthly or 0) * 12
            + uw.utilities_monthly_per_unit * units * 12)
    noi = egi - opex
    cap_rate = noi / all_in

    # ---- financing ------------------------------------------------------
    financing = uw.financing
    if financing == "auto":
        financing = "cash" if condition.cash_only else "loan"
    if financing == "loan":
        loan = listing.price * (1 - uw.down_payment_pct)
        debt_service = monthly_payment(loan, uw.interest_rate, uw.loan_term_years) * 12
        cash_in = all_in - loan
        dscr = noi / debt_service if debt_service else None
    else:
        debt_service, cash_in, dscr = 0.0, all_in, None
    cash_flow_annual = noi - debt_service
    coc = cash_flow_annual / cash_in if cash_in > 0 else 0.0

    # Max offer: the purchase price at which this property hits the target cap rate
    # after closing costs and rehab.
    max_offer = max(0.0, (noi / uw.target_cap_rate - rehab) / (1 + uw.closing_cost_pct))
    discount = (max_offer - listing.price) / listing.price

    penalties = 0.0
    if thin:
        penalties += 8
    if "no interior access" in condition.distress_signals:
        penalties += 5
    if condition.condition == "gut":
        penalties += 5            # biggest estimating risk
    s = score_deal(cap_rate, coc, dscr, discount, used_total / all_in, uw, penalties)

    return Analysis(
        listing_key=listing.key,
        market_rent=round(market_total, 0),
        section8_rent=round(s8_total, 0) if s8_available else None,
        rent_used=round(used_total, 0),
        rent_basis=basis,
        rehab_cost=rehab,
        all_in_cost=round(all_in, 0),
        noi=round(noi, 0),
        cap_rate=round(cap_rate, 4),
        cash_flow_monthly=round(cash_flow_annual / 12, 0),
        cash_on_cash=round(coc, 4),
        dscr=round(dscr, 2) if dscr is not None else None,
        gross_yield=round(gross_annual / listing.price, 4),
        one_percent_ratio=round(used_total / all_in, 4),
        max_offer=round(max_offer, -3),
        discount_to_max_offer=round(discount, 4),
        score=s,
        grade=grade(s),
        financing=financing,
        condition=condition,
        notes=notes,
    )


def analyze_all(db: Database, settings: Settings, zip_code: Optional[str] = None) -> tuple[int, int]:
    done = skipped = 0
    for listing in db.listings(zip_code):
        if settings.max_price and listing.price > settings.max_price:
            skipped += 1
            continue
        if settings.property_types and listing.property_type not in settings.property_types:
            skipped += 1
            continue
        a = analyze_listing(db, listing, settings)
        if a is None:
            skipped += 1
            continue
        db.save_analysis(a)
        done += 1
    return done, skipped
