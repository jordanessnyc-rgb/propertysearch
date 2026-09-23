"""Core data types shared across sources, the database and the analyzer."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Listing:
    """A property for sale, normalized from any source (MLS, RentCast, CSV, ...)."""

    source: str                      # e.g. "mls", "rentcast", "csv:auctions.csv"
    source_id: str                   # the source's own id (MLS number, URL, ...)
    address: str
    city: str = ""
    state: str = ""
    zip: str = ""
    price: float = 0.0
    beds: int = 0
    baths: float = 0.0
    sqft: int = 0
    units: int = 1                   # >1 for multifamily
    year_built: Optional[int] = None
    property_type: str = "single_family"
    status: str = "active"
    days_on_market: Optional[int] = None
    description: str = ""
    url: str = ""
    listing_type: str = "standard"   # standard, foreclosure, reo, short_sale, auction, probate, ...
    annual_taxes: Optional[float] = None
    hoa_monthly: Optional[float] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    unit_beds: list[int] = field(default_factory=list)  # bedroom count per unit, for multifamily

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    def beds_per_unit(self) -> list[int]:
        """Bedroom count for each rentable unit (best effort for multifamily)."""
        if self.unit_beds:
            return self.unit_beds
        units = max(self.units or 1, 1)
        if units == 1:
            return [self.beds]
        per = max(round(self.beds / units), 0) if self.beds else 2
        return [per] * units

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RentComp:
    """An observed rental listing (or rent estimate) used to estimate market rent."""

    source: str
    zip: str
    beds: int
    rent: float
    sqft: Optional[int] = None
    address: str = ""
    baths: Optional[float] = None


@dataclass
class ConditionReport:
    """What the listing text says about condition, distress and financing."""

    condition: str                   # turnkey, light, moderate, heavy, gut
    rehab_per_sqft: float            # $/sqft rehab budget implied by condition
    distress_signals: list[str] = field(default_factory=list)
    repair_items: list[str] = field(default_factory=list)
    positive_signals: list[str] = field(default_factory=list)
    cash_only: bool = False
    financing_notes: list[str] = field(default_factory=list)
    summary: str = ""
    method: str = "keywords"        # keywords | llm


@dataclass
class Analysis:
    listing_key: str
    market_rent: float               # total monthly, all units
    section8_rent: Optional[float]   # total monthly, all units (HUD FMR / SAFMR)
    rent_used: float
    rent_basis: str                  # "market" or "section8"
    rehab_cost: float
    all_in_cost: float
    noi: float
    cap_rate: float
    cash_flow_monthly: float         # after debt service (0 debt if cash deal)
    cash_on_cash: float
    dscr: Optional[float]
    gross_yield: float               # annual rent / price
    one_percent_ratio: float         # monthly rent / all-in cost
    max_offer: float                 # price that hits the target cap rate after rehab
    discount_to_max_offer: float     # (max_offer - price) / price
    score: float                     # 0-100
    grade: str                       # A-F
    financing: str                   # "cash" or "loan"
    condition: ConditionReport
    notes: list[str] = field(default_factory=list)
