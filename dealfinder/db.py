"""SQLite storage: listings, rent comps, HUD fair market rents, and analysis results."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .models import Analysis, ConditionReport, Listing, RentComp

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    address TEXT, city TEXT, state TEXT, zip TEXT,
    price REAL, beds INTEGER, baths REAL, sqft INTEGER, units INTEGER,
    year_built INTEGER, property_type TEXT, status TEXT, days_on_market INTEGER,
    description TEXT, url TEXT, listing_type TEXT,
    annual_taxes REAL, hoa_monthly REAL, lat REAL, lon REAL, unit_beds TEXT,
    first_seen TEXT, last_seen TEXT, price_history TEXT
);
CREATE INDEX IF NOT EXISTS listings_zip ON listings(zip);

CREATE TABLE IF NOT EXISTS rent_comps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, zip TEXT, beds INTEGER, rent REAL, sqft INTEGER,
    address TEXT, baths REAL, observed_at TEXT,
    UNIQUE(source, address, beds, rent)
);
CREATE INDEX IF NOT EXISTS rent_comps_zip ON rent_comps(zip, beds);

-- HUD Fair Market Rents (Section 8). zip = '' for county/metro-level rows.
CREATE TABLE IF NOT EXISTS fair_market_rents (
    area_key TEXT NOT NULL,     -- zip code, or a HUD entity id
    year INTEGER,
    beds INTEGER NOT NULL,
    rent REAL NOT NULL,
    source TEXT,               -- safmr | fmr | manual
    PRIMARY KEY (area_key, beds)
);

CREATE TABLE IF NOT EXISTS analyses (
    listing_key TEXT PRIMARY KEY,
    analyzed_at TEXT,
    score REAL,
    grade TEXT,
    data TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # ---- listings -------------------------------------------------------
    def upsert_listing(self, l: Listing) -> str:
        """Insert or update a listing. Returns 'new', 'price_change', or 'seen'."""
        row = self.conn.execute(
            "SELECT price, price_history, first_seen FROM listings WHERE key=?", (l.key,)
        ).fetchone()
        now = _now()
        history = json.loads(row["price_history"]) if row and row["price_history"] else []
        result = "new"
        if row:
            result = "seen"
            if row["price"] and l.price and abs(row["price"] - l.price) > 1:
                result = "price_change"
        if not history or history[-1]["price"] != l.price:
            history.append({"date": now, "price": l.price})
        self.conn.execute(
            """INSERT OR REPLACE INTO listings VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                l.key, l.source, l.source_id, l.address, l.city, l.state, l.zip,
                l.price, l.beds, l.baths, l.sqft, l.units, l.year_built, l.property_type,
                l.status, l.days_on_market, l.description, l.url, l.listing_type,
                l.annual_taxes, l.hoa_monthly, l.lat, l.lon, json.dumps(l.unit_beds),
                row["first_seen"] if row else now, now, json.dumps(history),
            ),
        )
        self.conn.commit()
        return result

    @staticmethod
    def _row_to_listing(r: sqlite3.Row) -> Listing:
        return Listing(
            source=r["source"], source_id=r["source_id"], address=r["address"] or "",
            city=r["city"] or "", state=r["state"] or "", zip=r["zip"] or "",
            price=r["price"] or 0.0, beds=r["beds"] or 0, baths=r["baths"] or 0.0,
            sqft=r["sqft"] or 0, units=r["units"] or 1, year_built=r["year_built"],
            property_type=r["property_type"] or "", status=r["status"] or "",
            days_on_market=r["days_on_market"], description=r["description"] or "",
            url=r["url"] or "", listing_type=r["listing_type"] or "standard",
            annual_taxes=r["annual_taxes"], hoa_monthly=r["hoa_monthly"],
            lat=r["lat"], lon=r["lon"], unit_beds=json.loads(r["unit_beds"] or "[]"),
        )

    def listings(self, zip_code: Optional[str] = None, active_only: bool = True) -> list[Listing]:
        sql, args = "SELECT * FROM listings WHERE 1=1", []
        if zip_code:
            sql += " AND zip=?"
            args.append(zip_code)
        if active_only:
            sql += " AND status IN ('active','coming_soon','pending_auction','')"
        return [self._row_to_listing(r) for r in self.conn.execute(sql, args)]

    def get_listing(self, key: str) -> Optional[Listing]:
        r = self.conn.execute("SELECT * FROM listings WHERE key=?", (key,)).fetchone()
        return self._row_to_listing(r) if r else None

    def price_history(self, key: str) -> list[dict]:
        r = self.conn.execute("SELECT price_history FROM listings WHERE key=?", (key,)).fetchone()
        return json.loads(r["price_history"]) if r and r["price_history"] else []

    # ---- rents -----------------------------------------------------------
    def add_rent_comps(self, comps: Iterable[RentComp]) -> int:
        n = 0
        for c in comps:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO rent_comps
                   (source, zip, beds, rent, sqft, address, baths, observed_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (c.source, c.zip, c.beds, c.rent, c.sqft, c.address, c.baths, _now()),
            )
            n += cur.rowcount
        self.conn.commit()
        return n

    def rent_comps(self, zip_code: str, beds: Optional[int] = None) -> list[RentComp]:
        sql, args = "SELECT * FROM rent_comps WHERE zip=?", [zip_code]
        if beds is not None:
            sql += " AND beds=?"
            args.append(beds)
        return [
            RentComp(source=r["source"], zip=r["zip"], beds=r["beds"], rent=r["rent"],
                     sqft=r["sqft"], address=r["address"] or "", baths=r["baths"])
            for r in self.conn.execute(sql, args)
        ]

    def set_fmr(self, area_key: str, beds: int, rent: float, year: Optional[int], source: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO fair_market_rents VALUES (?,?,?,?,?)",
            (area_key, year, beds, rent, source),
        )
        self.conn.commit()

    def fmr(self, area_key: str) -> dict[int, float]:
        rows = self.conn.execute(
            "SELECT beds, rent FROM fair_market_rents WHERE area_key=?", (area_key,)
        )
        return {r["beds"]: r["rent"] for r in rows}

    # ---- analyses --------------------------------------------------------
    def save_analysis(self, a: Analysis):
        self.conn.execute(
            "INSERT OR REPLACE INTO analyses VALUES (?,?,?,?,?)",
            (a.listing_key, _now(), a.score, a.grade, json.dumps(asdict(a))),
        )
        self.conn.commit()

    @staticmethod
    def _load_analysis(data: str) -> Analysis:
        d = json.loads(data)
        d["condition"] = ConditionReport(**d["condition"])
        return Analysis(**d)

    def top_analyses(self, limit: int = 25, min_score: float = 0) -> list[tuple[Analysis, Listing]]:
        rows = self.conn.execute(
            """SELECT a.data, l.* FROM analyses a JOIN listings l ON l.key = a.listing_key
               WHERE a.score >= ? ORDER BY a.score DESC LIMIT ?""",
            (min_score, limit),
        ).fetchall()
        return [(self._load_analysis(r["data"]), self._row_to_listing(r)) for r in rows]

    def get_analysis(self, key: str) -> Optional[Analysis]:
        r = self.conn.execute("SELECT data FROM analyses WHERE listing_key=?", (key,)).fetchone()
        return self._load_analysis(r["data"]) if r else None

    def stats(self) -> dict:
        q = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "listings": q("SELECT COUNT(*) FROM listings"),
            "rent_comps": q("SELECT COUNT(*) FROM rent_comps"),
            "fmr_areas": q("SELECT COUNT(DISTINCT area_key) FROM fair_market_rents"),
            "analyzed": q("SELECT COUNT(*) FROM analyses"),
        }
