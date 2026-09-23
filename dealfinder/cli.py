"""Command line interface. Run `python -m dealfinder --help`."""
from __future__ import annotations

import argparse
import csv
import logging
import sys

from .analysis.underwrite import analyze_all
from .config import Settings, load_settings
from .db import Database
from .sources.base import SourceError

log = logging.getLogger("dealfinder")


def _zips(args, settings: Settings) -> list[str]:
    zips = args.zip or settings.markets
    if not zips:
        sys.exit("No zip codes: pass --zip 12345 or set `markets` in config.toml")
    return zips


def cmd_demo(args, settings, db):
    from .sources.demo import load_demo
    print("Loaded demo data:", load_demo(db))
    print("Next: python -m dealfinder analyze && python -m dealfinder deals")


def cmd_fetch(args, settings: Settings, db: Database):
    zips = _zips(args, settings)
    wanted = args.source or ["reso", "rentcast", "hud"]
    for name in wanted:
        try:
            if name == "reso":
                from .sources.reso import ResoSource
                opts = settings.sources.get("reso", {})
                src = ResoSource(opts.get("base_url", ""), settings.reso_token,
                                 opts.get("extra_filter", ""))
            elif name == "rentcast":
                from .sources.rentcast import RentCastSource
                src = RentCastSource(settings.rentcast_api_key)
            elif name == "hud":
                from .sources.hud import HudFmrSource
                n = HudFmrSource(settings.hud_api_token,
                                 settings.sources.get("hud", {}).get("year")).load(db, zips)
                print(f"[hud] stored {n} Section 8 fair market rents")
                continue
            else:
                print(f"unknown source {name!r}")
                continue
            counts = {"new": 0, "price_change": 0, "seen": 0}
            for listing in src.fetch_listings(zips):
                counts[db.upsert_listing(listing)] += 1
            rents = db.add_rent_comps(src.fetch_rents(zips))
            print(f"[{name}] listings: {counts['new']} new, {counts['price_change']} price changes, "
                  f"{counts['seen']} unchanged; {rents} new rent comps")
        except SourceError as e:
            print(f"[{name}] skipped: {e}")


def cmd_import_listings(args, settings, db):
    from .sources.csv_import import read_listings_csv
    counts = {"new": 0, "price_change": 0, "seen": 0}
    for listing in read_listings_csv(args.path, args.source_name):
        counts[db.upsert_listing(listing)] += 1
    print(f"Imported listings from {args.path}: {counts}")


def cmd_import_rents(args, settings, db):
    from .sources.csv_import import read_rents_csv
    print(f"Imported {db.add_rent_comps(read_rents_csv(args.path, args.source_name))} rent comps")


def cmd_import_fmr(args, settings, db):
    from .sources.hud import import_fmr_csv
    print(f"Imported {import_fmr_csv(db, args.path)} Section 8 rent rows")


def cmd_analyze(args, settings, db):
    done, skipped = analyze_all(db, settings, args.zip[0] if args.zip else None)
    print(f"Analyzed {done} listings ({skipped} skipped: filtered out or no rent data)")


def _pct(x):
    return f"{x * 100:5.1f}%" if x is not None else "   - "


def cmd_deals(args, settings, db):
    rows = db.top_analyses(1_000_000, args.min_score)
    if args.distressed:
        rows = [(a, l) for a, l in rows if a.condition.distress_signals]
    if args.cash_only is not None:
        rows = [(a, l) for a, l in rows if a.condition.cash_only == args.cash_only]
    rows = rows[:args.limit]
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["score", "grade", "address", "zip", "price", "max_offer", "rent", "rent_basis",
                        "rehab", "cap_rate", "cash_on_cash", "cash_flow_mo", "condition", "cash_only",
                        "distress", "url", "key"])
            for a, l in rows:
                w.writerow([a.score, a.grade, l.address, l.zip, l.price, a.max_offer, a.rent_used,
                            a.rent_basis, a.rehab_cost, a.cap_rate, a.cash_on_cash, a.cash_flow_monthly,
                            a.condition.condition, a.condition.cash_only,
                            "; ".join(a.condition.distress_signals), l.url, l.key])
        print(f"Wrote {len(rows)} deals to {args.csv}")
        return
    if not rows:
        print("No analyzed deals yet. Run `fetch`/`import-*`, then `analyze`.")
        return
    print(f"{'SCORE':>5} {'G':1} {'PRICE':>9} {'MAX OFFER':>9} {'RENT/MO':>8} {'REHAB':>8} "
          f"{'CAP':>6} {'CoC':>6} {'CF/MO':>7} {'COND':9} {'$':4} ADDRESS")
    for a, l in rows:
        print(f"{a.score:5.1f} {a.grade:1} {l.price:9,.0f} {a.max_offer:9,.0f} {a.rent_used:8,.0f} "
              f"{a.rehab_cost:8,.0f} {_pct(a.cap_rate)} {_pct(a.cash_on_cash)} {a.cash_flow_monthly:7,.0f} "
              f"{a.condition.condition:9} {'CASH' if a.financing == 'cash' else 'loan':4} "
              f"{l.address}, {l.zip}  [{l.key}]")


