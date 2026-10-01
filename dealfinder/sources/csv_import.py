"""Import listings or rent comps from any CSV export.

This is how sites without an API get in: auction sites, wholesaler lists, PropStream /
BatchLeads exports, county foreclosure lists, a spreadsheet you keep by hand, etc.
Column names are matched loosely (e.g. "List Price", "price", "Asking" all work).
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Iterable

from ..models import Listing, RentComp
from .base import normalize_listing_type, normalize_property_type, to_float, to_int

ALIASES = {
    "address": ["address", "street address", "property address", "full address", "unparsedaddress", "street"],
    "city": ["city"],
    "state": ["state", "st", "stateorprovince", "state or province"],
    "zip": ["zip", "zipcode", "zip code", "postal code", "postalcode", "zip or postal code"],
    "price": ["price", "list price", "listprice", "asking price", "asking", "opening bid", "starting bid", "sale price"],
    "beds": ["beds", "bedrooms", "br", "bedroomstotal", "total beds"],
    "baths": ["baths", "bathrooms", "ba", "bathroomstotalinteger", "total baths"],
    "sqft": ["sqft", "square feet", "squarefootage", "living area", "livingarea", "building sqft", "sq ft"],
    "units": ["units", "unit count", "number of units", "numberofunitstotal"],
    "year_built": ["year built", "yearbuilt", "year"],
    "property_type": ["property type", "propertytype", "type"],
    "description": ["description", "remarks", "public remarks", "publicremarks", "notes", "listing description"],
    "url": ["url", "link", "listing url"],
    "listing_type": ["listing type", "sale type", "special listing conditions", "condition", "status type"],
    "source_id": ["id", "mls", "mls #", "mls#", "mls number", "listing id", "listingid"],
    "days_on_market": ["dom", "days on market", "daysonmarket"],
    "annual_taxes": ["taxes", "annual taxes", "tax amount", "taxannualamount"],
    "hoa_monthly": ["hoa", "hoa fee", "hoa monthly"],
    "rent": ["rent", "monthly rent", "list price", "price"],
}


def _pick(row: dict, field: str, default=""):
    for alias in ALIASES[field]:
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
    # Some exports put notes in the header, e.g. Redfin's "URL (SEE https://... FOR INFO)".
    for alias in ALIASES[field]:
        for key, value in row.items():
            if key.startswith(alias + " (") and value:
                return value
    return default


def _rows(path: str) -> Iterable[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            yield {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}


def read_listings_csv(path: str, source_name: str | None = None) -> Iterable[Listing]:
    source = source_name or f"csv:{Path(path).stem}"
    for row in _rows(path):
        address = _pick(row, "address")
        if not address:
            continue
        units = to_int(_pick(row, "units")) or 1
        zip_code = str(_pick(row, "zip"))[:5]
        sid = _pick(row, "source_id") or hashlib.sha1(f"{address}|{zip_code}".lower().encode()).hexdigest()[:12]
        yield Listing(
            source=source, source_id=str(sid), address=address,
            city=_pick(row, "city"), state=_pick(row, "state"), zip=zip_code,
            price=to_float(_pick(row, "price")), beds=to_int(_pick(row, "beds")),
            baths=to_float(_pick(row, "baths")), sqft=to_int(_pick(row, "sqft")), units=units,
            year_built=to_int(_pick(row, "year_built")) or None,
            property_type=normalize_property_type(_pick(row, "property_type"), units),
            days_on_market=to_int(_pick(row, "days_on_market")) or None,
            description=_pick(row, "description"), url=_pick(row, "url"),
            listing_type=normalize_listing_type(_pick(row, "listing_type"), source),
            annual_taxes=to_float(_pick(row, "annual_taxes")) or None,
            hoa_monthly=to_float(_pick(row, "hoa_monthly")) or None,
        )


def read_rents_csv(path: str, source_name: str | None = None) -> Iterable[RentComp]:
    source = source_name or f"csv:{Path(path).stem}"
    for row in _rows(path):
        rent = to_float(_pick(row, "rent"))
        zip_code = str(_pick(row, "zip"))[:5]
        if rent <= 0 or not zip_code:
            continue
        yield RentComp(source=source, zip=zip_code, beds=to_int(_pick(row, "beds")), rent=rent,
                       sqft=to_int(_pick(row, "sqft")) or None, address=_pick(row, "address"),
                       baths=to_float(_pick(row, "baths")) or None)
