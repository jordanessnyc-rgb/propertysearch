"""RentCast API (https://www.rentcast.io/api): nationwide for-sale listings, rental
listings and rent estimates. A paid key with a free tier. Set RENTCAST_API_KEY.

Sale listings include foreclosures and short sales (RentCast's `listingType`), and rental
listings are used as rent comps.
"""
from __future__ import annotations

from typing import Iterable

from ..models import Listing, RentComp
from .base import (ListingSource, RentSource, SourceError, http_get_json,
                   normalize_listing_type, normalize_property_type, to_float, to_int)

BASE = "https://api.rentcast.io/v1"


class RentCastSource(ListingSource, RentSource):
    name = "rentcast"

    def __init__(self, api_key: str, limit: int = 500, max_pages: int = 4):
        if not api_key:
            raise SourceError("RentCast source needs RENTCAST_API_KEY")
        self.headers = {"X-Api-Key": api_key}
        self.limit = limit
        self.max_pages = max_pages

    def _paged(self, path: str, params: dict) -> Iterable[dict]:
        for page in range(self.max_pages):
            batch = http_get_json(f"{BASE}{path}", params={**params, "limit": self.limit,
                                                           "offset": page * self.limit},
                                  headers=self.headers)
            if not isinstance(batch, list) or not batch:
                return
            yield from batch
            if len(batch) < self.limit:
                return

    def fetch_listings(self, zip_codes: list[str]) -> Iterable[Listing]:
        for z in zip_codes:
            for r in self._paged("/listings/sale", {"zipCode": z, "status": "Active"}):
                units = 1
                ptype = r.get("propertyType") or ""
                if "multi" in ptype.lower():
                    units = 2  # RentCast doesn't give unit counts; refine in the listing text
                yield Listing(
                    source=self.name, source_id=str(r.get("id")),
                    address=r.get("formattedAddress") or r.get("addressLine1") or "",
                    city=r.get("city") or "", state=r.get("state") or "",
                    zip=str(r.get("zipCode") or z)[:5],
                    price=to_float(r.get("price")), beds=to_int(r.get("bedrooms")),
                    baths=to_float(r.get("bathrooms")), sqft=to_int(r.get("squareFootage")),
                    units=units, year_built=r.get("yearBuilt"),
                    property_type=normalize_property_type(ptype, units),
                    status="active", days_on_market=r.get("daysOnMarket"),
                    description=r.get("description") or "",
                    listing_type=normalize_listing_type(r.get("listingType")),
                    hoa_monthly=to_float((r.get("hoa") or {}).get("fee")) or None,
                    lat=r.get("latitude"), lon=r.get("longitude"),
                )

    def fetch_rents(self, zip_codes: list[str]) -> Iterable[RentComp]:
        for z in zip_codes:
            for r in self._paged("/listings/rental/long-term", {"zipCode": z, "status": "Active"}):
                rent = to_float(r.get("price"))
                if rent <= 0:
                    continue
                yield RentComp(source=self.name, zip=str(r.get("zipCode") or z)[:5],
                               beds=to_int(r.get("bedrooms")), rent=rent,
                               sqft=to_int(r.get("squareFootage")) or None,
                               address=r.get("formattedAddress") or "",
                               baths=to_float(r.get("bathrooms")) or None)
