"""Read a listing's description and decide how much work the building needs, whether the
sale is distressed, and whether it's cash-only.

This is a fast rule-based reader that works offline. For a deeper read, turn on
`use_llm_reader` in config to have Claude read the listing (see llm_reader.py); the rule
reader is still used as the fallback.
"""
from __future__ import annotations

import re

from ..models import ConditionReport, Listing

# Rehab budget ($ per sqft of living area) for each condition level. Tune to your market.
REHAB_PER_SQFT = {"turnkey": 2.0, "light": 12.0, "moderate": 30.0, "heavy": 55.0, "gut": 90.0}
LEVELS = ["turnkey", "light", "moderate", "heavy", "gut"]
MIN_REHAB = {"turnkey": 0, "light": 5000, "moderate": 15000, "heavy": 35000, "gut": 60000}

# (regex, level, label). Levels raise the condition estimate to at least that level.
REPAIR_PATTERNS: list[tuple[str, str, str]] = [
    # gut / structural
    (r"\bgut(ted)?\b|gut rehab|down to the studs", "gut", "full gut rehab"),
    (r"fire[- ]damage[d]?|fire damaged|smoke damage", "gut", "fire damage"),
    (r"structural (damage|issues?|problems?)", "gut", "structural damage"),
    (r"foundation (issues?|problems?|damage|repair|needs|crack)", "heavy", "foundation issues"),
    (r"condemned|uninhabitable|not habitable|unsafe to enter", "gut", "uninhabitable / condemned"),
    (r"tear ?down|teardown|value (is )?in (the )?land|land value only", "gut", "possible teardown"),
    (r"collapsed|caved in", "gut", "collapse"),
    # heavy systems
    (r"roof (leaks?|is leaking|needs|damage)|needs (a )?(new )?roof|leaking roof", "heavy", "roof"),
    (r"needs (a )?(new )?(furnace|hvac|boiler|heating|ac\b|a/c|air conditioning)|(furnace|hvac|boiler) (is )?(not working|needs|broken)|no heat", "heavy", "HVAC / heating"),
    (r"\bmold\b|mildew", "heavy", "mold"),
    (r"water damage|flood(ed|ing)? damage|flooded", "heavy", "water damage"),
    (r"needs (a )?(complete|full|total|major|extensive) (rehab|renovation|remodel)|major repairs?|extensive repairs?|full rehab|complete rehab", "heavy", "major rehab"),
    (r"vandali[sz]ed|stripped|copper (was )?(stolen|removed)|missing (plumbing|copper|electrical)", "heavy", "vandalized / stripped"),
    (r"knob and tube|knob & tube|needs (new )?electrical|electrical (issues|needs)|rewir(e|ing)", "heavy", "electrical"),
    (r"needs (new )?plumbing|plumbing (issues|needs|problems)|sewer (line )?(issues|needs|problems|backup)", "heavy", "plumbing / sewer"),
    (r"termite|termites|wood destroying", "heavy", "termite damage"),
    (r"asbestos|lead paint|oil tank|underground tank", "moderate", "environmental (asbestos / lead / oil tank)"),
    (r"no utilities|utilities (are )?off|no water|no electric", "moderate", "utilities off"),
    # moderate
    (r"needs (a lot of )?work|needs (some )?repairs|in need of repair|needs rehab|rehab needed", "moderate", "needs work"),
    (r"fixer[- ]?upper|fixer\b|handyman special|contractor special|investor special|rehab opportunity|bring your contractor", "moderate", "fixer / investor special"),
    (r"needs (a )?(new )?kitchen|kitchen needs|full kitchen", "moderate", "kitchen"),
    (r"needs (new )?(bath|bathroom)s?|bath(room)?s? need", "moderate", "bathrooms"),
    (r"needs (new )?windows|windows need", "moderate", "windows"),
    (r"needs (new )?water heater", "light", "water heater"),
    (r"needs updat(ing|es)|\bdated\b|original (kitchen|bath)|outdated|needs modernization", "moderate", "dated / needs updating"),
    (r"\btlc\b|some love|needs love|elbow grease|sweat equity", "light", "TLC"),
    # light / cosmetic
    (r"cosmetic|needs (fresh )?paint|needs (new )?carpet|needs (new )?flooring|flooring needs", "light", "cosmetic"),
]

