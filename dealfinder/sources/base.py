"""Base classes for data sources plus a small HTTP helper (stdlib only)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable, Optional

from ..models import Listing, RentComp

USER_AGENT = "dealfinder/0.1"


class SourceError(RuntimeError):
    pass


def http_get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
                  retries: int = 3, timeout: int = 30) -> dict | list:
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json", **(headers or {})})
    delay = 2.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            body = e.read().decode("utf-8", "replace")[:300]
            raise SourceError(f"GET {url} -> HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise SourceError(f"GET {url} failed: {e.reason}") from e
    raise SourceError(f"GET {url} failed after {retries} attempts")


class ListingSource:
    """Something that yields properties for sale."""

    name = "base"

    def fetch_listings(self, zip_codes: list[str]) -> Iterable[Listing]:
        raise NotImplementedError


class RentSource:
    """Something that yields observed rents (rent comps)."""

    name = "base"

    def fetch_rents(self, zip_codes: list[str]) -> Iterable[RentComp]:
        raise NotImplementedError


def to_float(v) -> float:
    if v in (None, ""):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except ValueError:
        return 0.0


def to_int(v) -> int:
    return int(round(to_float(v)))


def normalize_listing_type(*values: str) -> str:
    """Map the many ways sites describe sale conditions to one vocabulary."""
    text = " ".join(str(v or "") for v in values).lower()
    table = [
        ("auction", "auction"),
        ("short sale", "short_sale"),
        ("short_sale", "short_sale"),
        ("reo", "reo"),
        ("bank owned", "reo"),
        ("real estate owned", "reo"),
        ("hud owned", "reo"),
        ("foreclos", "foreclosure"),
        ("notice of default", "pre_foreclosure"),
        ("pre-foreclosure", "pre_foreclosure"),
        ("preforeclosure", "pre_foreclosure"),
        ("lis pendens", "pre_foreclosure"),
        ("probate", "probate"),
        ("estate sale", "probate"),
        ("tax lien", "tax_sale"),
        ("tax deed", "tax_sale"),
        ("tax sale", "tax_sale"),
    ]
    for needle, label in table:
        if needle in text:
            return label
    return "standard"


def normalize_property_type(v: str, units: int = 1) -> str:
    t = (v or "").lower()
    if units and units >= 5 or "apartment" in t or "5+" in t:
        return "multifamily_5plus"
    if units and units >= 2 or any(k in t for k in ("multi", "duplex", "triplex", "fourplex", "quad", "2-4")):
        return "multifamily_2_4"
    if "condo" in t or "co-op" in t or "coop" in t:
        return "condo"
    if "town" in t:
        return "townhouse"
    if "land" in t or "lot" in t:
        return "land"
    if "manufactured" in t or "mobile" in t:
        return "manufactured"
    return "single_family"
