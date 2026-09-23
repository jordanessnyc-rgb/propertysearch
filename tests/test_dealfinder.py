import os
import tempfile
import unittest

from dealfinder.analysis.condition import read_listing
from dealfinder.analysis.rents import market_rent, section8_rent
from dealfinder.analysis.underwrite import analyze_listing, monthly_payment
from dealfinder.config import Settings
from dealfinder.db import Database
from dealfinder.models import Listing, RentComp
from dealfinder.sources.base import normalize_listing_type
from dealfinder.sources.csv_import import read_listings_csv
from dealfinder.sources.hud import parse_fmr_payload


def make_listing(desc="", **kw):
    base = dict(source="t", source_id="1", address="1 Main St", zip="11111", price=100000,
                beds=3, baths=1, sqft=1200, description=desc)
    base.update(kw)
    return Listing(**base)


class ConditionReaderTests(unittest.TestCase):
    def test_turnkey(self):
        r = read_listing(make_listing("Move-in ready, fully renovated with new roof and new HVAC."))
        self.assertEqual(r.condition, "turnkey")
        self.assertIn("new roof", r.positive_signals)
        self.assertFalse(r.cash_only)

    def test_needs_new_roof_is_a_repair_not_a_positive(self):
        r = read_listing(make_listing("Needs new roof. Priced to sell."))
        self.assertEqual(r.condition, "heavy")
        self.assertIn("roof", r.repair_items)
        self.assertNotIn("new roof", r.positive_signals)

    def test_updated_is_not_dated(self):
        r = read_listing(make_listing("Updated electrical panel 2021. Tenants in place, fully leased."))
        self.assertNotIn("dated / needs updating", r.repair_items)
        self.assertEqual(r.condition, "turnkey")

    def test_gut_and_cash_only(self):
        r = read_listing(make_listing("Fire damaged, needs complete gut. Cash buyers only."))
        self.assertEqual(r.condition, "gut")
        self.assertTrue(r.cash_only)

    def test_distress_signals(self):
        r = read_listing(make_listing("Bank owned, sold as-is. Motivated seller! Price reduced.",
                                      days_on_market=120), price_drops=2)
        for s in ("bank owned / REO", "sold as-is", "motivated seller", "price reduced",
                  "120 days on market", "2 price drop(s) tracked"):
            self.assertIn(s, r.distress_signals)

    def test_auction_implies_cash_and_no_access_implies_rehab(self):
        r = read_listing(make_listing("Online auction. No interior access.", listing_type="auction"))
        self.assertTrue(r.cash_only)
        self.assertEqual(r.condition, "moderate")

    def test_unfinanceable_condition_note(self):
        r = read_listing(make_listing("Mold and water damage throughout."))
        self.assertEqual(r.condition, "heavy")
        self.assertTrue(any("rehab loan" in n for n in r.financing_notes))


class RentTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.add_rent_comps(RentComp("t", "11111", 2, r, address=f"a{i}")
                               for i, r in enumerate([1000, 1100, 1200, 1050, 5000]))
        self.db.set_fmr("11111", 2, 1300, 2026, "fmr")
        self.db.set_fmr("11111", 4, 2000, 2026, "fmr")

    def test_market_rent_median(self):
        est = market_rent(self.db, "11111", 2)
        self.assertEqual(est.rent, 1100)
        self.assertEqual(est.comps, 5)

    def test_market_rent_scales_from_nearest_bedroom_count(self):
        est = market_rent(self.db, "11111", 3)
        self.assertGreater(est.rent, 1100)
        self.assertIn("scaled", est.method)

    def test_no_comps(self):
        self.assertIsNone(market_rent(self.db, "99999", 2).rent)

    def test_section8(self):
        self.assertEqual(section8_rent(self.db, "11111", 2), 1300)
        self.assertEqual(section8_rent(self.db, "11111", 2, 1.1), 1430)
        self.assertEqual(section8_rent(self.db, "11111", 5), 2300)  # +15% per bedroom over 4
        self.assertIsNone(section8_rent(self.db, "99999", 2))


class UnderwritingTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.add_rent_comps(RentComp("t", "11111", 3, 1500, address=f"a{i}") for i in range(5))
        self.settings = Settings()

    def test_monthly_payment(self):
        self.assertAlmostEqual(monthly_payment(100000, 0.06, 30), 599.55, places=2)

    def test_cheap_turnkey_beats_expensive_one(self):
        cheap = make_listing("Move-in ready, fully renovated.", source_id="c", price=90000)
        pricey = make_listing("Move-in ready, fully renovated.", source_id="p", price=250000)
        a = analyze_listing(self.db, cheap, self.settings)
        b = analyze_listing(self.db, pricey, self.settings)
        self.assertGreater(a.score, b.score)
        self.assertGreater(a.cap_rate, b.cap_rate)
        self.assertEqual(a.financing, "loan")

    def test_max_offer_hits_target_cap(self):
        l = make_listing("Move-in ready, fully renovated.", price=100000, annual_taxes=1500)
        a = analyze_listing(self.db, l, self.settings)
        uw = self.settings.underwriting
        implied_cap = a.noi / (a.max_offer * (1 + uw.closing_cost_pct) + a.rehab_cost)
        self.assertAlmostEqual(implied_cap, uw.target_cap_rate, places=2)

    def test_cash_only_listing_underwritten_as_cash(self):
        a = analyze_listing(self.db, make_listing("Cash only. Needs work."), self.settings)
        self.assertEqual(a.financing, "cash")
        self.assertIsNone(a.dscr)
        self.assertAlmostEqual(a.cash_on_cash, a.cap_rate, places=3)

    def test_no_rent_data_returns_none(self):
        self.assertIsNone(analyze_listing(self.db, make_listing(zip="99999"), self.settings))


class SourceParsingTests(unittest.TestCase):
    def test_listing_type(self):
        self.assertEqual(normalize_listing_type("Bank Owned"), "reo")
        self.assertEqual(normalize_listing_type("Short Sale"), "short_sale")
        self.assertEqual(normalize_listing_type("Probate Listing"), "probate")
        self.assertEqual(normalize_listing_type("Standard"), "standard")

    def test_hud_safmr_payload(self):
        payload = {"data": {"basicdata": [
            {"zip_code": "11111", "Efficiency": 900, "One-Bedroom": 1000, "Two-Bedroom": 1200,
             "Three-Bedroom": 1500, "Four-Bedroom": 1700, "year": "2026"},
            {"zip_code": "22222", "Two-Bedroom": 999}]}}
        rents, kind, year = parse_fmr_payload(payload, "11111")
        self.assertEqual((rents[2], kind, year), (1200, "safmr", 2026))

    def test_hud_county_payload(self):
        payload = {"data": {"year": 2026, "basicdata": {"zip_code": "MSA level", "Two-Bedroom": 1111}}}
        rents, kind, _ = parse_fmr_payload(payload, "33333")
        self.assertEqual((rents[2], kind), (1111, "fmr"))

    def test_csv_import_with_loose_headers(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write("Property Address,Zip Code,List Price,Bedrooms,Remarks,Sale Type\n")
            fh.write('12 Oak St,11111,"$85,000",3,Needs work,Foreclosure\n')
        try:
            [l] = list(read_listings_csv(fh.name))
            self.assertEqual((l.address, l.zip, l.price, l.beds, l.listing_type),
                             ("12 Oak St", "11111", 85000.0, 3, "foreclosure"))
        finally:
            os.unlink(fh.name)


class DatabaseTests(unittest.TestCase):
    def test_price_change_tracking(self):
        db = Database(":memory:")
        self.assertEqual(db.upsert_listing(make_listing(price=100000)), "new")
        self.assertEqual(db.upsert_listing(make_listing(price=100000)), "seen")
        self.assertEqual(db.upsert_listing(make_listing(price=90000)), "price_change")
        self.assertEqual([p["price"] for p in db.price_history("t:1")], [100000, 90000])


if __name__ == "__main__":
    unittest.main()
