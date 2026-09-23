"""Estimate market rent (from rent comps) and Section 8 rent (from HUD FMR) per unit."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Optional

from ..db import Database

BED_STEP = 1.18   # rough rent ratio between adjacent bedroom counts, used when comps are thin
MIN_COMPS = 3


@dataclass
class RentEstimate:
    rent: Optional[float]
    comps: int
    method: str


def _trimmed_median(values: list[float]) -> float:
    values = sorted(values)
    if len(values) >= 10:            # drop the top/bottom 10% to ignore outliers
        cut = len(values) // 10
        values = values[cut:len(values) - cut]
    return median(values)


def market_rent(db: Database, zip_code: str, beds: int) -> RentEstimate:
    beds = max(beds, 0)
    same = [c.rent for c in db.rent_comps(zip_code, beds)]
    if len(same) >= MIN_COMPS:
        return RentEstimate(round(_trimmed_median(same), -1), len(same), f"median of {len(same)} {beds}BR comps")

    # Thin data: scale from the nearest bedroom count that has enough comps.
    all_comps = db.rent_comps(zip_code)
    by_beds: dict[int, list[float]] = {}
    for c in all_comps:
        by_beds.setdefault(c.beds, []).append(c.rent)
    candidates = [(abs(b - beds), b) for b, v in by_beds.items() if len(v) >= MIN_COMPS]
    if candidates:
        _, b = min(candidates)
        est = _trimmed_median(by_beds[b]) * BED_STEP ** (beds - b)
        return RentEstimate(round(est, -1), len(by_beds[b]), f"scaled from {len(by_beds[b])} {b}BR comps")
    if same:
        return RentEstimate(round(median(same), -1), len(same), f"only {len(same)} {beds}BR comp(s): low confidence")
    return RentEstimate(None, 0, "no rent comps for this zip")


def section8_rent(db: Database, zip_code: str, beds: int, payment_standard_pct: float = 1.0) -> Optional[float]:
    fmr = db.fmr(zip_code)
    if not fmr:
        return None
    if beds in fmr:
        rent = fmr[beds]
    elif beds > 4 and 4 in fmr:
        rent = fmr[4] * (1 + 0.15 * (beds - 4))   # HUD rule: +15% of 4BR FMR per extra bedroom
    else:
        nearest = min(fmr, key=lambda b: abs(b - beds))
        rent = fmr[nearest] * BED_STEP ** (beds - nearest)
    return round(rent * payment_standard_pct, -1)
