"""Section 8 rents from HUD Fair Market Rents (FMR) / Small Area FMRs (SAFMR).

Free API token: https://www.huduser.gov/hudapi/public/register  -> set HUD_API_TOKEN.

For each zip code we look up its county via HUD's USPS crosswalk, fetch that county's FMR
data, and store the per-bedroom rent for the zip (SAFMR when HUD publishes zip-level rents
for the metro, otherwise the county/metro FMR). Local housing authorities set their payment
standard at 90-110% of FMR; tune `section8_payment_standard_pct` in config to match yours.

No token? Load your housing authority's payment standards with `dealfinder import-fmr`.
"""
from __future__ import annotations

import csv
from typing import Optional

from ..db import Database
from .base import SourceError, http_get_json, to_float

BASE = "https://www.huduser.gov/hudapi/public"
BED_FIELDS = {0: "Efficiency", 1: "One-Bedroom", 2: "Two-Bedroom", 3: "Three-Bedroom", 4: "Four-Bedroom"}


def parse_fmr_payload(payload: dict, zip_code: str) -> tuple[dict[int, float], str, Optional[int]]:
    """Return ({beds: rent}, 'safmr'|'fmr', year) for a zip from a HUD /fmr/data response."""
    data = payload.get("data", payload)
    basic = data.get("basicdata", data)
    rows = basic if isinstance(basic, list) else [basic]
    chosen, kind = None, "fmr"
    for row in rows:
        if str(row.get("zip_code", "")).strip() == zip_code:
            chosen, kind = row, "safmr"
            break
    if chosen is None:
        # fall back to the area-wide row ("MSA level") or the first row
        chosen = next((r for r in rows if "msa" in str(r.get("zip_code", "")).lower()), rows[0])
    rents = {beds: to_float(chosen.get(f)) for beds, f in BED_FIELDS.items() if chosen.get(f)}
    year = chosen.get("year") or data.get("year")
    return rents, kind, int(year) if year else None


class HudFmrSource:
    name = "hud"

    def __init__(self, token: str, year: Optional[int] = None):
        if not token:
            raise SourceError("HUD source needs HUD_API_TOKEN (free at huduser.gov)")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.year = year

    def county_for_zip(self, zip_code: str) -> str:
        data = http_get_json(f"{BASE}/usps", params={"type": 2, "query": zip_code}, headers=self.headers)
        results = (data.get("data") or {}).get("results") or []
        if not results:
            raise SourceError(f"HUD crosswalk has no county for zip {zip_code}")
        best = max(results, key=lambda r: to_float(r.get("res_ratio")))
        return str(best["geoid"])[:5]

    def load(self, db: Database, zip_codes: list[str]) -> int:
        n = 0
        for z in zip_codes:
            county = self.county_for_zip(z)
            params = {"year": self.year} if self.year else None
            payload = http_get_json(f"{BASE}/fmr/data/{county}99999", params=params, headers=self.headers)
            rents, kind, year = parse_fmr_payload(payload, z)
            for beds, rent in rents.items():
                db.set_fmr(z, beds, rent, year, kind)
                n += 1
        return n


def import_fmr_csv(db: Database, path: str) -> int:
    """Load payment standards / FMRs from a CSV with columns: zip, beds, rent [, year]."""
    n = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            row = {k.strip().lower(): v for k, v in row.items()}
            db.set_fmr(row["zip"].strip()[:5], int(float(row["beds"])), to_float(row["rent"]),
                       int(row["year"]) if row.get("year") else None, "manual")
            n += 1
    return n
