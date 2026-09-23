"""Made-up demo data so the whole pipeline can be tried without any API keys.

Every record is fictional (source = "demo", zips 99901/99902). Delete data/dealfinder.db
before loading real data, or use a separate db_path.
"""
from __future__ import annotations

import random

from ..db import Database
from ..models import Listing, RentComp

DESCRIPTIONS = [
    ("Move-in ready! Fully renovated in 2023 with new roof, new HVAC, updated kitchen with "
     "quartz counters and stainless appliances. Tenant in place paying market rent.", "standard"),
    ("Investor special! Property needs work - roof leaks, needs new furnace and full kitchen. "
     "Sold as-is, cash only. Bring your contractor.", "standard"),
    ("Bank owned REO. Property is being sold as-is with no repairs. Some water damage in "
     "basement, mold present. Cash or rehab loan only.", "reo"),
    ("Charming home with original hardwood floors, freshly painted, newer windows. "
     "Great starter or rental.", "standard"),
    ("Short sale, subject to lender approval. Needs cosmetic updates: carpet, paint and some "
     "TLC. Motivated seller.", "short_sale"),
    ("Fire damaged. Structural damage to rear of home, needs complete gut rehab. Cash buyers "
     "only, will not qualify for financing.", "standard"),
    ("Duplex, both units rented, long-term tenants. Separate utilities. Updated electrical "
     "panel 2021. Section 8 tenant in unit 2.", "standard"),
    ("Estate sale - probate. Home is dated but well maintained. Needs updating, original "
     "kitchen and baths. Price reduced!", "probate"),
    ("Foreclosure auction. No interior access, sold as-is. Possible foundation issues.", "auction"),
    ("Turnkey rental, fully leased. New water heater, updated bathrooms, low maintenance.", "standard"),
]

STREETS = ["Oak", "Maple", "Cedar", "Elm", "Pine", "Birch", "Walnut", "Chestnut", "Hickory", "Spruce"]


def load_demo(db: Database, seed: int = 7) -> dict:
    rnd = random.Random(seed)
    markets = {"99901": {"base_rent": {1: 850, 2: 1050, 3: 1300, 4: 1550}, "ppsf": 95},
               "99902": {"base_rent": {1: 1100, 2: 1400, 3: 1750, 4: 2050}, "ppsf": 160}}
    counts = {"listings": 0, "rent_comps": 0, "fmr": 0}

    for z, m in markets.items():
        comps = []
        for i in range(40):
            beds = rnd.choice([1, 2, 2, 3, 3, 3, 4])
            rent = m["base_rent"][beds] * rnd.uniform(0.85, 1.15)
            comps.append(RentComp(source="demo", zip=z, beds=beds, rent=round(rent, -1),
                                  sqft=int(500 + beds * 300 * rnd.uniform(0.8, 1.2)),
                                  address=f"{100 + i} {rnd.choice(STREETS)} Ave Apt {i % 4 + 1}"))
        counts["rent_comps"] += db.add_rent_comps(comps)
        for beds, rent in m["base_rent"].items():
            db.set_fmr(z, beds, round(rent * 1.08, -1), 2026, "demo")
            counts["fmr"] += 1

        for i in range(15):
            desc, ltype = DESCRIPTIONS[(i + (3 if z == "99902" else 0)) % len(DESCRIPTIONS)]
            units = 2 if "Duplex" in desc else 1
            beds = 4 if units == 2 else rnd.choice([2, 3, 3, 4])
            sqft = int((700 + beds * 350) * rnd.uniform(0.85, 1.15))
            distress = any(k in desc.lower() for k in ("needs", "as-is", "fire", "gut"))
            price = sqft * m["ppsf"] * rnd.uniform(0.45, 0.8 if distress else 1.25)
            db.upsert_listing(Listing(
                source="demo", source_id=f"{z}-{i}",
                address=f"{rnd.randint(100, 9999)} {rnd.choice(STREETS)} St",
                city="Demoville", state="ZZ", zip=z, price=round(price, -3), beds=beds,
                baths=rnd.choice([1, 1, 1.5, 2]), sqft=sqft, units=units,
                year_built=rnd.randint(1910, 1995),
                property_type="multifamily_2_4" if units == 2 else "single_family",
                days_on_market=rnd.randint(1, 180), description=desc, listing_type=ltype,
                unit_beds=[2, 2] if units == 2 else [],
            ))
            counts["listings"] += 1
    return counts