POSITIVE_PATTERNS: list[tuple[str, str]] = [
    (r"move[- ]in ready|turn[- ]?key|ready to move in", "move-in ready"),
    (r"(fully|completely|totally|newly|recently|beautifully) (renovated|remodeled|rehabbed|updated|restored)", "renovated"),
    (r"\b(new|newer|replaced) roof|roof (replaced|is new)|roof \(20[12]\d\)|roof 20[12]\d", "new roof"),
    (r"\b(new|newer|updated|replaced) (hvac|furnace|boiler|a/c|ac\b|central air)", "new HVAC"),
    (r"\b(new|updated) (electrical|electric panel|panel|plumbing|wiring)|updated electrical", "updated electrical/plumbing"),
    (r"\b(new|updated|renovated|remodeled) kitchen|quartz|granite|stainless( steel)? appliances", "updated kitchen"),
    (r"\b(new|updated|renovated|remodeled) bath(room)?s?", "updated baths"),
    (r"\bnew windows|replacement windows|newer windows", "newer windows"),
    (r"tenant[s]? in place|fully (leased|rented|occupied)|currently rented|both units rented|long[- ]term tenants?", "tenant(s) in place"),
    (r"section 8|housing choice voucher|hcv tenant", "section 8 tenant/approved"),
]

DISTRESS_PATTERNS: list[tuple[str, str]] = [
    (r"\bas[- ]is\b|as is,? where is|no repairs will be made|no seller repairs", "sold as-is"),
    (r"motivated seller|seller is motivated|must sell|bring (all|any) offers|all offers considered|make an offer", "motivated seller"),
    (r"price (reduced|reduction|drop|improvement)|reduced price|just reduced|new price", "price reduced"),
    (r"bank[- ]owned|\breo\b|lender[- ]owned|hud[- ]owned|hud home", "bank owned / REO"),
    (r"foreclos", "foreclosure"),
    (r"short sale|subject to (lender|bank|third[- ]party) approval", "short sale"),
    (r"\bauction\b|online bidding|opening bid|starting bid", "auction"),
    (r"estate sale|probate|court approval|heirs|executor|administrator", "estate / probate"),
    (r"tax (lien|deed|sale)|back taxes|delinquent taxes", "tax distress"),
    (r"divorce|relocat(ing|ion)|job transfer", "life event (divorce / relocation)"),
    (r"\bvacant\b|unoccupied|abandoned", "vacant"),
    (r"no (interior )?access|no showings|drive[- ]by only|sight unseen|exterior only", "no interior access"),
    (r"code violation|open permits?|city violation", "code violations / open permits"),
]

CASH_PATTERNS: list[tuple[str, str]] = [
    (r"cash (buyers? )?only|cash offers only|all cash|only cash", "cash only"),
    (r"will not (qualify|be eligible) for (conventional |traditional |fha |va )?(financing|loans?)|not financ(e)?able|won'?t qualify for (a )?(loan|financing|mortgage)|not eligible for financing|does not qualify for financing", "won't qualify for financing"),
    (r"cash or (hard money|rehab loan|203k|renovation loan|private money)|(hard money|rehab loan|203k) only", "cash or rehab/hard-money loan"),
    (r"\[listing terms: cash\]", "MLS listing terms: cash"),
]

UNFINANCEABLE_CONDITIONS = {"heavy", "gut"}
DISTRESS_TYPES = {"foreclosure", "reo", "short_sale", "auction", "probate", "pre_foreclosure", "tax_sale"}


def _find(patterns, text):
    hits = []
    for pat, *rest in patterns:
        if re.search(pat, text):
            hits.append(tuple(rest))
    return hits


