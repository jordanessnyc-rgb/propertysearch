"""MLS access through the RESO Web API (the standard OData API most MLSs expose).

You get access through your MLS / broker (or a vendor such as Bridge Interactive, Trestle,
Spark, MLS Grid). They give you a base URL and an access token. Set:

    RESO_ACCESS_TOKEN=...            (environment variable)

and in config.toml:

    [sources.reso]
    base_url = "https://api.bridgedataoutput.com/api/v2/OData/<dataset>"
    # optional extra OData filter, e.g. limit to residential + multifamily
    extra_filter = "PropertyType in ('Residential','Residential Income')"

This pulls active sale listings AND active/closed lease listings (as rent comps), so the MLS
can be both your deal feed and your rent-comp feed.
"""
from __future__ import annotations

from typing import Iterable, Optional

from ..models import Listing, RentComp
from .base import (ListingSource, RentSource, SourceError, http_get_json,
                   normalize_listing_type, normalize_property_type, to_float, to_int)

SELECT = ",".join([
    "ListingKey", "ListingId", "UnparsedAddress", "City", "StateOrProvince", "PostalCode",
    "ListPrice", "ClosePrice", "BedroomsTotal", "BathroomsTotalInteger", "LivingArea",
    "YearBuilt", "PropertyType", "PropertySubType", "StandardStatus", "DaysOnMarket",
    "PublicRemarks", "NumberOfUnitsTotal", "TaxAnnualAmount", "AssociationFee",
    "AssociationFeeFrequency", "Latitude", "Longitude", "SpecialListingConditions",
    "ListingTerms", "ListingURL",
])

STATUS_MAP = {"active": "active", "comingsoon": "coming_soon", "coming soon": "coming_soon",
              "pending": "pending", "activeundercontract": "pending", "closed": "sold"}


def _join(v) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v or "")


def _monthly_hoa(amount, freq) -> Optional[float]:
    a = to_float(amount)
    if not a:
        return None
    f = (freq or "Monthly").lower()
    return a / 12 if "annual" in f else a / 3 if "quarter" in f else a


class ResoSource(ListingSource, RentSource):
    name = "mls"

    def __init__(self, base_url: str, token: str, extra_filter: str = "", page_size: int = 200,
                 max_pages: int = 50):
        if not base_url or not token:
            raise SourceError("RESO source needs sources.reso.base_url and RESO_ACCESS_TOKEN")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.extra_filter = extra_filter
        self.page_size = page_size
        self.max_pages = max_pages

    def _query(self, flt: str) -> Iterable[dict]:
        url = f"{self.base_url}/Property"
        params = {"$filter": flt, "$top": self.page_size, "$select": SELECT}
        headers = {"Authorization": f"Bearer {self.token}"}
        for _ in range(self.max_pages):
            data = http_get_json(url, params=params, headers=headers)
            yield from data.get("value", [])
            nxt = data.get("@odata.nextLink")
            if not nxt:
                return
            url, params = nxt, None

    def _zip_filter(self, zips: list[str]) -> str:
        return "(" + " or ".join(f"PostalCode eq '{z}'" for z in zips) + ")"

    def fetch_listings(self, zip_codes: list[str]) -> Iterable[Listing]:
        flt = f"StandardStatus eq 'Active' and PropertyType ne 'Residential Lease' and {self._zip_filter(zip_codes)}"
        if self.extra_filter:
            flt += f" and ({self.extra_filter})"
        for r in self._query(flt):
            units = to_int(r.get("NumberOfUnitsTotal")) or 1
            terms = _join(r.get("ListingTerms"))
            remarks = r.get("PublicRemarks") or ""
            if terms:
                # Surface MLS financing terms to the condition reader (e.g. "Cash" only).
                remarks = f"{remarks}\n[Listing terms: {terms}]"
            yield Listing(
                source=self.name,
                source_id=str(r.get("ListingId") or r.get("ListingKey")),
                address=r.get("UnparsedAddress") or "",
                city=r.get("City") or "", state=r.get("StateOrProvince") or "",
                zip=str(r.get("PostalCode") or "")[:5],
                price=to_float(r.get("ListPrice")),
                beds=to_int(r.get("BedroomsTotal")),
                baths=to_float(r.get("BathroomsTotalInteger")),
                sqft=to_int(r.get("LivingArea")), units=units,
                year_built=to_int(r.get("YearBuilt")) or None,
                property_type=normalize_property_type(
                    f"{r.get('PropertyType')} {r.get('PropertySubType')}", units),
                status=STATUS_MAP.get(str(r.get("StandardStatus", "")).lower().replace(" ", ""), "active"),
                days_on_market=r.get("DaysOnMarket"),
                description=remarks, url=r.get("ListingURL") or "",
                listing_type=normalize_listing_type(_join(r.get("SpecialListingConditions"))),
                annual_taxes=to_float(r.get("TaxAnnualAmount")) or None,
                hoa_monthly=_monthly_hoa(r.get("AssociationFee"), r.get("AssociationFeeFrequency")),
                lat=r.get("Latitude"), lon=r.get("Longitude"),
            )

    def fetch_rents(self, zip_codes: list[str]) -> Iterable[RentComp]:
        flt = (f"PropertyType eq 'Residential Lease' and "
               f"(StandardStatus eq 'Active' or StandardStatus eq 'Closed') and {self._zip_filter(zip_codes)}")
        for r in self._query(flt):
            rent = to_float(r.get("ClosePrice")) or to_float(r.get("ListPrice"))
            if rent <= 0 or rent > 30000:
                continue
            yield RentComp(source=self.name, zip=str(r.get("PostalCode") or "")[:5],
                           beds=to_int(r.get("BedroomsTotal")), rent=rent,
                           sqft=to_int(r.get("LivingArea")) or None,
                           address=r.get("UnparsedAddress") or "",
                           baths=to_float(r.get("BathroomsTotalInteger")) or None)