def cmd_show(args, settings, db):
    l = db.get_listing(args.key)
    a = db.get_analysis(args.key)
    if not l:
        sys.exit(f"No listing {args.key}")
    print(f"{l.address}, {l.city} {l.state} {l.zip}  ({l.key})")
    print(f"  {l.property_type}, {l.units} unit(s), {l.beds}bd/{l.baths}ba, {l.sqft} sqft, built {l.year_built}")
    print(f"  Asking ${l.price:,.0f}  | sale type: {l.listing_type} | DOM: {l.days_on_market}")
    if l.url:
        print(f"  {l.url}")
    print(f"  Price history: {db.price_history(l.key)}")
    if not a:
        print("  (not analyzed yet)")
        return
    c = a.condition
    print(f"\nScore {a.score} ({a.grade}), financing assumed: {a.financing}")
    print(f"  Rent: market ${a.market_rent:,.0f}/mo, Section 8 "
          f"{'$%s/mo' % format(a.section8_rent, ',.0f') if a.section8_rent else 'n/a'}, "
          f"using ${a.rent_used:,.0f} ({a.rent_basis})")
    print(f"  Rehab ${a.rehab_cost:,.0f}  | all-in ${a.all_in_cost:,.0f}  | NOI ${a.noi:,.0f}/yr")
    print(f"  Cap {_pct(a.cap_rate)} | CoC {_pct(a.cash_on_cash)} | DSCR {a.dscr} | "
          f"cash flow ${a.cash_flow_monthly:,.0f}/mo | rent/all-in {_pct(a.one_percent_ratio)}")
    print(f"  Max offer for {settings.underwriting.target_cap_rate:.0%} cap: ${a.max_offer:,.0f} "
          f"({a.discount_to_max_offer:+.0%} vs asking)")
    print(f"\nCondition ({c.method}): {c.summary}")
    for label, items in (("Repairs", c.repair_items), ("Positives", c.positive_signals),
                         ("Distress", c.distress_signals), ("Financing", c.financing_notes),
                         ("Notes", a.notes)):
        if items:
            print(f"  {label}: {'; '.join(items)}")
    if args.description and l.description:
        print(f"\nDescription:\n{l.description}")


def cmd_stats(args, settings, db):
    for k, v in db.stats().items():
        print(f"{k:12} {v}")


def cmd_serve(args, settings, db):
    from .web.server import serve
    serve(db, settings, args.host, args.port)


def main(argv=None):
    p = argparse.ArgumentParser(prog="dealfinder", description="Find rental deals priced below what their rents support.")
    p.add_argument("--config", help="path to config.toml (default ./config.toml)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo", help="load made-up demo data to try things out")

    f = sub.add_parser("fetch", help="pull listings, rent comps and Section 8 rents from APIs")
    f.add_argument("--source", action="append", choices=["reso", "rentcast", "hud"])
    f.add_argument("--zip", action="append")

    for name, helptext in (("import-listings", "import for-sale listings from a CSV export"),
                           ("import-rents", "import rent comps from a CSV")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("path")
        s.add_argument("--source-name", help="label for this source (default csv:<filename>)")
    s = sub.add_parser("import-fmr", help="import Section 8 payment standards CSV (zip,beds,rent)")
    s.add_argument("path")

    a = sub.add_parser("analyze", help="(re)underwrite every active listing")
    a.add_argument("--zip", action="append")

    d = sub.add_parser("deals", help="list the best deals")
    d.add_argument("--limit", type=int, default=25)
    d.add_argument("--min-score", type=float, default=0)
    d.add_argument("--distressed", action="store_true", help="only listings with distress signals")
    d.add_argument("--cash-only", dest="cash_only", action="store_true", default=None)
    d.add_argument("--financeable", dest="cash_only", action="store_false")
    d.add_argument("--csv", help="write results to a CSV file instead of printing")

    s = sub.add_parser("show", help="full breakdown for one listing")
    s.add_argument("key")
    s.add_argument("--description", action="store_true")

    sub.add_parser("stats", help="database counts")

    w = sub.add_parser("serve", help="run the web dashboard")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    settings = load_settings(args.config)
    db = Database(settings.db_path)
    try:
        {
            "demo": cmd_demo, "fetch": cmd_fetch, "import-listings": cmd_import_listings,
            "import-rents": cmd_import_rents, "import-fmr": cmd_import_fmr,
            "analyze": cmd_analyze, "deals": cmd_deals, "show": cmd_show,
            "stats": cmd_stats, "serve": cmd_serve,
        }[args.cmd](args, settings, db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
