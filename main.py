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


async def cmd_status() -> None:
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

    args = parser.parse_args()

    if args.command == "scrape":
        asyncio.run(cmd_scrape(args))
    elif args.command == "benchmarks":
        asyncio.run(cmd_benchmarks())
    elif args.command == "status":
        asyncio.run(cmd_status())
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
