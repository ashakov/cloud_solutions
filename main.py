"""
CLI entry point for the Phuket Invest-Auditor scraper pipeline.

Usage:
    python main.py scrape                          # full run (default districts)
    python main.py scrape --district bang-tao --type condo
    python main.py scrape --source fazwaz
    python main.py benchmarks                      # recalculate benchmarks only
    python main.py status                          # show last 10 scraper runs
"""
import argparse
import asyncio
import sys

from utils.logging_setup import setup_logging
from scrapers.runner import run_all, ALL_PARSERS, DEFAULT_DISTRICTS, DEFAULT_PROP_TYPES
from parsers.fazwaz import FazWazParser
from parsers.dotproperty import DotPropertyParser

SOURCE_MAP = {
    "fazwaz": FazWazParser,
    "dotproperty": DotPropertyParser,
}


async def cmd_scrape(args: argparse.Namespace) -> None:
    districts = [args.district] if args.district else DEFAULT_DISTRICTS
    prop_types = [args.type] if args.type else DEFAULT_PROP_TYPES
    parsers = [SOURCE_MAP[args.source]] if args.source else ALL_PARSERS
    await run_all(districts=districts, property_types=prop_types, parsers=parsers)


async def cmd_benchmarks() -> None:
    from db.database import init_db, AsyncSessionLocal
    from parsers.normalizer import rebuild_benchmarks

    await init_db()
    async with AsyncSessionLocal() as session:
        n = await rebuild_benchmarks(session)
        print(f"Benchmarks rebuilt: {n} rows.")


def cmd_kpi(args: argparse.Namespace) -> None:
    from core.kpi_engine import KPIParams, all_scenarios
    from core.seasonality import implied_daily_rates

    daily_high = args.daily_high
    daily_peak = args.daily_peak or round(daily_high * 1.55, 0)
    daily_low  = args.daily_low  or round(daily_high * 0.55, 0)
    daily_shoulder = round(daily_high * 0.75, 0)

    params = KPIParams(
        purchase_price_thb=args.price,
        area_sqm=args.sqm,
        furniture_cost_thb=args.furniture,
        daily_peak_thb=daily_peak,
        daily_high_thb=daily_high,
        daily_shoulder_thb=daily_shoulder,
        daily_low_thb=daily_low,
        management_fee_pct=args.mgmt_fee,
    )

    results = all_scenarios(params)

    print(f"\n{'='*55}")
    print(f"  KPI ANALYSIS  —  ฿{args.price:,.0f}  |  {args.sqm:.0f} m²")
    print(f"  Nightly rates: peak ฿{daily_peak:,.0f}  high ฿{daily_high:,.0f}"
          f"  shoulder ฿{daily_shoulder:,.0f}  low ฿{daily_low:,.0f}")
    print(f"{'='*55}")
    for scenario, r in results.items():
        print(f"\n── {scenario.upper()} ──")
        print(r.summary())
    print()


async def cmd_locations(args: argparse.Namespace) -> None:
    """Show unique raw subdistrict/location values from DB — helps fix district mapping."""
    from db.database import init_db, AsyncSessionLocal
    from db.models import Property
    from sqlalchemy import select, func

    await init_db()
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Property.district, Property.subdistrict, func.count())
            .group_by(Property.district, Property.subdistrict)
            .order_by(Property.district, func.count().desc())
        )).all()
        print(f"\n{'District':<20} {'Count':>6}  Raw location text")
        print("-" * 80)
        for district, subdistrict, cnt in rows:
            print(f"{(district or ''):<20} {cnt:>6}  {subdistrict or ''}")


