"""
Scraper orchestrator — runs all parsers, persists results, rebuilds benchmarks.

FazWaz note: their server-side district filter is broken — it always returns
all Phuket listings regardless of area_name param. FazWazParser therefore
ignores the district param in the URL and parses the real district from each
card's .location-unit text. We run it once per property type (not per district).

DotProperty does support district filtering via ?location=, so we run it
once per district × property type.
"""
import asyncio
import logging
from datetime import datetime
from typing import Type

from sqlalchemy.ext.asyncio import AsyncSession

from config import SCRAPER_MAX_PAGES
from db.database import AsyncSessionLocal, init_db
from db.models import ScraperRun
from parsers.base import BaseParser
from parsers.fazwaz import FazWazParser
from parsers.dotproperty import DotPropertyParser
from parsers.normalizer import upsert_listing, rebuild_benchmarks

logger = logging.getLogger(__name__)

ALL_PARSERS: list[Type[BaseParser]] = [FazWazParser, DotPropertyParser]

DEFAULT_DISTRICTS = [
    "bang-tao", "kamala", "surin", "layan",
    "rawai", "nai-harn", "patong", "cherng-talay",
]
DEFAULT_PROP_TYPES = ["condo", "villa"]

# Parsers that return ALL districts in one pass (server ignores district filter)
DISTRICT_AGNOSTIC = {FazWazParser.SOURCE}


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
    """Main entry point: scrape then rebuild benchmarks.

    District-agnostic parsers (FazWaz) run once per property type.
    District-aware parsers (DotProperty) run once per district × type.
    """
    await init_db()

    districts = districts or DEFAULT_DISTRICTS
    property_types = property_types or DEFAULT_PROP_TYPES
    parsers = parsers or ALL_PARSERS

    async with AsyncSessionLocal() as session:
        for parser_cls in parsers:
            if parser_cls.SOURCE in DISTRICT_AGNOSTIC:
                # Run once per type — district is parsed from each card
                logger.info(
                    "[%s] district-agnostic: running %d type(s) × 1",
                    parser_cls.SOURCE, len(property_types),
                )
                for prop_type in property_types:
                    await run_parser(parser_cls, "all-phuket", prop_type, session)
                    await asyncio.sleep(3)
            else:
                # Run per district × type
                logger.info(
                    "[%s] district-aware: %d districts × %d types",
                    parser_cls.SOURCE, len(districts), len(property_types),
                )
                for district in districts:
                    for prop_type in property_types:
                        await run_parser(parser_cls, district, prop_type, session)
                        await asyncio.sleep(3)

        logger.info("All parsers done. Rebuilding benchmarks...")
        n = await rebuild_benchmarks(session)
        logger.info("Benchmarks ready: %d rows.", n)