def _negated_positive(pat: str, text: str) -> bool:
    """True if every match of a positive pattern is preceded by 'need(s)' -> it's a repair."""
    matches = list(re.finditer(pat, text))
    if not matches:
        return False
    return all(re.search(r"need(s|ed|ing)?( a| an)?\s*$", text[max(0, m.start() - 16):m.start()])
               for m in matches)


def read_listing(listing: Listing, price_drops: int = 0) -> ConditionReport:
    text = " ".join((listing.description or "").lower().split())

    repairs = _find(REPAIR_PATTERNS, text)
    level_idx = max((LEVELS.index(level) for level, _ in repairs), default=None)
    repair_items = sorted({label for _, label in repairs})

    positives = [label for pat, label in POSITIVE_PATTERNS
                 if re.search(pat, text) and not _negated_positive(pat, text)]

    notes: list[str] = []
    if level_idx is None:
        if any(p in positives for p in ("move-in ready", "renovated")):
            level_idx = LEVELS.index("turnkey")
        elif listing.year_built and listing.year_built < 1950 and "renovated" not in positives:
            level_idx = LEVELS.index("light")
            notes.append(f"built {listing.year_built}, no renovation mentioned: assuming light rehab")
        elif not text:
            level_idx = LEVELS.index("light")
            notes.append("no description: assuming light rehab")
        else:
            level_idx = LEVELS.index("light") if len(positives) < 2 else LEVELS.index("turnkey")
    else:
        # Several moderate items together usually add up to a heavier rehab.
        moderate_count = sum(1 for level, _ in repairs if level == "moderate")
        if LEVELS[level_idx] == "moderate" and moderate_count >= 3:
            level_idx += 1
        # Renovation mentions pull a light/moderate reading down a notch.
        if LEVELS[level_idx] in ("light", "moderate") and "renovated" in positives:
            level_idx -= 1

    distress = [label for pat, label in DISTRESS_PATTERNS if re.search(pat, text)]
    if listing.listing_type in DISTRESS_TYPES:
        tag = listing.listing_type.replace("_", " ")
        if not any(tag.split()[0] in d for d in distress):
            distress.append(f"sale type: {tag}")
    if listing.days_on_market and listing.days_on_market >= 90:
        distress.append(f"{listing.days_on_market} days on market")
    if price_drops:
        distress.append(f"{price_drops} price drop(s) tracked")
    if "no interior access" in distress and LEVELS[level_idx] in ("turnkey", "light"):
        level_idx = LEVELS.index("moderate")
        notes.append("no interior access: budgeting at least moderate rehab")

    condition = LEVELS[level_idx]
    financing = [label for pat, label in CASH_PATTERNS if re.search(pat, text)]
    cash_only = bool(financing)
    if listing.listing_type in ("auction", "tax_sale") and not cash_only:
        cash_only = True
        financing.append("auctions typically require cash / proof of funds")
    if condition in UNFINANCEABLE_CONDITIONS and not cash_only:
        financing.append(f"{condition} rehab: likely won't pass a conventional/FHA appraisal; plan on cash, hard money or a rehab loan")

    summary = f"{condition} rehab"
    if repair_items:
        summary += f": {', '.join(repair_items)}"
    if notes:
        summary += f" ({'; '.join(notes)})"

    return ConditionReport(
        condition=condition,
        rehab_per_sqft=REHAB_PER_SQFT[condition],
        distress_signals=distress,
        repair_items=repair_items,
        positive_signals=positives,
        cash_only=cash_only,
        financing_notes=financing,
        summary=summary,
        method="keywords",
    )


def rehab_estimate(report: ConditionReport, sqft: int, units: int, contingency_pct: float) -> float:
    area = sqft or 1100 * max(units, 1)   # assume ~1,100 sqft/unit when size is unknown
    base = max(area * report.rehab_per_sqft, MIN_REHAB.get(report.condition, 0))
    return round(base * (1 + contingency_pct), -2)