async def cmd_inspect(args: argparse.Namespace) -> None:
    from db.database import init_db, AsyncSessionLocal
    from db.models import Property, DistrictBenchmark
    from sqlalchemy import select, func

    await init_db()
    async with AsyncSessionLocal() as session:

        # ── Summary ───────────────────────────────────────────────────────
        total = (await session.execute(select(func.count()).select_from(Property))).scalar()
        print(f"\n{'='*65}")
        print(f"  TOTAL PROPERTIES IN DB: {total}")
        print(f"{'='*65}")

        # ── By source ─────────────────────────────────────────────────────
        rows = (await session.execute(
            select(Property.source, func.count())
            .group_by(Property.source)
        )).all()
        print("\n  By source:")
        for source, cnt in rows:
            print(f"    {source:<16} {cnt:>6}")

        # ── By district ───────────────────────────────────────────────────
        rows = (await session.execute(
            select(Property.district, func.count())
            .group_by(Property.district)
            .order_by(func.count().desc())
        )).all()
        print("\n  By district:")
        for district, cnt in rows:
            print(f"    {(district or 'N/A'):<20} {cnt:>6}")

        # ── Field fill-rate ───────────────────────────────────────────────
        checks = [
            ("price_thb",         Property.price_thb),
            ("area_sqm",          Property.area_sqm),
            ("price_per_sqm_thb", Property.price_per_sqm_thb),
            ("bedrooms",          Property.bedrooms),
            ("ownership_type≠?",  Property.ownership_type != "unknown"),
            ("has_pool",          Property.has_pool),
            ("is_off_plan",       Property.is_off_plan),
            ("rental_program",    Property.rental_program),
            ("has_hotel_license", Property.has_hotel_license),
        ]
        print(f"\n  Field fill-rate (out of {total}):")
        for label, col in checks:
            n = (await session.execute(
                select(func.count()).select_from(Property).where(col != None)  # noqa: E711
            )).scalar()
            bar = "█" * int(20 * n / total) if total else ""
            print(f"    {label:<24} {n:>5}  {bar} {100*n//total if total else 0}%")

        # ── Sample listings ───────────────────────────────────────────────
        samples = (await session.execute(
            select(Property)
            .where(Property.price_thb != None, Property.area_sqm != None)
            .order_by(Property.scraped_at.desc())
            .limit(args.n)
        )).scalars().all()

        print(f"\n  Last {args.n} listings with price+area:")
        print(f"  {'Project':<30} {'District':<14} {'฿/m²':>8} {'Price THB':>12} {'m²':>6} {'Beds':>4} {'Own':<10}")
        print(f"  {'-'*90}")
        for p in samples:
            print(
                f"  {(p.project_name or 'N/A')[:29]:<30} "
                f"{(p.district or '')[:13]:<14} "
                f"{p.price_per_sqm_thb:>8,.0f} "
                f"{p.price_thb:>12,.0f} "
                f"{p.area_sqm:>6.0f} "
                f"{(p.bedrooms or 0):>4} "
                f"{(p.ownership_type or ''):<10}"
            )

        # ── Benchmarks ────────────────────────────────────────────────────
        bmarks = (await session.execute(
            select(DistrictBenchmark)
            .where(DistrictBenchmark.ownership_type == "all")
            .order_by(DistrictBenchmark.median_price_per_sqm.desc())
        )).scalars().all()
        if bmarks:
            print(f"\n  District benchmarks (condo+villa, all ownership):")
            print(f"  {'District':<16} {'Type':<8} {'Median ฿/m²':>12} {'P25':>10} {'P75':>10} {'N':>5}")
            print(f"  {'-'*65}")
            for b in bmarks:
                print(
                    f"  {b.district:<16} {b.property_type:<8} "
                    f"{b.median_price_per_sqm:>12,.0f} "
                    f"{b.p25_price_per_sqm:>10,.0f} "
                    f"{b.p75_price_per_sqm:>10,.0f} "
                    f"{b.sample_count:>5}"
                )
        print()

    from db.database import init_db, AsyncSessionLocal
    from db.models import ScraperRun
    from sqlalchemy import select

    await init_db()
    async with AsyncSessionLocal() as session:
        stmt = (
            select(ScraperRun)
            .order_by(ScraperRun.started_at.desc())
            .limit(20)
        )
        runs = (await session.execute(stmt)).scalars().all()
        if not runs:
            print("No scraper runs found.")
            return
        print(f"{'ID':>4}  {'Source':<12} {'District':<16} {'Type':<12} {'Status':<8} {'Fetched':>7} {'New':>6}")
        print("-" * 75)
        for r in runs:
            print(
                f"{r.id:>4}  {r.source:<12} {(r.district or ''):<16} "
                f"{(r.property_type or ''):<12} {r.status:<8} "
                f"{(r.records_fetched or 0):>7} {(r.records_new or 0):>6}"
            )


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Phuket Invest-Auditor data pipeline")
    sub = parser.add_subparsers(dest="command")

    # scrape
    p_scrape = sub.add_parser("scrape", help="Run scrapers to collect property data")
    p_scrape.add_argument("--district", help="Single district slug (e.g. bang-tao)")
    p_scrape.add_argument("--type", dest="type", help="Property type: condo|villa|house")
    p_scrape.add_argument("--source", choices=list(SOURCE_MAP), help="Single source to run")

    # benchmarks
    sub.add_parser("benchmarks", help="Recalculate district benchmarks from existing data")

    # status
    sub.add_parser("status", help="Show recent scraper run history")

    # inspect
    p_inspect = sub.add_parser("inspect", help="Show DB contents: fill-rate, samples, benchmarks")
    p_inspect.add_argument("--n", type=int, default=10, help="Number of sample listings to show")

    # locations
    sub.add_parser("locations", help="Show unique raw location texts — helps fix district mapping")

    # kpi
    p_kpi = sub.add_parser("kpi", help="Calculate investment KPIs for a property")
    p_kpi.add_argument("--price",      type=float, required=True, help="Purchase price THB")
    p_kpi.add_argument("--sqm",        type=float, required=True, help="Area m²")
    p_kpi.add_argument("--daily-high", type=float, required=True, dest="daily_high",
                       help="Comparable nightly rate in high season (THB)")
    p_kpi.add_argument("--daily-peak", type=float, dest="daily_peak",
                       help="Peak nightly rate (default: high × 1.55)")
    p_kpi.add_argument("--daily-low",  type=float, dest="daily_low",
                       help="Low/monsoon nightly rate (default: high × 0.55)")
    p_kpi.add_argument("--furniture",  type=float, default=0.0,
                       help="Furniture + fitout cost THB (default: 0)")
    p_kpi.add_argument("--mgmt-fee",   type=float, dest="mgmt_fee", default=None,
                       help="Management fee fraction 0–1 (default: 0.25)")

    # seed
    p_seed = sub.add_parser("seed", help="Populate DB with synthetic Phuket data (no internet needed)")
    p_seed.add_argument("--count", type=int, default=40, help="Listings per district/type/ownership combo")

    args = parser.parse_args()

    if args.command == "scrape":
        asyncio.run(cmd_scrape(args))
    elif args.command == "benchmarks":
        asyncio.run(cmd_benchmarks())
    elif args.command == "status":
        asyncio.run(cmd_status())
    elif args.command == "kpi":
        cmd_kpi(args)
    elif args.command == "seed":
        from scrapers.seeder import seed
        asyncio.run(seed(listings_per_combo=args.count))
    elif args.command == "inspect":
        asyncio.run(cmd_inspect(args))
    elif args.command == "locations":
        asyncio.run(cmd_locations(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
