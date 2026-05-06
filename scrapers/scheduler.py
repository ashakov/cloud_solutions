"""
Automated scraping scheduler using APScheduler.

Schedule (Bangkok time, UTC+7):
  02:00 daily  — portal scrapers (FazWaz, DotProperty, DDProperty, Hipflat, Kaidee)
  03:30 daily  — rental scrapers (Airbnb)
  Sunday 04:00 — full re-scrape + benchmark rebuild

Usage:
  python main.py schedule          # start scheduler (blocks)
  python main.py schedule --once   # run all scrapers once immediately

Configure intervals in .env:
  SCHEDULE_PORTAL_CRON=0 2 * * *
  SCHEDULE_RENTAL_CRON=30 3 * * *
"""
import asyncio
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from scrapers.runner import run_all
from parsers.fazwaz import FazWazParser
from parsers.dotproperty import DotPropertyParser
from parsers.ddproperty import DDPropertyParser
from parsers.hipflat import HipflatParser
from parsers.kaidee import KaideeParser
from parsers.airbnb import AirbnbParser

logger = logging.getLogger(__name__)

# ── Parser groups ─────────────────────────────────────────────────────────────

SALE_PARSERS = [FazWazParser, DotPropertyParser, DDPropertyParser, HipflatParser, KaideeParser]
RENTAL_PARSERS = [AirbnbParser]

PORTAL_CRON = os.getenv("SCHEDULE_PORTAL_CRON", "0 2 * * *")    # 02:00 daily
RENTAL_CRON = os.getenv("SCHEDULE_RENTAL_CRON",  "30 3 * * *")  # 03:30 daily
FULL_CRON   = os.getenv("SCHEDULE_FULL_CRON",    "0 4 * * 0")   # Sunday 04:00

DISTRICTS = [
    "bang-tao", "kamala", "surin", "layan",
    "rawai", "nai-harn", "patong", "cherng-talay",
    "kata", "karon", "chalong", "phuket-town", "mai-khao",
]
PROP_TYPES = ["condo", "villa"]


async def _run_sale() -> None:
    logger.info("[Scheduler] Starting portal (sale) scrape run")
    try:
        await run_all(districts=DISTRICTS, property_types=PROP_TYPES, parsers=SALE_PARSERS)
    except Exception as exc:
        logger.exception("[Scheduler] Sale scrape failed: %s", exc)


async def _run_rental() -> None:
    logger.info("[Scheduler] Starting rental scrape run")
    try:
        await run_all(districts=DISTRICTS, property_types=PROP_TYPES, parsers=RENTAL_PARSERS)
    except Exception as exc:
        logger.exception("[Scheduler] Rental scrape failed: %s", exc)


async def _run_full() -> None:
    logger.info("[Scheduler] Starting FULL scrape run (sale + rental)")
    await _run_sale()
    await _run_rental()


def build_scheduler() -> AsyncIOScheduler:
    tz = "Asia/Bangkok"
    scheduler = AsyncIOScheduler(timezone=tz)

    def parse_cron(expr: str) -> CronTrigger:
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron: {expr!r}")
        minute, hour, day, month, dow = parts
        return CronTrigger(
            minute=minute, hour=hour, day=day,
            month=month, day_of_week=dow, timezone=tz,
        )

    scheduler.add_job(_run_sale,   parse_cron(PORTAL_CRON), id="sale",   replace_existing=True)
    scheduler.add_job(_run_rental, parse_cron(RENTAL_CRON), id="rental", replace_existing=True)
    scheduler.add_job(_run_full,   parse_cron(FULL_CRON),   id="full",   replace_existing=True)

    return scheduler


async def run_scheduled() -> None:
    """Start the scheduler and block until Ctrl+C."""
    scheduler = build_scheduler()
    scheduler.start()

    next_jobs = {
        job.id: job.next_run_time.strftime("%Y-%m-%d %H:%M %Z")
        for job in scheduler.get_jobs()
    }
    logger.info("[Scheduler] Started. Next runs: %s", next_jobs)

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("[Scheduler] Shutting down...")
        scheduler.shutdown()
