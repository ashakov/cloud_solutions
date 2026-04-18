"""
Scraper orchestrator — runs all parsers in sequence per district/type combo,
persists results, and rebuilds benchmarks.
"""
import asyncio
import logging
from datetime import datetime
from typing import Type

from sqlalchemy.ext.asyncio import AsyncSession

from config import PHUKET_DISTRICTS, PROPERTY_TYPES, SCRAPER_MAX_PAGES
from db.database import AsyncSessionLocal, init_db
from db.models import ScraperRun
from parsers.base import BaseParser
from parsers.fazwaz import FazWazParser
from parsers.dotproperty import DotPropertyParser
from parsers.normalizer import upsert_listing, rebuild_benchmarks

logger = logging.getLogger(__name__)

ALL_PARSERS: list[Type[BaseParser]] = [FazWazParser, DotPropertyParser]

# For MVP, only scrape the highest-demand districts and condos/villas
DEFAULT_DISTRICTS = [
    "bang-tao", "kamala", "surin", "layan",
    "rawai", "nai-harn", "patong", "cherng-talay",
]
DEFAULT_PROP_TYPES = ["condo", "villa"]


async def run_parser(
    parser_cls: Type[BaseParser],
    district: str,
    property_type: str,
    session: AsyncSession,
) -> ScraperRun:
    run = ScraperRun(
        source=parser_cls.SOURCE,
        district=district,
        property_type=property_type,
        started_at=datetime.utcnow(),
        status="running",
    )
    session.add(run)
    await session.commit()

    try:
        new_count = updated_count = fetched = 0
        parser = parser_cls()

        async for raw_listing in parser.scrape(district, property_type, SCRAPER_MAX_PAGES):
            fetched += 1
            is_new, is_updated = await upsert_listing(session, raw_listing)
            if is_new:
                new_count += 1
            elif is_updated:
                updated_count += 1

            # Commit in small batches to avoid huge transactions
            if fetched % 50 == 0:
                await session.commit()
                logger.info(
                    "[%s] %s/%s — fetched=%d new=%d updated=%d",
                    parser_cls.SOURCE, district, property_type,
                    fetched, new_count, updated_count,
                )

        await session.commit()
        run.status = "done"
        run.records_fetched = fetched
        run.records_new = new_count
        run.records_updated = updated_count
        run.completed_at = datetime.utcnow()
        logger.info(
            "[%s] DONE %s/%s — fetched=%d new=%d updated=%d",
            parser_cls.SOURCE, district, property_type,
            fetched, new_count, updated_count,
        )

    except Exception as exc:
        logger.exception("[%s] FAILED %s/%s: %s", parser_cls.SOURCE, district, property_type, exc)
        run.status = "error"
        run.error_message = str(exc)[:2000]
        run.completed_at = datetime.utcnow()

    await session.commit()
    return run


async def run_all(
    districts: list[str] | None = None,
    property_types: list[str] | None = None,
    parsers: list[Type[BaseParser]] | None = None,
) -> None:
    """Main entry point: scrape all combos then rebuild benchmarks."""
    await init_db()

    districts = districts or DEFAULT_DISTRICTS
    property_types = property_types or DEFAULT_PROP_TYPES
    parsers = parsers or ALL_PARSERS

    total_runs = len(parsers) * len(districts) * len(property_types)
    logger.info(
        "Starting scrape: %d parsers × %d districts × %d types = %d runs",
        len(parsers), len(districts), len(property_types), total_runs,
    )

    async with AsyncSessionLocal() as session:
        for parser_cls in parsers:
            for district in districts:
                for prop_type in property_types:
                    await run_parser(parser_cls, district, prop_type, session)
                    # Polite inter-run delay
                    await asyncio.sleep(3)

        logger.info("All parsers done. Rebuilding benchmarks...")
        n = await rebuild_benchmarks(session)
        logger.info("Benchmarks ready: %d rows.", n)
