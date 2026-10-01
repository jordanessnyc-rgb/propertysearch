# propertysearch — rental deal finder

Your own database of properties for sale, local market rents and Section 8 rents. Every
listing is underwritten against what it would actually rent for, so the ones priced below
what their rents support rise to the top.

- **Collects listings** from the MLS (RESO Web API), RentCast, and any CSV export (auction
  sites, wholesaler lists, PropStream/BatchLeads, county foreclosure lists, your own sheet).
- **Tracks rents**: market rent comps (MLS lease listings, RentCast rentals, CSV) and HUD
  Section 8 Fair Market Rents / Small Area FMRs, by zip code and bedroom count.
- **Reads the listing** to judge condition (turnkey → gut), estimate rehab, and pick up
  distress signals (REO, foreclosure, short sale, auction, probate, as-is, price cuts, long
  days on market) and cash-only terms. Optionally Claude reads each listing instead.
- **Underwrites every deal**: NOI, cap rate, cash flow, cash-on-cash, DSCR, the 1% rule, and a
  **max offer** (the price that hits your target cap rate after rehab). Cash-only listings
  are underwritten as cash purchases; the rest with your loan terms.
- **Scores and ranks** everything 0–100 (A–F), in the terminal or a local web dashboard.

## Quick start (no API keys needed)

Requires Python 3.11+. No packages to install for the core app.

```bash
python -m dealfinder demo        # load fictional demo data
python -m dealfinder analyze     # underwrite every listing
python -m dealfinder deals       # ranked table in the terminal
python -m dealfinder serve       # dashboard at http://127.0.0.1:8000
```

When you're ready for real data, delete `data/dealfinder.db` so the demo records go away.

## Using real data

1. `cp config.example.toml config.toml` and set your `markets` (zip codes), price limit and
   underwriting assumptions (down payment, rate, expenses, target cap rate, …).
2. Set whichever API keys you have as environment variables:

   | Source | What it gives you | How to get it | Variable |
   |---|---|---|---|
   | MLS (RESO Web API) | Active listings with full remarks, sale conditions (REO, short sale, auction, probate), financing terms, and lease listings as rent comps | Through your MLS membership / broker, or a vendor like Bridge, Trestle, Spark or MLS Grid | `RESO_ACCESS_TOKEN` and `sources.reso.base_url` in config |
   | RentCast | Nationwide sale listings (incl. foreclosures) and rental listings | rentcast.io/api (free tier) | `RENTCAST_API_KEY` |
   | HUD | Section 8 Fair Market Rents, zip-level where HUD publishes Small Area FMRs | huduser.gov/hudapi/public/register (free) | `HUD_API_TOKEN` |

3. Pull data and analyze:

   ```bash
   python -m dealfinder fetch                     # all configured sources, all markets
   python -m dealfinder fetch --source hud --zip 44105
   python -m dealfinder analyze
   python -m dealfinder deals --min-score 60
   ```

   Run `fetch` + `analyze` on a schedule (cron / Task Scheduler) to keep the database fresh.
   Price drops are tracked between runs and count as a distress signal.

### Sites without an API (CSV import)

Any site or tool that lets you export a spreadsheet can feed the database. Column names are
matched loosely (`List Price`, `Asking`, `Opening Bid` all work as price). See `templates/`.

```bash
python -m dealfinder import-listings auction_export.csv --source-name auction.com
python -m dealfinder import-rents my_rent_roll.csv
python -m dealfinder import-fmr housing_authority_payment_standards.csv   # zip,beds,rent
```

If your local housing authority publishes its own payment standards, importing them with
`import-fmr` is more accurate than HUD's FMR.

## Commands

| Command | What it does |
|---|---|
| `deals [--distressed] [--cash-only \| --financeable] [--min-score N] [--csv out.csv]` | Ranked deals, filterable; export to CSV |
| `show <key>` | Full breakdown for one listing (`--description` prints the listing text) |
| `serve` | Web dashboard with filters, sorting and a detail panel |
| `stats` | Counts of listings, comps, Section 8 areas, analyses |

## How a deal is scored

- **Rent**: median of same-bedroom comps in the zip (outliers trimmed); scaled from nearby
  bedroom counts when comps are thin. Section 8 = FMR × your payment-standard %. With
  `rent_basis = "best"`, each unit uses the higher of the two.
- **Rehab**: condition level × $/sqft (`REHAB_PER_SQFT` in `dealfinder/analysis/condition.py`,
  tune to your contractors' prices) plus contingency.
- **Expenses**: taxes (from the listing, or estimated), insurance, vacancy, management,
  maintenance, capex, HOA, owner-paid utilities.
- **Score** (0–100): cap rate vs target (35), cash-on-cash vs target (25), asking vs max offer
  (15), DSCR (10), rent ÷ all-in cost (15); minus penalties for thin rent data, no interior
  access, and gut rehabs.

## Letting Claude read listings

`pip install anthropic`, set `ANTHROPIC_API_KEY`, and set `use_llm_reader = true`. Claude then
judges condition, repairs, distress and cash-only terms from the listing text, which catches
wording the keyword rules miss. Costs apply per listing analyzed; it falls back to the
keyword reader on any error.

## Adding another source

Write a class with `fetch_listings(zips)` and/or `fetch_rents(zips)` that yields `Listing` /
`RentComp` objects (see `dealfinder/sources/rentcast.py` for a short example) and register it
in `cmd_fetch` in `dealfinder/cli.py`. Use official APIs, data feeds or exports: most listing
portals (Zillow, Realtor.com, Redfin) prohibit scraping in their terms of use, so they aren't
built in.

## Tests

```bash
python -m unittest discover -s tests
```

## Caveats

Estimates are a screening tool, not an appraisal: verify rents, taxes, insurance and rehab
scope before making an offer.
