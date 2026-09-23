"""Optional: have Claude read the listing instead of (well, on top of) the keyword rules.

Enable with `use_llm_reader = true` in config.toml, set ANTHROPIC_API_KEY, and
`pip install anthropic`. Results fall back to the keyword reader on any error.
"""
from __future__ import annotations

import json
import logging

from ..models import ConditionReport, Listing
from .condition import LEVELS, REHAB_PER_SQFT

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "condition": {"type": "string", "enum": LEVELS},
        "repair_items": {"type": "array", "items": {"type": "string"}},
        "positive_signals": {"type": "array", "items": {"type": "string"}},
        "distress_signals": {"type": "array", "items": {"type": "string"}},
        "cash_only": {"type": "boolean"},
        "financing_notes": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["condition", "repair_items", "positive_signals", "distress_signals",
                 "cash_only", "financing_notes", "summary"],
    "additionalProperties": False,
}

SYSTEM = """You are an experienced real estate investor and rehab estimator reviewing a \
listing for a buy-and-hold rental investor. Judge only from what the listing says; do not \
invent problems. Condition levels:
- turnkey: rent-ready, at most minor touch-ups
- light: cosmetic (paint, flooring, fixtures)
- moderate: kitchen/bath updates, some systems, several repairs
- heavy: major systems (roof, HVAC, electrical, plumbing), mold/water damage, foundation work
- gut: fire/structural damage, uninhabitable, full gut
Distress signals are signs the seller is motivated or the sale is non-standard (REO, \
foreclosure, short sale, auction, probate, as-is, price cuts, vacancy, long days on market). \
cash_only is true when the listing says cash only / won't qualify for financing, or the \
sale type (auction, tax sale) effectively requires cash. Keep the summary to one or two \
sentences."""


def read_listing_llm(listing: Listing, model: str, fallback: ConditionReport) -> ConditionReport:
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic package not installed; using keyword reader")
        return fallback

    facts = {
        "address": listing.address, "price": listing.price, "beds": listing.beds,
        "baths": listing.baths, "sqft": listing.sqft, "units": listing.units,
        "year_built": listing.year_built, "sale_type": listing.listing_type,
        "days_on_market": listing.days_on_market,
    }
    prompt = (f"Listing facts: {json.dumps(facts)}\n\nListing description:\n"
              f"{listing.description or '(no description)'}")
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if response.stop_reason != "end_turn":
            log.warning("LLM reader stopped with %s for %s", response.stop_reason, listing.key)
            return fallback
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
    except anthropic.APIConnectionError as e:
        log.warning("LLM reader connection error for %s: %s", listing.key, e)
        return fallback
    except anthropic.RateLimitError as e:
        log.warning("LLM reader rate limited for %s: %s", listing.key, e)
        return fallback
    except anthropic.APIStatusError as e:
        log.warning("LLM reader API error %s for %s: %s", e.status_code, listing.key, e)
        return fallback
    except (StopIteration, json.JSONDecodeError) as e:
        log.warning("LLM reader returned unusable output for %s: %s", listing.key, e)
        return fallback

    # Keep deterministic signals (days on market, tracked price drops) from the rule reader.
    distress = list(dict.fromkeys(data["distress_signals"] + [
        s for s in fallback.distress_signals if "days on market" in s or "price drop" in s]))
    return ConditionReport(
        condition=data["condition"],
        rehab_per_sqft=REHAB_PER_SQFT[data["condition"]],
        distress_signals=distress,
        repair_items=data["repair_items"],
        positive_signals=data["positive_signals"],
        cash_only=data["cash_only"] or fallback.cash_only,
        financing_notes=data["financing_notes"],
        summary=data["summary"],
        method="llm",
    )
